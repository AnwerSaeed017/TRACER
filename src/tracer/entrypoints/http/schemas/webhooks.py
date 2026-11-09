"""The published webhook contract for disruption reports.

Senders depend on this shape, so it is strict: unknown fields are rejected (a typo such as
"sumary" fails loudly instead of being ignored), and every list and string is bounded.
"""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from tracer.modules.supply_chain.domain.values import DisruptionKind

ExternalKey = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
KeyList = Annotated[list[ExternalKey], Field(max_length=100)]


class ExternalReferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suppliers: KeyList = []
    shipments: KeyList = []
    products: KeyList = []
    customer_orders: KeyList = []


class DisruptionReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DisruptionKind
    summary: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
    impact_start: AwareDatetime | None = None
    impact_end: AwareDatetime | None = None
    references: ExternalReferencesIn = ExternalReferencesIn()

    @model_validator(mode="after")
    def _impact_window(self) -> Self:
        if (self.impact_start is None) != (self.impact_end is None):
            raise ValueError("give both impact_start and impact_end, or neither")
        if self.impact_start and self.impact_end and self.impact_end <= self.impact_start:
            raise ValueError("impact_end must be after impact_start")
        return self


class ReportReceiptOut(BaseModel):
    document_id: UUID
    status: Literal["accepted", "duplicate"]
