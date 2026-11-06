"""The health-check contract and the logic that runs a set of checks.

A health check answers one question: "can this process serve traffic that needs X?".
Each dependency (database now; cache, broker and model gateway later) gets its own class
implementing ``HealthCheck``. Adding one is a new class registered in the composition root;
nothing that runs checks has to change.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HealthCheckResult:
    name: str
    healthy: bool
    detail: str | None = None


class HealthCheck(Protocol):
    @property
    def name(self) -> str: ...

    async def check(self) -> HealthCheckResult: ...


async def run_health_checks(
    checks: Sequence[HealthCheck], timeout_seconds: float
) -> list[HealthCheckResult]:
    """Run every check concurrently. A slow or broken check is reported unhealthy, never raised."""
    return list(await asyncio.gather(*(_run_one(check, timeout_seconds) for check in checks)))


async def _run_one(check: HealthCheck, timeout_seconds: float) -> HealthCheckResult:
    try:
        async with asyncio.timeout(timeout_seconds):
            return await check.check()
    except TimeoutError:
        return HealthCheckResult(check.name, healthy=False, detail="timed out")
    except Exception as exc:
        logger.warning("health check %s raised", check.name, exc_info=True)
        return HealthCheckResult(
            check.name, healthy=False, detail=f"check failed: {type(exc).__name__}"
        )
