"""Use case: a source system delivers a document that may report a disruption.

    1. look for an earlier document with identical content (a hint, not a verdict)
    2. store the raw document
    3. claim the source's event id in the inbox: this is THE duplicate check
    4. if a disruption was recognised, record DisruptionReportReceived in the outbox
    5. commit all of it in one transaction

Identity rules:
- the sender's **event id** is the only proof of a duplicate: a retry of the same event is
  answered DUPLICATE and changes nothing;
- identical **content** under a different event id is a new event, accepted and flagged with
  ``possible_duplicate_of``. A missed disruption is worse than a repeated one, so ingestion
  never drops a report on content alone; the consumer decides with business context.

A document in which no disruption was recognised (``report is None``) is still stored as
evidence and its event id remembered, but no event is recorded: UNRECOGNISED. Such documents
can be re-examined later by a better extractor (M5) without asking the sender again.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final, Protocol

from tracer.modules.ingestion.application.ports import IngestionUnitOfWork
from tracer.modules.ingestion.domain.report import DisruptionReportReceived, ExternalReferences
from tracer.modules.ingestion.domain.source_document import (
    Channel,
    SourceDocument,
    SourceDocumentId,
)
from tracer.platform.messaging.envelope import MessageContext
from tracer.shared.clock import Clock
from tracer.shared.identifiers import TenantId

DOCUMENT_AGGREGATE: Final = "source_document"


class ReceiptStatus(StrEnum):
    ACCEPTED = "accepted"  # stored, and a disruption report event will be delivered
    DUPLICATE = "duplicate"  # this event id was already received; nothing changed
    UNRECOGNISED = "unrecognised"  # stored as evidence; no disruption recognised in it


@dataclass(frozen=True, slots=True)
class ReportFields:
    """What the source said about the disruption, already validated or extracted."""

    kind: str
    summary: str
    impact_start: datetime | None
    impact_end: datetime | None
    references: ExternalReferences


@dataclass(frozen=True, slots=True)
class DisruptionReportCommand:
    tenant_id: TenantId
    channel: Channel
    source_name: str
    source_event_id: str
    media_type: str
    content: bytes  # the exact bytes received, kept as evidence
    report: ReportFields | None  # None: nothing recognisable as a disruption


@dataclass(frozen=True, slots=True)
class Receipt:
    status: ReceiptStatus
    document_id: SourceDocumentId


class ReceivesDisruptionReports(Protocol):
    async def receive(
        self, command: DisruptionReportCommand, context: MessageContext
    ) -> Receipt: ...


class DisruptionReportIntake:
    def __init__(
        self, unit_of_work: Callable[[MessageContext], IngestionUnitOfWork], clock: Clock
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    async def receive(self, command: DisruptionReportCommand, context: MessageContext) -> Receipt:
        now = self._clock.now()
        document = SourceDocument(
            id=SourceDocumentId.new(),
            tenant_id=command.tenant_id,
            channel=command.channel,
            source_name=command.source_name,
            media_type=command.media_type,
            content=command.content,
            received_at=now,
        )
        async with self._unit_of_work(context) as uow:
            earlier = await uow.documents.find_same_content(
                command.tenant_id, document.content_sha256
            )
            await uow.documents.add(document)
            claim = await uow.inbox.claim(
                tenant_id=command.tenant_id,
                source_name=command.source_name,
                event_id=command.source_event_id,
                document_id=document.id,
                received_at=now,
            )
            if not claim.claimed:
                # Leaving without commit also discards this attempt's document insert.
                return Receipt(ReceiptStatus.DUPLICATE, claim.document_id)

            report = command.report
            if report is None:
                await uow.commit()  # evidence and event id kept; nothing to announce
                return Receipt(ReceiptStatus.UNRECOGNISED, document.id)

            uow.record_events(
                aggregate_type=DOCUMENT_AGGREGATE,
                aggregate_id=document.id.value,
                first_version=1,
                events=[
                    DisruptionReportReceived(
                        tenant_id=command.tenant_id,
                        occurred_at=now,
                        document_id=document.id,
                        channel=command.channel,
                        source_name=command.source_name,
                        source_event_id=command.source_event_id,
                        possible_duplicate_of=earlier,
                        kind=report.kind,
                        summary=report.summary,
                        impact_start=report.impact_start,
                        impact_end=report.impact_end,
                        references=report.references,
                    )
                ],
            )
            await uow.commit()
        return Receipt(ReceiptStatus.ACCEPTED, document.id)
