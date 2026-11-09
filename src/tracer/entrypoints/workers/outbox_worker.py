"""The outbox worker process: ``tracer worker``.

Run one or several; ``FOR UPDATE SKIP LOCKED`` keeps them from claiming the same event, and
leases let a surviving worker pick up what a dead one had claimed. Stop with Ctrl+C: the
current batch finishes, then the loop exits. A hard kill is also safe; that is what the
leases are for.
"""

import asyncio
import logging
from datetime import timedelta
from uuid import UUID

from tracer.bootstrap.composition_root import build_container
from tracer.platform.configuration.settings import Settings

logger = logging.getLogger("tracer.outbox")


async def run_worker(settings: Settings) -> None:
    container = build_container(settings)
    stop = asyncio.Event()
    try:
        await container.outbox_dispatcher.run(
            stop, timedelta(seconds=settings.outbox_poll_interval_seconds)
        )
    except asyncio.CancelledError:  # Ctrl+C: asyncio.run cancels the main task
        stop.set()
    finally:
        await container.aclose()


async def replay_dead_event(settings: Settings, event_id: UUID, *, actor: str, reason: str) -> bool:
    container = build_container(settings)
    try:
        return await container.outbox_dispatcher.replay_dead(event_id, actor=actor, reason=reason)
    finally:
        await container.aclose()
