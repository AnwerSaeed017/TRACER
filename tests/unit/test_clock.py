from datetime import UTC, datetime, timedelta, timezone

import pytest

from tracer.shared.clock import FrozenClock, SystemClock


def test_system_clock_returns_aware_utc() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_frozen_clock_stays_still_until_advanced() -> None:
    start = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    clock = FrozenClock(start)
    assert clock.now() == start
    assert clock.now() == start

    clock.advance(timedelta(days=2))
    assert clock.now() == datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def test_frozen_clock_normalises_to_utc() -> None:
    karachi = timezone(timedelta(hours=5))
    clock = FrozenClock(datetime(2026, 11, 5, 14, 0, tzinfo=karachi))
    assert clock.now() == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert clock.now().tzinfo is UTC


def test_frozen_clock_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        FrozenClock(datetime(2026, 11, 5, 9, 0))  # noqa: DTZ001  # the point of the test
