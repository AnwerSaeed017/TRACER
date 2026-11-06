from datetime import UTC, datetime, timedelta, timezone

import pytest

from tests.support import T0
from tracer.shared.errors import InvalidValueError
from tracer.shared.time_range import TimeRange

HOUR = timedelta(hours=1)


def test_end_must_be_after_start() -> None:
    with pytest.raises(InvalidValueError, match="after start"):
        TimeRange(T0, T0)
    with pytest.raises(InvalidValueError, match="after start"):
        TimeRange(T0, T0 - HOUR)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(InvalidValueError, match="timezone-aware"):
        TimeRange(datetime(2026, 11, 5), T0)  # noqa: DTZ001


def test_boundaries_are_stored_in_utc() -> None:
    karachi = timezone(timedelta(hours=5))
    window = TimeRange(datetime(2026, 11, 5, 14, 0, tzinfo=karachi), T0 + HOUR)
    assert window.start == T0
    assert window.start.tzinfo is UTC
    assert window.duration == HOUR


def test_half_open_contains() -> None:
    window = TimeRange(T0, T0 + HOUR)
    assert window.contains(T0)
    assert window.contains(T0 + HOUR - timedelta(microseconds=1))
    assert not window.contains(T0 + HOUR)


def test_adjacent_ranges_do_not_overlap() -> None:
    first, second = TimeRange(T0, T0 + HOUR), TimeRange(T0 + HOUR, T0 + 2 * HOUR)
    assert not first.overlaps(second)
    assert first.overlaps(TimeRange(T0 + HOUR / 2, T0 + 2 * HOUR))
