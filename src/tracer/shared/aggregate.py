"""Building blocks for aggregates and the events they emit.

An aggregate root is the only way to change its own state. Each state-changing method:
1. checks that the action is allowed in the current status and that every rule holds;
2. changes the state;
3. records one domain event describing what happened.

If step 1 fails, nothing changes and no event is recorded. Events are collected in memory
and handed over with ``pull_events()``; the repository that saves the aggregate writes them
to the transactional outbox in the same database transaction as the new state.

**Version.** An aggregate's version is the number of events it has produced and committed
over its whole life: one version per event, always. An operation that records two events
advances it by two, and the events take consecutive positions. A new aggregate starts at 0.
The stored version is the one the database last saw, so saving with
"WHERE version = <the version I loaded>" detects a concurrent change (optimistic
concurrency), and each event's position (``aggregate_version``) is unique.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar

from tracer.shared.errors import InvalidTransitionError, InvalidValueError
from tracer.shared.identifiers import TenantId


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Something that happened in the business. Immutable, named in the past tense.

    ``EVENT_TYPE`` is the stable public name ("supply_chain.disruption_detected"): consumers
    subscribe to it, so it never changes when a class is renamed. ``SCHEMA_VERSION`` goes up
    when the event's fields change incompatibly.
    """

    EVENT_TYPE: ClassVar[str]
    SCHEMA_VERSION: ClassVar[int] = 1

    tenant_id: TenantId
    occurred_at: datetime


class AggregateRoot:
    AGGREGATE_TYPE: ClassVar[str]

    def __init__(self, *, version: int = 0) -> None:
        if version < 0:
            raise InvalidValueError("version cannot be negative")
        self._version = version
        self._pending_events: list[DomainEvent] = []

    @property
    def version(self) -> int:
        """Number of events handed over so far (and, once saved, the stored version)."""
        return self._version

    def _record(self, event: DomainEvent) -> None:
        self._pending_events.append(event)

    @property
    def pending_events(self) -> tuple[DomainEvent, ...]:
        """Events recorded but not yet committed. Reading them changes nothing."""
        return tuple(self._pending_events)

    def mark_events_committed(self) -> None:
        """Called once the events are durably stored: forget them and advance the version.

        Only after a successful commit. If the commit fails, the aggregate keeps its events and
        its old version, so saving it again in a new unit of work is correct.
        """
        self._version += len(self._pending_events)
        self._pending_events.clear()

    def pull_events(self) -> tuple[DomainEvent, ...]:
        """Take the pending events and mark them committed. For code without a database
        (domain tests, simulations); persistence uses ``pending_events`` + the unit of work."""
        events = self.pending_events
        self.mark_events_committed()
        return events


def ensure_status[S: StrEnum](
    aggregate: str, current: S, action: str, allowed: Collection[S]
) -> None:
    """Raise ``InvalidTransitionError`` unless ``current`` is one of ``allowed``."""
    if current not in allowed:
        raise InvalidTransitionError(aggregate, current.value, action)
