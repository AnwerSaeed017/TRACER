"""Turn exceptions into Problem Details responses.

Unexpected exceptions are handled by ``RequestContextMiddleware`` instead, so that a 500
still carries the request id header and is logged with the request's context.
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response

from tracer.entrypoints.http.problems import problem_response


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


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
