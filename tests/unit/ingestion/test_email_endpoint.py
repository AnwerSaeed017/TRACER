"""The email endpoint: same authentication as reports, raw MIME handed on untouched."""

from dataclasses import replace
from uuid import UUID

from fastapi.testclient import TestClient

from tests.emails import raw_email
from tests.support import T0, WEBHOOK_SOURCE, WEBHOOK_TENANT, signed_headers, webhook_settings
from tracer.bootstrap.composition_root import build_container
from tracer.entrypoints.http.app import create_app
from tracer.modules.ingestion.application.email_intake import InboundEmail
from tracer.modules.ingestion.application.intake import Receipt, ReceiptStatus
from tracer.modules.ingestion.domain.source_document import SourceDocumentId
from tracer.platform.messaging.envelope import MessageContext
from tracer.shared.clock import FrozenClock

URL = f"/webhooks/{WEBHOOK_SOURCE}/emails"


class FakeEmailIntake:
    def __init__(self) -> None:
        self.received: list[InboundEmail] = []

    async def receive(self, email: InboundEmail, context: MessageContext) -> Receipt:
        self.received.append(email)
        return Receipt(ReceiptStatus.UNRECOGNISED, SourceDocumentId.new())


def post(
    content: bytes, headers: dict[str, str], intake: FakeEmailIntake
) -> tuple[int, dict[str, object]]:
    container = build_container(webhook_settings(), clock=FrozenClock(T0))
    with TestClient(create_app(replace(container, email_intake=intake))) as http:
        response = http.post(URL, content=content, headers=headers)
    return response.status_code, response.json()


def mime_headers(content: bytes, event_id: str = "mail-1") -> dict[str, str]:
    return signed_headers(content, event_id=event_id, at=T0) | {"Content-Type": "message/rfc822"}


def test_a_signed_email_is_handed_over_byte_for_byte() -> None:
    intake = FakeEmailIntake()
    content = raw_email()
    status, body = post(content, mime_headers(content), intake)

    assert status == 202
    assert body["status"] == "unrecognised"
    [email] = intake.received
    assert email.raw == content
    assert email.source_event_id == "mail-1"
    assert email.tenant_id.value == UUID(WEBHOOK_TENANT)


def test_the_wrong_media_type_is_415() -> None:
    intake = FakeEmailIntake()
    content = raw_email()
    status, _ = post(
        content, signed_headers(content, event_id="mail-1", at=T0), intake
    )  # JSON type
    assert status == 415
    assert intake.received == []


def test_an_unsigned_email_is_401_before_anything_is_parsed() -> None:
    intake = FakeEmailIntake()
    content = raw_email()
    headers = mime_headers(content) | {"X-Tracer-Signature": "sha256=" + "0" * 64}
    status, body = post(content, headers, intake)
    assert status == 401
    assert body["detail"] == "authentication failed"
    assert intake.received == []
