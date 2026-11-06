import pytest

from tests.fakes import FakeHealthCheck
from tests.support import make_settings
from tracer.platform.database.engine import create_engine
from tracer.platform.database.health import DatabaseHealthCheck
from tracer.shared.health import run_health_checks


async def test_results_keep_the_order_of_the_checks() -> None:
    results = await run_health_checks(
        [FakeHealthCheck("a"), FakeHealthCheck("b", healthy=False, detail="down")],
        timeout_seconds=1,
    )
    assert [(r.name, r.healthy, r.detail) for r in results] == [
        ("a", True, None),
        ("b", False, "down"),
    ]


async def test_a_slow_check_is_reported_as_timed_out() -> None:
    [result] = await run_health_checks(
        [FakeHealthCheck("slow", delay_seconds=1)], timeout_seconds=0.05
    )
    assert not result.healthy
    assert result.detail == "timed out"


async def test_a_check_that_raises_is_reported_not_propagated() -> None:
    [result] = await run_health_checks(
        [FakeHealthCheck("broken", error=RuntimeError("password=hunter2"))], timeout_seconds=1
    )
    assert not result.healthy
    assert result.detail == "check failed: RuntimeError"  # type only, never the message


@pytest.mark.filterwarnings("ignore::ResourceWarning")
async def test_unreachable_database_is_not_healthy() -> None:
    engine = create_engine(make_settings())
    try:
        result = await DatabaseHealthCheck(engine, expected_revision="0001").check()
    finally:
        await engine.dispose()
    assert result.name == "database"
    assert not result.healthy
    assert result.detail is not None
    assert "not-a-real-password" not in result.detail
