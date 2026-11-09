"""Helpers shared by tests. Not test cases themselves."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tracer.modules.ingestion.adapters.webhook_signature import sign
from tracer.modules.ingestion.application.intake import DisruptionReportCommand
from tracer.modules.ingestion.domain.report import ExternalReferences
from tracer.modules.ingestion.domain.source_document import Channel
from tracer.platform.configuration.settings import Settings
from tracer.shared.identifiers import TenantId

# A fixed "now" for domain tests: 5 November 2026, 09:00 UTC.
T0 = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"

WEBHOOK_SOURCE = "acme-supplier-portal"
# Deliberately low-entropy and obviously fake, so the secret scanner has nothing to flag.
WEBHOOK_SECRET = "test-webhook-secret-" + "x" * 32
WEBHOOK_TENANT = "6a3f1c2e-9a0b-4d7e-8f11-2b3c4d5e6f70"


def webhook_settings(**overrides: Any) -> Settings:
    """Settings with one configured webhook source."""
    sources = [{"name": WEBHOOK_SOURCE, "tenant_id": WEBHOOK_TENANT, "secrets": [WEBHOOK_SECRET]}]
    return make_settings(webhook_sources=sources, **overrides)


def signed_headers(
    body: bytes, *, event_id: str, at: datetime, secret: str = WEBHOOK_SECRET
) -> dict[str, str]:
    """Headers a correct sender attaches to ``body`` for an attempt sent at time ``at``."""
    timestamp = int(at.timestamp())
    return {
        "Content-Type": "application/json",
        "X-Tracer-Event-Id": event_id,
        "X-Tracer-Timestamp": str(timestamp),
        "X-Tracer-Signature": sign(secret.encode(), event_id, timestamp, body),
    }


def report_command(
    event_id: str,
    tenant: TenantId,
    *,
    kind: str = "shipment_delay",
    content: bytes = b'{"kind":"shipment_delay","summary":"SHIP-501 delayed by 7 days"}',
) -> DisruptionReportCommand:
    """A disruption report as the webhook router would hand it to the intake use case."""
    return DisruptionReportCommand(
        tenant_id=tenant,
        channel=Channel.WEBHOOK,
        source_name=WEBHOOK_SOURCE,
        source_event_id=event_id,
        media_type="application/json",
        content=content,
        kind=kind,
        summary="SHIP-501 delayed by 7 days",
        impact_start=None,
        impact_end=None,
        references=ExternalReferences(),
    )


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
