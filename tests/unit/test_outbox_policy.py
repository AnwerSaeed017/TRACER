"""Retry policy and failure classification of the outbox dispatcher."""

from datetime import timedelta

import pytest

from tracer.platform.messaging.dispatcher import (
    PermanentDeliveryError,
    backoff_delay,
    is_permanent,
)
from tracer.shared.errors import (
    BusinessRuleError,
    ConcurrencyConflictError,
    InvalidTransitionError,
    InvalidValueError,
)

BASE = timedelta(seconds=1)
CAP = timedelta(seconds=300)


def upper_bound(low: float, high: float) -> float:
    return high


@pytest.mark.parametrize(
    ("attempt", "seconds"), [(1, 1), (2, 2), (3, 4), (4, 8), (9, 256), (10, 300), (30, 300)]
)
def test_backoff_doubles_up_to_the_cap(attempt: int, seconds: float) -> None:
    delay = backoff_delay(attempt, base=BASE, cap=CAP, jitter=upper_bound)
    assert delay == timedelta(seconds=seconds)


def test_jitter_spreads_retries_between_zero_and_the_ceiling() -> None:
    delays = {backoff_delay(5, base=BASE, cap=CAP).total_seconds() for _ in range(200)}
    assert all(0 <= d <= 16 for d in delays)
    assert len(delays) > 100  # genuinely spread out, not one value


@pytest.mark.parametrize(
    "error",
    [
        PermanentDeliveryError("unknown schema"),
        InvalidValueError("unknown kind"),
        InvalidTransitionError("case", "resolved", "resolve"),
        BusinessRuleError("rule"),
    ],
)
def test_errors_retrying_cannot_fix_are_permanent(error: Exception) -> None:
    assert is_permanent(error)


@pytest.mark.parametrize(
    "error",
    [
        ConcurrencyConflictError("case", "id", 3),  # reload and retry can succeed
        ConnectionError("database unreachable"),
        TimeoutError(),
        RuntimeError("anything unexpected"),
    ],
)
def test_everything_else_is_retried(error: Exception) -> None:
    assert not is_permanent(error)
