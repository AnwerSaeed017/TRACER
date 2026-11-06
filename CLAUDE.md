# CLAUDE.md

Engineering rules for TRACER (Temporal Risk Analysis, Causal Evaluation & Response), a
supply-chain decision intelligence platform. Anyone changing the code (human or AI)
follows these. What the product does lives in `docs/`; why each part is built the way it is
lives in `decision_logs/`; this file is how the code is built.

## Working method

- Build one module at a time, as vertical slices. Do not start a module until the previous
  one meets its acceptance criteria. Order: M0 foundation, M1 domain, M2 ingestion,
  M3 knowledge graph, M4 retrieval, M5 reasoning, M6 decisioning, M7 orchestration,
  M8 governance and execution, M9 evaluation and observability, M10 product and deployment.
- Every component goes **Learn → Design → Test → Implement → Validate → Document**.
- Every part that is built gets an entry in `decision_logs/` (context, options considered,
  decision, consequences, how it was verified). Code without a decision log entry is not done.
- No speculative code. Create a file, package, interface or dependency only when something
  uses it now. Empty folders and placeholder modules are not progress.
- Never claim a number (latency, cache hit rate, accuracy) that was not measured.

## Layout

```
src/tracer/
  entrypoints/   how the outside world calls in: http/ (FastAPI), cli/, workers/ (later)
  bootstrap/     composition root: builds and wires every concrete object
  modules/       business capabilities (from M1), each: domain/ application/ adapters/
  platform/      reusable infrastructure: configuration, database, observability, ...
  shared/        tiny dependency-free kernel: clock, health contract, ids, ...
migrations/      Alembic revisions (0001_baseline.py, 0002_...)
tests/           unit/ integration/ architecture/ (+ workflow/ evaluation/ e2e/ later)
decision_logs/   one document per module: what was built, why, how to learn it (local, git-ignored)
docs/            product research and target architecture (local, git-ignored)
env/             .env with local settings and secrets (local, git-ignored)
```

## Dependency rules (enforced by import-linter in CI)

```
entrypoints -> bootstrap -> modules -> platform -> shared
```

- A layer may import only layers below it.
- Inside a module: `adapters -> application -> domain`. The domain imports nothing from
  FastAPI, SQLAlchemy, Alembic, LangGraph, an LLM SDK, an ERP/WMS SDK or `platform/`.
- Modules talk to each other through their `application` API or domain events, never by
  importing another module's internals.
- Add an import-linter contract whenever a new boundary appears.

## Design

- **Hexagonal architecture.** The application layer defines ports (`typing.Protocol`);
  adapters implement them; `bootstrap/composition_root.py` is the only place that picks a
  concrete class. Never construct a collaborator inside a class.
- **Interfaces at real boundaries only**: persistence, external systems, models, clock,
  anything a test needs to fake. A pure function does not get an interface.
- **Cohesive modules.** Closely related small classes may share a file; split when a file
  has two reasons to change. Files are snake_case, named for what they hold.
- **Domain model.** Value objects are immutable and validate themselves. Aggregates
  protect their invariants; state transitions are methods that refuse invalid moves.
  Business rules are testable without a database.
- **Patterns, used where they solve a problem:** Repository (per aggregate), Unit of Work,
  Strategy, Adapter, State Machine, Transactional Outbox, Idempotent Consumer, Saga,
  Specification, Circuit Breaker. Not used yet: microservices, event sourcing, CQRS
  everywhere, a repository per table.

## Persistence

- PostgreSQL 16, SQLAlchemy 2 async with asyncpg. Async end to end: no blocking I/O inside
  `async def`.
- **The application use case owns the transaction** through a Unit of Work. Repositories
  use the session they are given and never commit.
- Ordinary queries use SQLAlchemy. Explicit SQL only where it earns it (recursive graph
  traversal, temporal queries), kept in the adapter that needs it and covered by integration
  tests.
- **Models are the source of truth.** Change the model, then
  `uv run alembic revision --autogenerate -m "..." --rev-id NNNN`, then review the file.
  Never edit a migration that has been merged. `tests/integration` fails if models and
  migrations disagree.
- Every business table has `tenant_id`; every query filters on it.
- Indexes for every filter and foreign key, declared on the model. Constraint names come
  from the naming convention in `platform/database/base.py`.
- Never hold a database lock (or a transaction) across an LLM call or an external API call.
- Timestamps are `TIMESTAMPTZ` in UTC. Read time from the injected `Clock`, never
  `datetime.now()`.

## Knowledge graph (M3)

- Four core tables: `ontology_entity_types`, `ontology_relation_types`, `graph_nodes`,
  `graph_edges`. Node and edge attributes are JSONB validated against the ontology's JSON
  Schema; endpoint types are validated by the ontology service on write.
- Edges are bi-temporal: `valid_from/valid_to` (true in the world) and
  `recorded_from/recorded_to` (known to the system). **Never update an assertion in place**:
  close `recorded_to` and insert the new version.
- Assertion status: OBSERVED, INFERRED, VERIFIED, DISPUTED, RETRACTED. Model output enters as
  INFERRED and is promoted only by evidence or deterministic verification.
- Every assertion carries evidence (source references). N-ary facts are reified as nodes
  (ALLOCATION, CAUSAL_RULE), not hyperedges.
- Traversals have a depth limit, cycle protection, tenant and relation-type filters and a
  statement timeout.

## Events and execution

- Durable events use a transactional outbox written in the same transaction as the business
  change, dispatched by a worker. `LISTEN/NOTIFY` may wake workers but is never the queue.
- Every consumer is idempotent (dedupe by source id or idempotency key).
- External actions follow an explicit state machine
  (PROPOSED → VALIDATED → AWAITING_APPROVAL → APPROVED → EXECUTING →
  COMMITTED | FAILED | UNKNOWN → RECONCILING → COMMITTED | COMPENSATED) with idempotency
  keys and durable receipts. A timeout is UNKNOWN, never FAILED.
- Approval is bound to the exact plan version (hash). A changed plan is re-validated and
  re-approved.

## AI boundaries

- **The model proposes; code decides.** Arithmetic, constraints, deadlines, inventory
  conservation, authorization and policy are deterministic code. A safety rule is never
  only a prompt.
- Model calls go through one model gateway (provider abstraction, timeouts, retries with
  backoff, budgets, cost tracking). Provider and model come from settings.
- Structured output only: validate against a Pydantic schema; re-ask with the error a
  bounded number of times.
- Prompts are versioned modules (`SYSTEM`, `USER`, `VERSION`). Change the text, bump the
  version. Prompts state general rules, never wording copied from an evaluation case.
- Untrusted text (emails, documents, supplier messages) is data, never instructions. It is
  marked as evidence and cannot grant permissions.
- The judge model uses a different provider from the agent it grades unless a setting says
  otherwise.
- Unit and workflow tests use fake models only; real models run only in evaluations.
- Reasoning capabilities (abduction, induction, deduction, counterfactual) live outside
  LangGraph and return structured results with evidence ids, assumptions and uncertainty.
  LangGraph nodes are thin: read state, call one application service, return changed fields.

## HTTP API

- Routers are thin: parse, call one application use case, map the result. No business
  logic, SQL or domain try/except in routers.
- Errors are RFC 9457 Problem Details (`application/problem+json`) with the request id.
  Domain errors map to statuses in one place. Unexpected errors return a generic 500.
- Validation lives in Pydantic schemas at the edge and in domain value objects. Never echo
  rejected input or internal details back to the client.
- Server-managed fields (ids, timestamps, tenant, status) are never taken from client input.
- Every list endpoint is paginated with a default and a maximum page size.
- `/health/live` checks nothing external; `/health/ready` checks every dependency.

## Configuration, security, logging

- All settings in `platform/configuration/settings.py` (pydantic-settings), from the
  environment with `env/.env` as the local fallback. Invalid configuration stops start-up.
  No hardcoded URLs, keys or model names.
- Secrets are `SecretStr`, are never logged, and never appear in error messages. Only
  `env/.env.example` is committed. gitleaks runs in pre-commit and CI.
- Log to stdout with `logging`, configured once. Use `extra={...}` for fields. Every line
  inside a request carries the request id. Do not log secrets, tokens, query strings or
  raw documents.
- Named constants live next to their single user; move a constant to a shared module only
  when a second user appears. Values that differ per environment are settings.

## Testing

| Kind | Where | Rule |
|---|---|---|
| Unit | `tests/unit/` | No network, no database; fakes from `tests/fakes.py` |
| Integration | `tests/integration/` | Real PostgreSQL via `TEST_DATABASE_URL` (name must end `_test`) |
| Architecture | `tests/architecture/` | Import contracts hold |
| Workflow, evaluation, contract, security, e2e | added with the modules that need them | |

- Test behaviour and failure paths, not implementation details. Every bug fix starts with a
  failing test.
- Prove recovery deliberately: timeouts, duplicates, crashes after a checkpoint, conflicting
  updates.

## Definition of done

All of these pass locally and in CI:

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run lint-imports
uv run alembic upgrade head
uv run pytest
```

And: each new class sits in one layer and depends only on what that layer allows; every
new dependency is wired in the composition root; failure paths return Problem Details with
the right status; each model change has a reviewed migration; the decision log is updated.

## Environment notes

Windows 11 development machine; PostgreSQL 16 installed locally (`D:\postgre sql\bin`);
databases `tracer` and `tracer_test`. Use `uv` for everything Python. Keep caches, data and
large files on D:. pgvector is not installed yet (needed from M4).
