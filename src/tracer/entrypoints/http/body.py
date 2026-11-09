"""Read a request body with a hard size limit.

``await request.body()`` reads everything a client sends into memory. Reading the stream and
stopping at the limit means an oversized (or endless) upload cannot exhaust memory.
"""

from http import HTTPStatus

from fastapi import HTTPException, Request


async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise HTTPException(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"the limit is {max_bytes} bytes")
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"the limit is {max_bytes} bytes"
            )
        chunks.append(chunk)
    return b"".join(chunks)
