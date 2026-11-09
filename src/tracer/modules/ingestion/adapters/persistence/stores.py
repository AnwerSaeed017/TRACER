"""PostgreSQL implementations of the document store and the inbox.

The inbox relies on ``INSERT ... ON CONFLICT DO NOTHING RETURNING``: the database decides
atomically whether an event id is new. A "check first, then insert" in Python would let two
concurrent requests both see "not there yet" and both insert.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tracer.modules.ingestion.adapters.persistence.models import (
    InboxMessageRecord,
    SourceDocumentRecord,
)
from tracer.modules.ingestion.application.ports import InboxClaim
from tracer.modules.ingestion.domain.source_document import SourceDocument, SourceDocumentId
from tracer.shared.identifiers import TenantId


class SqlAlchemySourceDocumentStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: SourceDocument) -> None:
        self._session.add(
            SourceDocumentRecord(
                id=document.id.value,
                tenant_id=document.tenant_id.value,
                channel=document.channel.value,
                source_name=document.source_name,
                media_type=document.media_type,
                content=document.content,
                content_sha256=document.content_sha256,
                size_bytes=document.size_bytes,
                received_at=document.received_at,
            )
        )
        await self._session.flush()  # the inbox row references it

    async def find_same_content(
        self, tenant_id: TenantId, content_sha256: str
    ) -> SourceDocumentId | None:
        found = await self._session.scalar(
            select(SourceDocumentRecord.id)
            .where(
                SourceDocumentRecord.tenant_id == tenant_id.value,
                SourceDocumentRecord.content_sha256 == content_sha256,
            )
            # Earliest first. Documents received at the very same instant tie and the id
            # breaks the tie arbitrarily; acceptable for a hint.
            .order_by(SourceDocumentRecord.received_at, SourceDocumentRecord.id)
            .limit(1)
        )
        return None if found is None else SourceDocumentId(found)


class SqlAlchemyInbox:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(
        self,
        *,
        tenant_id: TenantId,
        source_name: str,
        event_id: str,
        document_id: SourceDocumentId,
        received_at: datetime,
    ) -> InboxClaim:
        inserted = await self._session.scalar(
            insert(InboxMessageRecord)
            .values(
                tenant_id=tenant_id.value,
                source_name=source_name,
                event_id=event_id,
                document_id=document_id.value,
                received_at=received_at,
            )
            .on_conflict_do_nothing()
            .returning(InboxMessageRecord.document_id)
        )
        if inserted is not None:
            return InboxClaim(claimed=True, document_id=document_id)
        existing = await self._session.scalar(
            select(InboxMessageRecord.document_id).where(
                InboxMessageRecord.tenant_id == tenant_id.value,
                InboxMessageRecord.source_name == source_name,
                InboxMessageRecord.event_id == event_id,
            )
        )
        if existing is None:  # cannot happen: the conflict proves the row exists
            raise RuntimeError("inbox conflict without an existing event")
        return InboxClaim(claimed=False, document_id=SourceDocumentId(existing))
