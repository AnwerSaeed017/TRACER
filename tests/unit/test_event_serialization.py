"""Every domain event has a unique, stable type name and serialises to plain JSON."""

import json
import sys
from datetime import timedelta
from decimal import Decimal

import pytest

import tracer.modules.decisioning.domain.events
import tracer.modules.execution.domain.events
import tracer.modules.ingestion.domain.report
import tracer.modules.supply_chain.domain.events  # noqa: F401  # registers the event classes
from tests.support import T0
from tracer.modules.supply_chain.domain.disruption_case import DisruptionCase
from tracer.modules.supply_chain.domain.values import AffectedEntities, DisruptionKind
from tracer.platform.messaging.envelope import MessageContext, envelope_events
from tracer.platform.messaging.serialization import to_json_value
from tracer.shared.aggregate import DomainEvent
from tracer.shared.clock import FrozenClock
from tracer.shared.identifiers import EvidenceId, ProductId, SupplierId, TenantId
from tracer.shared.money import Money
from tracer.shared.quantity import EACH, Quantity


def all_event_classes() -> list[type[DomainEvent]]:
    # dataclass(slots=True) builds a new class; the original stays listed as a subclass. Keep
    # only the class its module actually exports.
    found: list[type[DomainEvent]] = []
    pending = list(DomainEvent.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        if getattr(sys.modules[cls.__module__], cls.__name__, None) is cls and cls not in found:
            found.append(cls)
    return found


def test_every_event_declares_a_unique_namespaced_type() -> None:
    classes = all_event_classes()
    assert len(classes) == 18  # update deliberately when an event type is added
    types = [cls.EVENT_TYPE for cls in classes]
    assert len(set(types)) == len(types)
    for cls in classes:
        context, _, name = cls.EVENT_TYPE.partition(".")
        assert context in {"ingestion", "supply_chain", "decisioning", "execution"}, cls
        assert name.islower(), cls
        assert cls.SCHEMA_VERSION >= 1


def test_values_become_plain_json() -> None:
    supplier_a, supplier_b = SupplierId.new(), SupplierId.new()
    value = {
        "ids": frozenset({supplier_b, supplier_a}),
        "money": Money.of("120.50", "USD"),
        "quantity": Quantity.of(20, EACH),
        "when": T0,
        "kind": DisruptionKind.SHIPMENT_DELAY,
        "nothing": None,
    }
    result = {key: to_json_value(item) for key, item in value.items()}
    assert result == {
        "ids": sorted([str(supplier_a), str(supplier_b)]),
        "money": {"amount": "120.50", "currency": "USD"},  # exact string, never a float
        "quantity": {"amount": "20", "unit": "EACH"},
        "when": "2026-11-05T09:00:00+00:00",
        "kind": "shipment_delay",
        "nothing": None,
    }
    json.dumps(result)  # really is plain JSON


def test_unknown_types_are_refused_rather_than_guessed() -> None:
    with pytest.raises(TypeError, match="cannot serialise"):
        to_json_value(object())


def test_decimal_keeps_its_exact_digits() -> None:
    assert to_json_value(Decimal("0.04375")) == "0.04375"


def test_envelopes_take_consecutive_versions_and_carry_tracing_ids() -> None:
    clock = FrozenClock(T0)
    case = DisruptionCase.detect(
        tenant_id=TenantId.new(),
        kind=DisruptionKind.OTHER,
        summary="s",
        evidence_ids=[EvidenceId.new()],
        affected=AffectedEntities(product_ids=frozenset({ProductId.new()})),
        clock=clock,
    )
    case.start_investigation(clock)
    clock.advance(timedelta(minutes=5))
    case.record_evidence(EvidenceId.new(), clock)
    events = case.pull_events()

    envelopes = envelope_events(
        aggregate_type=case.AGGREGATE_TYPE,
        aggregate_id=case.id.value,
        first_version=1,
        events=events,
        context=MessageContext(correlation_id="req-9", causation_id="msg-3"),
    )

    assert [e.aggregate_version for e in envelopes] == [1, 2, 3]
    assert len({e.event_id for e in envelopes}) == 3
    assert {(e.correlation_id, e.causation_id) for e in envelopes} == {("req-9", "msg-3")}
    assert envelopes[2].occurred_at == T0 + timedelta(minutes=5)
    assert "tenant_id" not in envelopes[0].payload  # lives in the envelope, not repeated
    assert case.version == 3


def test_trace_ids_are_bounded() -> None:
    with pytest.raises(ValueError, match="correlation_id"):
        MessageContext(correlation_id="x" * 65)
