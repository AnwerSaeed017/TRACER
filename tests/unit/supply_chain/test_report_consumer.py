"""The consumer refuses what it cannot read, as permanent failures, before touching the use case."""

from typing import Any
from uuid import uuid4

import pytest

from tests.support import T0
from tracer.modules.supply_chain.adapters.messaging.report_consumer import (
    DisruptionReportConsumer,
)
from tracer.platform.messaging.dispatcher import PermanentDeliveryError
from tracer.platform.messaging.envelope import EventEnvelope


class ExplodingUseCase:
    async def handle(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("must not be called")


def envelope(payload: dict[str, Any], schema_version: int = 1) -> EventEnvelope:
    return EventEnvelope(
        event_type="ingestion.disruption_report_received",
        schema_version=schema_version,
        tenant_id=uuid4(),
        aggregate_type="source_document",
        aggregate_id=uuid4(),
        aggregate_version=1,
        occurred_at=T0,
        payload=payload,
        correlation_id=None,
        causation_id=None,
    )


VALID = {"document_id": str(uuid4()), "kind": "shipment_delay", "summary": "late"}


def consumer() -> DisruptionReportConsumer:
    return DisruptionReportConsumer(ExplodingUseCase())  # type: ignore[arg-type]


async def test_an_unsupported_schema_version_is_permanent() -> None:
    with pytest.raises(PermanentDeliveryError, match="schema version 2"):
        await consumer().handle(envelope(VALID, schema_version=2))


@pytest.mark.parametrize(
    "payload",
    [
        {k: v for k, v in VALID.items() if k != "document_id"},
        {**VALID, "document_id": "not-a-uuid"},
        {**VALID, "impact_start": "2026-11-05T09:00:00"},  # naive datetime
    ],
)
async def test_a_malformed_payload_is_permanent(payload: dict[str, Any]) -> None:
    with pytest.raises(PermanentDeliveryError, match="malformed"):
        await consumer().handle(envelope(payload))


def test_subscribes_only_to_disruption_reports() -> None:
    assert consumer().event_types == {"ingestion.disruption_report_received"}
