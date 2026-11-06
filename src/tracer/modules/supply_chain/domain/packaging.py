"""Product-specific packaging conversion.

How many units a pallet or box holds is a fact about one product, not a law of units: a
pallet of product P9 may hold 48 boxes while a pallet of P12 holds 20. So this conversion
names its product and is used explicitly; ``Quantity.convert_to`` never guesses it.
"""

from dataclasses import dataclass

from tracer.shared.errors import IncompatibleUnitsError, InvalidValueError
from tracer.shared.identifiers import ProductId
from tracer.shared.quantity import Quantity, UnitOfMeasure


@dataclass(frozen=True, slots=True)
class PackagingConversion:
    """One ``package`` of ``product_id`` contains ``contents`` (1 PALLET of P9 = 48 BOX)."""

    product_id: ProductId
    package: UnitOfMeasure
    contents: Quantity

    def __post_init__(self) -> None:
        if not self.contents.is_positive:
            raise InvalidValueError("a package must contain a positive quantity")
        if self.contents.unit == self.package:
            raise InvalidValueError("a package cannot contain its own unit")

    def unpack(self, packages: Quantity) -> Quantity:
        """3 PALLET -> 144 BOX."""
        if packages.unit != self.package:
            raise IncompatibleUnitsError(packages.unit.code, self.package.code)
        return Quantity(packages.amount * self.contents.amount, self.contents.unit)

    def pack(self, contents: Quantity) -> tuple[Quantity, Quantity]:
        """100 BOX -> (2 PALLET, 4 BOX): full packages, and what is left over."""
        if contents.unit != self.contents.unit:
            raise IncompatibleUnitsError(contents.unit.code, self.contents.unit.code)
        full, rest = divmod(contents.amount, self.contents.amount)
        return Quantity(full, self.package), Quantity(rest, self.contents.unit)
