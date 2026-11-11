"""Turn exceptions into Problem Details responses, in one place.

Domain errors map to statuses by type, most specific first. Unexpected exceptions are handled
by ``RequestContextMiddleware`` instead, so that a 500 still carries the request id header
and is logged with the request's context.
"""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from tracer.entrypoints.http.problems import problem_response
from tracer.shared.errors import (
    AuthenticationError,
    BusinessRuleError,
    ConcurrencyConflictError,
    DomainError,
    InvalidTransitionError,
    InvalidValueError,
    NotFoundError,
)

logger = logging.getLogger("tracer.http")

_STATUS_BY_ERROR: tuple[tuple[type[DomainError], HTTPStatus], ...] = (
    (AuthenticationError, HTTPStatus.UNAUTHORIZED),
    (NotFoundError, HTTPStatus.NOT_FOUND),
    (InvalidValueError, HTTPStatus.UNPROCESSABLE_ENTITY),
    (ConcurrencyConflictError, HTTPStatus.CONFLICT),
    (InvalidTransitionError, HTTPStatus.CONFLICT),
    (BusinessRuleError, HTTPStatus.CONFLICT),
)


def status_for(error: DomainError) -> HTTPStatus:
    for error_type, status in _STATUS_BY_ERROR:
        if isinstance(error, error_type):
            return status
    return HTTPStatus.BAD_REQUEST


async def _http_exception(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, HTTPException)  # noqa: S101  # registered for this type only
    phrase = HTTPStatus(exc.status_code).phrase
    detail = None if exc.detail == phrase else str(exc.detail)
    return problem_response(
        exc.status_code, detail=detail, instance=request.url.path, headers=exc.headers
    )


async def _validation_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Location, message and type only. The rejected input is not echoed back: it may be large,
    # and it may be the very secret the client should not have sent.
    errors: list[dict[str, Any]] = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return problem_response(
        HTTPStatus.UNPROCESSABLE_ENTITY,
        detail="The request is not valid.",
        instance=request.url.path,
        errors=errors,
    )


async def _domain_error(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, DomainError)  # noqa: S101
    if isinstance(exc, AuthenticationError):
        # Repeated failures from one source mean an attack or a misconfigured sender: worth a
        # warning. Path only; headers and body may carry the material being guessed.
        logger.warning("authentication failed", extra={"path": request.url.path})
    # Domain messages are written for people and contain no internals, so they are shown.
    return problem_response(status_for(exc), detail=str(exc), instance=request.url.path)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(DomainError, _domain_error)
