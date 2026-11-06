"""Value objects and statuses of a disruption case."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final

from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import require_text, require_utc
from tracer.shared.identifiers import (
    CustomerOrderId,
    EntityId,
    ProductId,
    RecoveryPlanId,
    ShipmentId,
    SupplierId,
    UserId,
)

MAX_SUMMARY_LENGTH: Final = 2000
MAX_REASON_LENGTH: Final = 1000


class DisruptionKind(StrEnum):
    SHIPMENT_DELAY = "shipment_delay"
    SUPPLIER_FAILURE = "supplier_failure"
    PORT_CONGESTION = "port_congestion"
    CAPACITY_SHORTAGE = "capacity_shortage"
    QUALITY_ISSUE = "quality_issue"
    OTHER = "other"


class CaseStatus(StrEnum):
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


OPEN_STATUSES: Final = frozenset({CaseStatus.DETECTED, CaseStatus.INVESTIGATING})


class ResolutionOutcome(StrEnum):
    RECOVERED = "recovered"  # an approved recovery plan dealt with it
    IMPACT_ACCEPTED = "impact_accepted"  # no recovery: the business accepted the impact


def _require_ids[I: EntityId](ids: frozenset[I], id_type: type[I], field: str) -> None:
    if not isinstance(ids, frozenset) or not all(type(i) is id_type for i in ids):
        raise InvalidValueError(f"{field} must be a frozenset of {id_type.__name__}")


@dataclass(frozen=True, slots=True)
class AffectedEntities:
    """References (by id only) to what a disruption touches. Never embedded aggregates."""

    supplier_ids: frozenset[SupplierId] = frozenset()
    shipment_ids: frozenset[ShipmentId] = frozenset()
    product_ids: frozenset[ProductId] = frozenset()
    customer_order_ids: frozenset[CustomerOrderId] = frozenset()

    def __post_init__(self) -> None:
        _require_ids(self.supplier_ids, SupplierId, "supplier_ids")
        _require_ids(self.shipment_ids, ShipmentId, "shipment_ids")
        _require_ids(self.product_ids, ProductId, "product_ids")
        _require_ids(self.customer_order_ids, CustomerOrderId, "customer_order_ids")

    @property
    def is_empty(self) -> bool:
        return not (
            self.supplier_ids or self.shipment_ids or self.product_ids or self.customer_order_ids
        )

    def union(self, other: "AffectedEntities") -> "AffectedEntities":
        return AffectedEntities(
            self.supplier_ids | other.supplier_ids,
            self.shipment_ids | other.shipment_ids,
            self.product_ids | other.product_ids,
            self.customer_order_ids | other.customer_order_ids,
        )

    def difference(self, other: "AffectedEntities") -> "AffectedEntities":
        return AffectedEntities(
            self.supplier_ids - other.supplier_ids,
            self.shipment_ids - other.shipment_ids,
            self.product_ids - other.product_ids,
            self.customer_order_ids - other.customer_order_ids,
        )


@dataclass(frozen=True, slots=True)
class CaseResolution:
    """How a disruption ended. RECOVERED must name the plan; IMPACT_ACCEPTED must not."""

    outcome: ResolutionOutcome
    summary: str
    recovery_plan_id: RecoveryPlanId | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "summary", require_text(self.summary, "summary", max_length=MAX_SUMMARY_LENGTH)
        )
        has_plan = self.recovery_plan_id is not None
        if self.outcome is ResolutionOutcome.RECOVERED and not has_plan:
            raise InvalidValueError("a recovered disruption must name its recovery plan")
        if self.outcome is ResolutionOutcome.IMPACT_ACCEPTED and has_plan:
            raise InvalidValueError("an accepted impact has no recovery plan")


@dataclass(frozen=True, slots=True)
class CaseClosure:
    """Who closed the case, when, and either the resolution or the dismissal reason."""

    closed_at: datetime
    closed_by: UserId
    resolution: CaseResolution | None = None
    dismissal_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "closed_at", require_utc(self.closed_at, "closed_at"))
        if (self.resolution is None) == (self.dismissal_reason is None):
            raise InvalidValueError("a closure has either a resolution or a dismissal reason")
        if self.dismissal_reason is not None:
            reason = require_text(self.dismissal_reason, "reason", max_length=MAX_REASON_LENGTH)
            object.__setattr__(self, "dismissal_reason", reason)
