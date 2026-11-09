from datetime import timedelta

import pytest

from tests.support import T0
from tracer.modules.ingestion.adapters.webhook_signature import sign, verify_signature
from tracer.shared.errors import AuthenticationError

SECRET = b"s" * 32
OLD_SECRET = b"o" * 32
EVENT = "evt-501"
BODY = b'{"kind":"shipment_delay","summary":"late"}'
STAMP = int(T0.timestamp())
TOLERANCE = timedelta(minutes=5)


def verify(
    *,
    event_id: str | None = EVENT,
    body: bytes = BODY,
    timestamp: str | None = str(STAMP),
    signature: str | None = None,
    secrets: tuple[bytes, ...] = (SECRET,),
    now_offset: timedelta = timedelta(0),
) -> str:
    return verify_signature(
        secrets=secrets,
        event_id=event_id,
        timestamp_header=timestamp,
        signature_header=sign(SECRET, EVENT, STAMP, BODY) if signature is None else signature,
        body=body,
        now=T0 + now_offset,
        tolerance=TOLERANCE,
    )


def test_a_correct_signature_returns_the_authenticated_event_id() -> None:
    assert verify() == EVENT
    assert verify(now_offset=TOLERANCE) == EVENT  # the boundary itself is accepted


def test_a_retry_resigned_later_with_a_fresh_timestamp_passes() -> None:
    # Ten minutes after the first attempt: same event id, new timestamp, new signature.
    later = STAMP + 600
    signature = sign(SECRET, EVENT, later, BODY)
    assert verify(timestamp=str(later), signature=signature, now_offset=timedelta(minutes=10))


def test_any_active_secret_is_accepted_during_rotation() -> None:
    signature = sign(OLD_SECRET, EVENT, STAMP, BODY)
    assert verify(signature=signature, secrets=(SECRET, OLD_SECRET)) == EVENT


@pytest.mark.parametrize(
    "case",
    [
        {"body": BODY + b" "},  # one extra byte
        {"event_id": "evt-502"},  # event id swapped: it is signed too
        {"secrets": (b"x" * 32,)},  # wrong secret
        {"secrets": ()},  # no active secret
        {"timestamp": str(STAMP + 1)},  # timestamp changed after signing
        {"now_offset": TOLERANCE + timedelta(seconds=1)},  # replayed too late
        {"now_offset": -(TOLERANCE + timedelta(seconds=1))},  # timestamp from the future
        {"timestamp": None},
        {"timestamp": ""},
        {"timestamp": "12.5"},
        {"timestamp": "-100"},
        {"timestamp": "9" * 20},
        {"event_id": None},
        {"event_id": ""},
        {"event_id": "evt.501"},  # "." would make the signed message ambiguous
        {"event_id": "x" * 65},
        {"signature": ""},
        {"signature": "sha256=" + "0" * 64},
        {"signature": sign(SECRET, EVENT, STAMP, BODY).removeprefix("sha256=")},
    ],
)
def test_anything_else_fails_with_the_same_generic_error(case: dict[str, object]) -> None:
    with pytest.raises(AuthenticationError, match=r"^authentication failed$"):
        verify(**case)  # type: ignore[arg-type]
