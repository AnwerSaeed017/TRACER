"""Idempotent consumption: remember which events a consumer has already handled.

Delivery is at-least-once: after a crash or a lost acknowledgement the same event arrives
again. A consumer claims ``(consumer, event_id)`` inside the same transaction as the change the
event causes. If the claim already exists, the event's effect is already committed and the
consumer does nothing. The table, not memory, remembers, so it survives restarts.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracer.platform.messaging.models import ProcessedEventRecord


class SqlAlchemyProcessedEvents:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, *, consumer: str, event_id: UUID, at: datetime) -> bool:
        """True the first time; False if this consumer already handled the event."""
        inserted = await self._session.scalar(
            insert(ProcessedEventRecord)
            .values(consumer=consumer, event_id=event_id, processed_at=at)
            .on_conflict_do_nothing()
            .returning(ProcessedEventRecord.event_id)
        )
        return inserted is not None
