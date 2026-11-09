"""The outbox dispatcher: delivers committed events to in-process handlers, at least once.

One pass (``dispatch_once``):

1. **Claim** a batch in a short transaction: due ``pending`` events, plus ``processing`` events
   whose lease expired (their worker died). ``FOR UPDATE SKIP LOCKED`` lets several workers run
   side by side without claiming the same row. Claimed rows become ``processing`` with this
   worker's id and a lease; ``attempts`` goes up *now*, so an event that kills its worker still
   runs out of attempts. Commit, so no transaction is held while handlers work.
2. **Deliver** each event to every handler subscribed to its type.
3. **Settle**: success -> ``published``; transient failure -> back to ``pending`` with
   exponential backoff and jitter; permanent failure or attempts exhausted -> ``dead``.
   Settling only touches the row if this delivery's ``claim_token`` is still the current one:
   a fresh token is issued on every claim, so a delivery whose lease expired cannot overwrite
   the result of whoever claimed the event next, even when that is the same worker.

**Ordering.** An event is claimed only when every earlier event of the same aggregate is
published. At most one event per aggregate is in flight, and consumers see an aggregate's
events in order. A dead event therefore holds back the later events of its aggregate until
someone replays it: deliberately, because delivering them out of order could be wrong.

**Guarantee.** At-least-once delivery. A crash between a handler's commit and the
acknowledgement redelivers the event; handlers must be idempotent (see ``consumer.py``).
"""

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, and_, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from tracer.platform.messaging.envelope import EventEnvelope
from tracer.platform.messaging.models import OutboxEventRecord, OutboxStatus
from tracer.shared.clock import Clock
from tracer.shared.errors import ConcurrencyConflictError, DomainError

logger = logging.getLogger("tracer.outbox")

MAX_ERROR_LENGTH: Final = 2000


class PermanentDeliveryError(Exception):
    """Retrying cannot help (unknown schema version, malformed payload). Dead-letter at once."""


class EventHandler(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def event_types(self) -> frozenset[str]: ...

    async def handle(self, envelope: EventEnvelope) -> None: ...


@dataclass(frozen=True, slots=True)
class DispatchPolicy:
    batch_size: int
    lease: timedelta
    max_attempts: int
    backoff_base: timedelta
    backoff_max: timedelta


class Outcome(StrEnum):
    PUBLISHED = "published"
    RETRY = "retry"
    DEAD = "dead"
    LEASE_LOST = "lease_lost"  # another worker took the event over; its result stands


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    envelope: EventEnvelope
    attempt: int  # 1 for the first delivery
    claim_token: UUID  # proves this delivery still owns the claim when it settles


def is_permanent(error: BaseException) -> bool:
    """Will retrying the same event ever succeed?"""
    if isinstance(error, PermanentDeliveryError):
        return True
    if isinstance(error, ConcurrencyConflictError):
        return False  # someone else changed the aggregate; a retry reloads it
    return isinstance(error, DomainError)  # a business rule refused it: it will refuse again


def backoff_delay(
    attempt: int,
    *,
    base: timedelta,
    cap: timedelta,
    jitter: Callable[[float, float], float] = random.uniform,
) -> timedelta:
    """Exponential backoff with full jitter: a random delay in [0, min(cap, base * 2^(n-1))].

    The randomness spreads retries out, so many events failing together (a database blip) do
    not all retry at the same instant and fail together again.
    """
    ceiling = min(cap.total_seconds(), base.total_seconds() * 2 ** (attempt - 1))
    return timedelta(seconds=jitter(0.0, ceiling))


class OutboxDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        handlers: Sequence[EventHandler],
        policy: DispatchPolicy,
        worker_id: str,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._handlers = tuple(handlers)
        self._policy = policy
        self._worker_id = worker_id
        self._jitter = jitter

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch_once(self) -> int:
        """Claim, deliver and settle one batch. Returns how many events were claimed."""
        claimed = await self.claim_batch()
        for event in claimed:
            await self.deliver(event)
        return len(claimed)

    async def run(self, stop: asyncio.Event, poll_interval: timedelta) -> None:
        logger.info("outbox worker started", extra={"worker": self._worker_id})
        while not stop.is_set():
            if await self.dispatch_once() == 0:
                # Idle: sleep until the next poll, but wake at once if asked to stop.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), poll_interval.total_seconds())
        logger.info("outbox worker stopped", extra={"worker": self._worker_id})

    # ---------------------------------------------------------------- claim

    async def claim_batch(self) -> list[ClaimedEvent]:
        now = self._clock.now()
        event = OutboxEventRecord
        earlier = aliased(OutboxEventRecord)
        due = or_(
            and_(event.status == OutboxStatus.PENDING.value, event.next_attempt_at <= now),
            and_(event.status == OutboxStatus.PROCESSING.value, event.lease_expires_at < now),
        )
        earlier_unpublished = exists().where(
            earlier.aggregate_type == event.aggregate_type,
            earlier.aggregate_id == event.aggregate_id,
            earlier.aggregate_version < event.aggregate_version,
            earlier.status != OutboxStatus.PUBLISHED.value,
        )
        async with self._session_factory() as session, session.begin():
            rows = (
                (
                    await session.execute(
                        select(event)
                        .where(due, ~earlier_unpublished)
                        .order_by(event.next_attempt_at, event.recorded_at)
                        .limit(self._policy.batch_size)
                        .with_for_update(skip_locked=True, of=event)
                    )
                )
                .scalars()
                .all()
            )
            claimed: list[ClaimedEvent] = []
            for row in rows:
                token = uuid4()  # a fresh fencing token for this claim only
                row.status = OutboxStatus.PROCESSING.value
                row.claimed_by = self._worker_id
                row.claim_token = token
                row.lease_expires_at = now + self._policy.lease
                row.attempts += 1
                claimed.append(ClaimedEvent(_envelope(row), row.attempts, token))
        return claimed

    # ---------------------------------------------------------------- deliver

    async def deliver(self, claimed: ClaimedEvent) -> Outcome:
        envelope = claimed.envelope
        try:
            for handler in self._handlers:
                if envelope.event_type in handler.event_types:
                    await handler.handle(envelope)
        except Exception as error:  # every failure is classified, none escapes the worker
            return await self._settle_failure(claimed, error)
        return await self._settle(
            claimed,
            status=OutboxStatus.PUBLISHED,
            outcome=Outcome.PUBLISHED,
            published_at=self._clock.now(),
            last_error=None,
        )

    async def _settle_failure(self, claimed: ClaimedEvent, error: Exception) -> Outcome:
        description = f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]
        if is_permanent(error) or claimed.attempt >= self._policy.max_attempts:
            logger.error(
                "outbox event dead-lettered",
                extra=_log_fields(claimed, error=description),
                exc_info=error,
            )
            return await self._settle(
                claimed, status=OutboxStatus.DEAD, outcome=Outcome.DEAD, last_error=description
            )
        delay = backoff_delay(
            claimed.attempt,
            base=self._policy.backoff_base,
            cap=self._policy.backoff_max,
            jitter=self._jitter,
        )
        logger.warning(
            "outbox delivery failed; will retry",
            extra=_log_fields(
                claimed, error=description, retry_in_s=round(delay.total_seconds(), 2)
            ),
        )
        return await self._settle(
            claimed,
            status=OutboxStatus.PENDING,
            outcome=Outcome.RETRY,
            next_attempt_at=self._clock.now() + delay,
            last_error=description,
        )

    async def _settle(
        self, claimed: ClaimedEvent, *, status: OutboxStatus, outcome: Outcome, **values: Any
    ) -> Outcome:
        async with self._session_factory() as session, session.begin():
            result = await session.execute(
                update(OutboxEventRecord)
                .where(
                    OutboxEventRecord.event_id == claimed.envelope.event_id,
                    OutboxEventRecord.status == OutboxStatus.PROCESSING.value,
                    OutboxEventRecord.claim_token == claimed.claim_token,  # fencing
                )
                .values(
                    status=status.value,
                    claimed_by=None,
                    claim_token=None,
                    lease_expires_at=None,
                    **values,
                )
            )
        if cast(CursorResult[Any], result).rowcount != 1:
            logger.warning("outbox lease lost before settling", extra=_log_fields(claimed))
            return Outcome.LEASE_LOST
        if outcome is Outcome.PUBLISHED:
            logger.debug("outbox event published", extra=_log_fields(claimed))
        return outcome

    # ---------------------------------------------------------------- operations

    async def replay_dead(self, event_id: UUID, *, actor: str, reason: str) -> bool:
        """Give a dead event a fresh start after its cause was fixed. False if it is not dead.

        The payload is never changed, and the event goes through normal validation and
        idempotency again. ``last_error`` is kept until the next attempt overwrites it; who
        replayed it, why, and its previous attempts and error are written to the audit log.
        """
        if not actor.strip() or not reason.strip():
            raise ValueError("a replay needs an actor and a reason")
        async with self._session_factory() as session, session.begin():
            previous = await session.scalar(
                select(OutboxEventRecord)
                .where(
                    OutboxEventRecord.event_id == event_id,
                    OutboxEventRecord.status == OutboxStatus.DEAD.value,
                )
                .with_for_update()
            )
            if previous is None:
                return False
            logger.warning(
                "dead outbox event replayed",
                extra={
                    "event_id": str(event_id),
                    "event_type": previous.event_type,
                    "actor": actor,
                    "reason": reason,
                    "previous_attempts": previous.attempts,
                    "previous_error": previous.last_error,
                },
            )
            previous.status = OutboxStatus.PENDING.value
            previous.attempts = 0
            previous.next_attempt_at = self._clock.now()
        return True


def _envelope(row: OutboxEventRecord) -> EventEnvelope:
    return EventEnvelope(
        event_id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        tenant_id=row.tenant_id,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        occurred_at=row.occurred_at,
        payload=row.payload,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
    )


def _log_fields(claimed: ClaimedEvent, **extra: object) -> dict[str, object]:
    envelope = claimed.envelope
    return {
        "event_id": str(envelope.event_id),
        "event_type": envelope.event_type,
        "attempt": claimed.attempt,
        "correlation_id": envelope.correlation_id,
        **extra,
    }
