"""Inbound adapter: ``ingestion.disruption_report_received`` events -> the supply-chain use case.

The supply-chain context never imports ingestion code. It reads the event's JSON with its own
schema (a *tolerant reader*: it takes the fields it needs and ignores the rest), so ingestion
can add fields without breaking this consumer. The event type string and the fields read here
are the contract between the two contexts, pinned by an integration test.
"""

from typing import ClassVar
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError

from tracer.modules.supply_chain.application.detect_from_report import (
    CONSUMER,
    DetectDisruptionFromReport,
    ReportedDisruption,
)
from tracer.platform.messaging.dispatcher import PermanentDeliveryError
from tracer.platform.messaging.envelope import EventEnvelope, MessageContext
from tracer.shared.identifiers import TenantId


class _ReportPayloadV1(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    document_id: UUID
    kind: str
    summary: str
    impact_start: AwareDatetime | None = None
    impact_end: AwareDatetime | None = None
    possible_duplicate_of: UUID | None = None


class DisruptionReportConsumer:
    EVENT_TYPE: ClassVar[str] = "ingestion.disruption_report_received"
    SUPPORTED_SCHEMA_VERSIONS: ClassVar[frozenset[int]] = frozenset({1})

    def __init__(self, use_case: DetectDisruptionFromReport) -> None:
        self._use_case = use_case

    @property
    def name(self) -> str:
        return CONSUMER

    @property
    def event_types(self) -> frozenset[str]:
        return frozenset({self.EVENT_TYPE})

    async def handle(self, envelope: EventEnvelope) -> None:
        if envelope.schema_version not in self.SUPPORTED_SCHEMA_VERSIONS:
            raise PermanentDeliveryError(
                f"{self.EVENT_TYPE} schema version {envelope.schema_version} is not supported"
            )
        try:
            payload = _ReportPayloadV1.model_validate(envelope.payload)
        except ValidationError as error:
            raise PermanentDeliveryError(f"malformed {self.EVENT_TYPE} payload") from error

        await self._use_case.handle(
            ReportedDisruption(
                tenant_id=TenantId(envelope.tenant_id),
                document_id=payload.document_id,
                kind=payload.kind,
                summary=payload.summary,
                impact_start=payload.impact_start,
                impact_end=payload.impact_end,
                possible_duplicate_of=payload.possible_duplicate_of,
            ),
            event_id=envelope.event_id,
            context=MessageContext(
                correlation_id=envelope.correlation_id, causation_id=str(envelope.event_id)
            ),
        )
