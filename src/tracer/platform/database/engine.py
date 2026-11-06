"""The async engine (a connection pool) and the session factory built on it.

Creating an engine does not connect; the first connection is opened on first use. That is
why the application can start, report "not ready", and recover when the database comes
back, instead of crashing at import time.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tracer.platform.configuration.settings import Settings

APPLICATION_NAME = "tracer"


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_size,
        pool_pre_ping=True,  # replace connections the server closed while they sat idle
        connect_args={
            "timeout": settings.database_connect_timeout_seconds,
            "server_settings": {
                "application_name": APPLICATION_NAME,  # visible in pg_stat_activity
                "statement_timeout": str(settings.database_statement_timeout_ms),
                "timezone": "UTC",
            },
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: after commit, objects keep their loaded values instead of
    # triggering a lazy reload, which under asyncio would be an implicit, failing I/O call.
    return async_sessionmaker(engine, expire_on_commit=False)
