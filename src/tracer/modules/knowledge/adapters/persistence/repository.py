"""PostgreSQL repository for ontology packages.

Loading a package rebuilds it through the domain constructors (so every definition is
re-validated) and recomputes its fingerprint: a package whose stored rows no longer match the
fingerprint recorded at publish time is refused, not trusted.
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracer.modules.knowledge.adapters.persistence.models import (
    OntologyEntityTypeRecord,
    OntologyPackageRecord,
    OntologyRelationTypeRecord,
)
from tracer.modules.knowledge.domain.attributes import AttributeDefinition
from tracer.modules.knowledge.domain.ontology import (
    EntityTypeDefinition,
    EntityTypeExtension,
    OntologyPackageId,
    PackageContent,
    PackageScope,
    PublishedPackage,
    RelationTypeDefinition,
)
from tracer.shared.errors import ConcurrencyConflictError
from tracer.shared.identifiers import TenantId

_UNIQUE_VIOLATION = "23505"


class CorruptOntologyError(RuntimeError):
    """Stored ontology rows do not match their published fingerprint."""


class SqlAlchemyOntologyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest(self, key: str, tenant_id: TenantId | None) -> PublishedPackage | None:
        tenant_filter = (
            OntologyPackageRecord.tenant_id.is_(None)
            if tenant_id is None
            else OntologyPackageRecord.tenant_id == tenant_id.value
        )
        record = await self._session.scalar(
            select(OntologyPackageRecord)
            .where(OntologyPackageRecord.package_key == key, tenant_filter)
            .order_by(OntologyPackageRecord.version.desc())
            .limit(1)
        )
        return None if record is None else await self._load(record)

    async def get(self, package_id: OntologyPackageId) -> PublishedPackage | None:
        record = await self._session.get(OntologyPackageRecord, package_id.value)
        return None if record is None else await self._load(record)

    async def add(self, package: PublishedPackage) -> None:
        content = package.content
        self._session.add(
            OntologyPackageRecord(
                id=package.id.value,
                tenant_id=content.tenant_id.value if content.tenant_id else None,
                package_key=content.key,
                scope=content.scope.value,
                namespace=content.namespace,
                version=package.version,
                depends_on=[d.value for d in content.depends_on],
                description=content.description,
                content_sha256=package.fingerprint,
                published_at=package.published_at,
                published_by=package.published_by,
            )
        )
        try:
            await self._session.flush()  # the version row first: it is what can conflict
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
                raise ConcurrencyConflictError(
                    "ontology package", content.key, package.version - 1
                ) from None
            raise
        self._session.add_all(_type_rows(package))
        self._session.add_all(_relation_rows(package))

    async def _load(self, record: OntologyPackageRecord) -> PublishedPackage:
        types = (
            await self._session.scalars(
                select(OntologyEntityTypeRecord).where(
                    OntologyEntityTypeRecord.package_id == record.id
                )
            )
        ).all()
        relations = (
            await self._session.scalars(
                select(OntologyRelationTypeRecord).where(
                    OntologyRelationTypeRecord.package_id == record.id
                )
            )
        ).all()
        package = PublishedPackage(
            id=OntologyPackageId(record.id),
            version=record.version,
            published_at=record.published_at,
            published_by=record.published_by,
            content=PackageContent(
                key=record.package_key,
                scope=PackageScope(record.scope),
                namespace=record.namespace,
                tenant_id=TenantId(record.tenant_id) if record.tenant_id else None,
                depends_on=tuple(OntologyPackageId(d) for d in record.depends_on),
                description=record.description,
                entity_types=tuple(
                    EntityTypeDefinition(
                        key=row.type_key,
                        display_name=row.display_name or row.type_key,
                        description=row.description,
                        parent=row.parent_key,
                        abstract=row.abstract,
                        deprecated=row.deprecated,
                        attributes=_attributes_from(row.attributes),
                    )
                    for row in types
                    if row.kind == "type"
                ),
                extensions=tuple(
                    EntityTypeExtension(row.type_key, _attributes_from(row.attributes))
                    for row in types
                    if row.kind == "extension"
                ),
                relation_types=tuple(
                    RelationTypeDefinition(
                        key=row.relation_key,
                        display_name=row.display_name,
                        description=row.description,
                        source_types=frozenset(row.source_types),
                        target_types=frozenset(row.target_types),
                        deprecated=row.deprecated,
                        attributes=_attributes_from(row.attributes),
                    )
                    for row in relations
                ),
            ),
        )
        if package.fingerprint != record.content_sha256:
            raise CorruptOntologyError(
                f"{package.label()} does not match its published fingerprint"
            )
        return package


def _attributes_json(attributes: tuple[AttributeDefinition, ...]) -> dict[str, Any]:
    return {a.name: a.to_json() for a in attributes}


def _attributes_from(data: dict[str, Any]) -> tuple[AttributeDefinition, ...]:
    return tuple(AttributeDefinition.from_json(name, value) for name, value in data.items())


def _type_rows(package: PublishedPackage) -> list[OntologyEntityTypeRecord]:
    content = package.content
    rows = [
        OntologyEntityTypeRecord(
            package_id=package.id.value,
            type_key=t.key,
            kind="type",
            display_name=t.display_name,
            description=t.description,
            parent_key=t.parent,
            abstract=t.abstract,
            deprecated=t.deprecated,
            attributes=_attributes_json(t.attributes),
        )
        for t in content.entity_types
    ]
    rows += [
        OntologyEntityTypeRecord(
            package_id=package.id.value,
            type_key=e.type_key,
            kind="extension",
            display_name=None,
            description="",
            parent_key=None,
            abstract=False,
            deprecated=False,
            attributes=_attributes_json(e.attributes),
        )
        for e in content.extensions
    ]
    return rows


def _relation_rows(package: PublishedPackage) -> list[OntologyRelationTypeRecord]:
    return [
        OntologyRelationTypeRecord(
            package_id=package.id.value,
            relation_key=r.key,
            display_name=r.display_name,
            description=r.description,
            source_types=sorted(r.source_types),
            target_types=sorted(r.target_types),
            deprecated=r.deprecated,
            attributes=_attributes_json(r.attributes),
        )
        for r in package.content.relation_types
    ]
