"""Building blocks for aggregates and the events they emit.

An aggregate root is the only way to change its own state. Each state-changing method:
1. checks that the action is allowed in the current status and that every rule holds;
2. changes the state;
3. records one domain event describing what happened.

If step 1 fails, nothing changes and no event is recorded. Events are collected in memory
and handed over with ``pull_events()``; from M2 the application layer stores them in the
transactional outbox, in the same transaction as the state change.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from tracer.shared.errors import InvalidTransitionError
from tracer.shared.identifiers import TenantId


@dataclass(frozen=True, slots=True, kw_only=True)
class DomainEvent:
    """Something that happened in the business. Immutable, named in the past tense."""

    tenant_id: TenantId
    occurred_at: datetime


class AggregateRoot:
    def __init__(self) -> None:
        self._pending_events: list[DomainEvent] = []

    def _record(self, event: DomainEvent) -> None:
        self._pending_events.append(event)

    def pull_events(self) -> tuple[DomainEvent, ...]:
        """Return the events recorded since the last call, and forget them."""
        events = tuple(self._pending_events)
        self._pending_events.clear()
        return events


def ensure_status[S: StrEnum](
    aggregate: str, current: S, action: str, allowed: Collection[S]
) -> None:
    """Raise ``InvalidTransitionError`` unless ``current`` is one of ``allowed``."""
    if current not in allowed:
        raise InvalidTransitionError(aggregate, current.value, action)
