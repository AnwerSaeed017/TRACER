"""Inbound webhooks: structured disruption reports, and raw emails from an email gateway.

Both endpoints use the same signed protocol, and the order of checks matters, cheapest and
most protective first:
1. size limit (before reading an unbounded body into memory);
2. authentication: source, event id, timestamp and signature (before parsing anything an
   unauthenticated caller sent);
3. content validation;
4. the use case, in one transaction. 202 is returned only after it has committed.
"""

from dataclasses import dataclass
from datetime import timedelta
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Header, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from tracer.bootstrap.composition_root import Container
from tracer.entrypoints.http.body import read_limited_body
from tracer.entrypoints.http.dependencies import ContainerDep
from tracer.entrypoints.http.schemas.webhooks import DisruptionReportIn, ReportReceiptOut
from tracer.modules.ingestion.adapters.webhook_signature import verify_signature
from tracer.modules.ingestion.application.email_intake import EMAIL_MEDIA_TYPE, InboundEmail
from tracer.modules.ingestion.application.intake import (
    DisruptionReportCommand,
    Receipt,
    ReportFields,
)
from tracer.modules.ingestion.domain.report import ExternalReferences
from tracer.modules.ingestion.domain.source_document import Channel
from tracer.platform.configuration.settings import WebhookSource
from tracer.platform.messaging.envelope import MessageContext
from tracer.platform.observability.context import request_id_var
from tracer.shared.errors import AuthenticationError
from tracer.shared.identifiers import TenantId

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_JSON: Final = "application/json"
SourceName = Annotated[str, Path(max_length=63)]
SignatureHeader = Annotated[str | None, Header()]


@dataclass(frozen=True, slots=True)
class _Authenticated:
    source: WebhookSource
    event_id: str
    body: bytes

    @property
    def tenant_id(self) -> TenantId:
        return TenantId(self.source.tenant_id)  # from configuration, never the payload

    @property
    def context(self) -> MessageContext:
        return MessageContext(
            correlation_id=request_id_var.get(), causation_id=f"webhook:{self.event_id}"[:64]
        )


async def _authenticate(
    request: Request,
    container: Container,
    source: str,
    event_id: str | None,
    timestamp: str | None,
    signature: str | None,
) -> _Authenticated:
    settings = container.settings
    body = await read_limited_body(request, settings.webhook_max_body_bytes)
    configured = container.webhook_sources.get(source)
    if configured is None:  # same answer as a bad signature: sources cannot be enumerated
        raise AuthenticationError
    verified = verify_signature(
        secrets=[s.get_secret_value().encode() for s in configured.secrets],
        event_id=event_id,
        timestamp_header=timestamp,
        signature_header=signature,
        body=body,
        now=container.clock.now(),
        tolerance=timedelta(seconds=settings.webhook_tolerance_seconds),
    )
    return _Authenticated(configured, verified, body)


def _receipt(receipt: Receipt) -> ReportReceiptOut:
    return ReportReceiptOut(document_id=receipt.document_id.value, status=receipt.status.value)


@router.post(
    "/{source}/disruption-reports",
    status_code=HTTPStatus.ACCEPTED,
    summary="Report a supply-chain disruption (HMAC-signed JSON)",
    responses={401: {}, 413: {}, 422: {}},
)
async def receive_disruption_report(
    request: Request,
    container: ContainerDep,
    source: SourceName,
    x_tracer_event_id: SignatureHeader = None,
    x_tracer_timestamp: SignatureHeader = None,
    x_tracer_signature: SignatureHeader = None,
) -> ReportReceiptOut:
    sender = await _authenticate(
        request, container, source, x_tracer_event_id, x_tracer_timestamp, x_tracer_signature
    )
    try:
        report = DisruptionReportIn.model_validate_json(sender.body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None

    receipt = await container.disruption_intake.receive(
        DisruptionReportCommand(
            tenant_id=sender.tenant_id,
            channel=Channel.WEBHOOK,
            source_name=sender.source.name,
            source_event_id=sender.event_id,
            media_type=_JSON,
            content=sender.body,
            report=ReportFields(
                kind=report.kind.value,
                summary=report.summary,
                impact_start=report.impact_start,
                impact_end=report.impact_end,
                references=ExternalReferences(
                    suppliers=tuple(report.references.suppliers),
                    shipments=tuple(report.references.shipments),
                    products=tuple(report.references.products),
                    customer_orders=tuple(report.references.customer_orders),
                ),
            ),
        ),
        sender.context,
    )
    return _receipt(receipt)


@router.post(
    "/{source}/emails",
    status_code=HTTPStatus.ACCEPTED,
    summary="Deliver a raw email (MIME) from an inbound-email gateway (HMAC-signed)",
    responses={401: {}, 413: {}, 415: {}, 422: {}},
)
async def receive_email(
    request: Request,
    container: ContainerDep,
    source: SourceName,
    x_tracer_event_id: SignatureHeader = None,
    x_tracer_timestamp: SignatureHeader = None,
    x_tracer_signature: SignatureHeader = None,
) -> ReportReceiptOut:
    sender = await _authenticate(
        request, container, source, x_tracer_event_id, x_tracer_timestamp, x_tracer_signature
    )
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != EMAIL_MEDIA_TYPE:
        raise HTTPException(
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE, f"send the raw email as {EMAIL_MEDIA_TYPE}"
        )
    receipt = await container.email_intake.receive(
        InboundEmail(
            tenant_id=sender.tenant_id,
            source_name=sender.source.name,
            source_event_id=sender.event_id,
            raw=sender.body,
        ),
        sender.context,
    )
    return _receipt(receipt)
