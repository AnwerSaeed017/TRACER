"""Ontology tables: one row per published package version, plus its definitions.

Rows are written once and never changed: database triggers (migration 0006) reject UPDATE and
DELETE on all three tables, so immutability does not depend on application code alone.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from tracer.modules.knowledge.domain.ontology import PackageScope
from tracer.platform.database.base import Base

_SCOPES = ", ".join(repr(s.value) for s in PackageScope)


class OntologyPackageRecord(Base):
    __tablename__ = "ontology_packages"

    id: Mapped[UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[UUID | None]
    package_key: Mapped[str] = mapped_column(String(60))
    scope: Mapped[str] = mapped_column(String(16))
    namespace: Mapped[str | None] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer)
    depends_on: Mapped[list[UUID]] = mapped_column(ARRAY(PG_UUID(as_uuid=True)))
    description: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str] = mapped_column(String(100))

    __table_args__ = (
        # One row per version of a package; NULLS NOT DISTINCT so global packages (tenant NULL)
        # are unique too. This is what makes two concurrent publishes conflict.
        UniqueConstraint("tenant_id", "package_key", "version", postgresql_nulls_not_distinct=True),
        CheckConstraint(f"scope IN ({_SCOPES})", name="scope_valid"),
        CheckConstraint(
            "(scope = 'tenant') = (tenant_id IS NOT NULL)", name="tenant_matches_scope"
        ),
        CheckConstraint("(scope = 'core') = (namespace IS NULL)", name="namespace_matches_scope"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("content_sha256 ~ '^[0-9a-f]{64}$'", name="sha256_format"),
    )


class OntologyEntityTypeRecord(Base):
    __tablename__ = "ontology_entity_types"

    package_id: Mapped[UUID] = mapped_column(ForeignKey("ontology_packages.id"), primary_key=True)
    type_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # 'type' or 'extension'
    display_name: Mapped[str | None] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    parent_key: Mapped[str | None] = mapped_column(String(80))
    abstract: Mapped[bool] = mapped_column(Boolean)
    deprecated: Mapped[bool] = mapped_column(Boolean)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("kind IN ('type', 'extension')", name="kind_valid"),
        CheckConstraint(
            "kind = 'type' OR (display_name IS NULL AND parent_key IS NULL AND NOT abstract)",
            name="extension_has_attributes_only",
        ),
        Index("ix_ontology_entity_types_type_key", "type_key"),
    )


class OntologyRelationTypeRecord(Base):
    __tablename__ = "ontology_relation_types"

    package_id: Mapped[UUID] = mapped_column(ForeignKey("ontology_packages.id"), primary_key=True)
    relation_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    source_types: Mapped[list[str]] = mapped_column(ARRAY(String(80)))
    target_types: Mapped[list[str]] = mapped_column(ARRAY(String(80)))
    deprecated: Mapped[bool] = mapped_column(Boolean)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint(
            "cardinality(source_types) >= 1 AND cardinality(target_types) >= 1",
            name="has_endpoints",
        ),
    )
