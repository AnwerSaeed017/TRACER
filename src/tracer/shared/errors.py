"""Domain errors: a business rule refused an operation.

They carry messages written for a person (an operator, an API client), never internals.
The HTTP layer maps them to status codes in one place; the domain never knows about HTTP.
"""


class DomainError(Exception):
    """Base class for every business-rule failure."""


class InvalidValueError(DomainError, ValueError):
    """A value object was given a value it cannot represent (empty name, NaN amount ...)."""


class CurrencyMismatchError(DomainError):
    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"cannot combine {left} with {right}; convert explicitly first")
        self.left = left
        self.right = right


class IncompatibleUnitsError(DomainError):
    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"cannot combine {left} with {right}; convert explicitly first")
        self.left = left
        self.right = right


class InvalidTransitionError(DomainError):
    """An action is not allowed in the aggregate's current status."""

    def __init__(self, aggregate: str, status: str, action: str) -> None:
        super().__init__(f"cannot {action} this {aggregate}: it is {status}")
        self.aggregate = aggregate
        self.status = status
        self.action = action


class BusinessRuleError(DomainError):
    """A rule other than a status check refused the operation."""


class NotFoundError(DomainError):
    """The thing asked for does not exist (for this tenant)."""


class AuthenticationError(DomainError):
    """The caller could not prove who it is. The message is deliberately generic."""

    def __init__(self) -> None:
        super().__init__("authentication failed")


class ConcurrencyConflictError(DomainError):
    """Someone else changed the same aggregate first. Reload it and try again."""

    def __init__(self, aggregate: str, aggregate_id: str, expected_version: int) -> None:
        super().__init__(
            f"this {aggregate} was changed by someone else (expected version "
            f"{expected_version}); reload it and try again"
        )
        self.aggregate = aggregate
        self.aggregate_id = aggregate_id
        self.expected_version = expected_version
