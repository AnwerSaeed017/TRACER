import pytest

from tests.support import T0
from tracer.shared.clock import FrozenClock


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)
