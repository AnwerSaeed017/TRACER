"""Small validation helpers shared by value objects and aggregates."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from tracer.shared.errors import InvalidValueError


def require_text(value: object, field: str, *, max_length: int) -> str:
    """Return the text stripped of surrounding whitespace; reject empty or oversized text."""
    if not isinstance(value, str):
        raise InvalidValueError(f"{field} must be text")
    text = value.strip()
    if not text:
        raise InvalidValueError(f"{field} must not be empty")
    if len(text) > max_length:
        raise InvalidValueError(f"{field} must be at most {max_length} characters")
    return text


def require_utc(value: object, field: str) -> datetime:
    """Return the datetime converted to UTC; reject naive datetimes (no timezone)."""
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise InvalidValueError(f"{field} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def require_decimal(value: object, field: str) -> Decimal:
    """Accept only a finite Decimal: no float, no NaN, no infinity."""
    if not isinstance(value, Decimal):
        raise InvalidValueError(f"{field} must be a Decimal, not {type(value).__name__}")
    if not value.is_finite():
        raise InvalidValueError(f"{field} must be a finite number")
    return value


def to_decimal(value: object, field: str) -> Decimal:
    """Build a Decimal from a Decimal, an int or a numeric string. Floats are refused.

    A float cannot represent most decimal fractions exactly (``0.1 + 0.2 != 0.3``), so a
    float that reaches a money or quantity calculation is a bug, not an input to round.
    """
    if isinstance(value, Decimal):
        return require_decimal(value, field)
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str):
        try:
            return require_decimal(Decimal(value), field)
        except InvalidOperation:
            raise InvalidValueError(f"{field} must be a number, got {value!r}") from None
    raise InvalidValueError(
        f"{field} must be a Decimal, int or numeric string, not {type(value).__name__}"
    )
