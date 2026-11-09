"""What ingestion use cases need from outside. Adapters implement these."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from tracer.modules.ingestion.domain.source_document import SourceDocument, SourceDocumentId
from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import TenantId


@dataclass(frozen=True, slots=True)
class InboxClaim:
    claimed: bool  # False: this event id was already received from this source
    document_id: SourceDocumentId  # the document the event is (or was first) recorded with


class SourceDocumentStore(Protocol):
    async def add(self, document: SourceDocument) -> None: ...

    async def find_same_content(
        self, tenant_id: TenantId, content_sha256: str
    ) -> SourceDocumentId | None:
        """The earliest stored document with exactly this content, if any."""
        ...


class Inbox(Protocol):
    async def claim(
        self,
        *,
        tenant_id: TenantId,
        source_name: str,
        event_id: str,
        document_id: SourceDocumentId,
        received_at: datetime,
    ) -> InboxClaim:
        """Record a source's event id once. Safe under concurrency: exactly one claimant wins."""
        ...


class IngestionUnitOfWork(Protocol):
    @property
    def documents(self) -> SourceDocumentStore: ...

    @property
    def inbox(self) -> Inbox: ...

    def record_events(
        self,
        *,
        aggregate_type: str,
        aggregate_id: UUID,
        first_version: int,
        events: Sequence[DomainEvent],
    ) -> None:
        """Write events to the outbox in this unit of work's transaction."""
        ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
