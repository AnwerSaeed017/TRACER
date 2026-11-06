import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import migrate
from tests.support import MIGRATIONS_DIR
from tracer.platform.database.migrations import current_revision, head_revision

pytestmark = pytest.mark.integration


async def test_full_downgrade_and_upgrade_round_trip(
    alembic_config: Config, engine: AsyncEngine
) -> None:
    await migrate(alembic_config, "head")
    await migrate(alembic_config, "base")
    assert await current_revision(engine) is None

    await migrate(alembic_config, "head")
    assert await current_revision(engine) == head_revision(MIGRATIONS_DIR)


async def test_models_and_migrations_agree(alembic_config: Config, migrated: None) -> None:
    # Fails if a model changed without a migration: autogenerate would have something to add.
    await asyncio.to_thread(command.check, alembic_config)


def test_history_has_a_single_head() -> None:
    assert head_revision(MIGRATIONS_DIR) is not None  # raises if the history has split
