"""Outbox delivery against real PostgreSQL: at-least-once delivery, idempotent consumption.

The acceptance test of M2 step 3 is ``test_a_crash_after_the_consumer_commits_...``: an
event is not lost when the worker dies, and replaying it does not create a second case.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx2 as httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support import T0, WEBHOOK_SOURCE, report_command, signed_headers, webhook_settings
from tracer.bootstrap.composition_root import Container, build_container, dispatch_policy
from tracer.entrypoints.http.app import create_app
from tracer.modules.supply_chain.adapters.persistence.models import DisruptionCaseRecord
from tracer.modules.supply_chain.adapters.persistence.unit_of_work import (
    SqlAlchemySupplyChainUnitOfWork,
)
from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.modules.supply_chain.domain.values import DisruptionKind
from tracer.platform.messaging.dispatcher import EventHandler, OutboxDispatcher, Outcome
from tracer.platform.messaging.envelope import EventEnvelope, MessageContext
from tracer.platform.messaging.models import OutboxEventRecord, OutboxStatus
from tracer.shared.clock import FrozenClock
from tracer.shared.identifiers import EvidenceId, TenantId

pytestmark = pytest.mark.integration

REPORT_RECEIVED = "ingestion.disruption_report_received"
DETECTED = "supply_chain.disruption_detected"
LEASE = timedelta(seconds=30)


def no_jitter(low: float, high: float) -> float:
    return high  # deterministic: always the full backoff


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
async def container(
    test_database_url: str, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> AsyncIterator[Container]:
    settings = webhook_settings(database_url=test_database_url, outbox_max_attempts=3)
    container = build_container(settings, clock, worker_id="worker-a", jitter=no_jitter)
    yield container
    await container.aclose()


def dispatcher(
    container: Container, worker_id: str, handlers: tuple[EventHandler, ...] | None = None
) -> OutboxDispatcher:
    return OutboxDispatcher(
        container.session_factory,
        container.clock,
        container.event_handlers if handlers is None else handlers,
        dispatch_policy(container.settings),
        worker_id,
        no_jitter,
    )


async def outbox(sessions: async_sessionmaker[AsyncSession]) -> list[OutboxEventRecord]:
    async with sessions() as session:
        rows = await session.execute(
            select(OutboxEventRecord).order_by(
                OutboxEventRecord.recorded_at, OutboxEventRecord.aggregate_version
            )
        )
        return list(rows.scalars())


async def outbox_of_type(
    sessions: async_sessionmaker[AsyncSession], event_type: str
) -> list[OutboxEventRecord]:
    return [row for row in await outbox(sessions) if row.event_type == event_type]


async def cases(sessions: async_sessionmaker[AsyncSession]) -> list[DisruptionCaseRecord]:
    async with sessions() as session:
        return list((await session.execute(select(DisruptionCaseRecord))).scalars())


async def receive(container: Container, event_id: str, tenant: TenantId, **kw: Any) -> UUID:
    receipt = await container.disruption_intake.receive(
        report_command(event_id, tenant, **kw), MessageContext()
    )
    return receipt.document_id.value


class FailingFirst:
    """Wraps a handler and fails its first ``failures`` calls, like a database blip would."""

    def __init__(self, inner: EventHandler, failures: int = 1) -> None:
        self._inner = inner
        self._failures = failures
        self.calls = 0

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def event_types(self) -> frozenset[str]:
        return self._inner.event_types

    async def handle(self, envelope: EventEnvelope) -> None:
        self.calls += 1
        if self.calls <= self._failures:
            raise ConnectionError("database blip")
        await self._inner.handle(envelope)


class Recorder:
    def __init__(self, event_types: frozenset[str], *, fail_first: int = 0) -> None:
        self._event_types = event_types
        self._fail_first = fail_first
        self.seen: list[EventEnvelope] = []
        self.calls = 0

    @property
    def name(self) -> str:
        return "test.recorder"

    @property
    def event_types(self) -> frozenset[str]:
        return self._event_types

    async def handle(self, envelope: EventEnvelope) -> None:
        self.calls += 1
        if self.calls <= self._fail_first:
            raise ConnectionError("blip")
        await asyncio.sleep(0.005)  # long enough for concurrent workers to interleave
        self.seen.append(envelope)


# ---------------------------------------------------------------------- end to end


async def test_a_signed_webhook_becomes_a_disruption_case(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    body = json.dumps(
        {
            "kind": "port_congestion",
            "summary": "Karachi port closed for 48 hours",
            "impact_start": "2026-11-05T09:00:00Z",
            "impact_end": "2026-11-07T09:00:00Z",
        }
    ).encode()
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://tracer") as client:
        response = await client.post(
            f"/webhooks/{WEBHOOK_SOURCE}/disruption-reports",
            content=body,
            headers=signed_headers(body, event_id="evt-e2e", at=T0),
        )
    assert response.status_code == 202
    document_id = response.json()["document_id"]

    assert await container.outbox_dispatcher.dispatch_once() == 1

    [case] = await cases(sessions)
    assert case.kind == "port_congestion"
    assert case.summary == "Karachi port closed for 48 hours"
    assert case.status == "detected"
    assert [str(e) for e in case.evidence_ids] == [document_id]  # the raw webhook is evidence
    assert case.impact_start == T0

    [report] = await outbox_of_type(sessions, REPORT_RECEIVED)
    [detected] = await outbox_of_type(sessions, DETECTED)
    assert report.status == "published"
    assert report.attempts == 1
    assert detected.status == "pending"  # the case's own event, next in line
    assert detected.causation_id == str(report.event_id)  # caused by the report event
    assert detected.correlation_id == response.headers["X-Request-ID"]  # same request, end to end

    assert await container.outbox_dispatcher.dispatch_once() == 1  # DisruptionDetected
    assert await container.outbox_dispatcher.dispatch_once() == 0  # nothing left
    assert {row.status for row in await outbox(sessions)} == {"published"}


# ---------------------------------------------------------------------- the acceptance test


async def test_a_crash_after_the_consumer_commits_loses_nothing_and_duplicates_nothing(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    await receive(container, "evt-crash", TenantId.new())
    worker_a = container.outbox_dispatcher

    # 1-3. Worker A claims the event and the consumer commits a new case ...
    [claimed] = await worker_a.claim_batch()
    [consumer] = container.event_handlers
    await consumer.handle(claimed.envelope)
    assert len(await cases(sessions)) == 1
    # 4. ... then worker A dies before acknowledging. The event is still "processing".
    [stuck] = await outbox_of_type(sessions, REPORT_RECEIVED)
    assert stuck.status == "processing"
    assert stuck.claimed_by == "worker-a"

    # 5-6. The lease expires; worker B claims the event again and redelivers it.
    clock.advance(LEASE + timedelta(seconds=1))
    worker_b = dispatcher(container, "worker-b")
    batch = await worker_b.claim_batch()  # also picks up the case's own DisruptionDetected
    [reclaimed] = [c for c in batch if c.envelope.event_type == REPORT_RECEIVED]
    assert reclaimed.envelope.event_id == claimed.envelope.event_id
    assert reclaimed.attempt == 2
    # 7-9. The consumer recognises the event, changes nothing; the event is acknowledged.
    for event in batch:
        assert await worker_b.deliver(event) is Outcome.PUBLISHED

    assert len(await cases(sessions)) == 1  # one case, not two
    assert len(await outbox_of_type(sessions, DETECTED)) == 1  # one DisruptionDetected
    [report] = await outbox_of_type(sessions, REPORT_RECEIVED)
    assert report.status == "published"

    # Worker A comes back to life and tries to settle: fenced out, nothing overwritten.
    assert await worker_a.deliver(claimed) is Outcome.LEASE_LOST
    assert len(await cases(sessions)) == 1


async def test_a_failure_before_the_consumer_commits_is_retried_and_applied_once(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    await receive(container, "evt-blip", TenantId.new())
    flaky = FailingFirst(container.event_handlers[0])
    worker = dispatcher(container, "worker-a", (flaky,))

    assert await worker.dispatch_once() == 1
    [row] = await outbox_of_type(sessions, REPORT_RECEIVED)
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_attempt_at == T0 + timedelta(seconds=1)  # backoff for attempt 1
    assert row.last_error == "ConnectionError: database blip"
    assert await cases(sessions) == []  # the failed attempt left nothing behind

    assert await worker.dispatch_once() == 0  # not due yet
    clock.advance(timedelta(seconds=1))
    assert await worker.dispatch_once() == 1

    assert len(await cases(sessions)) == 1
    [row] = await outbox_of_type(sessions, REPORT_RECEIVED)
    assert (row.status, row.attempts, row.last_error) == ("published", 2, None)


# ---------------------------------------------------------------------- dead letters


async def test_a_permanent_failure_is_dead_lettered_at_once_and_can_be_replayed(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await receive(container, "evt-bad-kind", TenantId.new(), kind="alien_invasion")

    assert await container.outbox_dispatcher.dispatch_once() == 1
    [row] = await outbox(sessions)
    assert row.status == "dead"
    assert row.attempts == 1  # no pointless retries
    assert row.last_error is not None
    assert "unknown disruption kind 'alien_invasion'" in row.last_error
    assert await cases(sessions) == []

    assert await container.outbox_dispatcher.replay_dead(row.event_id)
    [replayed] = await outbox(sessions)
    assert (replayed.status, replayed.attempts) == ("pending", 0)
    assert not await container.outbox_dispatcher.replay_dead(row.event_id)  # no longer dead


async def test_retries_stop_after_the_maximum_attempts(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    await receive(container, "evt-always-fails", TenantId.new())
    worker = dispatcher(container, "worker-a", (FailingFirst(container.event_handlers[0], 99),))

    for _ in range(3):  # outbox_max_attempts=3 in this test's settings
        clock.advance(timedelta(minutes=10))
        assert await worker.dispatch_once() == 1

    [row] = await outbox(sessions)
    assert (row.status, row.attempts) == ("dead", 3)
    clock.advance(timedelta(hours=1))
    assert await worker.dispatch_once() == 0  # dead events are never picked up again


# ---------------------------------------------------------------------- concurrency and order


async def test_concurrent_workers_deliver_each_event_exactly_once(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    tenant = TenantId.new()
    for n in range(20):
        await receive(container, f"evt-{n}", tenant, content=f'{{"n":{n}}}'.encode())
    recorder = Recorder(frozenset({REPORT_RECEIVED}))
    workers = [dispatcher(container, f"worker-{n}", (recorder,)) for n in range(3)]

    async def drain(worker: OutboxDispatcher) -> None:
        while await worker.dispatch_once():
            pass

    await asyncio.gather(*(drain(w) for w in workers))

    delivered = [e.event_id for e in recorder.seen]
    assert len(delivered) == 20
    assert len(set(delivered)) == 20  # SKIP LOCKED: no event claimed by two workers
    assert {row.status for row in await outbox_of_type(sessions, REPORT_RECEIVED)} == {"published"}


async def test_an_aggregates_events_are_delivered_in_order_even_across_retries(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    case = DisruptionCase.detect(
        tenant_id=TenantId.new(),
        kind=DisruptionKind.OTHER,
        summary="ordering",
        evidence_ids=[EvidenceId.new()],
        clock=clock,
    )
    async with SqlAlchemySupplyChainUnitOfWork(sessions, clock) as uow:
        await uow.cases.add(case)
        await uow.commit()
    case.start_investigation(clock)
    async with SqlAlchemySupplyChainUnitOfWork(sessions, clock) as uow:
        await uow.cases.update(case)
        await uow.commit()

    recorder = Recorder(frozenset({DETECTED, "supply_chain.investigation_started"}), fail_first=1)
    worker = dispatcher(container, "worker-a", (recorder,))

    assert await worker.dispatch_once() == 1  # only version 1 is claimable; it fails
    assert await worker.dispatch_once() == 0  # v1 waits for its retry, so v2 must wait too
    clock.advance(timedelta(seconds=1))
    assert await worker.dispatch_once() == 1  # v1 delivered
    assert await worker.dispatch_once() == 1  # now v2

    assert [e.aggregate_version for e in recorder.seen] == [1, 2]


# ---------------------------------------------------------------------- business rule


async def test_an_identical_report_adds_evidence_to_the_open_case_instead_of_a_new_case(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    tenant = TenantId.new()
    first = await receive(container, "evt-monday", tenant)
    await container.outbox_dispatcher.dispatch_once()
    clock.advance(timedelta(days=1))
    second = await receive(container, "evt-tuesday", tenant)  # "still delayed", same bytes
    while await container.outbox_dispatcher.dispatch_once():
        pass

    [case] = await cases(sessions)
    assert set(case.evidence_ids) == {first, second}
    assert [
        r.event_type for r in await outbox(sessions) if r.aggregate_type == "disruption_case"
    ] == [
        DETECTED,
        "supply_chain.evidence_recorded",
    ]


# ---------------------------------------------------------------------- the worker loop


async def test_the_worker_loop_processes_events_and_stops_cleanly(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await receive(container, "evt-loop", TenantId.new())
    stop = asyncio.Event()
    worker = asyncio.create_task(
        container.outbox_dispatcher.run(stop, poll_interval=timedelta(milliseconds=20))
    )
    # Wait for the condition that is asserted below (both events published), not merely for
    # the case to exist: the case's own event is published on the worker's next pass.
    for _ in range(200):
        rows = await outbox(sessions)
        if len(rows) == 2 and all(row.status == OutboxStatus.PUBLISHED.value for row in rows):
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(worker, timeout=2)

    assert len(await cases(sessions)) == 1
    assert {row.status for row in await outbox(sessions)} == {OutboxStatus.PUBLISHED.value}
