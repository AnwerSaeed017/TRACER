"""Writing events to the outbox, inside the caller's transaction.

The transactional outbox solves the dual-write problem: "save the case" and "publish
DisruptionDetected" cannot be one transaction if the publish goes to a broker. Instead the
event row is inserted in the *same* database transaction as the case. Either both are
committed or neither is; a separate dispatcher delivers committed rows afterwards.
"""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tracer.platform.messaging.envelope import MessageContext, envelope_events
from tracer.platform.messaging.models import OutboxEventRecord, OutboxStatus
from tracer.shared.aggregate import DomainEvent
from tracer.shared.clock import Clock


class OutboxWriter:
    def __init__(self, session: AsyncSession, clock: Clock, context: MessageContext) -> None:
        self._session = session
        self._clock = clock
        self._context = context

    def append(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        first_version: int,
        events: Sequence[DomainEvent],
    ) -> None:
        now = self._clock.now()
        envelopes = envelope_events(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            first_version=first_version,
            events=events,
            context=self._context,
        )
        self._session.add_all(
            OutboxEventRecord(
                event_id=envelope.event_id,
                tenant_id=envelope.tenant_id,
                aggregate_type=envelope.aggregate_type,
                aggregate_id=envelope.aggregate_id,
                aggregate_version=envelope.aggregate_version,
                event_type=envelope.event_type,
                schema_version=envelope.schema_version,
                payload=envelope.payload,
                occurred_at=envelope.occurred_at,
                recorded_at=now,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                status=OutboxStatus.PENDING.value,
                attempts=0,
                next_attempt_at=now,
            )
            for envelope in envelopes
        )
