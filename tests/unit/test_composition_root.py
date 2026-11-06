from tests.support import make_settings
from tracer.bootstrap.composition_root import build_container
from tracer.shared.clock import SystemClock


async def test_container_wires_the_database_readiness_check() -> None:
    container = build_container(make_settings())
    try:
        assert [check.name for check in container.readiness_checks] == ["database"]
        assert isinstance(container.clock, SystemClock)
    finally:
        await container.aclose()  # safe even though no connection was ever opened
