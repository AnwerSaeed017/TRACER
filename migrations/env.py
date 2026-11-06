"""Alembic environment: how migrations connect to the database.

The URL comes from, in order:
1. ``config.attributes["database_url"]``: set by tests that migrate the test database;
2. ``Settings.database_url``: the application's own configuration.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import create_async_engine

from tracer.bootstrap.orm_models import metadata
from tracer.platform.configuration.settings import load_settings

config = context.config

# Use the ini file's logging only when run from the command line; when a test or the app
# calls Alembic programmatically, it keeps the caller's logging configuration.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if url:
        return str(url)
    return load_settings().database_url.get_secret_value()


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        compare_type=True,  # autogenerate notices column type changes, not only add/drop
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Render SQL without connecting: ``alembic upgrade head --sql``."""
    context.configure(
        url=_database_url(),
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_configure)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
