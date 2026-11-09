"""Tables for received documents and the event inbox."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from tracer.modules.ingestion.domain.source_document import Channel
from tracer.platform.database.base import Base

_CHANNELS = ", ".join(repr(c.value) for c in Channel)


class SourceDocumentRecord(Base):
    __tablename__ = "source_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    channel: Mapped[str] = mapped_column(String(20))
    source_name: Mapped[str] = mapped_column(String(100))
    media_type: Mapped[str] = mapped_column(String(100))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    content_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel_valid"),
        CheckConstraint(
            "size_bytes > 0 AND size_bytes = octet_length(content)", name="size_matches"
        ),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
        # Not unique: identical content may be a legitimate new event. Indexed to find it.
        Index("ix_source_documents_tenant_sha256", "tenant_id", "content_sha256"),
        Index("ix_source_documents_tenant_received_at", "tenant_id", "received_at"),
    )


class InboxMessageRecord(Base):
    """One row per event a source sent. Its primary key is what makes a retry harmless.

    Kept permanently (not just for the signature's replay window), so a retry days later is
    still recognised.
    """

    __tablename__ = "inbox_messages"

    tenant_id: Mapped[UUID] = mapped_column(primary_key=True)
    source_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("source_documents.id"), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
