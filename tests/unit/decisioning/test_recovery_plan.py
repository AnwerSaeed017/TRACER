from collections.abc import Callable
from dataclasses import replace

import pytest

from tests.support import T0
from tracer.modules.decisioning.domain.events import (
    RecoveryPlanApproved,
    RecoveryPlanProposed,
    RecoveryPlanRejected,
    RecoveryPlanRevised,
    RecoveryPlanValidated,
    RecoveryPlanWithdrawn,
)
from tracer.modules.decisioning.domain.recovery_plan import RecoveryPlan
from tracer.modules.decisioning.domain.values import (
    ActionKind,
    PlanContent,
    PlannedAction,
    PlanStatus,
    plan_fingerprint,
)
from tracer.shared.clock import FrozenClock
from tracer.shared.errors import BusinessRuleError, InvalidTransitionError, InvalidValueError
from tracer.shared.identifiers import DisruptionCaseId, ProductId, RecoveryPlanId, TenantId, UserId
from tracer.shared.money import Money
from tracer.shared.quantity import EACH, Quantity

TENANT = TenantId.new()
CASE = DisruptionCaseId.new()
MANAGER = UserId.new()
P9 = ProductId.new()

REALLOCATE = PlannedAction(
    ActionKind.REALLOCATE_INVENTORY,
    "Move 20 units of P9 from KHI to ORD-101",
    P9,
    Quantity.of(20, EACH),
)
NOTIFY = PlannedAction(ActionKind.NOTIFY_CUSTOMER, "Tell the customer the new date")
CONTENT = PlanContent((REALLOCATE,), Money.of("2000", "USD"), "Stock at KHI covers the order")
CHEAPER = replace(CONTENT, estimated_cost=Money.of("1500", "USD"))
PINNED_FINGERPRINT = "b2d6ce1987d05f7b6c6fc4769d5db3f4e668de4d1e9b38fbe18471bbf971ea97"


def propose(clock: FrozenClock, content: PlanContent = CONTENT) -> RecoveryPlan:
    plan = RecoveryPlan.propose(tenant_id=TENANT, case_id=CASE, content=content, clock=clock)
    plan.pull_events()
    return plan


def validated(clock: FrozenClock) -> RecoveryPlan:
    plan = propose(clock)
    plan.record_validation([], clock)
    plan.pull_events()
    return plan


def approved(clock: FrozenClock) -> RecoveryPlan:
    plan = validated(clock)
    plan.approve(by=MANAGER, fingerprint=plan.fingerprint, clock=clock)
    plan.pull_events()
    return plan


class TestActionsAndContent:
    def test_goods_actions_need_product_and_positive_quantity(self) -> None:
        with pytest.raises(InvalidValueError, match="product and a quantity"):
            PlannedAction(ActionKind.SWITCH_SUPPLIER, "switch")
        with pytest.raises(InvalidValueError, match="positive quantity"):
            PlannedAction(ActionKind.SWITCH_SUPPLIER, "switch", P9, Quantity.of(0, EACH))
        with pytest.raises(InvalidValueError, match="positive quantity"):
            PlannedAction(ActionKind.SWITCH_SUPPLIER, "switch", P9, Quantity.of(-5, EACH))

    def test_notification_moves_no_goods(self) -> None:
        with pytest.raises(InvalidValueError, match="does not move goods"):
            PlannedAction(ActionKind.NOTIFY_CUSTOMER, "notify", P9, Quantity.of(1, EACH))

    def test_plan_needs_actions_and_non_negative_cost(self) -> None:
        with pytest.raises(InvalidValueError, match="at least one action"):
            PlanContent((), Money.of("1", "USD"), "why")
        with pytest.raises(InvalidValueError, match="negative"):
            PlanContent((NOTIFY,), Money.of("-1", "USD"), "why")


class TestFingerprint:
    def test_is_stable_for_the_same_content(self) -> None:
        plan_id = RecoveryPlanId.new()
        assert plan_fingerprint(plan_id, 1, CONTENT) == plan_fingerprint(plan_id, 1, CONTENT)

    def test_ignores_decimal_scale(self) -> None:
        plan_id = RecoveryPlanId.new()
        same = replace(CONTENT, estimated_cost=Money.of("2000.00", "USD"))
        assert plan_fingerprint(plan_id, 1, CONTENT) == plan_fingerprint(plan_id, 1, same)

    @pytest.mark.parametrize(
        "changed",
        [
            replace(CONTENT, estimated_cost=Money.of("2000", "PKR")),
            replace(CONTENT, rationale="different reason"),
            replace(CONTENT, actions=(REALLOCATE, NOTIFY)),
            replace(CONTENT, actions=(replace(REALLOCATE, quantity=Quantity.of(21, EACH)),)),
        ],
    )
    def test_changes_when_any_approved_content_changes(self, changed: PlanContent) -> None:
        plan_id = RecoveryPlanId.new()
        assert plan_fingerprint(plan_id, 1, CONTENT) != plan_fingerprint(plan_id, 1, changed)

    def test_is_a_known_value_for_known_content(self) -> None:
        # Pins the canonical form. If this fails, serialisation changed: bump
        # FINGERPRINT_VERSION instead of updating the expected digest silently.
        plan_id = RecoveryPlanId.parse("00000000-0000-0000-0000-000000000001")
        content = PlanContent(
            (PlannedAction(ActionKind.NOTIFY_CUSTOMER, "Tell the customer"),),
            Money.of("10", "USD"),
            "why",
        )
        assert plan_fingerprint(plan_id, 1, content) == PINNED_FINGERPRINT

    def test_changes_with_the_revision(self) -> None:
        plan_id = RecoveryPlanId.new()
        assert plan_fingerprint(plan_id, 1, CONTENT) != plan_fingerprint(plan_id, 2, CONTENT)


class TestHappyPath:
    def test_propose_validate_approve(self, clock: FrozenClock) -> None:
        plan = RecoveryPlan.propose(tenant_id=TENANT, case_id=CASE, content=CONTENT, clock=clock)
        assert plan.pull_events() == (
            RecoveryPlanProposed(
                tenant_id=TENANT,
                occurred_at=T0,
                plan_id=plan.id,
                case_id=CASE,
                revision=1,
                fingerprint=plan.fingerprint,
            ),
        )

        plan.record_validation([], clock)
        assert isinstance(plan.pull_events()[0], RecoveryPlanValidated)

        reviewed = plan.fingerprint
        plan.approve(by=MANAGER, fingerprint=reviewed, clock=clock)
        assert plan.status is PlanStatus.APPROVED
        assert plan.approval is not None
        assert plan.approval.approved_by == MANAGER
        assert plan.pull_events() == (
            RecoveryPlanApproved(
                tenant_id=TENANT,
                occurred_at=T0,
                plan_id=plan.id,
                revision=1,
                fingerprint=reviewed,
                approved_by=MANAGER,
            ),
        )


class TestValidation:
    def test_failed_validation_rejects_and_lists_violations(self, clock: FrozenClock) -> None:
        plan = propose(clock)
        plan.record_validation(["available 12 EACH < required 20 EACH"], clock)
        assert plan.status is PlanStatus.REJECTED
        assert plan.violations == ("available 12 EACH < required 20 EACH",)
        [event] = plan.pull_events()
        assert isinstance(event, RecoveryPlanRejected)
        assert event.rejected_by is None
        assert event.violations == plan.violations

    def test_only_a_proposed_plan_is_validated(self, clock: FrozenClock) -> None:
        plan = validated(clock)
        with pytest.raises(InvalidTransitionError):
            plan.record_validation([], clock)
        assert plan.pull_events() == ()


class TestApprovalIsBoundToContent:
    def test_stale_fingerprint_is_refused(self, clock: FrozenClock) -> None:
        plan = validated(clock)
        stale = plan_fingerprint(plan.id, plan.revision, CHEAPER)  # what another screen showed
        with pytest.raises(BusinessRuleError, match="changed after it was reviewed"):
            plan.approve(by=MANAGER, fingerprint=stale, clock=clock)
        assert plan.status is PlanStatus.VALIDATED
        assert plan.approval is None
        assert plan.pull_events() == ()

    def test_cannot_approve_an_unvalidated_plan(self, clock: FrozenClock) -> None:
        plan = propose(clock)
        with pytest.raises(
            InvalidTransitionError, match="cannot approve this recovery plan: it is proposed"
        ):
            plan.approve(by=MANAGER, fingerprint=plan.fingerprint, clock=clock)
        assert plan.pull_events() == ()

    def test_revising_an_approved_plan_requires_new_approval(self, clock: FrozenClock) -> None:
        plan = approved(clock)
        old_fingerprint = plan.fingerprint

        plan.revise(CHEAPER, clock)

        assert plan.status is PlanStatus.PROPOSED
        assert plan.revision == 2
        assert plan.approval is None
        assert plan.fingerprint != old_fingerprint
        assert plan.pull_events() == (
            RecoveryPlanRevised(
                tenant_id=TENANT,
                occurred_at=T0,
                plan_id=plan.id,
                revision=2,
                fingerprint=plan.fingerprint,
            ),
        )
        with pytest.raises(InvalidTransitionError):
            plan.approve(by=MANAGER, fingerprint=plan.fingerprint, clock=clock)


class TestRejectionRevisionWithdrawal:
    def test_human_rejection_needs_a_reason(self, clock: FrozenClock) -> None:
        plan = validated(clock)
        with pytest.raises(InvalidValueError, match="reason"):
            plan.reject(" ", by=MANAGER, clock=clock)
        plan.reject("Too expensive", by=MANAGER, clock=clock)
        assert plan.status is PlanStatus.REJECTED
        assert plan.status_reason == "Too expensive"
        [event] = plan.pull_events()
        assert isinstance(event, RecoveryPlanRejected)
        assert event.rejected_by == MANAGER

    def test_a_rejected_plan_can_be_revised(self, clock: FrozenClock) -> None:
        plan = propose(clock)
        plan.record_validation(["shortfall"], clock)
        plan.revise(CHEAPER, clock)
        assert plan.status is PlanStatus.PROPOSED
        assert plan.violations == ()
        assert plan.status_reason is None

    def test_a_revision_must_change_something(self, clock: FrozenClock) -> None:
        plan = propose(clock)
        with pytest.raises(BusinessRuleError, match="must change"):
            plan.revise(CONTENT, clock)
        assert plan.revision == 1

    def test_withdrawal_is_final(self, clock: FrozenClock) -> None:
        plan = approved(clock)
        plan.withdraw("Supplier delivered after all", clock)
        assert plan.status is PlanStatus.WITHDRAWN
        assert plan.approval is None
        [event] = plan.pull_events()
        assert isinstance(event, RecoveryPlanWithdrawn)

        refused: list[Callable[[], None]] = [
            lambda: plan.revise(CHEAPER, clock),
            lambda: plan.withdraw("again", clock),
            lambda: plan.record_validation([], clock),
        ]
        for attempt in refused:
            with pytest.raises(InvalidTransitionError):
                attempt()
        assert plan.pull_events() == ()


class TestRehydration:
    def test_approval_must_match_current_content(self, clock: FrozenClock) -> None:
        plan = approved(clock)
        assert plan.approval is not None
        with pytest.raises(InvalidValueError, match="different plan content"):
            RecoveryPlan(
                plan_id=plan.id,
                tenant_id=TENANT,
                case_id=CASE,
                revision=1,
                content=CHEAPER,  # content changed but approval kept: corrupt
                status=PlanStatus.APPROVED,
                proposed_at=T0,
                approval=plan.approval,
                violations=(),
                status_reason=None,
            )
