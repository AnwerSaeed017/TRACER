"""The attribute vocabulary: what a node or edge property may be, and the check for a value.

A deliberately small, closed set of types. Two reasons:
1. compatibility between ontology versions must be *decidable*: with these types it is a
   handful of explicit rules (see ``compatibility.py``), with arbitrary JSON Schema it is not;
2. TRACER's value rules are built in: decimals are exact strings, money carries its currency
   and quantities their unit *per value*, datetimes must have a timezone.

Stored values (JSON):

    string / text   "Karachi"
    integer         12                         (never a float, never a boolean)
    decimal         "0.04375"                  (a string: exact)
    boolean         true
    datetime        "2026-11-05T09:00:00+00:00"
    enum            "reefer"                   (one of the declared values)
    money           {"amount": "120.50", "currency": "USD"}
    quantity        {"amount": "20", "unit": "EACH"}   (unit of the declared dimension)
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import to_decimal
from tracer.shared.money import Money
from tracer.shared.quantity import Dimension, Quantity

ATTRIBUTE_NAME: Final = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
ENUM_VALUE: Final = re.compile(r"^[a-z0-9][a-z0-9_]{0,62}$")
DEFAULT_STRING_LENGTH: Final = 255
MAX_STRING_LENGTH: Final = 10_000
MAX_TEXT_LENGTH: Final = 100_000
MAX_ENUM_VALUES: Final = 200
_DEFINITION_KEYS: Final = frozenset(
    {
        "type",
        "required",
        "max_length",
        "minimum",
        "maximum",
        "exclusive_minimum",
        "values",
        "dimension",
        "description",
    }
)


class AttributeType(StrEnum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    ENUM = "enum"
    MONEY = "money"
    QUANTITY = "quantity"


# Types that take minimum/maximum. For money and quantity the bounds apply to the amount.
_BOUNDED: Final = frozenset(
    {AttributeType.INTEGER, AttributeType.DECIMAL, AttributeType.MONEY, AttributeType.QUANTITY}
)


@dataclass(frozen=True, slots=True)
class AttributeDefinition:
    name: str
    type: AttributeType
    required: bool = False
    max_length: int | None = None  # string only
    minimum: Decimal | None = None  # integer / decimal / money / quantity (on the amount)
    maximum: Decimal | None = None
    exclusive_minimum: bool = False  # "greater than" instead of "at least": positive amounts
    values: tuple[str, ...] = ()  # enum only
    dimension: Dimension | None = None  # quantity only
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not ATTRIBUTE_NAME.fullmatch(self.name):
            raise InvalidValueError(
                f"attribute name {self.name!r} must be lower_snake_case, at most 63 characters"
            )
        try:
            kind = AttributeType(self.type)
        except ValueError:
            raise InvalidValueError(f"{self.name}: unknown attribute type {self.type!r}") from None
        object.__setattr__(self, "type", kind)
        self._check_length(kind)
        self._check_bounds(kind)
        self._check_values(kind)
        self._check_dimension(kind)

    def _check_length(self, kind: AttributeType) -> None:
        if self.max_length is None:
            if kind is AttributeType.STRING:
                object.__setattr__(self, "max_length", DEFAULT_STRING_LENGTH)
            return
        if kind is not AttributeType.STRING:
            raise InvalidValueError(f"{self.name}: max_length applies to string attributes only")
        if isinstance(self.max_length, bool) or not 1 <= self.max_length <= MAX_STRING_LENGTH:
            raise InvalidValueError(f"{self.name}: max_length must be 1 to {MAX_STRING_LENGTH}")

    def _check_bounds(self, kind: AttributeType) -> None:
        bounds = [b for b in (self.minimum, self.maximum) if b is not None]
        if bounds and kind not in _BOUNDED:
            raise InvalidValueError(f"{self.name}: minimum/maximum apply to numbers only")
        if self.exclusive_minimum and self.minimum is None:
            raise InvalidValueError(f"{self.name}: exclusive_minimum needs a minimum")
        for bound in bounds:
            if not isinstance(bound, Decimal) or not bound.is_finite():
                raise InvalidValueError(f"{self.name}: bounds must be finite Decimals")
            if kind is AttributeType.INTEGER and bound != bound.to_integral_value():
                raise InvalidValueError(f"{self.name}: integer bounds must be whole numbers")
        if (
            self.minimum is not None
            and self.maximum is not None
            and (
                self.minimum > self.maximum
                or (self.exclusive_minimum and self.minimum == self.maximum)
            )
        ):
            raise InvalidValueError(f"{self.name}: minimum is greater than maximum")

    def _check_values(self, kind: AttributeType) -> None:
        if kind is not AttributeType.ENUM:
            if self.values:
                raise InvalidValueError(f"{self.name}: values apply to enum attributes only")
            return
        values = tuple(self.values)
        if not values or len(values) > MAX_ENUM_VALUES:
            raise InvalidValueError(f"{self.name}: an enum needs 1 to {MAX_ENUM_VALUES} values")
        if len(set(values)) != len(values):
            raise InvalidValueError(f"{self.name}: enum values must be unique")
        if not all(isinstance(v, str) and ENUM_VALUE.fullmatch(v) for v in values):
            raise InvalidValueError(f"{self.name}: enum values must be lower_snake_case")
        object.__setattr__(self, "values", values)

    def _check_dimension(self, kind: AttributeType) -> None:
        if kind is not AttributeType.QUANTITY:
            if self.dimension is not None:
                raise InvalidValueError(
                    f"{self.name}: dimension applies to quantity attributes only"
                )
            return
        try:
            object.__setattr__(self, "dimension", Dimension(str(self.dimension)))
        except ValueError:
            dimensions = ", ".join(d.value for d in Dimension)
            raise InvalidValueError(
                f"{self.name}: a quantity needs a dimension ({dimensions})"
            ) from None

    # ------------------------------------------------------------------ values

    def check(self, value: object) -> str | None:
        """None if ``value`` is a valid stored value, else a message saying why not.

        A missing attribute is simply absent from the properties; ``null`` is never a value
        (one way to say "no value", not two).
        """
        if value is None:
            return f"{self.name}: must not be null (omit an attribute that has no value)"
        problem = _CHECKS[self.type](self, value)
        return None if problem is None else f"{self.name}: {problem}"

    # ------------------------------------------------------------------ JSON

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type.value, "required": self.required}
        if self.max_length is not None:
            data["max_length"] = self.max_length
        # Normalised: 1, 1.0 and 1.00 are one bound, one fingerprint, one contract.
        if self.minimum is not None:
            data["minimum"] = _canonical(self.minimum)
        if self.exclusive_minimum:
            data["exclusive_minimum"] = True
        if self.maximum is not None:
            data["maximum"] = _canonical(self.maximum)
        if self.values:
            data["values"] = list(self.values)
        if self.dimension is not None:
            data["dimension"] = self.dimension.value
        if self.description:
            data["description"] = self.description
        return data

    @classmethod
    def from_json(cls, name: str, data: Mapping[str, Any]) -> "AttributeDefinition":
        if not isinstance(data, Mapping):
            raise InvalidValueError(f"{name}: an attribute definition must be an object")
        unknown = set(data) - _DEFINITION_KEYS
        if unknown:
            raise InvalidValueError(f"{name}: unknown definition keys {sorted(unknown)}")
        if "type" not in data:
            raise InvalidValueError(f"{name}: an attribute definition needs a type")
        return cls(
            name=name,
            type=data["type"],
            required=bool(data.get("required", False)),
            max_length=data.get("max_length"),
            minimum=to_decimal(data["minimum"], "minimum") if "minimum" in data else None,
            maximum=to_decimal(data["maximum"], "maximum") if "maximum" in data else None,
            exclusive_minimum=bool(data.get("exclusive_minimum", False)),
            values=tuple(data.get("values", ())),
            dimension=data.get("dimension"),
            description=str(data.get("description", "")),
        )

    def same_contract_as(self, other: "AttributeDefinition") -> bool:
        """Equal in everything that affects which values are valid (description excluded)."""
        return self.to_json() | {"description": ""} == other.to_json() | {"description": ""}


# ---------------------------------------------------------------------- value checks


def _canonical(number: Decimal) -> str:
    return format(number.normalize(), "f") if number else "0"


def _bounded(definition: AttributeDefinition, number: Decimal) -> str | None:
    if definition.minimum is not None:
        if definition.exclusive_minimum and number <= definition.minimum:
            return f"must be greater than {definition.minimum}"
        if number < definition.minimum:
            return f"must be at least {definition.minimum}"
    if definition.maximum is not None and number > definition.maximum:
        return f"must be at most {definition.maximum}"
    return None


def _string(definition: AttributeDefinition, value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    limit = definition.max_length or DEFAULT_STRING_LENGTH
    return None if len(value) <= limit else f"must be at most {limit} characters"


def _text(definition: AttributeDefinition, value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    return (
        None if len(value) <= MAX_TEXT_LENGTH else f"must be at most {MAX_TEXT_LENGTH} characters"
    )


def _integer(definition: AttributeDefinition, value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return "must be a whole number"
    return _bounded(definition, Decimal(value))


def _decimal(definition: AttributeDefinition, value: object) -> str | None:
    if not isinstance(value, str):
        return 'must be a decimal written as a string, e.g. "12.50"'
    try:
        number = to_decimal(value, definition.name)
    except InvalidValueError:
        return 'must be a decimal written as a string, e.g. "12.50"'
    return _bounded(definition, number)


def _boolean(definition: AttributeDefinition, value: object) -> str | None:
    return None if isinstance(value, bool) else "must be true or false"


def _datetime(definition: AttributeDefinition, value: object) -> str | None:
    if not isinstance(value, str):
        return "must be an ISO 8601 datetime string"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return "must be an ISO 8601 datetime string"
    return None if moment.utcoffset() is not None else "must include a timezone"


def _enum(definition: AttributeDefinition, value: object) -> str | None:
    return None if value in definition.values else f"must be one of {list(definition.values)}"


def _pair(value: object, second: str) -> tuple[object, object] | None:
    if not isinstance(value, Mapping) or set(value) != {"amount", second}:
        return None
    return value["amount"], value[second]


def _money(definition: AttributeDefinition, value: object) -> str | None:
    pair = _pair(value, "currency")
    if pair is None or not isinstance(pair[0], str):
        return 'must be {"amount": "<decimal string>", "currency": "<ISO 4217>"}'
    try:
        money = Money.of(pair[0], pair[1])  # type: ignore[arg-type]
    except InvalidValueError as error:
        return str(error)
    return _bounded(definition, money.amount)


def _quantity(definition: AttributeDefinition, value: object) -> str | None:
    pair = _pair(value, "unit")
    if pair is None or not isinstance(pair[0], str) or not isinstance(pair[1], str):
        return 'must be {"amount": "<decimal string>", "unit": "<unit code>"}'
    try:
        quantity = Quantity.of(pair[0], pair[1])
    except InvalidValueError as error:
        return str(error)
    if quantity.unit.dimension is not definition.dimension:
        dimension = definition.dimension.value if definition.dimension else "?"
        return f"unit {quantity.unit} is not a {dimension} unit"
    return _bounded(definition, quantity.amount)


_CHECKS: Final = {
    AttributeType.STRING: _string,
    AttributeType.TEXT: _text,
    AttributeType.INTEGER: _integer,
    AttributeType.DECIMAL: _decimal,
    AttributeType.BOOLEAN: _boolean,
    AttributeType.DATETIME: _datetime,
    AttributeType.ENUM: _enum,
    AttributeType.MONEY: _money,
    AttributeType.QUANTITY: _quantity,
}
