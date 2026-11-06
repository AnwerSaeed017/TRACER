"""One error shape for every failed request: RFC 9457 Problem Details.

Clients (the web app, other services, a person with curl) learn one format. Every problem
carries the request id, so a user's bug report can be matched to the server log line.
"""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from pydantic import BaseModel
from starlette.responses import JSONResponse

from tracer.platform.observability.context import request_id_var

PROBLEM_MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    request_id: str | None = None
    errors: list[dict[str, Any]] | None = None


def problem_response(
    status: int,
    *,
    detail: str | None = None,
    instance: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    problem = Problem(
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=instance,
        request_id=request_id_var.get(),
        errors=errors,
    )
    return JSONResponse(
        problem.model_dump(exclude_none=True),
        status_code=status,
        media_type=PROBLEM_MEDIA_TYPE,
        headers=headers,
    )
