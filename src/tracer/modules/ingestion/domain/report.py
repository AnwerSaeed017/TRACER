"""A disruption report as a source system described it, and the event announcing it.

Source systems name things by *their* keys ("SUP-001", "SHIP-501"), not TRACER's ids.
Resolving those keys to entities is entity resolution (M3); until then the keys travel as
plain strings, and the raw document remains the evidence.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Final

from tracer.modules.ingestion.domain.source_document import Channel, SourceDocumentId
from tracer.shared.aggregate import DomainEvent
from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import require_text

MAX_KEYS_PER_KIND: Final = 100
MAX_KEY_LENGTH: Final = 64


def _keys(values: tuple[str, ...], field: str) -> tuple[str, ...]:
    if len(values) > MAX_KEYS_PER_KIND:
        raise InvalidValueError(f"{field} has more than {MAX_KEYS_PER_KIND} entries")
    cleaned = tuple(require_text(v, field, max_length=MAX_KEY_LENGTH) for v in values)
    return tuple(dict.fromkeys(cleaned))  # drop duplicates, keep the sender's order


@dataclass(frozen=True, slots=True)
class ExternalReferences:
    suppliers: tuple[str, ...] = ()
    shipments: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    customer_orders: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("suppliers", "shipments", "products", "customer_orders"):
            object.__setattr__(self, name, _keys(getattr(self, name), name))


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionReportReceived(DomainEvent):
    """A source reported a disruption; the original document is ``document_id``.

    ``kind`` is the source's category as validated at the edge. Ingestion does not interpret
    it; the supply-chain context does.

    ``source_event_id`` is the sender's own id for this event (stable across its retries).
    ``possible_duplicate_of`` names an earlier document with byte-identical content, if any:
    a hint for the consumer, never a reason for ingestion to drop the report.
    """

    EVENT_TYPE: ClassVar[str] = "ingestion.disruption_report_received"

    document_id: SourceDocumentId
    channel: Channel
    source_name: str
    source_event_id: str
    possible_duplicate_of: SourceDocumentId | None
    kind: str
    summary: str
    impact_start: datetime | None
    impact_end: datetime | None
    references: ExternalReferences
