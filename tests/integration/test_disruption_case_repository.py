"""DisruptionCase persistence: state and events are committed together, or not at all."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support import T0
from tracer.modules.supply_chain.adapters.persistence.models import DisruptionCaseRecord
from tracer.modules.supply_chain.adapters.persistence.unit_of_work import (
    SqlAlchemySupplyChainUnitOfWork,
)
from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.modules.supply_chain.domain.values import (
    AffectedEntities,
    CaseResolution,
    CaseStatus,
    DisruptionKind,
    ResolutionOutcome,
)
from tracer.platform.messaging.envelope import MessageContext
from tracer.platform.messaging.models import OutboxEventRecord
from tracer.shared.clock import FrozenClock
from tracer.shared.errors import ConcurrencyConflictError
from tracer.shared.identifiers import (
    EvidenceId,
    RecoveryPlanId,
    ShipmentId,
    SupplierId,
    TenantId,
    UserId,
)
from tracer.shared.time_range import TimeRange

pytestmark = pytest.mark.integration

TENANT = TenantId.new()
OPERATOR = UserId.new()
SUPPLIER = SupplierId.new()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


def unit_of_work(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock, request_id: str = "req-1"
) -> SqlAlchemySupplyChainUnitOfWork:
    return SqlAlchemySupplyChainUnitOfWork(
        sessions, clock, MessageContext(correlation_id=request_id)
    )


def new_case(clock: FrozenClock) -> DisruptionCase:
    return DisruptionCase.detect(
        tenant_id=TENANT,
        kind=DisruptionKind.SHIPMENT_DELAY,
        summary="SHIP-501 delayed by 7 days",
        evidence_ids=[EvidenceId.new()],
        affected=AffectedEntities(supplier_ids=frozenset({SUPPLIER})),
        impact_window=TimeRange(T0, T0 + timedelta(days=7)),
        clock=clock,
    )


async def outbox_rows(sessions: async_sessionmaker[AsyncSession]) -> list[OutboxEventRecord]:
    async with sessions() as session:
        query = select(OutboxEventRecord).order_by(OutboxEventRecord.aggregate_version)
        return list((await session.execute(query)).scalars())


async def case_count(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions() as session:
        return int(
            await session.scalar(select(func.count()).select_from(DisruptionCaseRecord)) or 0
        )


async def test_new_case_and_its_event_are_committed_together(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()

    [row] = await outbox_rows(sessions)
    assert row.event_type == "supply_chain.disruption_detected"
    assert row.schema_version == 1
    assert row.aggregate_type == "disruption_case"
    assert row.aggregate_id == case.id.value
    assert row.aggregate_version == 1
    assert row.tenant_id == TENANT.value
    assert row.correlation_id == "req-1"
    assert row.status == "pending"
    assert row.payload["case_id"] == str(case.id)
    assert row.payload["kind"] == "shipment_delay"
    assert row.payload["affected"]["supplier_ids"] == [str(SUPPLIER)]
    assert case.version == 1


async def test_events_stay_pending_until_the_commit_succeeds(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        assert case.version == 0  # saved, not yet committed
        assert len(case.pending_events) == 1
        await uow.commit()
    assert case.version == 1
    assert not case.pending_events


async def test_a_failed_commit_keeps_the_events_and_a_retry_succeeds(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()

    case.start_investigation(clock)

    async def save_but_fail() -> None:
        async with unit_of_work(sessions, clock) as uow:
            await uow.cases.update(case)
            await uow.session.execute(text("SELECT 1/0"))  # the transaction fails
            await uow.commit()

    with pytest.raises(DBAPIError, match="division by zero"):
        await save_but_fail()
    # still at version 1 with its event pending: nothing was lost
    assert case.version == 1
    assert len(case.pending_events) == 1

    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.update(case)  # the same instance, saved again
        await uow.commit()
    assert case.version == 2
    assert [r.aggregate_version for r in await outbox_rows(sessions)] == [1, 2]


async def test_without_commit_nothing_is_stored(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(new_case(clock))
        # no commit: leaving the block rolls back

    assert await case_count(sessions) == 0
    assert await outbox_rows(sessions) == []


async def test_an_error_rolls_back_state_and_events(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    async def save_then_crash() -> None:
        async with unit_of_work(sessions, clock) as uow:
            await uow.cases.add(new_case(clock))
            raise RuntimeError("crash between saving and committing")

    with pytest.raises(RuntimeError):
        await save_then_crash()

    assert await case_count(sessions) == 0
    assert await outbox_rows(sessions) == []


async def test_round_trip_through_every_column(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()

    async with unit_of_work(sessions, clock) as uow:
        loaded = await uow.cases.get(TENANT, case.id)
        assert loaded is not None
        loaded.start_investigation(clock)
        loaded.record_impact(AffectedEntities(shipment_ids=frozenset({ShipmentId.new()})), clock)
        resolution = CaseResolution(
            ResolutionOutcome.RECOVERED, "reallocated", RecoveryPlanId.new()
        )
        loaded.resolve(resolution, by=OPERATOR, clock=clock)
        await uow.cases.update(loaded)
        await uow.commit()

    async with unit_of_work(sessions, clock) as uow:
        final = await uow.cases.get(TENANT, case.id)
    assert final is not None
    assert final.status is CaseStatus.RESOLVED
    assert final.version == 4
    assert final.evidence_ids == case.evidence_ids
    assert final.impact_window == case.impact_window
    assert final.closure is not None
    assert final.closure.resolution == resolution
    assert final.affected.supplier_ids == {SUPPLIER}
    assert len(final.affected.shipment_ids) == 1

    rows = await outbox_rows(sessions)
    assert [(r.aggregate_version, r.event_type) for r in rows] == [
        (1, "supply_chain.disruption_detected"),
        (2, "supply_chain.investigation_started"),
        (3, "supply_chain.impact_recorded"),
        (4, "supply_chain.disruption_resolved"),
    ]


async def test_concurrent_change_is_detected_and_loses_nothing(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()

    # Two operators load version 1 of the same case at the same time.
    async with unit_of_work(sessions, clock) as first, unit_of_work(sessions, clock) as second:
        mine = await first.cases.get(TENANT, case.id)
        theirs = await second.cases.get(TENANT, case.id)
        assert mine is not None
        assert theirs is not None

        mine.start_investigation(clock)
        await first.cases.update(mine)
        await first.commit()

        theirs.dismiss("looks like a duplicate", by=OPERATOR, clock=clock)
        with pytest.raises(ConcurrencyConflictError, match="expected version 1"):
            await second.cases.update(theirs)

    async with unit_of_work(sessions, clock) as uow:
        stored = await uow.cases.get(TENANT, case.id)
    assert stored is not None
    assert stored.status is CaseStatus.INVESTIGATING  # the first change won
    assert [r.event_type for r in await outbox_rows(sessions)] == [
        "supply_chain.disruption_detected",
        "supply_chain.investigation_started",  # the loser's event was never written
    ]


async def test_another_tenant_cannot_read_the_case(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()

    async with unit_of_work(sessions, clock) as uow:
        assert await uow.cases.get(TenantId.new(), case.id) is None


async def test_update_without_changes_writes_nothing(
    sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = new_case(clock)
    async with unit_of_work(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()

    async with unit_of_work(sessions, clock) as uow:
        loaded = await uow.cases.get(TENANT, case.id)
        assert loaded is not None
        loaded.record_evidence(next(iter(case.evidence_ids)), clock)  # already known: no event
        await uow.cases.update(loaded)
        await uow.commit()

    assert len(await outbox_rows(sessions)) == 1
