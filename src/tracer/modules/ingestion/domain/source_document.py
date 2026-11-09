"""A document exactly as it arrived from outside: the evidence everything else refers back to.

It is stored byte for byte and never modified. Its SHA-256 lets TRACER notice that the same
content arrived before. That is a *hint* (a supplier may legitimately re-send an identical
"still delayed" report), never a reason to discard a new event.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final

from tracer.shared.errors import InvalidValueError
from tracer.shared.guards import require_text, require_utc
from tracer.shared.identifiers import EntityId, TenantId

MAX_DOCUMENT_BYTES: Final = 5 * 1024 * 1024


class SourceDocumentId(EntityId):
    __slots__ = ()


class Channel(StrEnum):
    WEBHOOK = "webhook"
    EMAIL = "email"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    id: SourceDocumentId
    tenant_id: TenantId
    channel: Channel
    source_name: str
    media_type: str
    content: bytes
    received_at: datetime
    content_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.content, bytes) or not self.content:
            raise InvalidValueError("a document needs content")
        if len(self.content) > MAX_DOCUMENT_BYTES:
            raise InvalidValueError(f"a document is at most {MAX_DOCUMENT_BYTES} bytes")
        object.__setattr__(self, "channel", Channel(self.channel))
        object.__setattr__(
            self, "source_name", require_text(self.source_name, "source_name", max_length=100)
        )
        object.__setattr__(
            self, "media_type", require_text(self.media_type, "media_type", max_length=100)
        )
        object.__setattr__(self, "received_at", require_utc(self.received_at, "received_at"))
        object.__setattr__(self, "content_sha256", hashlib.sha256(self.content).hexdigest())

    @property
    def size_bytes(self) -> int:
        return len(self.content)
