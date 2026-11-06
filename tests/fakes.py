"""In-memory stand-ins for ports, used by unit tests instead of real infrastructure."""

import asyncio

from tracer.shared.health import HealthCheckResult


class FakeHealthCheck:
    def __init__(
        self,
        name: str = "fake",
        *,
        healthy: bool = True,
        detail: str | None = None,
        delay_seconds: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self._name = name
        self._healthy = healthy
        self._detail = detail
        self._delay_seconds = delay_seconds
        self._error = error

    @property
    def name(self) -> str:
        return self._name

    async def check(self) -> HealthCheckResult:
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if self._error is not None:
            raise self._error
        return HealthCheckResult(self._name, healthy=self._healthy, detail=self._detail)
