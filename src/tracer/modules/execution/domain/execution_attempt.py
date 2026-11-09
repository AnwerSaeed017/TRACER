"""ExecutionAttempt: one approved action carried out in one external system.

    PENDING ──start──► EXECUTING ──succeeded──► COMMITTED (final)
                           │
                           ├──failed──► FAILED ──start (retryable, tries left)──► EXECUTING
                           │
                           └──no answer──► OUTCOME_UNKNOWN ──reconcile──► COMMITTED | FAILED

The rule that matters most: **a timeout is not a failure.** The request may have been
applied and only the response lost. An attempt in OUTCOME_UNKNOWN cannot be started again;
it must first be reconciled by asking the external system what actually happened. Every
try sends the same idempotency key, so even a mistaken resend is ignored downstream.
"""

from typing import ClassVar

from tracer.modules.execution.domain.events import (
    ActionExecuted,
    ExecutionFailed,
    ExecutionOutcomeUnknown,
    ExecutionReconciled,
    ExecutionStarted,
)
from tracer.modules.execution.domain.values import (
    DEFAULT_MAX_TRIES,
    MAX_OPERATION_LENGTH,
    MAX_REASON_LENGTH,
    MAX_REFERENCE_LENGTH,
    AttemptStatus,
    ExecutionFailure,
    TargetSystem,
    idempotency_key,
    require_fingerprint,
)
from tracer.shared.aggregate import AggregateRoot, ensure_status
from tracer.shared.clock import Clock
from tracer.shared.errors import BusinessRuleError, InvalidValueError
from tracer.shared.guards import require_text
from tracer.shared.identifiers import ExecutionAttemptId, RecoveryPlanId, TenantId

_NAME = "execution attempt"
_NOT_APPLIED = "not applied, confirmed by reconciliation"


class ExecutionAttempt(AggregateRoot):
    AGGREGATE_TYPE: ClassVar[str] = "execution_attempt"

    def __init__(
        self,
        *,
        attempt_id: ExecutionAttemptId,
        tenant_id: TenantId,
        plan_id: RecoveryPlanId,
        plan_fingerprint: str,
        action_index: int,
        target: TargetSystem,
        operation: str,
        max_tries: int,
        status: AttemptStatus,
        tries: int,
        external_reference: str | None,
        last_failure: ExecutionFailure | None,
        version: int = 0,
    ) -> None:
        """Rebuild an attempt from known state. New attempts come from ``create``."""
        super().__init__(version=version)
        self._id = attempt_id
        self._tenant_id = tenant_id
        self._plan_id = plan_id
        self._plan_fingerprint = require_fingerprint(plan_fingerprint)
        self._action_index = action_index
        self._target = TargetSystem(target)
        self._operation = require_text(operation, "operation", max_length=MAX_OPERATION_LENGTH)
        self._max_tries = max_tries
        self._status = AttemptStatus(status)
        self._tries = tries
        self._external_reference = external_reference
        self._last_failure = last_failure
        self._idempotency_key = idempotency_key(tenant_id, plan_id, plan_fingerprint, action_index)
        self._check_invariants()

    def _check_invariants(self) -> None:
        if self._action_index < 0:
            raise InvalidValueError("action_index cannot be negative")
        if self._max_tries < 1:
            raise InvalidValueError("max_tries must be at least 1")
        if not 0 <= self._tries <= self._max_tries:
            raise InvalidValueError("tries must be between 0 and max_tries")
        if (self._status is AttemptStatus.PENDING) != (self._tries == 0):
            raise InvalidValueError("an attempt has tries exactly when it has been started")
        if (self._status is AttemptStatus.COMMITTED) != (self._external_reference is not None):
            raise InvalidValueError("a committed attempt, and only it, has an external reference")
        if (self._status is AttemptStatus.FAILED) != (self._last_failure is not None):
            raise InvalidValueError("a failed attempt, and only it, has a failure")

    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        plan_id: RecoveryPlanId,
        plan_fingerprint: str,
        action_index: int,
        target: TargetSystem,
        operation: str,
        max_tries: int = DEFAULT_MAX_TRIES,
        attempt_id: ExecutionAttemptId | None = None,
    ) -> "ExecutionAttempt":
        """Prepare one approved action. Records no event: nothing has happened outside yet."""
        return cls(
            attempt_id=attempt_id or ExecutionAttemptId.new(),
            tenant_id=tenant_id,
            plan_id=plan_id,
            plan_fingerprint=plan_fingerprint,
            action_index=action_index,
            target=target,
            operation=operation,
            max_tries=max_tries,
            status=AttemptStatus.PENDING,
            tries=0,
            external_reference=None,
            last_failure=None,
        )

    # ------------------------------------------------------------------ state

    @property
    def id(self) -> ExecutionAttemptId:
        return self._id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    @property
    def plan_id(self) -> RecoveryPlanId:
        return self._plan_id

    @property
    def plan_fingerprint(self) -> str:
        return self._plan_fingerprint

    @property
    def action_index(self) -> int:
        return self._action_index

    @property
    def target(self) -> TargetSystem:
        return self._target

    @property
    def operation(self) -> str:
        return self._operation

    @property
    def idempotency_key(self) -> str:
        return self._idempotency_key

    @property
    def status(self) -> AttemptStatus:
        return self._status

    @property
    def tries(self) -> int:
        return self._tries

    @property
    def max_tries(self) -> int:
        return self._max_tries

    @property
    def external_reference(self) -> str | None:
        return self._external_reference

    @property
    def last_failure(self) -> ExecutionFailure | None:
        return self._last_failure

    @property
    def can_retry(self) -> bool:
        return (
            self._status is AttemptStatus.FAILED
            and self._last_failure is not None
            and self._last_failure.retryable
            and self._tries < self._max_tries
        )

    # ---------------------------------------------------------------- actions

    def start(self, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "start", {AttemptStatus.PENDING, AttemptStatus.FAILED})
        if self._status is AttemptStatus.FAILED and not self.can_retry:
            raise BusinessRuleError(
                "this attempt cannot be retried: the failure was final or no tries are left"
            )
        self._tries += 1
        self._status = AttemptStatus.EXECUTING
        self._last_failure = None
        self._record(
            ExecutionStarted(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                attempt_id=self._id,
                plan_id=self._plan_id,
                try_number=self._tries,
                idempotency_key=self._idempotency_key,
            )
        )

    def record_success(self, external_reference: str, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "record success for", {AttemptStatus.EXECUTING})
        reference = self._commit(external_reference)
        self._record(
            ActionExecuted(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                attempt_id=self._id,
                plan_id=self._plan_id,
                external_reference=reference,
            )
        )

    def record_failure(self, reason: str, *, retryable: bool, clock: Clock) -> None:
        ensure_status(_NAME, self._status, "record failure for", {AttemptStatus.EXECUTING})
        failure = ExecutionFailure(reason=reason, retryable=retryable)
        self._status = AttemptStatus.FAILED
        self._last_failure = failure
        self._record(
            ExecutionFailed(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                attempt_id=self._id,
                plan_id=self._plan_id,
                reason=failure.reason,
                retryable=failure.retryable,
            )
        )

    def record_unknown_outcome(self, reason: str, clock: Clock) -> None:
        ensure_status(
            _NAME, self._status, "record an unknown outcome for", {AttemptStatus.EXECUTING}
        )
        reason = require_text(reason, "reason", max_length=MAX_REASON_LENGTH)
        self._status = AttemptStatus.OUTCOME_UNKNOWN
        self._record(
            ExecutionOutcomeUnknown(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                attempt_id=self._id,
                plan_id=self._plan_id,
                reason=reason,
            )
        )

    def reconcile(self, *, applied: bool, external_reference: str | None, clock: Clock) -> None:
        """Record what the external system says happened to the request with our key."""
        ensure_status(_NAME, self._status, "reconcile", {AttemptStatus.OUTCOME_UNKNOWN})
        if applied:
            if external_reference is None:
                raise InvalidValueError("an applied action needs the external system's reference")
            self._commit(external_reference)
        else:
            if external_reference is not None:
                raise InvalidValueError("an action that was not applied has no external reference")
            self._status = AttemptStatus.FAILED
            self._last_failure = ExecutionFailure(reason=_NOT_APPLIED, retryable=True)
        self._record(
            ExecutionReconciled(
                tenant_id=self._tenant_id,
                occurred_at=clock.now(),
                attempt_id=self._id,
                plan_id=self._plan_id,
                applied=applied,
                external_reference=self._external_reference,
            )
        )

    def _commit(self, external_reference: str) -> str:
        reference = require_text(
            external_reference, "external_reference", max_length=MAX_REFERENCE_LENGTH
        )
        self._external_reference = reference
        self._status = AttemptStatus.COMMITTED
        return reference
