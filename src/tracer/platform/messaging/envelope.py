"""The envelope every stored and delivered event travels in.

The domain event says *what happened*. The envelope adds what a messaging system needs:

- ``event_id``: unique identity, so a consumer can ignore a duplicate delivery;
- ``event_type`` + ``schema_version``: how to read the payload, and how it evolves;
- ``aggregate_type`` / ``aggregate_id`` / ``aggregate_version``: which aggregate, and the
  event's position in that aggregate's history (unique, gap-free, ordered);
- ``correlation_id``: the request or workflow this belongs to (shared by everything it causes);
- ``causation_id``: the message that directly caused this one.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from tracer.platform.messaging.serialization import event_payload
from tracer.shared.aggregate import DomainEvent

MAX_TRACE_ID_LENGTH: Final = 64


@dataclass(frozen=True, slots=True)
class MessageContext:
    """Tracing ids carried from the cause (an HTTP request, a consumed event) to its effects."""

    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("correlation_id", "causation_id"):
            value = getattr(self, name)
            if value is not None and not 0 < len(value) <= MAX_TRACE_ID_LENGTH:
                raise ValueError(f"{name} must be 1 to {MAX_TRACE_ID_LENGTH} characters")


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_type: str
    schema_version: int
    tenant_id: UUID
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    occurred_at: datetime
    payload: dict[str, Any]
    correlation_id: str | None
    causation_id: str | None
    event_id: UUID = field(default_factory=uuid4)


def envelope_events(
    *,
    aggregate_type: str,
    aggregate_id: UUID,
    first_version: int,
    events: Sequence[DomainEvent],
    context: MessageContext,
) -> list[EventEnvelope]:
    """Wrap events produced together; they take consecutive versions from ``first_version``."""
    return [
        EventEnvelope(
            event_type=event.EVENT_TYPE,
            schema_version=event.SCHEMA_VERSION,
            tenant_id=event.tenant_id.value,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=first_version + offset,
            occurred_at=event.occurred_at,
            payload=event_payload(event),
            correlation_id=context.correlation_id,
            causation_id=context.causation_id,
        )
        for offset, event in enumerate(events)
    ]
