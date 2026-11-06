"""Helpers shared by tests. Not test cases themselves."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracer.platform.configuration.settings import Settings

# A fixed "now" for domain tests: 5 November 2026, 09:00 UTC.
T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

# Port 1 on loopback: nothing listens there, so connecting fails fast and deterministically.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://tracer:not-a-real-password@127.0.0.1:1/tracer"


def make_settings(**overrides: Any) -> Settings:
    """Settings for tests. Never reads env/.env: a developer's local file cannot change results."""
    values: dict[str, Any] = {
        "database_url": UNREACHABLE_DATABASE_URL,
        "environment": "test",
        "log_format": "console",
        "database_connect_timeout_seconds": 2.0,
        "migrations_dir": MIGRATIONS_DIR,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)
