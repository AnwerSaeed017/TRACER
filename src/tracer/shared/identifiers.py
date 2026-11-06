"""Strongly typed identifiers.

Every entity id wraps a UUID, but each kind of id is its own type: a ``SupplierId`` and a
``ProductId`` holding the same UUID are not equal, and mypy rejects passing one where the
other is expected. Passing a supplier id into a product lookup is a bug that otherwise only
shows up as "not found" in production.
"""

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid4

from tracer.shared.errors import InvalidValueError


@dataclass(frozen=True, slots=True)
class EntityId:
    value: UUID

    def __post_init__(self) -> None:
        if type(self) is EntityId:
            raise TypeError("EntityId is abstract; use a specific id type such as SupplierId")
        if not isinstance(self.value, UUID):
            raise InvalidValueError(f"{type(self).__name__} must wrap a UUID")

    @classmethod
    def new(cls) -> Self:
        return cls(uuid4())

    @classmethod
    def parse(cls, raw: str) -> Self:
        try:
            return cls(UUID(raw))
        except (ValueError, TypeError, AttributeError):
            raise InvalidValueError(f"{cls.__name__} must be a UUID, got {raw!r}") from None

    def __str__(self) -> str:
        return str(self.value)


# Dataclass equality compares the exact class, so ids of different kinds never compare equal.


class TenantId(EntityId):
    __slots__ = ()


class UserId(EntityId):
    __slots__ = ()


class SupplierId(EntityId):
    __slots__ = ()


class ProductId(EntityId):
    __slots__ = ()


class ShipmentId(EntityId):
    __slots__ = ()


class CustomerOrderId(EntityId):
    __slots__ = ()


class EvidenceId(EntityId):
    __slots__ = ()


class DisruptionCaseId(EntityId):
    __slots__ = ()


class RecoveryPlanId(EntityId):
    __slots__ = ()


class ExecutionAttemptId(EntityId):
    __slots__ = ()
