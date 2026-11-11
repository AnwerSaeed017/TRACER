"""Cross-package rules, inheritance, and validation of graph values against the ontology."""

import pytest

from tests.knowledge import CORE, attribute, published, tenant_content
from tracer.modules.knowledge.domain.attributes import AttributeType
from tracer.modules.knowledge.domain.core_ontology import CORE_ONTOLOGY
from tracer.modules.knowledge.domain.ontology import (
    EntityTypeDefinition,
    EntityTypeExtension,
    OntologyError,
    PackageContent,
    PackageScope,
    RelationTypeDefinition,
)
from tracer.modules.knowledge.domain.resolved import ResolvedOntology
from tracer.shared.errors import InvalidValueError
from tracer.shared.identifiers import TenantId

TENANT = TenantId.new()
CORE_ONLY = ResolvedOntology([CORE])


def with_tenant(**definitions: object) -> ResolvedOntology:
    return ResolvedOntology([CORE, published(tenant_content(TENANT, **definitions))])  # type: ignore[arg-type]


class TestCoreOntology:
    def test_resolves_with_the_flagship_vocabulary(self) -> None:
        assert {
            "SUPPLIER",
            "SHIPMENT",
            "PRODUCT",
            "CUSTOMER_ORDER",
            "WAREHOUSE",
        } <= CORE_ONLY.entity_type_keys
        assert {
            "SUPPLIES",
            "SENT_BY",
            "CARRIES",
            "REQUIRES",
            "STORED_AT",
        } <= CORE_ONLY.relation_type_keys

    def test_subtypes_inherit_parent_attributes(self) -> None:
        warehouse = CORE_ONLY.entity_type("WAREHOUSE")
        assert warehouse.ancestors == ("FACILITY",)
        assert set(warehouse.attributes) == {"country", "city", "storage_condition"}

    def test_abstract_types_cannot_have_instances(self) -> None:
        assert CORE_ONLY.validate_properties("FACILITY", {}) == [
            "FACILITY is abstract and cannot have instances"
        ]
        assert CORE_ONLY.validate_properties("WAREHOUSE", {"city": "Karachi"}) == []


class TestEntityValidation:
    def test_valid_properties(self) -> None:
        props = {"country": "PK", "risk_score": "0.35"}
        assert CORE_ONLY.validate_properties("SUPPLIER", props) == []

    def test_every_problem_is_reported(self) -> None:
        problems = CORE_ONLY.validate_properties(
            "SUPPLIER", {"country": "Pakistan", "risk_score": 0.35, "colour": "red"}
        )
        assert sorted(problems) == sorted(
            [
                "SUPPLIER: unknown attribute colour",
                "SUPPLIER.country: must be at most 2 characters",
                'SUPPLIER.risk_score: must be a decimal written as a string, e.g. "12.50"',
            ]
        )

    def test_unknown_type(self) -> None:
        assert CORE_ONLY.validate_properties("SPACESHIP", {}) == ["unknown entity type SPACESHIP"]

    def test_properties_must_be_an_object(self) -> None:
        assert CORE_ONLY.validate_properties("SUPPLIER", ["not", "a", "mapping"]) == [  # type: ignore[arg-type]
            "SUPPLIER: properties must be an object"
        ]


class TestRelationValidation:
    def test_supplies_accepts_supplier_to_product(self) -> None:
        props = {"lead_time_days": 12, "unit_price": {"amount": "120.50", "currency": "USD"}}
        assert CORE_ONLY.validate_relation("SUPPLIES", "SUPPLIER", "PRODUCT", props) == []

    def test_supplies_rejects_warehouse_to_customer(self) -> None:
        problems = CORE_ONLY.validate_relation("SUPPLIES", "WAREHOUSE", "CUSTOMER", {})
        assert problems == [
            "SUPPLIES cannot start at WAREHOUSE (allowed: ['SUPPLIER'])",
            "SUPPLIES cannot end at CUSTOMER (allowed: ['PRODUCT'])",
        ]

    def test_an_endpoint_accepts_subtypes(self) -> None:
        # DESTINED_FOR ends at FACILITY; WAREHOUSE is a FACILITY.
        assert CORE_ONLY.validate_relation("DESTINED_FOR", "SHIPMENT", "WAREHOUSE", {}) == []

    def test_relation_properties_are_validated(self) -> None:
        problems = CORE_ONLY.validate_relation("CARRIES", "SHIPMENT", "PRODUCT", {})
        assert problems == ["CARRIES: missing required attribute quantity"]

    def test_unknown_relation(self) -> None:
        assert CORE_ONLY.validate_relation("TELEPORTS", "SHIPMENT", "PRODUCT", {}) == [
            "unknown relation type TELEPORTS"
        ]


class TestTenantExtensions:
    def test_a_tenant_can_add_a_subtype_of_a_core_type(self) -> None:
        ontology = with_tenant(
            entity_types=(
                EntityTypeDefinition(
                    "acme.COLD_ROOM",
                    "Cold room",
                    parent="WAREHOUSE",
                    attributes=(attribute("min_celsius", AttributeType.INTEGER),),
                ),
            )
        )
        cold_room = ontology.entity_type("acme.COLD_ROOM")
        assert cold_room.ancestors == ("WAREHOUSE", "FACILITY")
        assert {"min_celsius", "storage_condition", "city"} <= set(cold_room.attributes)
        assert ontology.validate_relation("STORED_AT", "INVENTORY_LOT", "acme.COLD_ROOM", {}) == []

    def test_a_tenant_can_add_optional_attributes_to_a_core_type(self) -> None:
        ontology = with_tenant(
            extensions=(EntityTypeExtension("SUPPLIER", (attribute("acme_vendor_code"),)),)
        )
        assert ontology.validate_properties("SUPPLIER", {"acme_vendor_code": "V-812"}) == []
        assert CORE_ONLY.validate_properties("SUPPLIER", {"acme_vendor_code": "V-812"}) == [
            "SUPPLIER: unknown attribute acme_vendor_code"  # other tenants are unaffected
        ]

    def test_extended_attributes_reach_subtypes(self) -> None:
        ontology = with_tenant(
            extensions=(EntityTypeExtension("FACILITY", (attribute("site_code"),)),)
        )
        assert "site_code" in ontology.entity_type("WAREHOUSE").attributes

    def test_a_tenant_cannot_redefine_a_core_type(self) -> None:
        # First line of defence: a tenant's keys must carry its namespace, so "SUPPLIER" is
        # not even expressible in a tenant package.
        with pytest.raises(InvalidValueError, match=r"need 'acme\.'"):
            tenant_content(TENANT, entity_types=(EntityTypeDefinition("SUPPLIER", "Supplier"),))

    def test_two_packages_claiming_one_key_are_refused(self) -> None:
        # Second line of defence: an industry package and a tenant that picked the same
        # namespace both define mfg.MACHINE. The chain refuses the second definition.
        industry = published(
            PackageContent(
                "manufacturing",
                PackageScope.INDUSTRY,
                "mfg",
                None,
                (CORE.id,),
                entity_types=(EntityTypeDefinition("mfg.MACHINE", "Machine"),),
            )
        )
        tenant = published(
            tenant_content(
                TENANT,
                namespace="mfg",
                depends_on=(CORE.id, industry.id),
                entity_types=(EntityTypeDefinition("mfg.MACHINE", "Our machine"),),
            )
        )
        with pytest.raises(
            OntologyError, match=r"mfg\.MACHINE is already defined by manufacturing v1"
        ):
            ResolvedOntology([CORE, industry, tenant])

    def test_a_tenant_cannot_override_an_inherited_attribute(self) -> None:
        with pytest.raises(OntologyError, match="attribute city is already defined"):
            with_tenant(
                entity_types=(
                    EntityTypeDefinition(
                        "acme.DEPOT", "Depot", parent="FACILITY", attributes=(attribute("city"),)
                    ),
                )
            )

    def test_an_extension_cannot_collide_with_an_existing_attribute(self) -> None:
        with pytest.raises(OntologyError, match="SUPPLIER: attribute country is already defined"):
            with_tenant(extensions=(EntityTypeExtension("SUPPLIER", (attribute("country"),)),))

    def test_references_must_exist(self) -> None:
        with pytest.raises(OntologyError) as caught:
            with_tenant(
                entity_types=(EntityTypeDefinition("acme.ROBOT", "Robot", parent="MACHINE"),),
                extensions=(EntityTypeExtension("SPACESHIP", (attribute("fuel"),)),),
                relation_types=(
                    RelationTypeDefinition(
                        "acme.REPAIRS", "repairs", frozenset({"acme.ROBOT"}), frozenset({"MACHINE"})
                    ),
                ),
            )
        assert set(caught.value.problems) == {
            "acme.ROBOT: unknown parent type MACHINE",
            "extension of unknown type SPACESHIP",
            "relation acme.REPAIRS refers to unknown type MACHINE",
        }

    def test_inheritance_cycles_are_refused(self) -> None:
        with pytest.raises(OntologyError, match="inheritance cycle"):
            with_tenant(
                entity_types=(
                    EntityTypeDefinition("acme.AA", "A", parent="acme.BB"),
                    EntityTypeDefinition("acme.BB", "B", parent="acme.AA"),
                )
            )

    def test_two_versions_of_one_package_cannot_be_combined(self) -> None:
        with pytest.raises(OntologyError, match="two versions of the same package"):
            ResolvedOntology([CORE, published(CORE_ONTOLOGY, version=2)])
