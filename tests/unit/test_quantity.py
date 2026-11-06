from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tracer.shared.errors import IncompatibleUnitsError, InvalidValueError
from tracer.shared.quantity import BOX, CM, EACH, KG, ML, PALLET, TONNE, G, L, M, Quantity

grams = st.decimals(min_value=-(10**9), max_value=10**9, places=3, allow_nan=False)


class TestConstruction:
    def test_unit_by_object_or_code(self) -> None:
        assert Quantity.of("100", "kg") == Quantity.of(100, KG)

    def test_unknown_unit_is_rejected(self) -> None:
        with pytest.raises(InvalidValueError, match="unknown unit"):
            Quantity.of(1, "BUSHEL")

    @pytest.mark.parametrize("unit", [EACH, BOX, PALLET])
    def test_count_units_are_whole_numbers(self, unit: object) -> None:
        with pytest.raises(InvalidValueError, match="whole numbers"):
            Quantity.of("2.5", unit)  # type: ignore[arg-type]

    def test_continuous_units_accept_fractions(self) -> None:
        assert Quantity.of("2.5", KG).amount == Decimal("2.5")

    def test_negative_quantities_are_allowed(self) -> None:
        adjustment = Quantity.of(-10, EACH)  # an inventory write-off
        assert adjustment.is_negative

    def test_refuses_float(self) -> None:
        with pytest.raises(InvalidValueError):
            Quantity.of(1.5, KG)  # type: ignore[arg-type]


class TestArithmetic:
    def test_same_unit(self) -> None:
        assert Quantity.of(12, EACH) + Quantity.of(8, EACH) == Quantity.of(20, EACH)
        assert Quantity.of(12, EACH) - Quantity.of(20, EACH) == Quantity.of(-8, EACH)
        assert Quantity.of(3, EACH) * 4 == Quantity.of(12, EACH)
        assert Quantity.of(12, EACH) < Quantity.of(20, EACH)

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            (Quantity.of(1, KG), Quantity.of(500, G)),  # same dimension: still explicit
            (Quantity.of(1, KG), Quantity.of(1, EACH)),
            (Quantity.of(1, PALLET), Quantity.of(48, BOX)),
        ],
    )
    def test_different_units_never_combine(self, left: Quantity, right: Quantity) -> None:
        with pytest.raises(IncompatibleUnitsError):
            left + right
        with pytest.raises(IncompatibleUnitsError):
            left < right  # noqa: B015  # the comparison itself is what raises

    def test_multiplying_count_units_must_stay_whole(self) -> None:
        with pytest.raises(InvalidValueError):
            Quantity.of(3, EACH) * Decimal("0.5")


class TestConversion:
    @pytest.mark.parametrize(
        ("source", "target", "expected"),
        [
            (Quantity.of(1500, G), KG, Quantity.of("1.5", KG)),
            (Quantity.of("2.5", TONNE), KG, Quantity.of(2500, KG)),
            (Quantity.of(250, CM), M, Quantity.of("2.5", M)),
            (Quantity.of(750, ML), L, Quantity.of("0.75", L)),
        ],
    )
    def test_standard_conversions(
        self, source: Quantity, target: object, expected: Quantity
    ) -> None:
        assert source.convert_to(target) == expected  # type: ignore[arg-type]

    def test_converting_to_the_same_unit_is_identity(self) -> None:
        quantity = Quantity.of(5, KG)
        assert quantity.convert_to(KG) is quantity

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (Quantity.of(1, KG), M),  # different dimension
            (Quantity.of(1, PALLET), EACH),  # packaging is product-specific
            (Quantity.of(48, EACH), BOX),
        ],
    )
    def test_no_standard_conversion(self, source: Quantity, target: object) -> None:
        with pytest.raises(IncompatibleUnitsError):
            source.convert_to(target)  # type: ignore[arg-type]

    @given(grams)
    def test_gram_kilogram_round_trip_is_exact(self, amount: Decimal) -> None:
        original = Quantity(amount, G)
        assert original.convert_to(KG).convert_to(G) == original
