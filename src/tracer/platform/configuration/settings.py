"""All configuration, read once, validated once.

Values come from environment variables, falling back to ``env/.env`` for local
development. A missing or invalid value stops the process at start-up with a clear message,
rather than failing later on the first request that happens to need it.
"""

from enum import StrEnum
from pathlib import Path
from typing import Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_WEBHOOK_SECRET_LENGTH: Final = 32

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


class WebhookSource(BaseModel):
    """A system allowed to send webhooks: which tenant it belongs to, and its signing secrets.

    The tenant comes from this configuration, never from the request: a sender cannot claim to
    be another tenant. Configured as JSON in WEBHOOK_SOURCES, e.g.
    [{"name": "acme-supplier-portal", "tenant_id": "<uuid>", "secrets": ["<32+ characters>"]}]

    ``secrets`` holds 1 to 3 active secrets. Rotation without downtime: add the new secret,
    let the sender switch, then remove the old one; during the overlap either is accepted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    tenant_id: UUID
    secrets: tuple[SecretStr, ...] = Field(min_length=1, max_length=3)

    @field_validator("secrets")
    @classmethod
    def _long_enough(cls, value: tuple[SecretStr, ...]) -> tuple[SecretStr, ...]:
        if any(len(s.get_secret_value()) < MIN_WEBHOOK_SECRET_LENGTH for s in value):
            raise ValueError(f"each secret must be at least {MIN_WEBHOOK_SECRET_LENGTH} characters")
        return value


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

    webhook_sources: tuple[WebhookSource, ...] = ()
    webhook_tolerance_seconds: int = Field(default=300, ge=30, le=900)
    webhook_max_body_bytes: int = Field(default=262_144, ge=1024, le=5_242_880)

    @field_validator("webhook_sources")
    @classmethod
    def _unique_source_names(cls, value: tuple[WebhookSource, ...]) -> tuple[WebhookSource, ...]:
        names = [source.name for source in value]
        if len(names) != len(set(names)):
            raise ValueError("webhook source names must be unique")
        return value

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
