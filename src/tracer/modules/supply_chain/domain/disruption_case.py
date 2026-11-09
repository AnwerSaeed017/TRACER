"""DisruptionCase: the lifecycle of one operational disruption.

    DETECTED ──start_investigation──► INVESTIGATING ──resolve──► RESOLVED
        │                                   │
        └──────────────dismiss──────────────┴──────────────────► DISMISSED

- A case is detected *from evidence*: at least one evidence id is required.
- Evidence and affected entities can be added while the case is open; adding something
  already known changes nothing and records no event (safe to repeat).
- Only a case under investigation can be resolved, and only with a resolution record.
- Resolved and dismissed are final.
"""

from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

from tracer.modules.supply_chain.domain.events import (
    DisruptionDetected,
    DisruptionDismissed,
    DisruptionResolved,
    EvidenceRecorded,
    ImpactRecorded,
    InvestigationStarted,
)
from tracer.modules.supply_chain.domain.values import (
    MAX_REASON_LENGTH,
    MAX_SUMMARY_LENGTH,
    OPEN_STATUSES,
    AffectedEntities,
    CaseClosure,
    CaseResolution,
    CaseStatus,
    DisruptionKind,
)
from tracer.shared.aggregate import AggregateRoot, ensure_status
from tracer.shared.clock import Clock
from tracer.shared.errors import BusinessRuleError, InvalidValueError
from tracer.shared.guards import require_text, require_utc
from tracer.shared.identifiers import DisruptionCaseId, EvidenceId, TenantId, UserId
from tracer.shared.time_range import TimeRange

_NAME = "disruption case"


class DisruptionCase(AggregateRoot):
    AGGREGATE_TYPE: ClassVar[str] = "disruption_case"

    def __init__(
        self,
        *,
        case_id: DisruptionCaseId,
        tenant_id: TenantId,
        kind: DisruptionKind,
        summary: str,
        detected_at: datetime,
        evidence_ids: frozenset[EvidenceId],
        affected: AffectedEntities,
        impact_window: TimeRange | None,
        status: CaseStatus,
        closure: CaseClosure | None,
        version: int = 0,
    ) -> None:
        """Rebuild a case from known state. New cases come from ``detect``."""
        super().__init__(version=version)
        self._id = case_id
        self._tenant_id = tenant_id
        self._kind = DisruptionKind(kind)
        self._summary = require_text(summary, "summary", max_length=MAX_SUMMARY_LENGTH)
        self._detected_at = require_utc(detected_at, "detected_at")
        self._evidence_ids = evidence_ids
        self._affected = affected
        self._impact_window = impact_window
        self._status = CaseStatus(status)
        self._closure = closure
        self._check_invariants()

    def _check_invariants(self) -> None:
        if not self._evidence_ids:
            raise BusinessRuleError("a disruption case needs at least one piece of evidence")
        closed = self._status not in OPEN_STATUSES
        if closed != (self._closure is not None):
            raise InvalidValueError("only resolved or dismissed cases have a closure")
        if self._closure is not None:
            resolved = self._closure.resolution is not None
            if resolved != (self._status is CaseStatus.RESOLVED):
                raise InvalidValueError("the closure does not match the case status")

    @classmethod
    def detect(
        cls,
        *,
        tenant_id: TenantId,
        kind: DisruptionKind,
        summary: str,
        evidence_ids: Iterable[EvidenceId],
        clock: Clock,
        affected: AffectedEntities | None = None,
        impact_window: TimeRange | None = None,
        case_id: DisruptionCaseId | None = None,
    ) -> "DisruptionCase":
        case = cls(
            case_id=case_id or DisruptionCaseId.new(),
            tenant_id=tenant_id,
            kind=kind,
            summary=summary,
            detected_at=clock.now(),
            evidence_ids=frozenset(evidence_ids),
            affected=affected or AffectedEntities(),
            impact_window=impact_window,
            status=CaseStatus.DETECTED,
            closure=None,
        )
        case._record(
            DisruptionDetected(
                tenant_id=case.tenant_id,
                occurred_at=case.detected_at,
                case_id=case.id,
                kind=case.kind,
                evidence_ids=case.evidence_ids,
                affected=case.affected,
            )
        )
        return case

    # ------------------------------------------------------------------ state

    @property
    def id(self) -> DisruptionCaseId:
        return self._id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def kind(self) -> DisruptionKind:
        return self._kind

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def detected_at(self) -> datetime:
        return self._detected_at

    @property
    def evidence_ids(self) -> frozenset[EvidenceId]:
        return self._evidence_ids

    @property
    def affected(self) -> AffectedEntities:
        return self._affected

    @property
    def impact_window(self) -> TimeRange | None:
        return self._impact_window

    @property
    def status(self) -> CaseStatus:
        return self._status

    @property
    def closure(self) -> CaseClosure | None:
        return self._closure

    @property
    def is_open(self) -> bool:
        return self._status in OPEN_STATUSES

    # ---------------------------------------------------------------- actions

    def record_evidence(self, evidence_id: EvidenceId, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "add evidence to", OPEN_STATUSES)
        if evidence_id in self._evidence_ids:
            return
        self._evidence_ids = self._evidence_ids | {evidence_id}
        self._record(
            EvidenceRecorded(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                case_id=self._id,
                evidence_id=evidence_id,
            )
        )

    def record_impact(self, affected: AffectedEntities, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "record impact on", OPEN_STATUSES)
        added = affected.difference(self._affected)
        if added.is_empty:
            return
        self._affected = self._affected.union(added)
        self._record(
            ImpactRecorded(
                tenant_id=self._tenant_id, occurred_at=clock.now(), case_id=self._id, added=added
            )
        )

    def start_investigation(self, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "start investigating", {CaseStatus.DETECTED})
        self._status = CaseStatus.INVESTIGATING
        self._record(
            InvestigationStarted(
                tenant_id=self._tenant_id, occurred_at=clock.now(), case_id=self._id
            )
        )

    def resolve(self, resolution: CaseResolution, *, by: UserId, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "resolve", {CaseStatus.INVESTIGATING})
        now = clock.now()
        self._closure = CaseClosure(closed_at=now, closed_by=by, resolution=resolution)
        self._status = CaseStatus.RESOLVED
        self._record(
            DisruptionResolved(
                tenant_id=self._tenant_id,
                occurred_at=now,
                case_id=self._id,
                outcome=resolution.outcome,
                recovery_plan_id=resolution.recovery_plan_id,
                resolved_by=by,
            )
        )

    def dismiss(self, reason: str, *, by: UserId, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "dismiss", OPEN_STATUSES)
        reason = require_text(reason, "reason", max_length=MAX_REASON_LENGTH)
        now = clock.now()
        self._closure = CaseClosure(closed_at=now, closed_by=by, dismissal_reason=reason)
        self._status = CaseStatus.DISMISSED
        self._record(
            DisruptionDismissed(
                tenant_id=self._tenant_id,
                occurred_at=now,
                case_id=self._id,
                reason=reason,
                dismissed_by=by,
            )
        )
