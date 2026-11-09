from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracer.modules.supply_chain.adapters.persistence.repository import (
    SqlAlchemyDisruptionCaseRepository,
)
from tracer.platform.database.unit_of_work import SqlAlchemyUnitOfWork
from tracer.platform.messaging.consumer import SqlAlchemyProcessedEvents
from tracer.platform.messaging.envelope import MessageContext
from tracer.platform.messaging.outbox import OutboxWriter
from tracer.shared.clock import Clock


class SqlAlchemySupplyChainUnitOfWork(SqlAlchemyUnitOfWork):
    """One transaction in which cases are read and written, their events recorded, and the
    consumed event marked processed."""

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
    def cases(self) -> SqlAlchemyDisruptionCaseRepository:
        return SqlAlchemyDisruptionCaseRepository(
            self.session, OutboxWriter(self.session, self._clock, self._context), self.track
        )

    @property
    def processed_events(self) -> SqlAlchemyProcessedEvents:
        return SqlAlchemyProcessedEvents(self.session)
