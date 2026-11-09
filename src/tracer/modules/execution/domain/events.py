from dataclasses import dataclass
from typing import ClassVar

from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import ExecutionAttemptId, RecoveryPlanId


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionStarted(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "execution.execution_started"

    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    try_number: int
    idempotency_key: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionExecuted(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "execution.action_executed"

    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    external_reference: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionFailed(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "execution.execution_failed"

    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    reason: str
    retryable: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionOutcomeUnknown(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "execution.execution_outcome_unknown"

    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    reason: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionReconciled(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "execution.execution_reconciled"

    attempt_id: ExecutionAttemptId
    plan_id: RecoveryPlanId
    applied: bool
    external_reference: str | None
