"""What the knowledge use cases need from storage. Adapters implement these."""

from types import TracebackType
from typing import Protocol, Self

from tracer.modules.knowledge.domain.ontology import OntologyPackageId, PublishedPackage
from tracer.shared.identifiers import TenantId


class OntologyRepository(Protocol):
    async def latest(self, key: str, tenant_id: TenantId | None) -> PublishedPackage | None:
        """The newest published version of a package (global when ``tenant_id`` is None)."""
        ...

    async def get(self, package_id: OntologyPackageId) -> PublishedPackage | None: ...

    async def add(self, package: PublishedPackage) -> None:
        """Store a new version. Raises ConcurrencyConflictError if that version already exists."""
        ...


class OntologyUnitOfWork(Protocol):
    @property
    def ontology(self) -> OntologyRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
