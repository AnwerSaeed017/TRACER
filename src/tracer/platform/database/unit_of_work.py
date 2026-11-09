"""Unit of Work: one database transaction around one application operation.

    async with uow:
        case = await uow.cases.get(...)
        case.start_investigation(clock)
        await uow.cases.update(case)
        await uow.commit()          # explicit: nothing is saved without it

Leaving the block without ``commit()`` (an exception, or simply forgetting) rolls back.
The application use case decides the transaction boundary; repositories only use the session
they are given and never commit.

Repositories register each aggregate they save with ``track``. Only after the commit has
succeeded are those aggregates told their events are committed; if the commit fails, they
keep their pending events and old version, exactly as before the attempt.
"""

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tracer.shared.aggregate import AggregateRoot


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._tracked: list[AggregateRoot] = []

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("the unit of work is not active; use it in an 'async with' block")
        return self._session

    def track(self, aggregate: AggregateRoot) -> None:
        """Mark ``aggregate``'s pending events committed when (and only if) this commits."""
        if any(tracked is aggregate for tracked in self._tracked):
            raise RuntimeError("an aggregate can be saved once per unit of work")
        self._tracked.append(aggregate)

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("a unit of work cannot be nested or reused while active")
        self._session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        session = self.session
        try:
            await session.rollback()  # a no-op after a successful commit
        finally:
            await session.close()
            self._session = None
            self._tracked.clear()

    async def commit(self) -> None:
        await self.session.commit()
        for aggregate in self._tracked:
            aggregate.mark_events_committed()
        self._tracked.clear()
