"""Use case: an inbound-email gateway delivers a raw email (MIME, RFC 5322).

    raw bytes ──parse──► ParsedEmail ──extract──► ReportFields | None ──► DisruptionReportIntake

The raw bytes, not the parsed or extracted form, are stored as evidence. Extraction sits
behind ``DisruptionExtractor``: the deterministic keyword baseline today, a model-based
extractor from M5, with no change to this use case or to anything downstream.
"""

from dataclasses import dataclass
from typing import Final, Protocol

from tracer.modules.ingestion.application.intake import (
    DisruptionReportCommand,
    Receipt,
    ReceivesDisruptionReports,
    ReportFields,
)
from tracer.modules.ingestion.domain.email import ParsedEmail
from tracer.modules.ingestion.domain.source_document import Channel
from tracer.platform.messaging.envelope import MessageContext
from tracer.shared.identifiers import TenantId

EMAIL_MEDIA_TYPE: Final = "message/rfc822"


class EmailParser(Protocol):
    def parse(self, raw: bytes) -> ParsedEmail:
        """Raise InvalidValueError if ``raw`` is not a usable email."""
        ...


class DisruptionExtractor(Protocol):
    def extract(self, email: ParsedEmail) -> ReportFields | None:
        """The disruption the email reports, or None if none is recognisable."""
        ...


@dataclass(frozen=True, slots=True)
class InboundEmail:
    tenant_id: TenantId
    source_name: str
    source_event_id: str
    raw: bytes


class ReceivesEmails(Protocol):
    async def receive(self, email: InboundEmail, context: MessageContext) -> Receipt: ...


class EmailIntake:
    def __init__(
        self,
        parser: EmailParser,
        extractor: DisruptionExtractor,
        intake: ReceivesDisruptionReports,
    ) -> None:
        self._parser = parser
        self._extractor = extractor
        self._intake = intake

    async def receive(self, email: InboundEmail, context: MessageContext) -> Receipt:
        parsed = self._parser.parse(email.raw)
        return await self._intake.receive(
            DisruptionReportCommand(
                tenant_id=email.tenant_id,
                channel=Channel.EMAIL,
                source_name=email.source_name,
                source_event_id=email.source_event_id,
                media_type=EMAIL_MEDIA_TYPE,
                content=email.raw,
                report=self._extractor.extract(parsed),
            ),
            context,
        )
