"""Request context: a request id for every request, one log line per request, a safe 500.

Written as plain ASGI middleware rather than Starlette's ``BaseHTTPMiddleware``: it does not
buffer the response body, and context variables set here are visible to the endpoint.
"""

import logging
import re
import time
import uuid
from http import HTTPStatus

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tracer.entrypoints.http.problems import problem_response
from tracer.platform.observability.context import request_id_var

logger = logging.getLogger("tracer.http")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's id only if it is short and plain: it is written into our logs verbatim.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATH_PREFIX = "/health/"


def _request_id(scope: Scope) -> str:
    supplied = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if supplied and _VALID_REQUEST_ID.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _request_id(scope)
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = HTTPStatus.INTERNAL_SERVER_ERROR.value
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("unhandled error")
            if response_started:
                raise  # too late to send a clean error; let the server close the connection
            # Generic message only: internals (stack, SQL, file paths) never reach the client.
            response = problem_response(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred.",
                instance=scope["path"],
            )
            await response(scope, receive, send_with_request_id)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope["path"]
            level = logging.DEBUG if path.startswith(_QUIET_PATH_PREFIX) else logging.INFO
            logger.log(
                level,
                "request completed",
                extra={
                    "method": scope["method"],
                    "path": path,  # no query string: it can carry tokens or personal data
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            request_id_var.reset(token)
