"""Build the object graph once, at start-up.

Every entry point (HTTP server, worker, CLI, test) calls ``build_container`` and receives
fully wired objects. Nothing else in the code base constructs its own dependencies, so
swapping an implementation (a real WMS for a mock, one model provider for another) is a
one-line change here.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tracer.modules.ingestion.adapters.persistence.unit_of_work import (
    SqlAlchemyIngestionUnitOfWork,
)
from tracer.modules.ingestion.application.intake import (
    DisruptionReportIntake,
    ReceivesDisruptionReports,
)
from tracer.platform.configuration.settings import Settings, WebhookSource
from tracer.platform.database.engine import create_engine, create_session_factory
from tracer.platform.database.health import DatabaseHealthCheck
from tracer.platform.database.migrations import head_revision
from tracer.platform.messaging.envelope import MessageContext
from tracer.shared.clock import Clock, SystemClock
from tracer.shared.health import HealthCheck


@dataclass(frozen=True, slots=True)
class Container:
    settings: Settings
    clock: Clock
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    readiness_checks: tuple[HealthCheck, ...]
    webhook_sources: Mapping[str, WebhookSource]
    disruption_intake: ReceivesDisruptionReports

    async def aclose(self) -> None:
        """Release pooled connections. Called once when the process shuts down."""
        await self.engine.dispose()


def build_container(settings: Settings, clock: Clock | None = None) -> Container:
    clock = clock or SystemClock()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    def ingestion_unit_of_work(context: MessageContext) -> SqlAlchemyIngestionUnitOfWork:
        return SqlAlchemyIngestionUnitOfWork(session_factory, clock, context)

    return Container(
        settings=settings,
        clock=clock,
        engine=engine,
        session_factory=session_factory,
        readiness_checks=(DatabaseHealthCheck(engine, head_revision(settings.migrations_dir)),),
        webhook_sources=MappingProxyType({s.name: s for s in settings.webhook_sources}),
        disruption_intake=DisruptionReportIntake(ingestion_unit_of_work, clock),
    )
