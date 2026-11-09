# TRACER

**Temporal Risk Analysis, Causal Evaluation & Response**: a supply-chain decision
intelligence platform.

When a supplier disruption arrives, TRACER resolves which suppliers, shipments, products and
customer orders it touches, investigates likely causes against evidence, simulates recovery
options, verifies them against business rules, asks an authorised person to approve, executes
through the existing ERP/WMS and confirms the result.

> Status: **M2, integration and ingestion (in progress).** Signed webhook intake with evidence
> storage, delivery deduplication and a transactional outbox. Domain model (M1) in place.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- PostgreSQL 16 with two databases: `tracer` and `tracer_test`

## Setup

```bash
uv sync                                  # create .venv and install locked dependencies
# create env/.env (git-ignored) with at least DATABASE_URL, see Configuration
uv run alembic upgrade head              # migrate the database
uv run pre-commit install                # run the quality checks before every commit
```

## Configuration

Settings are read from environment variables, falling back to `env/.env` (never committed).
Invalid values stop start-up with a message naming the setting.

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | required | `postgresql+asyncpg://user:password@host:5432/tracer` |
| `TEST_DATABASE_URL` | unset | integration tests only; database name must end in `_test` |
| `ENVIRONMENT` | `development` | `development`, `test` or `production` (hides `/docs`) |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_FORMAT` | `json` | `json` or `console` |
| `HTTP_HOST` / `HTTP_PORT` | `127.0.0.1` / `8000` | |
| `DATABASE_POOL_SIZE` | `5` | 1 to 50 |
| `DATABASE_CONNECT_TIMEOUT_SECONDS` | `5` | |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `5000` | enforced by PostgreSQL on every connection |
| `READINESS_TIMEOUT_SECONDS` | `3` | per health check |
| `OUTBOX_BATCH_SIZE` | `50` | events claimed per worker pass |
| `OUTBOX_LEASE_SECONDS` | `30` | a claimed event is reclaimable after this if its worker died |
| `OUTBOX_MAX_ATTEMPTS` | `10` | then the event is dead-lettered |
| `OUTBOX_BACKOFF_BASE_SECONDS` / `_MAX_SECONDS` | `1` / `300` | exponential backoff with full jitter |
| `OUTBOX_POLL_INTERVAL_SECONDS` | `1` | idle wait between passes |
| `WEBHOOK_SOURCES` | `[]` | JSON list: `[{"name": "acme-portal", "tenant_id": "<uuid>", "secrets": ["<32+ chars>"]}]` |
| `WEBHOOK_TOLERANCE_SECONDS` | `300` | how old a signed timestamp may be (replay window) |
| `WEBHOOK_MAX_BODY_BYTES` | `262144` | larger bodies get 413 |

## Run

```bash
uv run tracer serve                      # http://127.0.0.1:8000/docs
uv run tracer worker                     # deliver outbox events (run one or several)
uv run tracer outbox replay <event-id>   # retry a dead-lettered event after fixing its cause
```

The API stores events in a transactional outbox; the worker delivers them to consumers
(at least once, in order per aggregate, with retries and dead-lettering). A received
disruption report becomes a `DisruptionCase` once a worker has delivered it.

- `GET /health/live`: the process is up.
- `GET /health/ready`: the database is reachable and migrated to the latest revision.
- `POST /webhooks/{source}/disruption-reports`: a signed disruption report (see below).

### Sending a webhook

Each request carries three headers:

| Header | Value | On a retry |
|---|---|---|
| `X-Tracer-Event-Id` | your id for this event (1–64 of `A-Za-z0-9_:-`, no `.`) | **the same** |
| `X-Tracer-Timestamp` | Unix time in seconds when this attempt is sent | **new** |
| `X-Tracer-Signature` | `sha256=` + hex HMAC-SHA256 of `"<event id>.<timestamp>." + <raw body>` | **recomputed** |

Sign the exact bytes you send. Timestamps more than 5 minutes from TRACER's clock (either way)
are refused, so always re-sign when you retry. A source may have up to three active secrets,
which allows rotation without downtime.

Body:

```json
{
  "kind": "shipment_delay",
  "summary": "SHIP-501 delayed by 7 days",
  "impact_start": "2026-11-10T00:00:00Z",
  "impact_end": "2026-11-17T00:00:00Z",
  "references": {"suppliers": ["SUP-001"], "shipments": ["SHIP-501"]}
}
```

`kind` is one of `shipment_delay`, `supplier_failure`, `port_congestion`,
`capacity_shortage`, `quality_issue`, `other`. Unknown fields are rejected.
The answer is `202` once the report is durably stored, with `status` `accepted` or
`duplicate` (this event id was already received): stop retrying on either. Identical content
under a *new* event id is accepted as a new event. `401` means the source, event id, timestamp
or signature was not accepted.

## Check

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run pytest                            # integration tests need TEST_DATABASE_URL
```

## Documentation

- [CLAUDE.md](CLAUDE.md): engineering rules for anyone (human or AI) changing the code.
