"""HMAC-SHA256 webhook signatures (modelled on the Standard Webhooks specification).

Three headers per request:

    X-Tracer-Event-Id    identifies the *logical event*; the SAME on every retry
    X-Tracer-Timestamp   when *this attempt* was sent (unix seconds); NEW on every retry
    X-Tracer-Signature   sha256=<hex HMAC-SHA256(secret, "<event_id>.<timestamp>." + body)>

- The signature covers the event id, the timestamp and the *exact bytes* of the body, so none
  of them can be changed without detection. The fields are separated by "." and the event id
  and timestamp cannot contain ".", so the signed message is unambiguous.
- Old timestamps (and ones too far in the future) are refused, so a captured request cannot be
  replayed later. A legitimate retry is re-signed with a fresh timestamp and passes; its
  unchanged event id is what makes it a duplicate rather than a new event.
- The comparison is constant-time (``hmac.compare_digest``), so timing reveals nothing.
- Every failure raises the same generic ``AuthenticationError``.

This module is the TRACER protocol. A provider with its own scheme gets its own verifier
behind the same boundary; no single scheme is assumed for every sender.
"""

import hashlib
import hmac
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Final

from tracer.shared.errors import AuthenticationError

SIGNATURE_PREFIX: Final = "sha256="
EVENT_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9_:-]{1,64}$")  # no "." by design
_MAX_TIMESTAMP_DIGITS: Final = 12


def sign(secret: bytes, event_id: str, timestamp: int, body: bytes) -> str:
    """The header value a correct sender produces. Used by tests and client examples."""
    message = f"{event_id}.{timestamp}.".encode() + body
    return SIGNATURE_PREFIX + hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_signature(
    *,
    secrets: Sequence[bytes],
    event_id: str | None,
    timestamp_header: str | None,
    signature_header: str | None,
    body: bytes,
    now: datetime,
    tolerance: timedelta,
) -> str:
    """Return the authenticated event id, or raise ``AuthenticationError``."""
    if not event_id or not EVENT_ID_PATTERN.fullmatch(event_id):
        raise AuthenticationError
    if not timestamp_header or not signature_header:
        raise AuthenticationError
    if not timestamp_header.isdigit() or len(timestamp_header) > _MAX_TIMESTAMP_DIGITS:
        raise AuthenticationError
    timestamp = int(timestamp_header)
    if abs(now - datetime.fromtimestamp(timestamp, UTC)) > tolerance:
        raise AuthenticationError
    received = signature_header.encode()
    # Check every active secret (rotation), and do not stop early: same work either way.
    matches = [
        hmac.compare_digest(sign(secret, event_id, timestamp, body).encode(), received)
        for secret in secrets
    ]
    if not any(matches):
        raise AuthenticationError
    return event_id
