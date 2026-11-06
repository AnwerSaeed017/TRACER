from dataclasses import dataclass

from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import ExecutionAttemptId, RecoveryPlanId


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionStarted(DomainEvent):
    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    try_number: int
    idempotency_key: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionExecuted(DomainEvent):
    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    external_reference: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionFailed(DomainEvent):
    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    reason: str
    retryable: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionOutcomeUnknown(DomainEvent):
    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionReconciled(DomainEvent):
    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    applied: bool
    external_reference: str | None
