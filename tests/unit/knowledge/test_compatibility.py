"""A new version may add and deprecate; anything that could invalidate existing data is refused."""

from dataclasses import replace

import pytest

from tests.knowledge import attribute, tenant_content
from tracer.modules.knowledge.domain.attributes import AttributeType
from tracer.modules.knowledge.domain.compatibility import incompatibilities
from tracer.modules.knowledge.domain.core_ontology import CORE_ONTOLOGY
from tracer.modules.knowledge.domain.ontology import (
    EntityTypeDefinition,
    PackageContent,
    RelationTypeDefinition,
)
from tracer.shared.identifiers import TenantId

V1 = CORE_ONTOLOGY


def change_type(key: str, **changes: object) -> PackageContent:
    types = tuple(replace(t, **changes) if t.key == key else t for t in V1.entity_types)  # type: ignore[arg-type]
    return replace(V1, entity_types=types)


def change_relation(key: str, **changes: object) -> PackageContent:
    relations = tuple(replace(r, **changes) if r.key == key else r for r in V1.relation_types)  # type: ignore[arg-type]
    return replace(V1, relation_types=relations)


def supplier() -> EntityTypeDefinition:
    return next(t for t in V1.entity_types if t.key == "SUPPLIER")


def supplies() -> RelationTypeDefinition:
    return next(r for r in V1.relation_types if r.key == "SUPPLIES")


class TestAllowed:
    def test_identical_content(self) -> None:
        assert incompatibilities(V1, V1) == []

    def test_adding_a_type_and_a_relation(self) -> None:
        v2 = replace(
            V1,
            entity_types=(*V1.entity_types, EntityTypeDefinition("DOCK", "Dock")),
            relation_types=(
                *V1.relation_types,
                RelationTypeDefinition(
                    "DOCKS_AT", "docks at", frozenset({"SHIPMENT"}), frozenset({"DOCK"})
                ),
            ),
        )
        assert incompatibilities(V1, v2) == []

    def test_adding_an_optional_attribute_and_deprecating(self) -> None:
        v2 = change_type(
            "SUPPLIER", attributes=(*supplier().attributes, attribute("duns")), deprecated=True
        )
        assert incompatibilities(V1, v2) == []

    def test_widening_relation_endpoints(self) -> None:
        v2 = change_relation("SUPPLIES", source_types=frozenset({"SUPPLIER", "FACTORY"}))
        assert incompatibilities(V1, v2) == []

    def test_rewording_a_description(self) -> None:
        assert incompatibilities(V1, change_type("SUPPLIER", description="reworded")) == []


class TestRefused:
    @pytest.mark.parametrize(
        ("v2", "message"),
        [
            (
                replace(V1, entity_types=tuple(t for t in V1.entity_types if t.key != "CARRIER")),
                "entity type CARRIER was removed (mark it deprecated instead)",
            ),
            (
                replace(
                    V1, relation_types=tuple(r for r in V1.relation_types if r.key != "SENT_BY")
                ),
                "relation SENT_BY was removed (mark it deprecated instead)",
            ),
            (
                change_type("WAREHOUSE", parent=None),
                "WAREHOUSE: parent changed from FACILITY to None",
            ),
            (change_type("SUPPLIER", abstract=True), "SUPPLIER: became abstract"),
            (change_type("SUPPLIER", attributes=()), "SUPPLIER: attribute country was removed"),
            (
                change_type(
                    "SUPPLIER",
                    attributes=(*supplier().attributes, attribute("duns", required=True)),
                ),
                "SUPPLIER: new attribute duns must be optional",
            ),
            (
                change_type(
                    "SUPPLIER",
                    attributes=(
                        attribute(
                            "country", max_length=3
                        ),  # was 2: values valid before stay valid,
                        *supplier().attributes[1:],  # but any change is refused: keep it simple
                    ),
                ),
                "SUPPLIER: attribute country changed its definition",
            ),
            (
                change_relation("SUPPLIES", target_types=frozenset({"CARRIER"})),
                "SUPPLIES: target_types no longer accept ['PRODUCT']",
            ),
            (
                change_relation(
                    "SUPPLIES",
                    attributes=(
                        attribute("lead_time_days", AttributeType.DECIMAL),
                        *supplies().attributes[1:],
                    ),
                ),
                "SUPPLIES: attribute lead_time_days changed its definition",
            ),
        ],
    )
    def test_breaking_changes_are_reported(self, v2: PackageContent, message: str) -> None:
        problems = incompatibilities(V1, v2)
        assert any(message in p for p in problems), problems


def test_a_new_version_keeps_the_package_identity() -> None:
    first, other = TenantId.new(), TenantId.new()
    assert incompatibilities(tenant_content(first), tenant_content(other)) == [
        "a new version must keep the package key, scope, namespace and tenant"
    ]
