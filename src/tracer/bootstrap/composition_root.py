"""Build the object graph once, at start-up.

Every entry point (HTTP server, worker, CLI, test) calls ``build_container`` and receives
fully wired objects. Nothing else in the code base constructs its own dependencies, so
swapping an implementation (a real WMS for a mock, one model provider for another) is a
one-line change here.
"""

import os
import random
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tracer.modules.ingestion.adapters.email.keyword_extractor import KeywordDisruptionExtractor
from tracer.modules.ingestion.adapters.email.parser import StandardLibraryEmailParser
from tracer.modules.ingestion.adapters.persistence.unit_of_work import (
    SqlAlchemyIngestionUnitOfWork,
)
from tracer.modules.ingestion.application.email_intake import EmailIntake, ReceivesEmails
from tracer.modules.ingestion.application.intake import (
    DisruptionReportIntake,
    ReceivesDisruptionReports,
)
from tracer.modules.knowledge.adapters.persistence.unit_of_work import (
    SqlAlchemyOntologyUnitOfWork,
)
from tracer.modules.knowledge.application.ontology_registry import OntologyRegistry
from tracer.modules.supply_chain.adapters.messaging.report_consumer import (
    DisruptionReportConsumer,
)
from tracer.modules.supply_chain.adapters.persistence.unit_of_work import (
    SqlAlchemySupplyChainUnitOfWork,
)
from tracer.modules.supply_chain.application.detect_from_report import DetectDisruptionFromReport
from tracer.platform.configuration.settings import Settings, WebhookSource
from tracer.platform.database.engine import create_engine, create_session_factory
from tracer.platform.database.health import DatabaseHealthCheck
from tracer.platform.database.migrations import head_revision
from tracer.platform.messaging.dispatcher import DispatchPolicy, EventHandler, OutboxDispatcher
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
    email_intake: ReceivesEmails
    event_handlers: tuple[EventHandler, ...]
    outbox_dispatcher: OutboxDispatcher
    ontology_registry: OntologyRegistry

    async def aclose(self) -> None:
        """Release pooled connections. Called once when the process shuts down."""
        await self.engine.dispose()


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def dispatch_policy(settings: Settings) -> DispatchPolicy:
    return DispatchPolicy(
        batch_size=settings.outbox_batch_size,
        lease=timedelta(seconds=settings.outbox_lease_seconds),
        max_attempts=settings.outbox_max_attempts,
        backoff_base=timedelta(seconds=settings.outbox_backoff_base_seconds),
        backoff_max=timedelta(seconds=settings.outbox_backoff_max_seconds),
    )


def build_container(
    settings: Settings,
    clock: Clock | None = None,
    *,
    worker_id: str | None = None,
    jitter: Callable[[float, float], float] = random.uniform,
) -> Container:
    clock = clock or SystemClock()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    def ingestion_unit_of_work(context: MessageContext) -> SqlAlchemyIngestionUnitOfWork:
        return SqlAlchemyIngestionUnitOfWork(session_factory, clock, context)

    def supply_chain_unit_of_work(context: MessageContext) -> SqlAlchemySupplyChainUnitOfWork:
        return SqlAlchemySupplyChainUnitOfWork(session_factory, clock, context)

    disruption_intake = DisruptionReportIntake(ingestion_unit_of_work, clock)
    # The extractor is the seam for M5: a model-based extractor replaces the keyword baseline
    # here, and nothing else changes.
    email_intake = EmailIntake(
        StandardLibraryEmailParser(), KeywordDisruptionExtractor(), disruption_intake
    )

    # Every in-process event subscriber. A new consumer is one line here.
    handlers: tuple[EventHandler, ...] = (
        DisruptionReportConsumer(DetectDisruptionFromReport(supply_chain_unit_of_work, clock)),
    )

    return Container(
        settings=settings,
        clock=clock,
        engine=engine,
        session_factory=session_factory,
        readiness_checks=(DatabaseHealthCheck(engine, head_revision(settings.migrations_dir)),),
        webhook_sources=MappingProxyType({s.name: s for s in settings.webhook_sources}),
        disruption_intake=disruption_intake,
        email_intake=email_intake,
        event_handlers=handlers,
        outbox_dispatcher=OutboxDispatcher(
            session_factory,
            clock,
            handlers,
            dispatch_policy(settings),
            worker_id or default_worker_id(),
            jitter,
        ),
        ontology_registry=OntologyRegistry(
            lambda: SqlAlchemyOntologyUnitOfWork(session_factory), clock
        ),
    )
