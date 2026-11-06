import pytest

from tracer.modules.supply_chain.domain.packaging import PackagingConversion
from tracer.shared.errors import IncompatibleUnitsError, InvalidValueError
from tracer.shared.identifiers import ProductId
from tracer.shared.quantity import BOX, EACH, KG, PALLET, Quantity

P9_PALLET = PackagingConversion(ProductId.new(), PALLET, Quantity.of(48, BOX))


def test_unpack_packages_into_contents() -> None:
    assert P9_PALLET.unpack(Quantity.of(3, PALLET)) == Quantity.of(144, BOX)


def test_pack_returns_full_packages_and_remainder() -> None:
    assert P9_PALLET.pack(Quantity.of(100, BOX)) == (Quantity.of(2, PALLET), Quantity.of(4, BOX))


def test_conversion_is_per_product() -> None:
    p12_pallet = PackagingConversion(ProductId.new(), PALLET, Quantity.of(20, BOX))
    assert p12_pallet.unpack(Quantity.of(1, PALLET)) != P9_PALLET.unpack(Quantity.of(1, PALLET))


def test_wrong_units_are_refused() -> None:
    with pytest.raises(IncompatibleUnitsError):
        P9_PALLET.unpack(Quantity.of(3, BOX))
    with pytest.raises(IncompatibleUnitsError):
        P9_PALLET.pack(Quantity.of(100, EACH))


@pytest.mark.parametrize(
    "contents", [Quantity.of(0, BOX), Quantity.of(-1, BOX), Quantity.of(1, PALLET)]
)
def test_invalid_definitions(contents: Quantity) -> None:
    with pytest.raises(InvalidValueError):
        PackagingConversion(ProductId.new(), PALLET, contents)


def test_works_for_mass_contents() -> None:
    sack = PackagingConversion(ProductId.new(), BOX, Quantity.of(25, KG))
    assert sack.unpack(Quantity.of(4, BOX)) == Quantity.of(100, KG)
