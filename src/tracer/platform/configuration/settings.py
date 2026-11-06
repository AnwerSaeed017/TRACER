"""All configuration, read once, validated once.

Values come from environment variables, falling back to ``env/.env`` for local
development. A missing or invalid value stops the process at start-up with a clear message,
rather than failing later on the first request that happens to need it.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path("env/.env")
ASYNC_POSTGRES_SCHEME = "postgresql+asyncpg://"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8-sig",  # tolerate a byte-order mark written by Windows tools
        extra="ignore",  # env/.env also holds settings for later modules
        frozen=True,
        hide_input_in_errors=True,  # a bad DATABASE_URL must not print its password
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.JSON

    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8000, ge=1, le=65535)

    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    database_statement_timeout_ms: int = Field(default=5000, ge=100, le=600_000)

    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    migrations_dir: Path = Path("migrations")

    @field_validator("database_url")
    @classmethod
    def _require_async_postgres(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(ASYNC_POSTGRES_SCHEME):
            raise ValueError(f"must start with {ASYNC_POSTGRES_SCHEME}")
        return value

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


def load_settings() -> Settings:
    """Read settings from the environment and env/.env. Raises ``ValidationError`` if invalid."""
    return Settings()  # required fields come from the environment
