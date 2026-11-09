from dataclasses import dataclass
from typing import ClassVar

from tracer.modules.supply_chain.domain.values import (
    AffectedEntities,
    DisruptionKind,
    ResolutionOutcome,
)
from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import DisruptionCaseId, EvidenceId, RecoveryPlanId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionDetected(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "supply_chain.disruption_detected"

    case_id: DisruptionCaseId
    kind: DisruptionKind
    evidence_ids: frozenset[EvidenceId]
    affected: AffectedEntities


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRecorded(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "supply_chain.evidence_recorded"

    case_id: DisruptionCaseId
    evidence_id: EvidenceId


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactRecorded(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "supply_chain.impact_recorded"

    case_id: DisruptionCaseId
    added: AffectedEntities  # only what is new, not the whole set


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationStarted(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "supply_chain.investigation_started"

    case_id: DisruptionCaseId


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionResolved(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "supply_chain.disruption_resolved"

    case_id: DisruptionCaseId
    outcome: ResolutionOutcome
    recovery_plan_id: RecoveryPlanId | None
    resolved_by: UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionDismissed(DomainEvent):
    EVENT_TYPE: ClassVar[str] = "supply_chain.disruption_dismissed"

    case_id: DisruptionCaseId
    reason: str
    dismissed_by: UserId
