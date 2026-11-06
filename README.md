# TRACER

**Temporal Risk Analysis, Causal Evaluation & Response**: a supply-chain decision
intelligence platform.

When a supplier disruption arrives, TRACER resolves which suppliers, shipments, products and
customer orders it touches, investigates likely causes against evidence, simulates recovery
options, verifies them against business rules, asks an authorised person to approve, executes
through the existing ERP/WMS and confirms the result.

> Status: **M0, engineering foundation.** No business features yet.

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

## Run

```bash
uv run tracer serve                      # http://127.0.0.1:8000/docs
```

- `GET /health/live`: the process is up.
- `GET /health/ready`: the database is reachable and migrated to the latest revision.

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
