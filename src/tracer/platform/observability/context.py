"""Per-request context that travels with the code without being passed by hand.

A ``ContextVar`` is local to the current asyncio task, so two concurrent requests never
see each other's request id. Anything that logs inside a request gets the id for free.
"""

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
