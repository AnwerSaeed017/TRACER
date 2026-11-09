from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracer.modules.ingestion.adapters.persistence.stores import (
    SqlAlchemyInbox,
    SqlAlchemySourceDocumentStore,
)
from tracer.platform.database.unit_of_work import SqlAlchemyUnitOfWork
from tracer.platform.messaging.envelope import MessageContext
from tracer.platform.messaging.outbox import OutboxWriter
from tracer.shared.aggregate import DomainEvent
from tracer.shared.clock import Clock


class SqlAlchemyIngestionUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        context: MessageContext | None = None,
    ) -> None:
        super().__init__(session_factory)
        self._clock = clock
        self._context = context or MessageContext()

    @property
    def documents(self) -> SqlAlchemySourceDocumentStore:
        return SqlAlchemySourceDocumentStore(self.session)

    @property
    def inbox(self) -> SqlAlchemyInbox:
        return SqlAlchemyInbox(self.session)

    def record_events(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        first_version: int,
        events: Sequence[DomainEvent],
    ) -> None:
        OutboxWriter(self.session, self._clock, self._context).append(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            first_version=first_version,
            events=events,
        )
