from collections.abc import Callable
from datetime import timedelta

import pytest

from tests.support import T0
from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.modules.supply_chain.domain.events import (
    DisruptionDetected,
    DisruptionDismissed,
    DisruptionResolved,
    EvidenceRecorded,
    ImpactRecorded,
    InvestigationStarted,
)
from tracer.modules.supply_chain.domain.values import (
    AffectedEntities,
    CaseClosure,
    CaseResolution,
    CaseStatus,
    DisruptionKind,
    ResolutionOutcome,
)
from tracer.shared.clock import FrozenClock
from tracer.shared.errors import BusinessRuleError, InvalidTransitionError, InvalidValueError
from tracer.shared.identifiers import (
    CustomerOrderId,
    EvidenceId,
    RecoveryPlanId,
    ShipmentId,
    SupplierId,
    TenantId,
    UserId,
)
from tracer.shared.time_range import TimeRange

TENANT = TenantId.new()
OPERATOR = UserId.new()
EMAIL = EvidenceId.new()
SUPPLIER = SupplierId.new()
SHIPMENT = ShipmentId.new()
ORDER = CustomerOrderId.new()

RECOVERED = CaseResolution(
    ResolutionOutcome.RECOVERED, "Stock reallocated from KHI", RecoveryPlanId.new()
)


def detect(clock: FrozenClock, **overrides: object) -> DisruptionCase:
    values: dict[str, object] = {
        "tenant_id": TENANT,
        "kind": DisruptionKind.SHIPMENT_DELAY,
        "summary": "Supplier reports SHIP-501 delayed by 7 days",
        "evidence_ids": [EMAIL],
        "affected": AffectedEntities(supplier_ids=frozenset({SUPPLIER})),
        "clock": clock,
    }
    values.update(overrides)
    return DisruptionCase.detect(**values)  # type: ignore[arg-type]


def investigating(clock: FrozenClock) -> DisruptionCase:
    case = detect(clock)
    case.start_investigation(clock)
    case.pull_events()
    return case


class TestDetection:
    def test_detected_case_records_one_event(self, clock: FrozenClock) -> None:
        window = TimeRange(T0, T0 + timedelta(days=7))
        case = detect(clock, impact_window=window)

        assert case.status is CaseStatus.DETECTED
        assert case.is_open
        assert case.detected_at == T0
        assert case.impact_window == window
        [event] = case.pull_events()
        assert event == DisruptionDetected(
            tenant_id=TENANT,
            occurred_at=T0,
            case_id=case.id,
            kind=DisruptionKind.SHIPMENT_DELAY,
            evidence_ids=frozenset({EMAIL}),
            affected=AffectedEntities(supplier_ids=frozenset({SUPPLIER})),
        )

    def test_events_are_handed_over_once(self, clock: FrozenClock) -> None:
        case = detect(clock)
        assert len(case.pull_events()) == 1
        assert case.pull_events() == ()

    def test_requires_evidence(self, clock: FrozenClock) -> None:
        with pytest.raises(BusinessRuleError, match="evidence"):
            detect(clock, evidence_ids=[])

    @pytest.mark.parametrize("summary", ["", "   ", "x" * 2001])
    def test_requires_a_reasonable_summary(self, clock: FrozenClock, summary: str) -> None:
        with pytest.raises(InvalidValueError, match="summary"):
            detect(clock, summary=summary)

    def test_affected_entities_must_have_the_right_id_types(self) -> None:
        with pytest.raises(InvalidValueError, match="supplier_ids"):
            AffectedEntities(supplier_ids=frozenset({SHIPMENT}))  # type: ignore[arg-type]


class TestEvidenceAndImpact:
    def test_new_evidence_is_recorded(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()
        correction = EvidenceId.new()
        clock.advance(timedelta(hours=2))

        case.record_evidence(correction, clock)

        assert case.evidence_ids == {EMAIL, correction}
        assert case.pull_events() == (
            EvidenceRecorded(
                tenant_id=TENANT,
                occurred_at=T0 + timedelta(hours=2),
                case_id=case.id,
                evidence_id=correction,
            ),
        )

    def test_known_evidence_is_a_no_op(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()
        case.record_evidence(EMAIL, clock)
        assert case.pull_events() == ()

    def test_impact_event_carries_only_what_is_new(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()

        case.record_impact(
            AffectedEntities(
                supplier_ids=frozenset({SUPPLIER}),  # already known
                shipment_ids=frozenset({SHIPMENT}),
                customer_order_ids=frozenset({ORDER}),
            ),
            clock,
        )

        [event] = case.pull_events()
        assert isinstance(event, ImpactRecorded)
        assert event.added == AffectedEntities(
            shipment_ids=frozenset({SHIPMENT}), customer_order_ids=frozenset({ORDER})
        )
        assert case.affected.supplier_ids == {SUPPLIER}
        assert case.affected.customer_order_ids == {ORDER}

    def test_known_impact_is_a_no_op(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()
        case.record_impact(AffectedEntities(supplier_ids=frozenset({SUPPLIER})), clock)
        assert case.pull_events() == ()


class TestLifecycle:
    def test_investigation_starts_from_detected(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()
        case.start_investigation(clock)
        assert case.status is CaseStatus.INVESTIGATING
        assert case.pull_events() == (
            InvestigationStarted(tenant_id=TENANT, occurred_at=T0, case_id=case.id),
        )

    def test_resolve_records_who_how_and_when(self, clock: FrozenClock) -> None:
        case = investigating(clock)
        clock.advance(timedelta(days=1))

        case.resolve(RECOVERED, by=OPERATOR, clock=clock)

        assert case.status is CaseStatus.RESOLVED
        assert not case.is_open
        assert case.closure == CaseClosure(
            closed_at=T0 + timedelta(days=1), closed_by=OPERATOR, resolution=RECOVERED
        )
        [event] = case.pull_events()
        assert isinstance(event, DisruptionResolved)
        assert event.recovery_plan_id == RECOVERED.recovery_plan_id
        assert event.resolved_by == OPERATOR

    def test_dismiss_from_detected_or_investigating(self, clock: FrozenClock) -> None:
        for case in (detect(clock), investigating(clock)):
            case.pull_events()
            case.dismiss("  Duplicate of an earlier email  ", by=OPERATOR, clock=clock)
            assert case.status is CaseStatus.DISMISSED
            [event] = case.pull_events()
            assert isinstance(event, DisruptionDismissed)
            assert event.reason == "Duplicate of an earlier email"


class TestRejectedTransitions:
    """A refused action raises, leaves the state unchanged and records no event."""

    def test_cannot_resolve_before_investigating(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()
        with pytest.raises(
            InvalidTransitionError, match="cannot resolve this disruption case: it is detected"
        ):
            case.resolve(RECOVERED, by=OPERATOR, clock=clock)
        assert case.status is CaseStatus.DETECTED
        assert case.closure is None
        assert case.pull_events() == ()

    def test_cannot_start_investigating_twice(self, clock: FrozenClock) -> None:
        case = investigating(clock)
        with pytest.raises(InvalidTransitionError):
            case.start_investigation(clock)
        assert case.pull_events() == ()

    @pytest.mark.parametrize("final", ["resolve", "dismiss"])
    def test_closed_cases_are_final(self, clock: FrozenClock, final: str) -> None:
        case = investigating(clock)
        if final == "resolve":
            case.resolve(RECOVERED, by=OPERATOR, clock=clock)
        else:
            case.dismiss("false alarm", by=OPERATOR, clock=clock)
        case.pull_events()
        status = case.status

        attempts: list[Callable[[], None]] = [
            lambda: case.start_investigation(clock),
            lambda: case.resolve(RECOVERED, by=OPERATOR, clock=clock),
            lambda: case.dismiss("again", by=OPERATOR, clock=clock),
            lambda: case.record_evidence(EvidenceId.new(), clock),
            lambda: case.record_impact(AffectedEntities(shipment_ids=frozenset({SHIPMENT})), clock),
        ]
        for attempt in attempts:
            with pytest.raises(InvalidTransitionError):
                attempt()
        assert case.status is status
        assert case.pull_events() == ()

    def test_dismissal_needs_a_reason(self, clock: FrozenClock) -> None:
        case = detect(clock)
        case.pull_events()
        with pytest.raises(InvalidValueError, match="reason"):
            case.dismiss("  ", by=OPERATOR, clock=clock)
        assert case.status is CaseStatus.DETECTED
        assert case.pull_events() == ()


class TestResolutionRecord:
    def test_recovered_must_name_a_plan(self) -> None:
        with pytest.raises(InvalidValueError, match="recovery plan"):
            CaseResolution(ResolutionOutcome.RECOVERED, "done")

    def test_accepted_impact_has_no_plan(self) -> None:
        with pytest.raises(InvalidValueError, match="no recovery plan"):
            CaseResolution(ResolutionOutcome.IMPACT_ACCEPTED, "accepted", RecoveryPlanId.new())


class TestRehydration:
    """Loading a case from storage re-checks its invariants: corrupt state is refused."""

    def test_open_case_cannot_carry_a_closure(self, clock: FrozenClock) -> None:
        with pytest.raises(InvalidValueError, match="closure"):
            DisruptionCase(
                case_id=detect(clock).id,
                tenant_id=TENANT,
                kind=DisruptionKind.OTHER,
                summary="s",
                detected_at=T0,
                evidence_ids=frozenset({EMAIL}),
                affected=AffectedEntities(),
                impact_window=None,
                status=CaseStatus.INVESTIGATING,
                closure=CaseClosure(closed_at=T0, closed_by=OPERATOR, dismissal_reason="x"),
            )

    def test_resolved_case_needs_a_resolution_not_a_dismissal(self, clock: FrozenClock) -> None:
        with pytest.raises(InvalidValueError, match="does not match"):
            DisruptionCase(
                case_id=detect(clock).id,
                tenant_id=TENANT,
                kind=DisruptionKind.OTHER,
                summary="s",
                detected_at=T0,
                evidence_ids=frozenset({EMAIL}),
                affected=AffectedEntities(),
                impact_window=None,
                status=CaseStatus.RESOLVED,
                closure=CaseClosure(closed_at=T0, closed_by=OPERATOR, dismissal_reason="x"),
            )

    def test_rebuilding_records_no_events(self, clock: FrozenClock) -> None:
        original = detect(clock)
        rebuilt = DisruptionCase(
            case_id=original.id,
            tenant_id=original.tenant_id,
            kind=original.kind,
            summary=original.summary,
            detected_at=original.detected_at,
            evidence_ids=original.evidence_ids,
            affected=original.affected,
            impact_window=None,
            status=original.status,
            closure=None,
        )
        assert rebuilt.pull_events() == ()
