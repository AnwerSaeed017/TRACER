"""Value objects and statuses of a recovery plan, and the plan fingerprint."""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import require_text, require_utc
from tracer.shared.identifiers import EvidenceId, ProductId, RecoveryPlanId, UserId
from tracer.shared.money import Money
from tracer.shared.quantity import Quantity

MAX_DESCRIPTION_LENGTH: Final = 500
MAX_RATIONALE_LENGTH: Final = 4000
MAX_REASON_LENGTH: Final = 1000
MAX_ACTIONS: Final = 50
# Version of the canonical form below. Changing how anything is serialised (decimals, enums,
# new fields) means a new version, so stored approvals stay checkable against the form they
# were made with. Part of the hashed payload.
FINGERPRINT_VERSION: Final = 1


class PlanStatus(StrEnum):
    PROPOSED = "proposed"  # generated, not yet checked
    VALIDATED = "validated"  # passed the deterministic checks; waiting for a person
    APPROVED = "approved"  # a person approved exactly this revision
    REJECTED = "rejected"  # failed the checks or was turned down; can be revised
    WITHDRAWN = "withdrawn"  # no longer relevant; final


class ActionKind(StrEnum):
    REALLOCATE_INVENTORY = "reallocate_inventory"
    EXPEDITE_SHIPMENT = "expedite_shipment"
    SWITCH_SUPPLIER = "switch_supplier"
    SPLIT_SHIPMENT = "split_shipment"
    NOTIFY_CUSTOMER = "notify_customer"


# Actions that move goods must say which product and how much.
GOODS_ACTIONS: Final = frozenset(ActionKind) - {ActionKind.NOTIFY_CUSTOMER}


@dataclass(frozen=True, slots=True)
class PlannedAction:
    kind: ActionKind
    description: str
    product_id: ProductId | None = None
    quantity: Quantity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", ActionKind(self.kind))
        object.__setattr__(
            self,
            "description",
            require_text(self.description, "description", max_length=MAX_DESCRIPTION_LENGTH),
        )
        if self.kind in GOODS_ACTIONS:
            if self.product_id is None or self.quantity is None:
                raise InvalidValueError(f"{self.kind} needs a product and a quantity")
            if not self.quantity.is_positive:
                raise InvalidValueError(f"{self.kind} needs a positive quantity")
        elif self.product_id is not None or self.quantity is not None:
            raise InvalidValueError(f"{self.kind} does not move goods; give no product or quantity")


@dataclass(frozen=True, slots=True)
class PlanContent:
    """Everything a person approves. Changing any of it requires a new approval."""

    actions: tuple[PlannedAction, ...]
    estimated_cost: Money
    rationale: str
    evidence_ids: frozenset[EvidenceId] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.actions, tuple) or not self.actions:
            raise InvalidValueError("a plan needs at least one action")
        if len(self.actions) > MAX_ACTIONS:
            raise InvalidValueError(f"a plan has at most {MAX_ACTIONS} actions")
        if self.estimated_cost.is_negative:
            raise InvalidValueError("estimated cost cannot be negative")
        object.__setattr__(
            self,
            "rationale",
            require_text(self.rationale, "rationale", max_length=MAX_RATIONALE_LENGTH),
        )


@dataclass(frozen=True, slots=True)
class Approval:
    approved_by: UserId
    approved_at: datetime
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "approved_at", require_utc(self.approved_at, "approved_at"))


def _decimal(value: Decimal) -> str:
    # 1, 1.0 and 1.00 are the same amount and must produce the same fingerprint.
    return format(value.normalize(), "f")


def plan_fingerprint(plan_id: RecoveryPlanId, revision: int, content: PlanContent) -> str:
    """SHA-256 of the plan's canonical form: what an approval is bound to.

    The approver's screen shows a fingerprint; approving sends it back. If the plan changed
    in between, the fingerprints differ and the approval is refused.
    """
    canonical = {
        "fingerprint_version": FINGERPRINT_VERSION,
        "plan_id": str(plan_id),
        "revision": revision,
        "actions": [
            {
                "kind": action.kind.value,
                "description": action.description,
                "product_id": str(action.product_id) if action.product_id else None,
                "quantity": (
                    [_decimal(action.quantity.amount), action.quantity.unit.code]
                    if action.quantity
                    else None
                ),
            }
            for action in content.actions
        ],
        "estimated_cost": [
            _decimal(content.estimated_cost.amount),
            content.estimated_cost.currency,
        ],
        "rationale": content.rationale,
        "evidence_ids": sorted(str(e) for e in content.evidence_ids),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
