"""Integration tests run against a real PostgreSQL test database.

They are skipped when TEST_DATABASE_URL is not set, and they refuse to run against any
database whose name does not end in ``_test``: these tests drop the whole schema, and a
typo in an environment variable must never be able to do that to real data.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.support import ALEMBIC_INI, make_settings
from tracer.platform.configuration.settings import ENV_FILE
from tracer.platform.database.engine import create_engine, create_session_factory

REQUIRED_SUFFIX = "_test"


class _TestEnvironment(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8-sig", extra="ignore"
    )
    test_database_url: SecretStr | None = None


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = _TestEnvironment().test_database_url
    if url is None:
        pytest.skip("TEST_DATABASE_URL is not set")
    value = url.get_secret_value()
    database = make_url(value).database or ""
    if not database.endswith(REQUIRED_SUFFIX):
        pytest.fail(f"refusing to run: test database name must end in {REQUIRED_SUFFIX!r}")
    return value


@pytest.fixture
def alembic_config(test_database_url: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["database_url"] = test_database_url
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
async def engine(test_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(make_settings(database_url=test_database_url))
    yield engine
    await engine.dispose()


async def migrate(config: Config, revision: str) -> None:
    """Run an Alembic command off the event loop (Alembic starts its own loop)."""
    if revision == "base":
        await asyncio.to_thread(command.downgrade, config, "base")
    else:
        await asyncio.to_thread(command.upgrade, config, revision)


@pytest.fixture
async def migrated(alembic_config: Config) -> None:
    await migrate(alembic_config, "head")


# Every table the application writes. Each test that uses `sessions` starts from empty tables.
APPLICATION_TABLES = (
    "outbox_events, processed_events, disruption_cases, inbox_messages, source_documents, "
    "ontology_relation_types, ontology_entity_types, ontology_packages"
)
# TRUNCATE is deliberately not blocked by the ontology immutability triggers (row-level
# UPDATE/DELETE only), which is what lets each test start from empty tables.


@pytest.fixture
async def sessions(engine: AsyncEngine, migrated: None) -> async_sessionmaker[AsyncSession]:
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {APPLICATION_TABLES}"))
    return create_session_factory(engine)
