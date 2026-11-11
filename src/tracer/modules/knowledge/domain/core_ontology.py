"""TRACER core ontology, version 1: the vocabulary every tenant shares.

Ontology as code: reviewed like code, published by ``tracer ontology publish-core`` (a no-op
when unchanged). A change here becomes the next core version and must pass the compatibility
check: add and deprecate, never remove or redefine.

Shape of the supply chain it describes:

    SUPPLIER --SUPPLIES--> PRODUCT <--REQUIRES-- CUSTOMER_ORDER --PLACED_BY--> CUSTOMER
       ^                      ^                        ^
    SENT_BY                CARRIES                FOR_ORDER
       |                      |                        |
    SHIPMENT ----------------+                    ALLOCATION --ALLOCATES--> INVENTORY_LOT
       |--TRANSPORTED_BY--> CARRIER
       |--PASSES_THROUGH--> PORT              INVENTORY_LOT --LOT_OF--> PRODUCT
       '--DESTINED_FOR---> FACILITY           INVENTORY_LOT --STORED_AT--> WAREHOUSE
    FACILITY (abstract: WAREHOUSE | FACTORY) --PART_OF--> FACILITY   (business hierarchy)

``ALLOCATION`` is a reified n-ary fact (order + lot + quantity) instead of a hyperedge.
``FACILITY`` is abstract: only its subtypes have instances.
"""

from decimal import Decimal
from typing import Any, Final

from tracer.modules.knowledge.domain.attributes import AttributeDefinition, AttributeType
from tracer.modules.knowledge.domain.ontology import (
    CORE_PACKAGE_KEY,
    EntityTypeDefinition,
    PackageContent,
    PackageScope,
    RelationTypeDefinition,
)
from tracer.shared.quantity import Dimension


def _a(name: str, kind: AttributeType, **options: Any) -> AttributeDefinition:
    return AttributeDefinition(name=name, type=kind, **options)


_COUNTRY = _a("country", AttributeType.STRING, max_length=2, description="ISO 3166-1 alpha-2")
_ZERO = Decimal(0)

_TYPES: Final = (
    EntityTypeDefinition(
        "SUPPLIER",
        "Supplier",
        "An organisation the tenant buys from.",
        attributes=(
            _COUNTRY,
            _a("risk_score", AttributeType.DECIMAL, minimum=_ZERO, maximum=Decimal(1)),
        ),
    ),
    EntityTypeDefinition(
        "PRODUCT",
        "Product",
        "Something bought, stored or sold (a SKU).",
        attributes=(_a("unit_of_measure", AttributeType.STRING, max_length=16),),
    ),
    EntityTypeDefinition("CARRIER", "Carrier", "A company that transports goods."),
    EntityTypeDefinition(
        "CUSTOMER", "Customer", "An organisation the tenant sells to.", attributes=(_COUNTRY,)
    ),
    EntityTypeDefinition(
        "FACILITY",
        "Facility",
        "A physical site. Abstract: use a subtype.",
        abstract=True,
        attributes=(_COUNTRY, _a("city", AttributeType.STRING, max_length=100)),
    ),
    EntityTypeDefinition(
        "WAREHOUSE",
        "Warehouse",
        "A site where inventory is stored.",
        parent="FACILITY",
        attributes=(
            _a(
                "storage_condition",
                AttributeType.ENUM,
                values=("ambient", "chilled", "frozen", "hazardous"),
            ),
        ),
    ),
    EntityTypeDefinition(
        "FACTORY", "Factory", "A site where goods are produced.", parent="FACILITY"
    ),
    EntityTypeDefinition("PORT", "Port", "A sea, air or inland port.", attributes=(_COUNTRY,)),
    EntityTypeDefinition(
        "SHIPMENT",
        "Shipment",
        "Goods in transit.",
        attributes=(
            _a(
                "status",
                AttributeType.ENUM,
                values=("planned", "in_transit", "delayed", "delivered", "cancelled"),
            ),
            _a("expected_arrival", AttributeType.DATETIME),
        ),
    ),
    EntityTypeDefinition(
        "CUSTOMER_ORDER",
        "Customer order",
        "Demand from a customer.",
        attributes=(
            _a("status", AttributeType.ENUM, values=("open", "allocated", "shipped", "cancelled")),
            _a("promised_date", AttributeType.DATETIME),
        ),
    ),
    EntityTypeDefinition(
        "INVENTORY_LOT", "Inventory lot", "A quantity of one product held together."
    ),
    EntityTypeDefinition(
        "ALLOCATION",
        "Allocation",
        "Inventory reserved for an order (a reified relationship: order + lot + quantity).",
        attributes=(_a("status", AttributeType.ENUM, values=("reserved", "picked", "released")),),
    ),
)

_COUNT = _a("quantity", AttributeType.QUANTITY, required=True, dimension=Dimension.COUNT)

_RELATIONS: Final = (
    RelationTypeDefinition(
        "SUPPLIES",
        "supplies",
        frozenset({"SUPPLIER"}),
        frozenset({"PRODUCT"}),
        attributes=(
            _a("lead_time_days", AttributeType.INTEGER, minimum=_ZERO),
            _a("unit_price", AttributeType.MONEY),
        ),
    ),
    RelationTypeDefinition("SENT_BY", "sent by", frozenset({"SHIPMENT"}), frozenset({"SUPPLIER"})),
    RelationTypeDefinition(
        "CARRIES", "carries", frozenset({"SHIPMENT"}), frozenset({"PRODUCT"}), attributes=(_COUNT,)
    ),
    RelationTypeDefinition(
        "TRANSPORTED_BY", "transported by", frozenset({"SHIPMENT"}), frozenset({"CARRIER"})
    ),
    RelationTypeDefinition(
        "PASSES_THROUGH", "passes through", frozenset({"SHIPMENT"}), frozenset({"PORT"})
    ),
    RelationTypeDefinition(
        "DESTINED_FOR", "destined for", frozenset({"SHIPMENT"}), frozenset({"FACILITY"})
    ),
    RelationTypeDefinition(
        "PLACED_BY", "placed by", frozenset({"CUSTOMER_ORDER"}), frozenset({"CUSTOMER"})
    ),
    RelationTypeDefinition(
        "REQUIRES",
        "requires",
        frozenset({"CUSTOMER_ORDER"}),
        frozenset({"PRODUCT"}),
        attributes=(_COUNT, _a("need_by", AttributeType.DATETIME)),
    ),
    RelationTypeDefinition(
        "LOT_OF",
        "lot of",
        frozenset({"INVENTORY_LOT"}),
        frozenset({"PRODUCT"}),
        attributes=(_COUNT,),
    ),
    RelationTypeDefinition(
        "STORED_AT", "stored at", frozenset({"INVENTORY_LOT"}), frozenset({"WAREHOUSE"})
    ),
    RelationTypeDefinition(
        "ALLOCATES",
        "allocates",
        frozenset({"ALLOCATION"}),
        frozenset({"INVENTORY_LOT"}),
        attributes=(_COUNT,),
    ),
    RelationTypeDefinition(
        "FOR_ORDER", "for order", frozenset({"ALLOCATION"}), frozenset({"CUSTOMER_ORDER"})
    ),
    RelationTypeDefinition(
        "PART_OF",
        "part of",
        frozenset({"FACILITY"}),
        frozenset({"FACILITY"}),
        description="Business hierarchy between sites (instance level, not type inheritance).",
    ),
)

CORE_ONTOLOGY: Final = PackageContent(
    key=CORE_PACKAGE_KEY,
    scope=PackageScope.CORE,
    namespace=None,
    tenant_id=None,
    entity_types=_TYPES,
    relation_types=_RELATIONS,
    description="TRACER core supply-chain vocabulary.",
)
