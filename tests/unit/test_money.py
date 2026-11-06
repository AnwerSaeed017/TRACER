from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tracer.shared.errors import CurrencyMismatchError, InvalidValueError
from tracer.shared.money import ExchangeRate, Money

amounts = st.decimals(min_value=-(10**9), max_value=10**9, places=4, allow_nan=False)


def usd(amount: str) -> Money:
    return Money.of(amount, "USD")


class TestConstruction:
    def test_keeps_the_exact_digits(self) -> None:
        assert usd("120.50").amount == Decimal("120.50")

    def test_accepts_int_and_decimal(self) -> None:
        assert Money.of(5, "PKR") == Money(Decimal(5), "PKR")

    @pytest.mark.parametrize("bad", [0.1, 1.5, True])
    def test_refuses_float_and_bool(self, bad: object) -> None:
        with pytest.raises(InvalidValueError):
            Money.of(bad, "USD")  # type: ignore[arg-type]

    def test_refuses_float_passed_directly(self) -> None:
        with pytest.raises(InvalidValueError, match="Decimal"):
            Money(0.1, "USD")  # type: ignore[arg-type]

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "abc", ""])
    def test_refuses_non_finite_or_non_numeric(self, bad: str) -> None:
        with pytest.raises(InvalidValueError):
            Money.of(bad, "USD")

    @pytest.mark.parametrize("bad", ["usd", "US", "USDT", "", "1SD"])
    def test_refuses_malformed_currency(self, bad: str) -> None:
        with pytest.raises(InvalidValueError, match="ISO 4217"):
            Money.of("1", bad)

    def test_negative_amounts_are_allowed(self) -> None:
        assert usd("-5").is_negative  # a refund or credit is real

    def test_is_immutable(self) -> None:
        money = usd("1")
        with pytest.raises(AttributeError):
            money.amount = Decimal(2)  # type: ignore[misc]


class TestArithmetic:
    def test_same_currency_adds_and_subtracts(self) -> None:
        assert usd("120.50") + usd("0.25") == usd("120.75")
        assert usd("10") - usd("12.5") == usd("-2.5")
        assert -usd("3") == usd("-3")

    def test_decimal_avoids_float_drift(self) -> None:
        assert usd("0.1") + usd("0.2") == usd("0.3")  # 0.1 + 0.2 != 0.3 in float

    def test_multiplies_by_quantity(self) -> None:
        assert usd("120.50") * 20 == usd("2410.00")
        assert usd("2.5") * Decimal("0.5") == usd("1.25")

    def test_refuses_float_factor(self) -> None:
        with pytest.raises(InvalidValueError):
            usd("1") * 1.5  # type: ignore[operator]

    @pytest.mark.parametrize("operation", ["add", "sub", "lt", "le", "gt", "ge"])
    def test_different_currencies_never_combine(self, operation: str) -> None:
        left, right = Money.of("120.50", "USD"), Money.of("120.50", "PKR")
        with pytest.raises(CurrencyMismatchError, match="USD with PKR"):
            getattr(left, f"__{operation}__")(right)

    def test_different_currencies_are_simply_not_equal(self) -> None:
        assert Money.of("1", "USD") != Money.of("1", "PKR")

    def test_equal_amounts_with_different_scale_are_equal(self) -> None:
        assert usd("1.0") == usd("1.00")
        assert hash(usd("1.0")) == hash(usd("1.00"))

    def test_ordering_within_a_currency(self) -> None:
        assert usd("1") < usd("2") <= usd("2") < usd("3")
        assert usd("3") > usd("2") >= usd("2")

    @given(amounts, amounts)
    def test_addition_is_commutative(self, a: Decimal, b: Decimal) -> None:
        assert Money(a, "USD") + Money(b, "USD") == Money(b, "USD") + Money(a, "USD")

    @given(amounts, amounts, amounts)
    def test_addition_is_associative(self, a: Decimal, b: Decimal, c: Decimal) -> None:
        x, y, z = Money(a, "EUR"), Money(b, "EUR"), Money(c, "EUR")
        assert (x + y) + z == x + (y + z)

    @given(amounts, amounts)
    def test_subtraction_undoes_addition(self, a: Decimal, b: Decimal) -> None:
        assert (Money(a, "USD") + Money(b, "USD")) - Money(b, "USD") == Money(a, "USD")


class TestRounding:
    def test_no_rounding_happens_implicitly(self) -> None:
        unit_price = usd("0.04375")  # unit prices need more precision than cents
        assert (unit_price * 1000).amount == Decimal("43.75000")

    def test_rounds_to_minor_units_with_bankers_rounding(self) -> None:
        assert usd("2.345").rounded(2) == usd("2.34")  # half to even
        assert usd("2.355").rounded(2) == usd("2.36")
        assert Money.of("1500.5", "JPY").rounded(0) == Money.of("1500", "JPY")

    def test_rounding_mode_is_explicit(self) -> None:
        assert usd("2.345").rounded(2, ROUND_HALF_UP) == usd("2.35")

    def test_refuses_absurd_minor_units(self) -> None:
        with pytest.raises(InvalidValueError):
            usd("1").rounded(-1)


class TestConversion:
    def rate(self, **overrides: object) -> ExchangeRate:
        values: dict[str, object] = {
            "base": "USD",
            "quote": "PKR",
            "rate": Decimal("278.50"),
            "as_of": datetime(2026, 11, 5, 9, 0, tzinfo=UTC),
            "source": "State Bank of Pakistan",
        }
        values.update(overrides)
        return ExchangeRate(**values)  # type: ignore[arg-type]

    def test_converts_with_an_explicit_rate(self) -> None:
        assert usd("10").convert(self.rate()) == Money.of("2785.00", "PKR")

    def test_refuses_a_rate_for_another_currency(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            Money.of("10", "EUR").convert(self.rate())

    def test_rate_records_its_source_and_time_in_utc(self) -> None:
        karachi = timezone(timedelta(hours=5))
        rate = self.rate(as_of=datetime(2026, 11, 5, 14, 0, tzinfo=karachi), source="  SBP  ")
        assert rate.as_of == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
        assert rate.source == "SBP"

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"quote": "USD"}, "two different currencies"),
            ({"rate": Decimal(0)}, "positive"),
            ({"rate": Decimal(-1)}, "positive"),
            ({"as_of": datetime(2026, 11, 5)}, "timezone-aware"),  # noqa: DTZ001
            ({"source": " "}, "source"),
        ],
    )
    def test_rate_rejects_invalid_values(self, overrides: dict[str, object], message: str) -> None:
        with pytest.raises(InvalidValueError, match=message):
            self.rate(**overrides)
