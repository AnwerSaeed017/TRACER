"""Rules a package must satisfy on its own: scope, namespace, keys."""

from dataclasses import replace

import pytest

from tests.knowledge import CORE, attribute, tenant_content
from tracer.modules.knowledge.domain.core_ontology import CORE_ONTOLOGY
from tracer.modules.knowledge.domain.ontology import (
    EntityTypeDefinition,
    EntityTypeExtension,
    OntologyPackageId,
    PackageContent,
    PackageScope,
    RelationTypeDefinition,
)
from tracer.shared.errors import InvalidValueError
from tracer.shared.identifiers import TenantId

TENANT = TenantId.new()


def test_the_core_package_has_no_namespace_tenant_or_dependencies() -> None:
    with pytest.raises(InvalidValueError, match="core package"):
        replace(CORE_ONTOLOGY, namespace="core")
    with pytest.raises(InvalidValueError, match="core package"):
        replace(CORE_ONTOLOGY, depends_on=(OntologyPackageId.new(),))


def test_a_tenant_package_belongs_to_one_tenant_and_builds_on_something() -> None:
    with pytest.raises(InvalidValueError, match="exactly one tenant"):
        replace(tenant_content(TENANT), tenant_id=None)
    with pytest.raises(InvalidValueError, match="namespace and dependencies"):
        tenant_content(TENANT, depends_on=())


def test_an_industry_package_belongs_to_no_tenant() -> None:
    with pytest.raises(InvalidValueError, match="belongs to no tenant"):
        PackageContent("manufacturing", PackageScope.INDUSTRY, "mfg", TENANT, (CORE.id,))


@pytest.mark.parametrize("key", ["COLD_ROOM", "other.COLD_ROOM"])
def test_non_core_keys_carry_the_package_namespace(key: str) -> None:
    with pytest.raises(InvalidValueError, match=r"need 'acme\.'"):
        tenant_content(TENANT, entity_types=(EntityTypeDefinition(key, "Cold room"),))


def test_core_keys_carry_no_namespace() -> None:
    with pytest.raises(InvalidValueError, match="no namespace prefix"):
        replace(CORE_ONTOLOGY, entity_types=(EntityTypeDefinition("xx.THING", "Thing"),))


@pytest.mark.parametrize("key", ["supplier", "Supplier", "SUP PLIER", "acme.cold_room", "X"])
def test_definition_keys_are_upper_snake(key: str) -> None:
    with pytest.raises(InvalidValueError, match="not a valid definition key"):
        EntityTypeDefinition(key, "x")


def test_duplicate_keys_in_one_package_are_refused() -> None:
    room = EntityTypeDefinition("acme.COLD_ROOM", "Cold room")
    with pytest.raises(InvalidValueError, match="duplicate entity type keys"):
        tenant_content(TENANT, entity_types=(room, room))


def test_duplicate_attribute_names_are_refused() -> None:
    with pytest.raises(InvalidValueError, match="attribute names must be unique"):
        EntityTypeDefinition("THING", "Thing", attributes=(attribute("a"), attribute("a")))


def test_an_extension_adds_only_optional_attributes() -> None:
    with pytest.raises(InvalidValueError, match="must be optional"):
        EntityTypeExtension("SUPPLIER", (attribute("acme_code", required=True),))


def test_a_relation_needs_endpoints() -> None:
    with pytest.raises(InvalidValueError, match="source_types must not be empty"):
        RelationTypeDefinition("acme.FEEDS", "feeds", frozenset(), frozenset({"PRODUCT"}))


def test_the_fingerprint_ignores_definition_order() -> None:
    reordered = replace(CORE_ONTOLOGY, entity_types=tuple(reversed(CORE_ONTOLOGY.entity_types)))
    assert reordered.fingerprint() == CORE_ONTOLOGY.fingerprint()
    changed = replace(CORE_ONTOLOGY, description="different")
    assert changed.fingerprint() != CORE_ONTOLOGY.fingerprint()
