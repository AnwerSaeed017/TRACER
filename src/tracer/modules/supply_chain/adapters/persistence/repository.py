"""PostgreSQL repository for DisruptionCase.

Mapping is explicit (domain <-> row), not ORM-mapped domain classes: the domain stays pure
Python, and every column is visible here. Saving a case also writes its pending events to
the outbox in the same session, so state and events are committed together or not at all.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracer.modules.supply_chain.adapters.persistence.models import DisruptionCaseRecord
from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.modules.supply_chain.domain.values import (
    OPEN_STATUSES,
    AffectedEntities,
    CaseClosure,
    CaseResolution,
    CaseStatus,
    DisruptionKind,
    ResolutionOutcome,
)
from tracer.platform.messaging.outbox import OutboxWriter
from tracer.shared.aggregate import AggregateRoot
from tracer.shared.errors import ConcurrencyConflictError
from tracer.shared.identifiers import (
    CustomerOrderId,
    DisruptionCaseId,
    EvidenceId,
    ProductId,
    RecoveryPlanId,
    ShipmentId,
    SupplierId,
    TenantId,
    UserId,
)
from tracer.shared.time_range import TimeRange


class SqlAlchemyDisruptionCaseRepository:
    def __init__(
        self,
        session: AsyncSession,
        outbox: OutboxWriter,
        track: Callable[[AggregateRoot], None],
    ) -> None:
        self._session = session
        self._outbox = outbox
        self._track = track  # the unit of work marks events committed only after COMMIT

    async def get(self, tenant_id: TenantId, case_id: DisruptionCaseId) -> DisruptionCase | None:
        statement = select(DisruptionCaseRecord).where(
            DisruptionCaseRecord.id == case_id.value,
            DisruptionCaseRecord.tenant_id == tenant_id.value,
        )
        record = (await self._session.execute(statement)).scalar_one_or_none()
        return None if record is None else _to_domain(record)

    async def find_open_by_evidence(
        self, tenant_id: TenantId, evidence_id: EvidenceId
    ) -> DisruptionCase | None:
        statement = (
            select(DisruptionCaseRecord)
            .where(
                DisruptionCaseRecord.tenant_id == tenant_id.value,
                DisruptionCaseRecord.evidence_ids.contains([evidence_id.value]),  # GIN-indexed @>
                DisruptionCaseRecord.status.in_([s.value for s in OPEN_STATUSES]),
            )
            .order_by(DisruptionCaseRecord.detected_at, DisruptionCaseRecord.id)
            .limit(1)
        )
        record = (await self._session.execute(statement)).scalar_one_or_none()
        return None if record is None else _to_domain(record)

    async def add(self, case: DisruptionCase) -> None:
        events = case.pending_events
        self._session.add(DisruptionCaseRecord(**_columns(case, case.version + len(events))))
        self._outbox.append(
            aggregate_type=case.AGGREGATE_TYPE,
            aggregate_id=case.id.value,
            first_version=case.version + 1,
            events=events,
        )
        self._track(case)

    async def update(self, case: DisruptionCase) -> None:
        expected_version = case.version
        events = case.pending_events
        if not events:
            return  # nothing happened, nothing to save
        columns = _columns(case, expected_version + len(events))
        del columns["id"], columns["tenant_id"]
        result = await self._session.execute(
            update(DisruptionCaseRecord)
            .where(
                DisruptionCaseRecord.id == case.id.value,
                DisruptionCaseRecord.tenant_id == case.tenant_id.value,
                DisruptionCaseRecord.version == expected_version,
            )
            .values(**columns)
            .execution_options(synchronize_session=False)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise ConcurrencyConflictError(case.AGGREGATE_TYPE, str(case.id), expected_version)
        self._outbox.append(
            aggregate_type=case.AGGREGATE_TYPE,
            aggregate_id=case.id.value,
            first_version=expected_version + 1,
            events=events,
        )
        self._track(case)


# ---------------------------------------------------------------------- mapping


def _ids(values: frozenset[Any]) -> list[str]:
    return sorted(str(v) for v in values)


def _affected_to_json(affected: AffectedEntities) -> dict[str, list[str]]:
    return {
        "supplier_ids": _ids(affected.supplier_ids),
        "shipment_ids": _ids(affected.shipment_ids),
        "product_ids": _ids(affected.product_ids),
        "customer_order_ids": _ids(affected.customer_order_ids),
    }


def _affected_from_json(data: dict[str, Any]) -> AffectedEntities:
    return AffectedEntities(
        supplier_ids=frozenset(SupplierId.parse(v) for v in data["supplier_ids"]),
        shipment_ids=frozenset(ShipmentId.parse(v) for v in data["shipment_ids"]),
        product_ids=frozenset(ProductId.parse(v) for v in data["product_ids"]),
        customer_order_ids=frozenset(CustomerOrderId.parse(v) for v in data["customer_order_ids"]),
    )


def _closure_to_json(closure: CaseClosure | None) -> dict[str, Any] | None:
    if closure is None:
        return None
    resolution = closure.resolution
    return {
        "closed_at": closure.closed_at.isoformat(),
        "closed_by": str(closure.closed_by),
        "dismissal_reason": closure.dismissal_reason,
        "resolution": None
        if resolution is None
        else {
            "outcome": resolution.outcome.value,
            "summary": resolution.summary,
            "recovery_plan_id": str(resolution.recovery_plan_id)
            if resolution.recovery_plan_id
            else None,
        },
    }


def _closure_from_json(data: dict[str, Any] | None) -> CaseClosure | None:
    if data is None:
        return None
    resolution = data["resolution"]
    return CaseClosure(
        closed_at=datetime.fromisoformat(data["closed_at"]),
        closed_by=UserId.parse(data["closed_by"]),
        dismissal_reason=data["dismissal_reason"],
        resolution=None
        if resolution is None
        else CaseResolution(
            outcome=ResolutionOutcome(resolution["outcome"]),
            summary=resolution["summary"],
            recovery_plan_id=RecoveryPlanId.parse(resolution["recovery_plan_id"])
            if resolution["recovery_plan_id"]
            else None,
        ),
    )


def _columns(case: DisruptionCase, version: int) -> dict[str, Any]:
    """Row values for ``case`` as it will be once its pending events are committed."""
    window = case.impact_window
    return {
        "id": case.id.value,
        "tenant_id": case.tenant_id.value,
        "kind": case.kind.value,
        "summary": case.summary,
        "status": case.status.value,
        "detected_at": case.detected_at,
        "evidence_ids": sorted(e.value for e in case.evidence_ids),
        "affected": _affected_to_json(case.affected),
        "impact_start": window.start if window else None,
        "impact_end": window.end if window else None,
        "closure": _closure_to_json(case.closure),
        "version": version,
    }


def _to_domain(record: DisruptionCaseRecord) -> DisruptionCase:
    window = (
        TimeRange(record.impact_start, record.impact_end)
        if record.impact_start is not None and record.impact_end is not None
        else None
    )
    return DisruptionCase(
        case_id=DisruptionCaseId(record.id),
        tenant_id=TenantId(record.tenant_id),
        kind=DisruptionKind(record.kind),
        summary=record.summary,
        detected_at=record.detected_at,
        evidence_ids=frozenset(EvidenceId(UUID(str(e))) for e in record.evidence_ids),
        affected=_affected_from_json(record.affected),
        impact_window=window,
        status=CaseStatus(record.status),
        closure=_closure_from_json(record.closure),
        version=record.version,
    )
