"""Inbound webhooks.

The order of checks matters, cheapest and most protective first:
1. size limit (before reading an unbounded body into memory);
2. authentication: source, event id, timestamp and signature (before parsing anything an
   unauthenticated caller sent);
3. schema validation;
4. the use case, in one transaction. 202 is returned only after it has committed.
"""

from datetime import timedelta
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Header, Path, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from tracer.entrypoints.http.body import read_limited_body
from tracer.entrypoints.http.dependencies import ContainerDep
from tracer.entrypoints.http.schemas.webhooks import DisruptionReportIn, ReportReceiptOut
from tracer.modules.ingestion.adapters.webhook_signature import verify_signature
from tracer.modules.ingestion.application.intake import DisruptionReportCommand
from tracer.modules.ingestion.domain.report import ExternalReferences
from tracer.modules.ingestion.domain.source_document import Channel
from tracer.platform.messaging.envelope import MessageContext
from tracer.platform.observability.context import request_id_var
from tracer.shared.errors import AuthenticationError
from tracer.shared.identifiers import TenantId

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_JSON: Final = "application/json"


@router.post(
    "/{source}/disruption-reports",
    status_code=HTTPStatus.ACCEPTED,
    summary="Report a supply-chain disruption (HMAC-signed)",
    responses={401: {}, 413: {}, 422: {}},
)
async def receive_disruption_report(
    request: Request,
    container: ContainerDep,
    source: Annotated[str, Path(max_length=63)],
    x_tracer_event_id: Annotated[str | None, Header()] = None,
    x_tracer_timestamp: Annotated[str | None, Header()] = None,
    x_tracer_signature: Annotated[str | None, Header()] = None,
) -> ReportReceiptOut:
    settings = container.settings
    body = await read_limited_body(request, settings.webhook_max_body_bytes)

    configured = container.webhook_sources.get(source)
    if configured is None:  # same answer as a bad signature: sources cannot be enumerated
        raise AuthenticationError
    event_id = verify_signature(
        secrets=[s.get_secret_value().encode() for s in configured.secrets],
        event_id=x_tracer_event_id,
        timestamp_header=x_tracer_timestamp,
        signature_header=x_tracer_signature,
        body=body,
        now=container.clock.now(),
        tolerance=timedelta(seconds=settings.webhook_tolerance_seconds),
    )

    try:
        report = DisruptionReportIn.model_validate_json(body)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None

    receipt = await container.disruption_intake.receive(
        DisruptionReportCommand(
            tenant_id=TenantId(configured.tenant_id),  # from configuration, never the payload
            channel=Channel.WEBHOOK,
            source_name=configured.name,
            source_event_id=event_id,
            media_type=_JSON,
            content=body,
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
        MessageContext(
            correlation_id=request_id_var.get(), causation_id=f"webhook:{event_id}"[:64]
        ),
    )
    return ReportReceiptOut(document_id=receipt.document_id.value, status=receipt.status.value)
