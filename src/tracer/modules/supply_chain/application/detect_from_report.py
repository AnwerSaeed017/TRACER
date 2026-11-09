"""Use case: a disruption report was received; open a case, or add to an open one.

Runs once per event, whatever the delivery does:

    one transaction:
        claim (consumer, event_id)          already claimed -> the effect is committed; stop
        possible duplicate of an open case? -> add this report as further evidence to it
        otherwise                           -> DisruptionCase.detect(...)
        save the case (its event goes to the outbox)
    commit

The claim and the case change commit together, so a crash at any point leaves either both or
neither: the event is never applied twice and never lost.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from tracer.modules.supply_chain.application.ports import SupplyChainUnitOfWork
from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.modules.supply_chain.domain.values import DisruptionKind
from tracer.platform.messaging.envelope import MessageContext
from tracer.shared.clock import Clock
from tracer.shared.errors import InvalidValueError
from tracer.shared.identifiers import DisruptionCaseId, EvidenceId, TenantId
from tracer.shared.time_range import TimeRange

CONSUMER: Final = "supply_chain.detect_disruption_from_report"


@dataclass(frozen=True, slots=True)
class ReportedDisruption:
    tenant_id: TenantId
    document_id: UUID  # the raw report: this case's evidence
    kind: str
    summary: str
    impact_start: datetime | None
    impact_end: datetime | None
    possible_duplicate_of: UUID | None  # an earlier document with identical content


class ReportOutcome(StrEnum):
    CASE_DETECTED = "case_detected"
    EVIDENCE_ADDED = "evidence_added"  # identical to evidence of an open case
    ALREADY_PROCESSED = "already_processed"  # a redelivery; nothing changed


@dataclass(frozen=True, slots=True)
class ReportResult:
    outcome: ReportOutcome
    case_id: DisruptionCaseId | None


class DetectDisruptionFromReport:
    def __init__(
        self, unit_of_work: Callable[[MessageContext], SupplyChainUnitOfWork], clock: Clock
    ) -> None:
        self._unit_of_work = unit_of_work
        self._clock = clock

    async def handle(
        self, report: ReportedDisruption, *, event_id: UUID, context: MessageContext
    ) -> ReportResult:
        kind = _kind(report.kind)
        evidence = EvidenceId(report.document_id)
        async with self._unit_of_work(context) as uow:
            first_time = await uow.processed_events.claim(
                consumer=CONSUMER, event_id=event_id, at=self._clock.now()
            )
            if not first_time:
                return ReportResult(ReportOutcome.ALREADY_PROCESSED, None)

            if report.possible_duplicate_of is not None:
                open_case = await uow.cases.find_open_by_evidence(
                    report.tenant_id, EvidenceId(report.possible_duplicate_of)
                )
                if open_case is not None:
                    open_case.record_evidence(evidence, self._clock)
                    await uow.cases.update(open_case)
                    await uow.commit()
                    return ReportResult(ReportOutcome.EVIDENCE_ADDED, open_case.id)

            case = DisruptionCase.detect(
                tenant_id=report.tenant_id,
                kind=kind,
                summary=report.summary,
                evidence_ids=[evidence],
                impact_window=_window(report.impact_start, report.impact_end),
                clock=self._clock,
            )
            await uow.cases.add(case)
            await uow.commit()
        return ReportResult(ReportOutcome.CASE_DETECTED, case.id)


def _kind(raw: str) -> DisruptionKind:
    try:
        return DisruptionKind(raw)
    except ValueError:
        raise InvalidValueError(f"unknown disruption kind {raw!r}") from None


def _window(start: datetime | None, end: datetime | None) -> TimeRange | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        raise InvalidValueError("an impact window needs both a start and an end")
    return TimeRange(start, end)
