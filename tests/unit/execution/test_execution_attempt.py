from collections.abc import Callable

import pytest

from tests.support import T0
from tracer.modules.execution.domain.events import (
    ActionExecuted,
    ExecutionFailed,
    ExecutionOutcomeUnknown,
    ExecutionReconciled,
    ExecutionStarted,
)
from tracer.modules.execution.domain.execution_attempt import ExecutionAttempt
from tracer.modules.execution.domain.values import AttemptStatus, TargetSystem
from tracer.shared.clock import FrozenClock
from tracer.shared.errors import BusinessRuleError, InvalidTransitionError, InvalidValueError
from tracer.shared.identifiers import RecoveryPlanId, TenantId

TENANT = TenantId.new()
PLAN = RecoveryPlanId.new()
FINGERPRINT = "a" * 64


def create(**overrides: object) -> ExecutionAttempt:
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "plan_id": PLAN,
        "plan_fingerprint": FINGERPRINT,
        "action_index": 0,
        "target": TargetSystem.WMS,
        "operation": "reserve_inventory",
    }
    values.update(overrides)
    return ExecutionAttempt.create(**values)  # type: ignore[arg-type]


def executing(clock: FrozenClock, **overrides: object) -> ExecutionAttempt:
    attempt = create(**overrides)
    attempt.start(clock)
    attempt.pull_events()
    return attempt


class TestIdempotencyKey:
    def test_same_approved_action_same_key(self) -> None:
        assert create().idempotency_key == create().idempotency_key  # even as two attempts

    @pytest.mark.parametrize(
        "change",
        [
            {"action_index": 1},
            {"plan_fingerprint": "b" * 64},
            {"plan_id": RecoveryPlanId.new()},
            {"tenant_id": TenantId.new()},
        ],
    )
    def test_different_action_different_key(self, change: dict[str, object]) -> None:
        assert create(**change).idempotency_key != create().idempotency_key

    def test_fingerprint_must_be_a_sha256_digest(self) -> None:
        with pytest.raises(InvalidValueError, match="SHA-256"):
            create(plan_fingerprint="not-a-digest")


class TestHappyPath:
    def test_creation_records_nothing(self) -> None:
        attempt = create()
        assert attempt.status is AttemptStatus.PENDING
        assert attempt.pull_events() == ()

    def test_start_then_succeed(self, clock: FrozenClock) -> None:
        attempt = create()
        attempt.start(clock)
        assert attempt.pull_events() == (
            ExecutionStarted(
                tenant_id=TENANT,
                occurred_at=T0,
                attempt_id=attempt.id,
                plan_id=PLAN,
                try_number=1,
                idempotency_key=attempt.idempotency_key,
            ),
        )
        attempt.record_success("WMS-RES-7781", clock)
        assert attempt.status is AttemptStatus.COMMITTED
        assert attempt.external_reference == "WMS-RES-7781"
        [event] = attempt.pull_events()
        assert isinstance(event, ActionExecuted)
        assert event.external_reference == "WMS-RES-7781"


class TestFailureAndRetry:
    def test_retryable_failure_can_be_retried_with_the_same_key(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        key = attempt.idempotency_key
        attempt.record_failure("WMS returned 503", retryable=True, clock=clock)
        [failed] = attempt.pull_events()
        assert isinstance(failed, ExecutionFailed)
        assert attempt.can_retry

        attempt.start(clock)

        [started] = attempt.pull_events()
        assert isinstance(started, ExecutionStarted)
        assert started.try_number == 2
        assert started.idempotency_key == key
        assert attempt.last_failure is None

    def test_final_failure_cannot_be_retried(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        attempt.record_failure("SKU does not exist in WMS", retryable=False, clock=clock)
        attempt.pull_events()
        with pytest.raises(BusinessRuleError, match="cannot be retried"):
            attempt.start(clock)
        assert attempt.status is AttemptStatus.FAILED
        assert attempt.pull_events() == ()

    def test_tries_are_limited(self, clock: FrozenClock) -> None:
        attempt = create(max_tries=2)
        for _ in range(2):
            attempt.start(clock)
            attempt.record_failure("503", retryable=True, clock=clock)
        assert not attempt.can_retry
        with pytest.raises(BusinessRuleError):
            attempt.start(clock)
        assert attempt.tries == 2


class TestUnknownOutcome:
    """A timeout is not a failure: the request may have been applied."""

    def test_timeout_blocks_blind_retry(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        attempt.record_unknown_outcome("WMS did not answer within 10 s", clock)
        [event] = attempt.pull_events()
        assert isinstance(event, ExecutionOutcomeUnknown)

        with pytest.raises(
            InvalidTransitionError,
            match="cannot start this execution attempt: it is outcome_unknown",
        ):
            attempt.start(clock)
        assert attempt.tries == 1
        assert attempt.pull_events() == ()

    def test_reconciled_as_applied_commits_without_resending(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        attempt.record_unknown_outcome("timeout", clock)
        attempt.pull_events()

        attempt.reconcile(applied=True, external_reference="WMS-RES-7781", clock=clock)

        assert attempt.status is AttemptStatus.COMMITTED
        assert attempt.tries == 1  # never sent twice
        assert attempt.pull_events() == (
            ExecutionReconciled(
                tenant_id=TENANT,
                occurred_at=T0,
                attempt_id=attempt.id,
                plan_id=PLAN,
                applied=True,
                external_reference="WMS-RES-7781",
            ),
        )

    def test_reconciled_as_not_applied_becomes_retryable(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        attempt.record_unknown_outcome("timeout", clock)
        attempt.reconcile(applied=False, external_reference=None, clock=clock)
        assert attempt.status is AttemptStatus.FAILED
        assert attempt.can_retry

    def test_reconciliation_must_be_consistent(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        attempt.record_unknown_outcome("timeout", clock)
        attempt.pull_events()
        with pytest.raises(InvalidValueError, match="needs the external system's reference"):
            attempt.reconcile(applied=True, external_reference=None, clock=clock)
        with pytest.raises(InvalidValueError, match="has no external reference"):
            attempt.reconcile(applied=False, external_reference="X", clock=clock)
        assert attempt.status is AttemptStatus.OUTCOME_UNKNOWN
        assert attempt.pull_events() == ()


class TestRejectedTransitions:
    def test_cannot_record_results_before_starting(self, clock: FrozenClock) -> None:
        attempt = create()
        refused: list[Callable[[], None]] = [
            lambda: attempt.record_success("X", clock),
            lambda: attempt.record_failure("x", retryable=True, clock=clock),
            lambda: attempt.record_unknown_outcome("x", clock),
            lambda: attempt.reconcile(applied=True, external_reference="X", clock=clock),
        ]
        for action in refused:
            with pytest.raises(InvalidTransitionError):
                action()
        assert attempt.status is AttemptStatus.PENDING
        assert attempt.pull_events() == ()

    def test_committed_is_final(self, clock: FrozenClock) -> None:
        attempt = executing(clock)
        attempt.record_success("X", clock)
        attempt.pull_events()
        with pytest.raises(InvalidTransitionError):
            attempt.start(clock)
        with pytest.raises(InvalidTransitionError):
            attempt.record_failure("late failure", retryable=True, clock=clock)
        assert attempt.pull_events() == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"action_index": -1}, "action_index"),
        ({"max_tries": 0}, "max_tries"),
        ({"operation": " "}, "operation"),
    ],
)
def test_invalid_attempts_are_refused(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(InvalidValueError, match=message):
        create(**overrides)
