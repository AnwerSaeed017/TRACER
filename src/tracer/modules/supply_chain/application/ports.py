"""What the supply-chain use cases need from the outside world. Adapters implement these."""

from types import TracebackType
from typing import Protocol, Self

from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.shared.identifiers import DisruptionCaseId, TenantId


class DisruptionCaseRepository(Protocol):
    async def get(self, tenant_id: TenantId, case_id: DisruptionCaseId) -> DisruptionCase | None:
        """The case, or None if it does not exist *for this tenant*."""
        ...

    async def add(self, case: DisruptionCase) -> None:
        """Store a new case together with its pending events."""
        ...

    async def update(self, case: DisruptionCase) -> None:
        """Store changes together with their events. Raises ConcurrencyConflictError if the
        case changed since it was loaded."""
        ...


class SupplyChainUnitOfWork(Protocol):
    @property
    def cases(self) -> DisruptionCaseRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
