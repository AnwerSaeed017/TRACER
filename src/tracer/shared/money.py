"""Money: an exact Decimal amount in an explicit currency.

Rules:
- the amount is a ``Decimal``; floats are refused;
- every amount has an ISO 4217 currency code;
- adding, subtracting or ordering two amounts in different currencies raises
  ``CurrencyMismatchError``. Equality simply says "not equal";
- conversion happens only through an ``ExchangeRate`` that names its source and time;
- no automatic rounding: unit prices may need more precision than the currency's minor unit,
  so rounding is an explicit call (``rounded``) at the point where an amount is settled.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from tracer.shared.errors import CurrencyMismatchError, InvalidValueError
from tracer.shared.guards import require_decimal, require_text, require_utc, to_decimal

_CURRENCY_CODE = re.compile(r"[A-Z]{3}")
_MAX_MINOR_UNITS = 8


def _require_currency(code: object) -> str:
    if not isinstance(code, str) or not _CURRENCY_CODE.fullmatch(code):
        raise InvalidValueError(
            f"currency must be a 3-letter ISO 4217 code such as USD, got {code!r}"
        )
    return code


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        require_decimal(self.amount, "amount")
        _require_currency(self.currency)

    @classmethod
    def of(cls, amount: Decimal | int | str, currency: str) -> "Money":
        """Money.of("120.50", "USD"). Strings keep the exact digits you wrote."""
        return cls(to_decimal(amount, "amount"), currency)

    @classmethod
    def zero(cls, currency: str) -> "Money":
        return cls(Decimal(0), currency)

    @property
    def is_zero(self) -> bool:
        return self.amount.is_zero()

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    def _same_currency(self, other: "Money") -> None:
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    def __add__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: "Money") -> "Money":
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> "Money":
        """Scale by a quantity or rate: unit_price * 20."""
        return Money(self.amount * to_decimal(factor, "factor"), self.currency)

    def __lt__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._same_currency(other)
        return self.amount >= other.amount

    def rounded(self, minor_units: int, rounding: str = ROUND_HALF_EVEN) -> "Money":
        """Round to the currency's minor units (USD 2, JPY 0, KWD 3); banker's rounding default."""
        if not 0 <= minor_units <= _MAX_MINOR_UNITS:
            raise InvalidValueError(f"minor_units must be between 0 and {_MAX_MINOR_UNITS}")
        return Money(self.amount.quantize(Decimal(1).scaleb(-minor_units), rounding), self.currency)

    def convert(self, rate: "ExchangeRate") -> "Money":
        """Convert with an explicit rate. The result is not rounded."""
        if rate.base != self.currency:
            raise CurrencyMismatchError(self.currency, rate.base)
        return Money(self.amount * rate.rate, rate.quote)

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


@dataclass(frozen=True, slots=True)
class ExchangeRate:
    """1 unit of ``base`` = ``rate`` units of ``quote``, as published by ``source`` at ``as_of``."""

    base: str
    quote: str
    rate: Decimal
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        _require_currency(self.base)
        _require_currency(self.quote)
        if self.base == self.quote:
            raise InvalidValueError("an exchange rate needs two different currencies")
        if require_decimal(self.rate, "rate") <= 0:
            raise InvalidValueError("rate must be positive")
        object.__setattr__(self, "as_of", require_utc(self.as_of, "as_of"))
        object.__setattr__(self, "source", require_text(self.source, "source", max_length=200))
