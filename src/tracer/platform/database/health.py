"""Readiness check for PostgreSQL: reachable, and at exactly the revision the code expects."""

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from tracer.platform.database.migrations import current_revision
from tracer.shared.health import HealthCheckResult


class DatabaseHealthCheck:
    def __init__(self, engine: AsyncEngine, expected_revision: str | None) -> None:
        self._engine = engine
        self._expected_revision = expected_revision

    @property
    def name(self) -> str:
        return "database"

    async def check(self) -> HealthCheckResult:
        try:
            revision = await current_revision(self._engine)
        except (OSError, SQLAlchemyError) as exc:
            # Only the error type: messages can contain host names or user names.
            return HealthCheckResult(self.name, healthy=False, detail=type(exc).__name__)

        if revision != self._expected_revision:
            return HealthCheckResult(
                self.name,
                healthy=False,
                detail=(
                    f"schema at revision {revision or 'none'}, "
                    f"code expects {self._expected_revision or 'none'}"
                ),
            )
        return HealthCheckResult(self.name, healthy=True, detail=f"revision {revision}")
