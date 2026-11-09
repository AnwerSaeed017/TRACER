"""What the supply-chain use cases need from the outside world. Adapters implement these."""

from datetime import datetime
from types import TracebackType
from typing import Protocol, Self
from uuid import UUID

from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.shared.identifiers import DisruptionCaseId, EvidenceId, TenantId


class DisruptionCaseRepository(Protocol):
    async def get(self, tenant_id: TenantId, case_id: DisruptionCaseId) -> DisruptionCase | None:
        """The case, or None if it does not exist *for this tenant*."""
        ...

    async def find_open_by_evidence(
        self, tenant_id: TenantId, evidence_id: EvidenceId
    ) -> DisruptionCase | None:
        """The oldest open case that cites this evidence, if any."""
        ...

    async def add(self, case: DisruptionCase) -> None:
        """Store a new case together with its pending events."""
        ...

    async def update(self, case: DisruptionCase) -> None:
        """Store changes together with their events. Raises ConcurrencyConflictError if the
        case changed since it was loaded."""
        ...


class ProcessedEvents(Protocol):
    async def claim(self, *, consumer: str, event_id: UUID, at: datetime) -> bool:
        """True the first time this consumer sees the event; False for a redelivery."""
        ...


class SupplyChainUnitOfWork(Protocol):
    @property
    def cases(self) -> DisruptionCaseRepository: ...

    @property
    def processed_events(self) -> ProcessedEvents: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...
