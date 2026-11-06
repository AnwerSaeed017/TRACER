"""Value objects and statuses of an execution attempt, and the idempotency key."""

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import require_text
from tracer.shared.identifiers import RecoveryPlanId, TenantId

MAX_REASON_LENGTH: Final = 1000
MAX_REFERENCE_LENGTH: Final = 200
MAX_OPERATION_LENGTH: Final = 100
DEFAULT_MAX_TRIES: Final = 3
_SHA256_HEX: Final = re.compile(r"[0-9a-f]{64}")


class TargetSystem(StrEnum):
    ERP = "erp"
    WMS = "wms"


class AttemptStatus(StrEnum):
    PENDING = "pending"  # created, not sent yet
    EXECUTING = "executing"  # request sent, waiting for the answer
    COMMITTED = "committed"  # the external system confirmed it; final
    FAILED = "failed"  # the external system confirmed it did NOT happen
    OUTCOME_UNKNOWN = "outcome_unknown"  # no answer (timeout, lost connection): reconcile first


@dataclass(frozen=True, slots=True)
class ExecutionFailure:
    reason: str
    retryable: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "reason", require_text(self.reason, "reason", max_length=MAX_REASON_LENGTH)
        )


def require_fingerprint(value: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise InvalidValueError("plan fingerprint must be a lowercase SHA-256 hex digest")
    return value


def idempotency_key(
    tenant_id: TenantId, plan_id: RecoveryPlanId, plan_fingerprint: str, action_index: int
) -> str:
    """The same approved action always gets the same key.

    The external system stores the key with the first request it accepts and ignores any
    later request with that key. Deriving the key (instead of generating a random one) means
    even a duplicated attempt, created twice by a retried message, cannot execute twice.
    """
    material = f"{tenant_id}|{plan_id}|{plan_fingerprint}|{action_index}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
