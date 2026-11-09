"""Table for the DisruptionCase aggregate. The domain class knows nothing about it."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from tracer.modules.supply_chain.domain.values import CaseStatus, DisruptionKind
from tracer.platform.database.base import Base


def _in(column: str, values: type[CaseStatus] | type[DisruptionKind]) -> str:
    return f"{column} IN ({', '.join(repr(v.value) for v in values)})"


class DisruptionCaseRecord(Base):
    __tablename__ = "disruption_cases"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(40))
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_ids: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)))
    affected: Mapped[dict[str, Any]] = mapped_column(JSONB)
    impact_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    impact_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # none_as_null: Python None must become SQL NULL. By default SQLAlchemy stores it as the
    # JSON value null, which is NOT NULL to the database, so "closure IS NULL" would miss it.
    closure: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    version: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        # The database repeats the most important domain rules: a bug in a future code path
        # (or a hand-written SQL fix) cannot store a case the domain would refuse.
        CheckConstraint(_in("status", CaseStatus), name="status_valid"),
        CheckConstraint(_in("kind", DisruptionKind), name="kind_valid"),
        CheckConstraint("cardinality(evidence_ids) >= 1", name="has_evidence"),
        CheckConstraint("version >= 0", name="version_non_negative"),
        CheckConstraint(
            "(impact_start IS NULL) = (impact_end IS NULL) "
            "AND (impact_end IS NULL OR impact_end > impact_start)",
            name="impact_window_valid",
        ),
        CheckConstraint(
            "(closure IS NULL) = (status IN ('detected', 'investigating'))",
            name="closure_matches_status",
        ),
        # Every query is per tenant; the common ones filter on status or sort by time.
        Index("ix_disruption_cases_tenant_status", "tenant_id", "status"),
        Index("ix_disruption_cases_tenant_detected_at", "tenant_id", "detected_at"),
        # "Which case cites this evidence?": array containment (@>) needs a GIN index.
        Index("ix_disruption_cases_evidence_ids", "evidence_ids", postgresql_using="gin"),
    )
