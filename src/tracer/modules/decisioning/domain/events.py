from dataclasses import dataclass

from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import DisruptionCaseId, RecoveryPlanId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanProposed(DomainEvent):
    plan_id: RecoveryPlanId
    case_id: DisruptionCaseId
    revision: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanValidated(DomainEvent):
    plan_id: RecoveryPlanId
    revision: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanApproved(DomainEvent):
    plan_id: RecoveryPlanId
    revision: int
    fingerprint: str
    approved_by: UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanRejected(DomainEvent):
    plan_id: RecoveryPlanId
    revision: int
    reason: str
    violations: tuple[str, ...]  # rule failures from validation; empty for a human rejection
    rejected_by: UserId | None  # None when the deterministic checks rejected it


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanRevised(DomainEvent):
    plan_id: RecoveryPlanId
    revision: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanWithdrawn(DomainEvent):
    plan_id: RecoveryPlanId
    revision: int
    reason: str
