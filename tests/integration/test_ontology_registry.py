"""The ontology registry against real PostgreSQL."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.knowledge import attribute, tenant_content
from tests.support import T0
from tracer.modules.knowledge.adapters.persistence.repository import CorruptOntologyError
from tracer.modules.knowledge.adapters.persistence.unit_of_work import (
    SqlAlchemyOntologyUnitOfWork,
)
from tracer.modules.knowledge.application.ontology_registry import OntologyRegistry
from tracer.modules.knowledge.domain.core_ontology import CORE_ONTOLOGY
from tracer.modules.knowledge.domain.ontology import (
    EntityTypeDefinition,
    EntityTypeExtension,
    OntologyError,
    OntologyPackageId,
    PublishedPackage,
)
from tracer.shared.clock import FrozenClock
from tracer.shared.errors import ConcurrencyConflictError, NotFoundError
from tracer.shared.identifiers import TenantId

pytestmark = pytest.mark.integration

COLD_ROOM = EntityTypeDefinition("acme.COLD_ROOM", "Cold room", parent="WAREHOUSE")


@pytest.fixture
def registry(sessions: async_sessionmaker[AsyncSession]) -> OntologyRegistry:
    return OntologyRegistry(lambda: SqlAlchemyOntologyUnitOfWork(sessions), FrozenClock(T0))


async def publish_core(registry: OntologyRegistry) -> OntologyPackageId:
    return (await registry.publish(CORE_ONTOLOGY, published_by="tests")).package.id


async def test_publishing_the_core_is_idempotent(registry: OntologyRegistry) -> None:
    first = await registry.publish(CORE_ONTOLOGY, published_by="tests")
    again = await registry.publish(CORE_ONTOLOGY, published_by="tests")

    assert (first.created, first.package.version) == (True, 1)
    assert (again.created, again.package.id) == (False, first.package.id)
    resolved = await registry.resolve(first.package.id)
    assert "SUPPLIER" in resolved.entity_type_keys  # stored, loaded, re-validated, resolvable


async def test_a_compatible_change_is_the_next_version_and_old_versions_stay_resolvable(
    registry: OntologyRegistry,
) -> None:
    v1 = await publish_core(registry)
    v2_content = replace(
        CORE_ONTOLOGY,
        entity_types=(*CORE_ONTOLOGY.entity_types, EntityTypeDefinition("DOCK", "Dock")),
    )
    v2 = await registry.publish(v2_content, published_by="tests")

    assert v2.package.version == 2
    assert "DOCK" in (await registry.resolve(v2.package.id)).entity_type_keys
    assert "DOCK" not in (await registry.resolve(v1)).entity_type_keys  # v1 unchanged


async def test_an_incompatible_change_is_refused_and_nothing_is_stored(
    registry: OntologyRegistry,
) -> None:
    await publish_core(registry)
    without_carrier = replace(
        CORE_ONTOLOGY,
        entity_types=tuple(t for t in CORE_ONTOLOGY.entity_types if t.key != "CARRIER"),
        relation_types=tuple(r for r in CORE_ONTOLOGY.relation_types if r.key != "TRANSPORTED_BY"),
    )
    with pytest.raises(
        OntologyError, match=r"not compatible with tracer\.core v1: entity type CARRIER was removed"
    ):
        await registry.publish(without_carrier, published_by="tests")

    again = await registry.publish(CORE_ONTOLOGY, published_by="tests")
    assert (again.created, again.package.version) == (False, 1)  # still only v1


async def test_a_tenant_extends_the_core_without_copying_it(registry: OntologyRegistry) -> None:
    core = await publish_core(registry)
    tenant = TenantId.new()
    content = tenant_content(
        tenant,
        depends_on=(core,),
        entity_types=(COLD_ROOM,),
        extensions=(EntityTypeExtension("SUPPLIER", (attribute("acme_vendor_code"),)),),
    )
    await registry.publish(content, published_by="acme-admin")

    ontology = await registry.resolve_for_tenant(tenant)
    assert ontology.tenant_id == tenant
    assert ontology.package_ids[0] == core  # the shared core, by reference
    assert ontology.entity_type("acme.COLD_ROOM").ancestors == ("WAREHOUSE", "FACILITY")
    assert ontology.validate_properties("SUPPLIER", {"acme_vendor_code": "V-812"}) == []


async def test_a_tenant_cannot_see_another_tenants_ontology(registry: OntologyRegistry) -> None:
    core = await publish_core(registry)
    acme, globex = TenantId.new(), TenantId.new()
    await registry.publish(
        tenant_content(acme, depends_on=(core,), entity_types=(COLD_ROOM,)), published_by="acme"
    )

    with pytest.raises(NotFoundError):
        await registry.resolve_for_tenant(globex)
    globex_ontology = replace(tenant_content(globex, depends_on=(core,), namespace="globex"))
    await registry.publish(globex_ontology, published_by="globex")
    assert "acme.COLD_ROOM" not in (await registry.resolve_for_tenant(globex)).entity_type_keys


async def test_a_tenant_package_cannot_depend_on_another_tenants_package(
    registry: OntologyRegistry,
) -> None:
    core = await publish_core(registry)
    acme = await registry.publish(
        tenant_content(TenantId.new(), depends_on=(core,), entity_types=(COLD_ROOM,)),
        published_by="acme",
    )
    with pytest.raises(OntologyError, match="may depend on global packages only"):
        await registry.publish(
            tenant_content(TenantId.new(), depends_on=(core, acme.package.id), namespace="globex"),
            published_by="globex",
        )


async def test_an_unknown_dependency_is_refused(registry: OntologyRegistry) -> None:
    with pytest.raises(OntologyError, match="depends on unknown package"):
        await registry.publish(
            tenant_content(TenantId.new(), depends_on=(OntologyPackageId.new(),)),
            published_by="tests",
        )


async def test_published_rows_cannot_be_changed_even_with_raw_sql(
    registry: OntologyRegistry, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await publish_core(registry)
    statements = [
        "UPDATE ontology_entity_types SET description = 'tampered' WHERE type_key = 'SUPPLIER'",
        "DELETE FROM ontology_relation_types WHERE relation_key = 'SUPPLIES'",
        "UPDATE ontology_packages SET version = 99",
    ]
    for statement in statements:
        async with sessions() as session:
            with pytest.raises(DBAPIError, match="published ontology definitions are immutable"):
                await session.execute(text(statement))


async def test_tampered_rows_are_detected_when_loaded(
    registry: OntologyRegistry, sessions: async_sessionmaker[AsyncSession]
) -> None:
    core = await publish_core(registry)
    async with sessions() as session, session.begin():
        # Simulate tampering by someone who bypassed the trigger (e.g. a superuser).
        await session.execute(text("ALTER TABLE ontology_entity_types DISABLE TRIGGER USER"))
        await session.execute(
            text("UPDATE ontology_entity_types SET abstract = true WHERE type_key = 'SUPPLIER'")
        )
        await session.execute(text("ALTER TABLE ontology_entity_types ENABLE TRIGGER USER"))

    with pytest.raises(CorruptOntologyError, match="does not match its published fingerprint"):
        await registry.resolve(core)


async def test_concurrent_publishes_of_one_package_cannot_both_win(
    registry: OntologyRegistry,
) -> None:
    core = await publish_core(registry)
    tenant = TenantId.new()
    first = tenant_content(tenant, depends_on=(core,), entity_types=(COLD_ROOM,))
    second = tenant_content(
        tenant, depends_on=(core,), entity_types=(EntityTypeDefinition("acme.DEPOT", "Depot"),)
    )

    results = await asyncio.gather(
        registry.publish(first, published_by="a"),
        registry.publish(second, published_by="b"),
        return_exceptions=True,
    )

    # Which defence fires depends on timing: if the loser read "latest" before the winner
    # committed, both write v1 and the unique constraint refuses one (ConcurrencyConflictError);
    # if it read after, it proposes v2 without COLD_ROOM and the compatibility check refuses it.
    # Either way exactly one wins. The constraint path is pinned deterministically below.
    won = [r for r in results if not isinstance(r, BaseException)]
    lost = [r for r in results if isinstance(r, BaseException)]
    assert len(won) == 1
    assert len(lost) == 1
    assert isinstance(lost[0], ConcurrencyConflictError | OntologyError)
    assert won[0].package.version == 1


async def test_the_database_refuses_a_second_copy_of_the_same_version(
    registry: OntologyRegistry, sessions: async_sessionmaker[AsyncSession]
) -> None:
    core = await publish_core(registry)
    tenant = TenantId.new()
    content = tenant_content(tenant, depends_on=(core,), entity_types=(COLD_ROOM,))

    def version_1() -> PublishedPackage:
        return PublishedPackage(OntologyPackageId.new(), 1, content, T0, "tests")

    async with SqlAlchemyOntologyUnitOfWork(sessions) as first:
        await first.ontology.add(version_1())  # holds the (tenant, key, 1) slot, uncommitted

        async def second_writer() -> None:
            async with SqlAlchemyOntologyUnitOfWork(sessions) as second:
                await second.ontology.add(version_1())  # waits on the unique index ...
                await second.commit()

        racing = asyncio.create_task(second_writer())
        await asyncio.sleep(0.2)  # let the second writer reach the index and wait
        await first.commit()  # ... the first commits, so the second must fail

    with pytest.raises(ConcurrencyConflictError, match="ontology package"):
        await racing
    assert (await registry.resolve_for_tenant(tenant)).entity_type("acme.COLD_ROOM")
