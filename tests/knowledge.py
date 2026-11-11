"""Builders for ontology tests."""

from typing import Any

from tests.support import T0
from tracer.modules.knowledge.domain.attributes import AttributeDefinition, AttributeType
from tracer.modules.knowledge.domain.core_ontology import CORE_ONTOLOGY
from tracer.modules.knowledge.domain.ontology import (
    TENANT_PACKAGE_KEY,
    EntityTypeDefinition,
    EntityTypeExtension,
    OntologyPackageId,
    PackageContent,
    PackageScope,
    PublishedPackage,
    RelationTypeDefinition,
)
from tracer.shared.identifiers import TenantId


def attribute(
    name: str, kind: AttributeType = AttributeType.STRING, **options: Any
) -> AttributeDefinition:
    return AttributeDefinition(name=name, type=kind, **options)


def published(content: PackageContent, version: int = 1) -> PublishedPackage:
    return PublishedPackage(OntologyPackageId.new(), version, content, T0, "tests")


CORE = published(CORE_ONTOLOGY)


def tenant_content(
    tenant: TenantId,
    *,
    depends_on: tuple[OntologyPackageId, ...] | None = None,
    namespace: str = "acme",
    entity_types: tuple[EntityTypeDefinition, ...] = (),
    extensions: tuple[EntityTypeExtension, ...] = (),
    relation_types: tuple[RelationTypeDefinition, ...] = (),
) -> PackageContent:
    return PackageContent(
        key=TENANT_PACKAGE_KEY,
        scope=PackageScope.TENANT,
        namespace=namespace,
        tenant_id=tenant,
        depends_on=depends_on if depends_on is not None else (CORE.id,),
        entity_types=entity_types,
        extensions=extensions,
        relation_types=relation_types,
    )
