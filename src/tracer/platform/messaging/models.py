"""The outbox table: events waiting to be delivered, written in the business transaction."""

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
    PUBLISHED = "published"  # delivered to every subscriber
    DEAD = "dead"  # gave up after the maximum number of attempts; needs a person


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
    last_error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # One event per position in an aggregate's history: a duplicate write cannot slip in.
        UniqueConstraint("aggregate_type", "aggregate_id", "aggregate_version"),
        CheckConstraint(_in("status", OutboxStatus), name="status_valid"),
        CheckConstraint("aggregate_version >= 1", name="aggregate_version_positive"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The dispatcher's query: pending events due now, oldest first. Partial index, so it
        # stays small however many published events accumulate.
        Index(
            "ix_outbox_events_pending_due",
            "next_attempt_at",
            postgresql_where="status = 'pending'",
        ),
    )
