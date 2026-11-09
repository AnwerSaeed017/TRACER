from dataclasses import dataclass
from typing import ClassVar

from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import DisruptionCaseId, RecoveryPlanId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanProposed(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "decisioning.recovery_plan_proposed"

    plan_id: RecoveryPlanId
    case_id: DisruptionCaseId
    revision: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanValidated(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "decisioning.recovery_plan_validated"

    plan_id: RecoveryPlanId
    revision: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanApproved(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "decisioning.recovery_plan_approved"

    plan_id: RecoveryPlanId
    revision: int
    fingerprint: str
    approved_by: UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanRejected(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "decisioning.recovery_plan_rejected"

    plan_id: RecoveryPlanId
    revision: int
    reason: str
    violations: tuple[str, ...]  # rule failures from validation; empty for a human rejection
    rejected_by: UserId | None  # None when the deterministic checks rejected it


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanRevised(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "decisioning.recovery_plan_revised"

    plan_id: RecoveryPlanId
    revision: int
    fingerprint: str


@dataclass(frozen=True, slots=True, kw_only=True)
class RecoveryPlanWithdrawn(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "decisioning.recovery_plan_withdrawn"

    plan_id: RecoveryPlanId
    revision: int
    reason: str
