"""Webhook intake against real PostgreSQL: evidence, inbox and outbox, atomically."""

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import httpx2 as httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.support import T0, WEBHOOK_SOURCE, report_command, signed_headers, webhook_settings
from tracer.bootstrap.composition_root import Container, build_container
from tracer.entrypoints.http.app import create_app
from tracer.modules.ingestion.adapters.persistence.models import (
    InboxMessageRecord,
    SourceDocumentRecord,
)
from tracer.modules.ingestion.application.intake import DisruptionReportCommand, ReceiptStatus
from tracer.platform.messaging.envelope import MessageContext
from tracer.platform.messaging.models import OutboxEventRecord
from tracer.shared.clock import FrozenClock
from tracer.shared.identifiers import TenantId

pytestmark = pytest.mark.integration

URL = f"/webhooks/{WEBHOOK_SOURCE}/disruption-reports"
REPORT = json.dumps(
    {
        "kind": "shipment_delay",
        "summary": "SHIP-501 delayed by 7 days",
        "references": {"suppliers": ["SUP-001"], "shipments": ["SHIP-501"]},
    }
).encode()


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
async def container(
    test_database_url: str, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> AsyncIterator[Container]:
    container = build_container(webhook_settings(database_url=test_database_url), clock=clock)
    yield container
    await container.aclose()


async def post(
    container: Container, body: bytes, event_id: str, at: datetime = T0
) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://tracer") as client:
        return await client.post(
            URL, content=body, headers=signed_headers(body, event_id=event_id, at=at)
        )


async def counts(sessions: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    """(documents, inbox events, outbox events)."""
    async with sessions() as session:

        async def count(model: type[object]) -> int:
            return int(await session.scalar(select(func.count()).select_from(model)) or 0)

        return (
            await count(SourceDocumentRecord),
            await count(InboxMessageRecord),
            await count(OutboxEventRecord),
        )


async def outbox_payloads(sessions: async_sessionmaker[AsyncSession]) -> list[dict[str, object]]:
    async with sessions() as session:
        rows = await session.execute(
            select(OutboxEventRecord).order_by(OutboxEventRecord.recorded_at)
        )
        return [row.payload for row in rows.scalars()]


def command(event_id: str, tenant: TenantId) -> DisruptionReportCommand:
    return report_command(event_id, tenant, content=REPORT)


async def test_an_accepted_report_stores_evidence_inbox_and_event(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    response = await post(container, REPORT, "evt-1")

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    document_id = response.json()["document_id"]
    async with sessions() as session:
        document = await session.get(SourceDocumentRecord, document_id)
        [event] = (await session.execute(select(OutboxEventRecord))).scalars()
    assert document is not None
    assert document.content == REPORT  # byte for byte
    assert event.event_type == "ingestion.disruption_report_received"
    assert event.aggregate_type == "source_document"
    assert str(event.aggregate_id) == document_id
    assert event.causation_id == "webhook:evt-1"
    assert event.correlation_id == response.headers["X-Request-ID"]
    assert event.payload["source_event_id"] == "evt-1"
    assert event.payload["possible_duplicate_of"] is None
    assert event.payload["references"]["shipments"] == ["SHIP-501"]
    assert await counts(sessions) == (1, 1, 1)


async def test_a_retry_minutes_later_with_a_fresh_signature_is_a_duplicate_not_a_401(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    first = await post(container, REPORT, "evt-1", at=T0)
    clock.advance(timedelta(minutes=10))  # beyond the 5-minute window of the first attempt
    retry = await post(container, REPORT, "evt-1", at=T0 + timedelta(minutes=10))

    assert retry.status_code == 202
    assert retry.json() == {"document_id": first.json()["document_id"], "status": "duplicate"}
    assert await counts(sessions) == (1, 1, 1)  # the retry left no trace


async def test_identical_content_under_a_new_event_id_is_accepted_and_flagged(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    first = await post(container, REPORT, "evt-1")
    again = await post(container, REPORT, "evt-2")  # e.g. "still delayed", a week later

    assert again.json()["status"] == "accepted"
    assert again.json()["document_id"] != first.json()["document_id"]
    assert await counts(sessions) == (2, 2, 2)
    payloads = await outbox_payloads(sessions)
    assert payloads[1]["possible_duplicate_of"] == first.json()["document_id"]


async def test_concurrent_copies_of_one_event_are_accepted_exactly_once(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    same_event = command("evt-race", TenantId.new())
    receipts = await asyncio.gather(
        *(container.disruption_intake.receive(same_event, MessageContext()) for _ in range(5))
    )

    statuses = [r.status for r in receipts]
    assert statuses.count(ReceiptStatus.ACCEPTED) == 1
    assert statuses.count(ReceiptStatus.DUPLICATE) == 4
    assert len({r.document_id for r in receipts}) == 1  # every duplicate points at the winner
    assert await counts(sessions) == (1, 1, 1)


async def test_different_event_ids_with_identical_payload_are_all_accepted(
    container: Container, sessions: async_sessionmaker[AsyncSession], clock: FrozenClock
) -> None:
    tenant = TenantId.new()
    receipts = []
    for n in range(3):  # three "still delayed" reports, a day apart
        receipts.append(
            await container.disruption_intake.receive(command(f"evt-{n}", tenant), MessageContext())
        )
        clock.advance(timedelta(days=1))

    assert [r.status for r in receipts] == [ReceiptStatus.ACCEPTED] * 3
    assert await counts(sessions) == (3, 3, 3)
    payloads = await outbox_payloads(sessions)
    first_document = str(receipts[0].document_id)
    assert [p["possible_duplicate_of"] for p in payloads] == [None, first_document, first_document]


async def test_a_rejected_signature_stores_nothing(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    transport = httpx.ASGITransport(app=create_app(container))
    headers = signed_headers(REPORT, event_id="evt-1", at=T0)
    async with httpx.AsyncClient(transport=transport, base_url="http://tracer") as client:
        response = await client.post(URL, content=REPORT + b" ", headers=headers)
    assert response.status_code == 401
    assert await counts(sessions) == (0, 0, 0)
