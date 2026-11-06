"""Build the object graph once, at start-up.

Every entry point (HTTP server, worker, CLI, test) calls ``build_container`` and receives
fully wired objects. Nothing else in the code base constructs its own dependencies, so
swapping an implementation (a real WMS for a mock, one model provider for another) is a
one-line change here.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tracer.platform.configuration.settings import Settings
from tracer.platform.database.engine import create_engine, create_session_factory
from tracer.platform.database.health import DatabaseHealthCheck
from tracer.platform.database.migrations import head_revision
from tracer.shared.clock import Clock, SystemClock
from tracer.shared.health import HealthCheck


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    readiness_checks: tuple[HealthCheck, ...]

    async def aclose(self) -> None:
        """Release pooled connections. Called once when the process shuts down."""
        await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings)
    return Container(
        settings=settings,
        clock=SystemClock(),
        engine=engine,
        session_factory=create_session_factory(engine),
        readiness_checks=(DatabaseHealthCheck(engine, head_revision(settings.migrations_dir)),),
    )
