import httpx2 as httpx
import pytest
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import migrate
from tests.support import MIGRATIONS_DIR, make_settings
from tracer.bootstrap.composition_root import build_container
from tracer.entrypoints.http.app import create_app
from tracer.platform.database.health import DatabaseHealthCheck
from tracer.platform.database.migrations import head_revision

pytestmark = pytest.mark.integration


async def test_healthy_when_migrated_to_head(engine: AsyncEngine, migrated: None) -> None:
    result = await DatabaseHealthCheck(engine, head_revision(MIGRATIONS_DIR)).check()
    assert result.healthy
    assert result.detail == f"revision {head_revision(MIGRATIONS_DIR)}"


async def test_not_healthy_when_never_migrated(alembic_config: Config, engine: AsyncEngine) -> None:
    await migrate(alembic_config, "base")
    try:
        result = await DatabaseHealthCheck(engine, head_revision(MIGRATIONS_DIR)).check()
    finally:
        await migrate(alembic_config, "head")
    assert not result.healthy
    assert result.detail is not None
    assert "schema at revision none" in result.detail


async def test_not_healthy_when_code_expects_a_newer_revision(
    engine: AsyncEngine, migrated: None
) -> None:
    result = await DatabaseHealthCheck(engine, expected_revision="9999").check()
    assert not result.healthy
    assert result.detail is not None
    assert "code expects 9999" in result.detail


async def test_ready_endpoint_end_to_end(test_database_url: str, migrated: None) -> None:
    container = build_container(make_settings(database_url=test_database_url))
    app = create_app(container)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://tracer"
        ) as client:
            response = await client.get("/health/ready")
    finally:
        await container.aclose()
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


async def test_ready_endpoint_reports_unreachable_database() -> None:
    container = build_container(make_settings())  # points at a closed port
    app = create_app(container)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://tracer"
        ) as client:
            response = await client.get("/health/ready")
    finally:
        await container.aclose()
    assert response.status_code == 503
