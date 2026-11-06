"""TimeRange: a half-open interval [start, end) in UTC.

Half-open means the end instant is not inside the range, so consecutive ranges
[09:00, 10:00) and [10:00, 11:00) touch without overlapping. This is the same convention
the bi-temporal knowledge graph (M3) uses for valid time and recorded time.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import require_utc


@dataclass(frozen=True, slots=True)
class TimeRange:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        start = require_utc(self.start, "start")
        end = require_utc(self.end, "end")
        if end <= start:
            raise InvalidValueError("end must be after start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def contains(self, moment: datetime) -> bool:
        return self.start <= require_utc(moment, "moment") < self.end

    def overlaps(self, other: "TimeRange") -> bool:
        return self.start < other.end and other.start < self.end
