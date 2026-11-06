"""Quantity: an exact Decimal amount in an explicit unit of measure.

Rules:
- arithmetic and ordering require the *same* unit; ``1 KG + 500 G`` raises until one side
  is converted explicitly with ``convert_to``;
- standard conversions (G <-> KG, CM <-> M, ML <-> L) work within a dimension;
- packaging units (BOX, PALLET) have no standard size, so they never convert here; how many
  units a pallet holds depends on the product (see supply_chain.domain.packaging);
- count units are whole numbers: 2.5 EACH is rejected;
- a quantity may be negative (an inventory adjustment of -10 EACH is real). Whether a
  particular quantity must be positive is a rule of the business concept that holds it.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from tracer.shared.errors import IncompatibleUnitsError, InvalidValueError
from tracer.shared.guards import require_decimal, to_decimal


class Dimension(StrEnum):
    COUNT = "count"
    MASS = "mass"
    LENGTH = "length"
    VOLUME = "volume"


@dataclass(frozen=True, slots=True)
class UnitOfMeasure:
    code: str
    dimension: Dimension
    # Size in the dimension's base unit (KG, M, L, EACH); None means no standard size.
    factor_to_base: Decimal | None
    integral: bool = False

    def __str__(self) -> str:
        return self.code


EACH: Final = UnitOfMeasure("EACH", Dimension.COUNT, Decimal(1), integral=True)
BOX: Final = UnitOfMeasure("BOX", Dimension.COUNT, None, integral=True)
PALLET: Final = UnitOfMeasure("PALLET", Dimension.COUNT, None, integral=True)
G: Final = UnitOfMeasure("G", Dimension.MASS, Decimal("0.001"))
KG: Final = UnitOfMeasure("KG", Dimension.MASS, Decimal(1))
TONNE: Final = UnitOfMeasure("TONNE", Dimension.MASS, Decimal(1000))
CM: Final = UnitOfMeasure("CM", Dimension.LENGTH, Decimal("0.01"))
M: Final = UnitOfMeasure("M", Dimension.LENGTH, Decimal(1))
ML: Final = UnitOfMeasure("ML", Dimension.VOLUME, Decimal("0.001"))
L: Final = UnitOfMeasure("L", Dimension.VOLUME, Decimal(1))

UNITS: Final = MappingProxyType(
    {u.code: u for u in (EACH, BOX, PALLET, G, KG, TONNE, CM, M, ML, L)}
)


def unit_of_measure(code: str) -> UnitOfMeasure:
    """Look up a unit by its code ("KG"). Unknown codes are rejected, not guessed."""
    try:
        return UNITS[code.strip().upper()]
    except (KeyError, AttributeError):
        raise InvalidValueError(f"unknown unit of measure {code!r}") from None


@dataclass(frozen=True, slots=True)
class Quantity:
    amount: Decimal
    unit: UnitOfMeasure

    def __post_init__(self) -> None:
        require_decimal(self.amount, "amount")
        if not isinstance(self.unit, UnitOfMeasure):
            raise InvalidValueError("unit must be a UnitOfMeasure")
        if self.unit.integral and self.amount != self.amount.to_integral_value():
            raise InvalidValueError(f"{self.unit} is counted in whole numbers, got {self.amount}")

    @classmethod
    def of(cls, amount: Decimal | int | str, unit: UnitOfMeasure | str) -> "Quantity":
        resolved = unit if isinstance(unit, UnitOfMeasure) else unit_of_measure(unit)
        return cls(to_decimal(amount, "amount"), resolved)

    @classmethod
    def zero(cls, unit: UnitOfMeasure) -> "Quantity":
        return cls(Decimal(0), unit)

    @property
    def is_zero(self) -> bool:
        return self.amount.is_zero()

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def _same_unit(self, other: "Quantity") -> None:
        if not isinstance(other, Quantity):
            raise TypeError(f"expected Quantity, got {type(other).__name__}")
        if other.unit != self.unit:
            raise IncompatibleUnitsError(self.unit.code, other.unit.code)

    def __add__(self, other: "Quantity") -> "Quantity":
        self._same_unit(other)
        return Quantity(self.amount + other.amount, self.unit)

    def __sub__(self, other: "Quantity") -> "Quantity":
        self._same_unit(other)
        return Quantity(self.amount - other.amount, self.unit)

    def __neg__(self) -> "Quantity":
        return Quantity(-self.amount, self.unit)

    def __mul__(self, factor: Decimal | int) -> "Quantity":
        return Quantity(self.amount * to_decimal(factor, "factor"), self.unit)

    def __lt__(self, other: "Quantity") -> bool:
        self._same_unit(other)
        return self.amount < other.amount

    def __le__(self, other: "Quantity") -> bool:
        self._same_unit(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Quantity") -> bool:
        self._same_unit(other)
        return self.amount > other.amount

    def __ge__(self, other: "Quantity") -> bool:
        self._same_unit(other)
        return self.amount >= other.amount

    def convert_to(self, target: UnitOfMeasure) -> "Quantity":
        """Standard conversion within one dimension: 1500 G -> 1.5 KG."""
        if target == self.unit:
            return self
        source_factor, target_factor = self.unit.factor_to_base, target.factor_to_base
        if (
            target.dimension is not self.unit.dimension
            or source_factor is None
            or target_factor is None
        ):
            raise IncompatibleUnitsError(self.unit.code, target.code)
        return Quantity(self.amount * source_factor / target_factor, target)

    def __str__(self) -> str:
        return f"{self.amount} {self.unit}"
