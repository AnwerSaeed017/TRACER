"""The webhook endpoint with a fake intake: every refusal happens before the use case runs."""

import json
from dataclasses import replace
from typing import Any
from uuid import UUID

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from tests.support import T0, WEBHOOK_SOURCE, WEBHOOK_TENANT, signed_headers, webhook_settings
from tracer.bootstrap.composition_root import build_container
from tracer.entrypoints.http.app import create_app
from tracer.entrypoints.http.problems import PROBLEM_MEDIA_TYPE
from tracer.modules.ingestion.application.intake import (
    DisruptionReportCommand,
    Receipt,
    ReceiptStatus,
)
from tracer.modules.ingestion.domain.report import ExternalReferences
from tracer.modules.ingestion.domain.source_document import Channel, SourceDocumentId
from tracer.platform.messaging.envelope import MessageContext
from tracer.shared.clock import FrozenClock

URL = f"/webhooks/{WEBHOOK_SOURCE}/disruption-reports"
REPORT: dict[str, Any] = {
    "kind": "shipment_delay",
    "summary": "  SHIP-501 delayed by 7 days  ",
    "impact_start": "2026-11-05T09:00:00Z",
    "impact_end": "2026-11-12T09:00:00Z",
    "references": {"suppliers": ["SUP-001"], "shipments": ["SHIP-501", "SHIP-501"]},
}


class FakeIntake:
    def __init__(self, status: ReceiptStatus = ReceiptStatus.ACCEPTED) -> None:
        self.calls: list[tuple[DisruptionReportCommand, MessageContext]] = []
        self.document_id = SourceDocumentId.new()
        self._status = status

    async def receive(self, command: DisruptionReportCommand, context: MessageContext) -> Receipt:
        self.calls.append((command, context))
        return Receipt(self._status, self.document_id)


def body(report: object = REPORT) -> bytes:
    return json.dumps(report).encode()


def post(
    intake: FakeIntake,
    content: bytes,
    headers: dict[str, str],
    *,
    clock: FrozenClock | None = None,
    path: str = URL,
    **settings: Any,
) -> httpx.Response:
    container = build_container(webhook_settings(**settings), clock=clock or FrozenClock(T0))
    with TestClient(create_app(replace(container, disruption_intake=intake))) as http:
        return http.post(path, content=content, headers=headers)


def test_a_signed_valid_report_is_handed_to_the_use_case() -> None:
    intake = FakeIntake()
    content = body()
    response = post(intake, content, signed_headers(content, event_id="evt-1", at=T0))

    assert response.status_code == 202
    assert response.json() == {"document_id": str(intake.document_id), "status": "accepted"}
    [(command, context)] = intake.calls
    assert command.tenant_id.value == UUID(WEBHOOK_TENANT)  # from configuration
    assert command.channel is Channel.WEBHOOK
    assert command.source_name == WEBHOOK_SOURCE
    assert command.source_event_id == "evt-1"
    assert command.content == content  # the exact bytes, kept as evidence
    assert command.report is not None
    assert command.report.kind == "shipment_delay"
    assert command.report.summary == "SHIP-501 delayed by 7 days"
    assert command.report.references == ExternalReferences(
        suppliers=("SUP-001",), shipments=("SHIP-501",)
    )
    assert context.causation_id == "webhook:evt-1"
    assert context.correlation_id is not None  # the request id


def test_a_duplicate_is_still_a_success_for_the_sender() -> None:
    intake = FakeIntake(ReceiptStatus.DUPLICATE)
    content = body()
    response = post(intake, content, signed_headers(content, event_id="evt-1", at=T0))
    assert response.status_code == 202
    assert response.json()["status"] == "duplicate"


def test_a_tenant_in_the_payload_is_rejected_not_trusted() -> None:
    intake = FakeIntake()
    content = body({**REPORT, "tenant_id": "00000000-0000-0000-0000-000000000000"})
    response = post(intake, content, signed_headers(content, event_id="evt-1", at=T0))
    assert response.status_code == 422
    assert intake.calls == []


@pytest.mark.parametrize(
    "tamper",
    [
        lambda h: h | {"X-Tracer-Signature": "sha256=" + "0" * 64},
        lambda h: {k: v for k, v in h.items() if k != "X-Tracer-Signature"},
        lambda h: {k: v for k, v in h.items() if k != "X-Tracer-Event-Id"},
        lambda h: h | {"X-Tracer-Event-Id": "evt-2"},  # event id is signed
        lambda h: h | {"X-Tracer-Timestamp": str(int(T0.timestamp()) - 3600)},
    ],
)
def test_bad_signatures_are_401_and_never_reach_the_use_case(tamper: Any) -> None:
    intake = FakeIntake()
    content = body()
    response = post(intake, content, tamper(signed_headers(content, event_id="evt-1", at=T0)))
    assert response.status_code == 401
    assert response.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert response.json()["detail"] == "authentication failed"
    assert intake.calls == []


def test_failed_authentication_is_logged_without_secrets(caplog: pytest.LogCaptureFixture) -> None:
    content = body()
    headers = signed_headers(content, event_id="evt-1", at=T0) | {
        "X-Tracer-Signature": "sha256=" + "f" * 64
    }
    with caplog.at_level("WARNING", logger="tracer.http"):
        post(FakeIntake(), content, headers)
    [record] = [r for r in caplog.records if r.getMessage() == "authentication failed"]
    assert record.levelname == "WARNING"
    assert "f" * 64 not in caplog.text


def test_an_unknown_source_looks_exactly_like_a_bad_signature() -> None:
    content = body()
    response = post(
        FakeIntake(),
        content,
        signed_headers(content, event_id="evt-1", at=T0),
        path="/webhooks/someone-else/disruption-reports",
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "authentication failed"


def test_an_oversized_body_is_refused_before_anything_else() -> None:
    intake = FakeIntake()
    content = body({**REPORT, "summary": "x" * 2000})
    headers = signed_headers(content, event_id="evt-1", at=T0)
    response = post(intake, content, headers, webhook_max_body_bytes=1024)
    assert response.status_code == 413
    assert intake.calls == []


@pytest.mark.parametrize(
    ("report", "location"),
    [
        ({**REPORT, "kind": "alien_invasion"}, ["kind"]),
        ({**REPORT, "sumary": "typo"}, ["sumary"]),  # unknown fields are rejected
        ({**REPORT, "summary": "   "}, ["summary"]),
        ({**REPORT, "impact_end": None}, []),  # half a window
        ({**REPORT, "impact_start": "2026-11-05T09:00:00"}, ["impact_start"]),  # no timezone
        ({**REPORT, "references": {"suppliers": ["x" * 65]}}, ["references", "suppliers", 0]),
    ],
)
def test_invalid_reports_are_422_with_the_failing_field(
    report: dict[str, object], location: list[object]
) -> None:
    intake = FakeIntake()
    content = body(report)
    response = post(intake, content, signed_headers(content, event_id="evt-1", at=T0))
    assert response.status_code == 422
    errors = response.json()["errors"]
    assert any(error["loc"][: len(location)] == location for error in errors), errors
    assert intake.calls == []


def test_malformed_json_is_422() -> None:
    intake = FakeIntake()
    content = b"{not json"
    response = post(intake, content, signed_headers(content, event_id="evt-1", at=T0))
    assert response.status_code == 422
    assert intake.calls == []
