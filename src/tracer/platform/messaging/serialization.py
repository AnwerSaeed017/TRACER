"""Turn domain events into JSON-safe payloads.

The rules are fixed, because consumers and stored history depend on them:
typed ids and UUIDs -> string; Decimal -> string (exact; never a JSON float);
datetime -> ISO 8601; enums -> their value; units of measure -> their code;
sets -> sorted lists (stable order); dataclasses -> objects of their fields.
"""

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import EntityId
from tracer.shared.quantity import UnitOfMeasure

# Already in the envelope; repeating them in the payload would invite disagreement.
_ENVELOPE_FIELDS = frozenset({"tenant_id", "occurred_at"})


def to_json_value(value: object) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, EntityId):
        return str(value.value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, UnitOfMeasure):
        return value.code
    if isinstance(value, frozenset | set):
        return sorted((to_json_value(item) for item in value), key=str)
    if isinstance(value, tuple | list):
        return [to_json_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json_value(getattr(value, f.name)) for f in fields(value)}
    raise TypeError(f"cannot serialise {type(value).__name__} into an event payload")


def event_payload(event: DomainEvent) -> dict[str, Any]:
    return {
        f.name: to_json_value(getattr(event, f.name))
        for f in fields(event)
        if f.name not in _ENVELOPE_FIELDS
    }
