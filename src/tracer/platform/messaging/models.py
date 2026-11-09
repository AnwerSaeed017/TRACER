"""Messaging tables: the outbox (events waiting for delivery) and consumers' processed events."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from tracer.platform.database.base import Base


class OutboxStatus(StrEnum):
    PENDING = "pending"  # waiting for delivery (or for its next retry)
    PROCESSING = "processing"  # claimed by a worker until lease_expires_at
    PUBLISHED = "published"  # delivered to every subscriber
    DEAD = "dead"  # gave up (permanent failure or attempts exhausted); needs a person


def _in(column: str, values: type[StrEnum]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"

    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    aggregate_type: Mapped[str] = mapped_column(String(60))
    aggregate_id: Mapped[UUID]
    aggregate_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(120))
    schema_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))

    status: Mapped[str] = mapped_column(String(16), default=OutboxStatus.PENDING.value)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    claimed_by: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # One event per position in an aggregate's history: a duplicate write cannot slip in.
        # Also serves the dispatcher's "is an earlier event of this aggregate unpublished?" check.
        UniqueConstraint("aggregate_type", "aggregate_id", "aggregate_version"),
        CheckConstraint(_in("status", OutboxStatus), name="status_valid"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        CheckConstraint(
            "(status = 'processing') = (claimed_by IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="lease_matches_status",
        ),
        # The dispatcher's two queries, as small partial indexes: due pending events, and
        # processing events whose lease may have expired (crash recovery).
        Index(
            "ix_outbox_events_pending_due",
            "next_attempt_at",
            postgresql_where="status = 'pending'",
        ),
        Index(
            "ix_outbox_events_processing_lease",
            "lease_expires_at",
            postgresql_where="status = 'processing'",
        ),
    )


class ProcessedEventRecord(Base):
    """One row per (consumer, event) handled. Written in the consumer's own transaction, so
    "the event's effect" and "the event is done" are committed together or not at all."""

    __tablename__ = "processed_events"

    consumer: Mapped[str] = mapped_column(String(100), primary_key=True)
    event_id: Mapped[UUID] = mapped_column(primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
