"""RecoveryPlan: a proposed response to a disruption, its validation and its approval.

    PROPOSED ──validation passed──► VALIDATED ──approve(fingerprint)──► APPROVED
        │                               │
        └──validation failed──► REJECTED ◄──reject──┘
                                    │
    revise (from any status but WITHDRAWN) ──► PROPOSED, revision + 1
    withdraw (from any status but WITHDRAWN) ──► WITHDRAWN (final)

- Approval is bound to the exact content: ``approve`` takes the fingerprint the approver
  reviewed and refuses if the plan has changed since.
- Any revision, even of an approved plan, returns it to PROPOSED: it must be validated and
  approved again.
- Validation results come from deterministic checks in code (M5/M8); the plan only records
  them. A failed validation must list what failed.
"""

from collections.abc import Sequence
from datetime import datetime

from tracer.modules.decisioning.domain.events import (
    RecoveryPlanApproved,
    RecoveryPlanProposed,
    RecoveryPlanRejected,
    RecoveryPlanRevised,
    RecoveryPlanValidated,
    RecoveryPlanWithdrawn,
)
from tracer.modules.decisioning.domain.values import (
    MAX_REASON_LENGTH,
    Approval,
    PlanContent,
    PlanStatus,
    plan_fingerprint,
)
from tracer.shared.aggregate import AggregateRoot, ensure_status
from tracer.shared.clock import Clock
from tracer.shared.errors import BusinessRuleError, InvalidValueError
from tracer.shared.guards import require_text, require_utc
from tracer.shared.identifiers import DisruptionCaseId, RecoveryPlanId, TenantId, UserId

_NAME = "recovery plan"
_REVISABLE = frozenset(PlanStatus) - {PlanStatus.WITHDRAWN}
_VALIDATION_FAILED = "failed validation"


class RecoveryPlan(AggregateRoot):
    def __init__(
        self,
        *,
        plan_id: RecoveryPlanId,
        tenant_id: TenantId,
        case_id: DisruptionCaseId,
        revision: int,
        content: PlanContent,
        status: PlanStatus,
        proposed_at: datetime,
        approval: Approval | None,
        violations: tuple[str, ...],
        status_reason: str | None,
    ) -> None:
        """Rebuild a plan from known state. New plans come from ``propose``."""
        super().__init__()
        self._id = plan_id
        self._tenant_id = tenant_id
        self._case_id = case_id
        self._revision = revision
        self._content = content
        self._status = PlanStatus(status)
        self._proposed_at = require_utc(proposed_at, "proposed_at")
        self._approval = approval
        self._violations = violations
        self._status_reason = status_reason
        self._check_invariants()

    def _check_invariants(self) -> None:
        if self._revision < 1:
            raise InvalidValueError("revision starts at 1")
        approved = self._status is PlanStatus.APPROVED
        if approved != (self._approval is not None):
            raise InvalidValueError("only an approved plan has an approval")
        if self._approval is not None and self._approval.fingerprint != self.fingerprint:
            raise InvalidValueError("the approval is for different plan content")
        needs_reason = self._status in {PlanStatus.REJECTED, PlanStatus.WITHDRAWN}
        if needs_reason != (self._status_reason is not None):
            raise InvalidValueError("rejected and withdrawn plans, and only they, have a reason")

    @classmethod
    def propose(
        cls,
        *,
        tenant_id: TenantId,
        case_id: DisruptionCaseId,
        content: PlanContent,
        clock: Clock,
        plan_id: RecoveryPlanId | None = None,
    ) -> "RecoveryPlan":
        plan = cls(
            plan_id=plan_id or RecoveryPlanId.new(),
            tenant_id=tenant_id,
            case_id=case_id,
            revision=1,
            content=content,
            status=PlanStatus.PROPOSED,
            proposed_at=clock.now(),
            approval=None,
            violations=(),
            status_reason=None,
        )
        plan._record(
            RecoveryPlanProposed(
                tenant_id=tenant_id,
                occurred_at=plan.proposed_at,
                plan_id=plan.id,
                case_id=case_id,
                revision=plan.revision,
                fingerprint=plan.fingerprint,
            )
        )
        return plan

    # ------------------------------------------------------------------ state

    @property
    def id(self) -> RecoveryPlanId:
        return self._id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def case_id(self) -> DisruptionCaseId:
        return self._case_id

    @property
    def revision(self) -> int:
        return self._revision

    @property
    def content(self) -> PlanContent:
        return self._content

    @property
    def status(self) -> PlanStatus:
        return self._status

    @property
    def proposed_at(self) -> datetime:
        return self._proposed_at

    @property
    def approval(self) -> Approval | None:
        return self._approval

    @property
    def violations(self) -> tuple[str, ...]:
        return self._violations

    @property
    def status_reason(self) -> str | None:
        return self._status_reason

    @property
    def fingerprint(self) -> str:
        return plan_fingerprint(self._id, self._revision, self._content)

    # ---------------------------------------------------------------- actions

    def record_validation(self, violations: Sequence[str], clock: Clock) -> None:
        """Record the result of the deterministic checks: no violations means it passed."""
        ensure_status(_NAME, self._status, "validate", {PlanStatus.PROPOSED})
        found = tuple(
            require_text(v, "violation", max_length=MAX_REASON_LENGTH) for v in violations
        )
        now = clock.now()
        if not found:
            self._status = PlanStatus.VALIDATED
            self._record(
                RecoveryPlanValidated(
                    tenant_id=self._tenant_id,
                    occurred_at=now,
                    plan_id=self._id,
                    revision=self._revision,
                    fingerprint=self.fingerprint,
                )
            )
            return
        self._status = PlanStatus.REJECTED
        self._violations = found
        self._status_reason = _VALIDATION_FAILED
        self._record(
            RecoveryPlanRejected(
                tenant_id=self._tenant_id,
                occurred_at=now,
                plan_id=self._id,
                revision=self._revision,
                reason=_VALIDATION_FAILED,
                violations=found,
                rejected_by=None,
            )
        )

    def approve(self, *, by: UserId, fingerprint: str, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "approve", {PlanStatus.VALIDATED})
        if fingerprint != self.fingerprint:
            raise BusinessRuleError(
                f"the plan changed after it was reviewed; review revision {self._revision} again"
            )
        now = clock.now()
        self._approval = Approval(approved_by=by, approved_at=now, fingerprint=fingerprint)
        self._status = PlanStatus.APPROVED
        self._record(
            RecoveryPlanApproved(
                tenant_id=self._tenant_id,
                occurred_at=now,
                plan_id=self._id,
                revision=self._revision,
                fingerprint=fingerprint,
                approved_by=by,
            )
        )

    def reject(self, reason: str, *, by: UserId, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "reject", {PlanStatus.VALIDATED})
        reason = require_text(reason, "reason", max_length=MAX_REASON_LENGTH)
        self._status = PlanStatus.REJECTED
        self._status_reason = reason
        self._record(
            RecoveryPlanRejected(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                plan_id=self._id,
                revision=self._revision,
                reason=reason,
                violations=(),
                rejected_by=by,
            )
        )

    def revise(self, content: PlanContent, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "revise", _REVISABLE)
        if content == self._content:
            raise BusinessRuleError("a revision must change the plan")
        self._content = content
        self._revision += 1
        self._status = PlanStatus.PROPOSED
        self._approval = None
        self._violations = ()
        self._status_reason = None
        self._record(
            RecoveryPlanRevised(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                plan_id=self._id,
                revision=self._revision,
                fingerprint=self.fingerprint,
            )
        )

    def withdraw(self, reason: str, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "withdraw", _REVISABLE)
        reason = require_text(reason, "reason", max_length=MAX_REASON_LENGTH)
        self._status = PlanStatus.WITHDRAWN
        self._approval = None
        self._status_reason = reason
        self._record(
            RecoveryPlanWithdrawn(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                plan_id=self._id,
                revision=self._revision,
                reason=reason,
            )
        )
