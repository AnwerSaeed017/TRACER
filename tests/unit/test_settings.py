from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support import make_settings
from tracer.platform.configuration.settings import Environment, LogFormat, Settings

PASSWORD = "s3cret-pw"


def test_missing_database_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_sync_driver_url_is_rejected_without_revealing_the_password() -> None:
    with pytest.raises(ValidationError) as caught:
        make_settings(database_url=f"postgresql://postgres:{PASSWORD}@localhost/tracer")
    assert "postgresql+asyncpg://" in str(caught.value)
    assert PASSWORD not in str(caught.value)


def test_database_url_is_masked_in_repr() -> None:
    settings = make_settings(database_url=f"postgresql+asyncpg://u:{PASSWORD}@localhost/tracer")
    assert PASSWORD not in repr(settings)
    assert PASSWORD in settings.database_url.get_secret_value()


@pytest.mark.parametrize("port", [0, 65536])
def test_http_port_out_of_range_is_rejected(port: int) -> None:
    with pytest.raises(ValidationError, match="http_port"):
        make_settings(http_port=port)


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValidationError, match="log_level"):
        make_settings(log_level="LOUD")


def test_values_come_from_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@db.internal/tracer")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LOG_FORMAT", "json")
    settings = Settings(_env_file=None)
    assert settings.environment is Environment.PRODUCTION
    assert settings.is_production
    assert settings.log_format is LogFormat.JSON


def test_env_file_with_byte_order_mark_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("HTTP_PORT", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nDATABASE_URL=postgresql+asyncpg://u:p@localhost/x\nHTTP_PORT=9001\n",
        encoding="utf-8-sig",
    )
    settings = Settings(_env_file=env_file)
    assert settings.http_port == 9001


def test_settings_are_immutable() -> None:
    settings = make_settings()
    with pytest.raises(ValidationError):
        settings.http_port = 1  # type: ignore[misc]
