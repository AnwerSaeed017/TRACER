from dataclasses import dataclass

from tracer.modules.supply_chain.domain.values import (
    AffectedEntities,
    DisruptionKind,
    ResolutionOutcome,
)
from tracer.shared.aggregate import DomainEvent
from tracer.shared.identifiers import DisruptionCaseId, EvidenceId, RecoveryPlanId, UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionDetected(DomainEvent):
    case_id: DisruptionCaseId
    kind: DisruptionKind
    evidence_ids: frozenset[EvidenceId]
    affected: AffectedEntities


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRecorded(DomainEvent):
    case_id: DisruptionCaseId
    evidence_id: EvidenceId


@dataclass(frozen=True, slots=True, kw_only=True)
class ImpactRecorded(DomainEvent):
    case_id: DisruptionCaseId
    added: AffectedEntities  # only what is new, not the whole set


@dataclass(frozen=True, slots=True, kw_only=True)
class InvestigationStarted(DomainEvent):
    case_id: DisruptionCaseId


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionResolved(DomainEvent):
    case_id: DisruptionCaseId
    outcome: ResolutionOutcome
    recovery_plan_id: RecoveryPlanId | None
    resolved_by: UserId


@dataclass(frozen=True, slots=True, kw_only=True)
class DisruptionDismissed(DomainEvent):
    case_id: DisruptionCaseId
    reason: str
    dismissed_by: UserId
