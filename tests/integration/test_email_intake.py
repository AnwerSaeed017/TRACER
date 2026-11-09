"""Email intake against real PostgreSQL, through to a DisruptionCase."""

from collections.abc import AsyncIterator

import httpx2 as httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.emails import raw_email
from tests.support import T0, WEBHOOK_SOURCE, signed_headers, webhook_settings
from tracer.bootstrap.composition_root import Container, build_container
from tracer.entrypoints.http.app import create_app
from tracer.modules.ingestion.adapters.persistence.models import (
    InboxMessageRecord,
    SourceDocumentRecord,
)
from tracer.modules.supply_chain.adapters.persistence.models import DisruptionCaseRecord
from tracer.platform.messaging.models import OutboxEventRecord
from tracer.shared.clock import FrozenClock

pytestmark = pytest.mark.integration

URL = f"/webhooks/{WEBHOOK_SOURCE}/emails"


@pytest.fixture
async def container(
    test_database_url: str, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Container]:
    container = build_container(
        webhook_settings(database_url=test_database_url), clock=FrozenClock(T0)
    )
    yield container
    await container.aclose()


async def send(container: Container, content: bytes, event_id: str) -> httpx.Response:
    headers = signed_headers(content, event_id=event_id, at=T0) | {"Content-Type": "message/rfc822"}
    transport = httpx.ASGITransport(app=create_app(container))
    async with httpx.AsyncClient(transport=transport, base_url="http://tracer") as client:
        return await client.post(URL, content=content, headers=headers)


async def rows(sessions: async_sessionmaker[AsyncSession], model: type) -> list:  # type: ignore[type-arg]
    async with sessions() as session:
        return list((await session.execute(select(model))).scalars())


async def test_a_disruption_email_becomes_a_case_with_the_raw_email_as_evidence(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    content = raw_email(
        subject="Karachi port closed for 48 hours",
        body="All vessels held. SHIP-501 and SHIP-502 affected. Supplier SUP-001.",
    )
    response = await send(container, content, "mail-1")
    assert response.status_code == 202
    assert response.json()["status"] == "accepted"

    [document] = await rows(sessions, SourceDocumentRecord)
    assert document.content == content  # the whole MIME message, byte for byte
    assert document.media_type == "message/rfc822"
    assert document.channel == "email"
    [event] = await rows(sessions, OutboxEventRecord)
    assert event.payload["kind"] == "port_congestion"
    assert event.payload["channel"] == "email"
    assert event.payload["references"]["shipments"] == ["SHIP-501", "SHIP-502"]

    while await container.outbox_dispatcher.dispatch_once():
        pass
    [case] = await rows(sessions, DisruptionCaseRecord)
    assert case.kind == "port_congestion"
    assert case.summary == "Karachi port closed for 48 hours"
    assert [str(e) for e in case.evidence_ids] == [str(document.id)]


async def test_an_unrecognised_email_is_kept_as_evidence_without_an_event(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    content = raw_email(subject="Holiday opening hours", body="We close at 3pm on Friday.")
    response = await send(container, content, "mail-2")

    assert response.status_code == 202
    assert response.json()["status"] == "unrecognised"
    assert len(await rows(sessions, SourceDocumentRecord)) == 1  # kept for a better extractor
    assert len(await rows(sessions, InboxMessageRecord)) == 1  # event id remembered
    assert await rows(sessions, OutboxEventRecord) == []  # nothing announced
    assert await container.outbox_dispatcher.dispatch_once() == 0
    assert await rows(sessions, DisruptionCaseRecord) == []


async def test_a_redelivered_email_is_a_duplicate(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    content = raw_email()
    first = await send(container, content, "mail-3")
    again = await send(container, content, "mail-3")

    assert again.json() == {"document_id": first.json()["document_id"], "status": "duplicate"}
    assert len(await rows(sessions, SourceDocumentRecord)) == 1
    assert len(await rows(sessions, OutboxEventRecord)) == 1


async def test_bytes_that_are_not_an_email_are_422_and_store_nothing(
    container: Container, sessions: async_sessionmaker[AsyncSession]
) -> None:
    response = await send(container, b"just some bytes without headers", "mail-4")
    assert response.status_code == 422
    assert await rows(sessions, SourceDocumentRecord) == []
