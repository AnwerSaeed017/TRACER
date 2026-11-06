"""Read migration state: which revision the code expects, which one the database is at."""

from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncEngine


def head_revision(migrations_dir: Path) -> str | None:
    """The latest revision in the migrations folder. Raises if the history has split heads."""
    return ScriptDirectory(str(migrations_dir)).get_current_head()


def _current_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


async def current_revision(engine: AsyncEngine) -> str | None:
    """The revision the database is at, or None if it was never migrated."""
    async with engine.connect() as connection:
        return await connection.run_sync(_current_revision)
