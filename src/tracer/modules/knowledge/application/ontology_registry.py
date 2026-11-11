"""Use cases: publish an ontology package version, and resolve the ontology a tenant uses.

Publishing:
    1. identical content to the latest version?  -> return it, create nothing (idempotent)
    2. version = latest + 1 (or 1)
    3. load its dependency chain (global packages only; one version per package)
    4. resolve the chain + the new package: every cross-package rule must hold
    5. compatible with the previous version of the same package?
    6. store it (a concurrent publish of the same version loses with ConcurrencyConflictError)

Resolving for a tenant: the tenant's latest package version plus exactly what it pins.
Old versions stay resolvable forever, so facts validated under them remain explainable.
"""

from collections.abc import Callable
from dataclasses import dataclass

from tracer.modules.knowledge.application.ports import OntologyUnitOfWork
from tracer.modules.knowledge.domain.compatibility import incompatibilities
from tracer.modules.knowledge.domain.ontology import (
    TENANT_PACKAGE_KEY,
    OntologyError,
    OntologyPackageId,
    PackageContent,
    PackageScope,
    PublishedPackage,
)
from tracer.modules.knowledge.domain.resolved import ResolvedOntology
from tracer.shared.clock import Clock
from tracer.shared.errors import NotFoundError
from tracer.shared.identifiers import TenantId


@dataclass(frozen=True, slots=True)
class PublishResult:
    package: PublishedPackage
    created: bool  # False: identical content was already the latest version


class OntologyRegistry:
    def __init__(self, unit_of_work: Callable[[], OntologyUnitOfWork], clock: Clock) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    async def publish(self, content: PackageContent, *, published_by: str) -> PublishResult:
        async with self._unit_of_work() as uow:
            previous = await uow.ontology.latest(content.key, content.tenant_id)
            if previous is not None and previous.fingerprint == content.fingerprint():
                return PublishResult(previous, created=False)

            package = PublishedPackage(
                id=OntologyPackageId.new(),
                version=1 if previous is None else previous.version + 1,
                content=content,
                published_at=self._clock.now(),
                published_by=published_by,
            )
            ResolvedOntology(await self._chain(uow, package))  # raises OntologyError
            if previous is not None:
                problems = incompatibilities(previous.content, content)
                if problems:
                    raise OntologyError(
                        [f"not compatible with {previous.label()}: {p}" for p in problems]
                    )
            await uow.ontology.add(package)
            await uow.commit()
        return PublishResult(package, created=True)

    async def resolve(self, package_id: OntologyPackageId) -> ResolvedOntology:
        async with self._unit_of_work() as uow:
            package = await uow.ontology.get(package_id)
            if package is None:
                raise NotFoundError(f"ontology package {package_id} does not exist")
            return ResolvedOntology(await self._chain(uow, package))

    async def resolve_for_tenant(self, tenant_id: TenantId) -> ResolvedOntology:
        async with self._unit_of_work() as uow:
            package = await uow.ontology.latest(TENANT_PACKAGE_KEY, tenant_id)
            if package is None:
                raise NotFoundError(f"tenant {tenant_id} has no ontology yet")
            return ResolvedOntology(await self._chain(uow, package))

    async def _chain(
        self, uow: OntologyUnitOfWork, package: PublishedPackage
    ) -> list[PublishedPackage]:
        """``package`` and everything it depends on, dependencies first."""
        ordered: list[PublishedPackage] = []
        seen: set[OntologyPackageId] = set()

        async def visit(current: PublishedPackage, path: tuple[OntologyPackageId, ...]) -> None:
            if current.id in path:
                raise OntologyError([f"dependency cycle through {current.label()}"])
            if current.id in seen:
                return
            for dependency_id in current.content.depends_on:
                dependency = await uow.ontology.get(dependency_id)
                if dependency is None:
                    raise OntologyError(
                        [f"{current.label()} depends on unknown package {dependency_id}"]
                    )
                if (
                    dependency.content.tenant_id is not None
                    or dependency.content.scope is PackageScope.TENANT
                ):
                    # Tenant isolation: nothing may depend on a tenant's private package.
                    raise OntologyError([f"{current.label()} may depend on global packages only"])
                await visit(dependency, (*path, current.id))
            seen.add(current.id)
            ordered.append(current)

        await visit(package, ())
        if ordered[0].content.scope is not PackageScope.CORE:
            raise OntologyError([f"{package.label()} does not build on the core ontology"])
        return ordered
