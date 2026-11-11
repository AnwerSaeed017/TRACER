from decimal import Decimal
from typing import Any

import pytest

from tests.knowledge import attribute
from tracer.modules.knowledge.domain.attributes import AttributeDefinition, AttributeType
from tracer.shared.errors import InvalidValueError
from tracer.shared.quantity import Dimension

T = AttributeType


class TestDefinitions:
    @pytest.mark.parametrize("name", ["", "Name", "1st", "has space", "x" * 64, "kebab-case"])
    def test_names_are_lower_snake_case(self, name: str) -> None:
        with pytest.raises(InvalidValueError, match="lower_snake_case"):
            attribute(name)

    def test_unknown_type_is_refused(self) -> None:
        with pytest.raises(InvalidValueError, match="unknown attribute type"):
            attribute("x", "geometry")  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("kind", "options", "message"),
        [
            (T.INTEGER, {"max_length": 5}, "max_length applies to string"),
            (T.STRING, {"max_length": 0}, "max_length must be"),
            (T.STRING, {"minimum": Decimal(1)}, "apply to numbers only"),
            (T.INTEGER, {"minimum": Decimal("1.5")}, "whole numbers"),
            (T.DECIMAL, {"minimum": Decimal(5), "maximum": Decimal(1)}, "greater than maximum"),
            (T.ENUM, {}, "needs 1 to"),
            (T.ENUM, {"values": ("a", "a")}, "unique"),
            (T.ENUM, {"values": ("Ambient",)}, "lower_snake_case"),
            (T.STRING, {"values": ("a",)}, "enum attributes only"),
            (T.QUANTITY, {}, "needs a dimension"),
            (T.MONEY, {"dimension": Dimension.MASS}, "quantity attributes only"),
        ],
    )
    def test_ill_formed_definitions_are_refused(
        self, kind: AttributeType, options: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(InvalidValueError, match=message):
            attribute("x", kind, **options)

    def test_strings_get_a_default_length_limit(self) -> None:
        assert attribute("name").max_length == 255

    def test_json_round_trip(self) -> None:
        original = attribute(
            "lead_time_days", T.INTEGER, required=True, minimum=Decimal(0), maximum=Decimal(365)
        )
        assert AttributeDefinition.from_json("lead_time_days", original.to_json()) == original

    def test_unknown_definition_keys_are_refused(self) -> None:
        with pytest.raises(InvalidValueError, match="unknown definition keys"):
            AttributeDefinition.from_json("x", {"type": "string", "pattern": ".*"})

    def test_contract_ignores_the_description(self) -> None:
        assert attribute("x", description="old").same_contract_as(attribute("x", description="new"))
        assert not attribute("x").same_contract_as(attribute("x", required=True))


class TestValues:
    @pytest.mark.parametrize(
        ("definition", "value"),
        [
            (attribute("s", max_length=5), "Lahor"),
            (attribute("t", T.TEXT), "long notes"),
            (attribute("i", T.INTEGER, minimum=Decimal(0)), 12),
            (attribute("d", T.DECIMAL, maximum=Decimal(1)), "0.72"),
            (attribute("b", T.BOOLEAN), False),
            (attribute("dt", T.DATETIME), "2026-11-05T09:00:00+00:00"),
            (attribute("e", T.ENUM, values=("chilled", "frozen")), "frozen"),
            (attribute("m", T.MONEY), {"amount": "120.50", "currency": "USD"}),
            (
                attribute("q", T.QUANTITY, dimension=Dimension.COUNT),
                {"amount": "20", "unit": "EACH"},
            ),
        ],
    )
    def test_valid_values(self, definition: AttributeDefinition, value: object) -> None:
        assert definition.check(value) is None

    @pytest.mark.parametrize(
        ("definition", "value", "message"),
        [
            (attribute("s", max_length=5), "Karachi", "at most 5"),
            (attribute("s"), 5, "must be text"),
            (attribute("i", T.INTEGER), 1.5, "whole number"),
            (attribute("i", T.INTEGER), True, "whole number"),  # bool is not an integer here
            (attribute("i", T.INTEGER, minimum=Decimal(0)), -1, "at least 0"),
            (attribute("d", T.DECIMAL), 0.72, "decimal written as a string"),  # float refused
            (attribute("d", T.DECIMAL), "NaN", "decimal written as a string"),
            (attribute("d", T.DECIMAL, maximum=Decimal(1)), "1.5", "at most 1"),
            (attribute("b", T.BOOLEAN), "true", "true or false"),
            (attribute("dt", T.DATETIME), "2026-11-05T09:00:00", "timezone"),
            (attribute("dt", T.DATETIME), "next tuesday", "ISO 8601"),
            (attribute("e", T.ENUM, values=("chilled",)), "warm", "one of"),
            (attribute("m", T.MONEY), {"amount": 120.5, "currency": "USD"}, "amount"),
            (attribute("m", T.MONEY), {"amount": "1", "currency": "usd"}, "ISO 4217"),
            (attribute("m", T.MONEY), {"amount": "1"}, "currency"),
            (
                attribute("q", T.QUANTITY, dimension=Dimension.COUNT),
                {"amount": "2", "unit": "KG"},
                "not a count unit",
            ),
            (
                attribute("q", T.QUANTITY, dimension=Dimension.COUNT),
                {"amount": "2.5", "unit": "EACH"},
                "whole numbers",
            ),
            (
                attribute("q", T.QUANTITY, dimension=Dimension.MASS),
                {"amount": "2", "unit": "BUSHEL"},
                "unknown unit",
            ),
        ],
    )
    def test_invalid_values_say_why(
        self, definition: AttributeDefinition, value: object, message: str
    ) -> None:
        problem = definition.check(value)
        assert problem is not None
        assert problem.startswith(f"{definition.name}: ")
        assert message in problem


class TestReviewRules:
    def test_null_is_never_a_value(self) -> None:
        problem = attribute("city").check(None)
        assert problem == "city: must not be null (omit an attribute that has no value)"

    def test_exclusive_minimum_expresses_positive(self) -> None:
        positive = attribute(
            "quantity",
            T.QUANTITY,
            dimension=Dimension.COUNT,
            minimum=Decimal(0),
            exclusive_minimum=True,
        )
        assert positive.check({"amount": "1", "unit": "EACH"}) is None
        assert "greater than 0" in (positive.check({"amount": "0", "unit": "EACH"}) or "")
        assert "greater than 0" in (positive.check({"amount": "-5", "unit": "EACH"}) or "")

    def test_bounds_apply_to_money_amounts(self) -> None:
        price = attribute("unit_price", T.MONEY, minimum=Decimal(0))
        assert price.check({"amount": "0", "currency": "USD"}) is None
        assert "at least 0" in (price.check({"amount": "-1", "currency": "USD"}) or "")

    def test_exclusive_minimum_needs_a_minimum(self) -> None:
        with pytest.raises(InvalidValueError, match="needs a minimum"):
            attribute("x", T.DECIMAL, exclusive_minimum=True)

    def test_equal_decimals_are_one_contract_and_one_fingerprint(self) -> None:
        one = attribute("x", T.DECIMAL, minimum=Decimal("1"))
        one_point_oh = attribute("x", T.DECIMAL, minimum=Decimal("1.00"))
        assert one.to_json() == one_point_oh.to_json()
        assert one.same_contract_as(one_point_oh)
