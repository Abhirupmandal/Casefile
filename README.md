# CASEFILE

**AI-powered insurance claim adjudication with a supervisor–worker agent architecture**

[![CI](https://github.com/Abhirupmandal/Casefile/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Abhirupmandal/Casefile/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Types: mypy strict](https://img.shields.io/badge/types-mypy%20strict-informational.svg)](pyproject.toml)

CASEFILE runs an insurance claim end to end: documents are parsed, evidence is
gathered through typed read-only tools, a reviewer recommends a decision, and a
human approval gate has the final say. Every hop between agents is a Pydantic
contract, every workflow is budgeted across tokens, cost, steps, time and rework
cycles, and every state transition is checkpointed so a run can be replayed
deterministically.

---

## What it gives you

| Capability | Where | Notes |
|---|---|---|
| Supervisor–worker agents | `src/casefile/agents/` | Extractor, Investigator, Reviewer; routing in `SupervisorAgent` |
| Validation pipeline | `src/casefile/agents/base.py` | Provider JSON → Pydantic → domain consistency, bounded retries |
| Typed tool registry | `src/casefile/tools/` | Six read-only tools, per-agent authorization, cache + rate limits |
| Workflow engine | `src/casefile/workflow/` | State machine, lifecycle hooks, batch orchestration, explainability |
| Budget enforcement | `src/casefile/budget/` | Pre-flight checks; terminal states on exhaustion |
| Checkpoint & replay | `src/casefile/checkpoint/` | Resume, verify-mode replay, divergence reporting |
| Human approval + counter-offers | `src/casefile/approval/` | Approval queue, negotiation lifecycle |
| Fraud & multilingual intake | `src/casefile/fraud/`, `src/casefile/translation/` | Signal scoring, translation client protocol |
| REST API + Operations Console | `src/casefile/api/` | 13 routers, versioned `/api/v1`, OpenAPI at `/docs` |
| Observability | `src/casefile/observability/` | OTel spans, metrics, structured logs |
| Evaluation harness | `src/casefile/evaluation/` | 30-case dataset plus G01–G15 golden scenarios |

Tests never touch a real LLM: the suite runs on `DeterministicProvider`, and the
LangChain adapter is only constructed when a provider key is configured.

---

## Architecture

```
                        ┌──────────────┐
                        │  Supervisor  │
                        │ orchestration│
                        │   + budgets  │
                        └──────┬───────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌──────────────┐       ┌──────────────┐       ┌──────────────┐
│  Extractor   │──────▶│ Investigator │──────▶│   Reviewer   │
│ parse claim  │       │ gather proof │       │ recommend    │
└──────────────┘       └──────────────┘       └──────┬───────┘
                                                      │
                              ┌───────────────────────┼──────────┐
                              ▼                       ▼          ▼
                        APPROVE                  REWORK      REJECT
                              │                       │          │
                              ▼                       └─▶ back   ▼
                      HUMAN_APPROVAL                    to      REJECTED
                              │                    Investigator
                     ┌────────┴────────┐
                     ▼                 ▼
                 APPROVED           REJECTED
```

- **State machine**: 14 states, 8 terminal states (`docs/state-machine.md`)
- **Orchestration**: LangGraph (`docs/adr/ADR-001-langgraph-orchestration.md`)
- **Persistence**: SQLite as system of record — checkpoints, event log, budget state
- **Cache / rate limiting**: Redis (optional in tests and local runs)
- **Observability**: OpenTelemetry traces, metrics and structured logs

---

## Quick start

**Prerequisites**: Python 3.11+, Poetry 2.4+, Docker (optional, for Redis and Jaeger).

```bash
git clone https://github.com/Abhirupmandal/Casefile.git
cd Casefile

poetry install
cp .env.example .env          # add API keys only if you want live provider calls
poetry run alembic upgrade head

poetry run casefile healthcheck
poetry run casefile serve --reload   # API on http://127.0.0.1:8000, console at /console
```

Redis and Jaeger are optional for local work:

```bash
docker compose up -d redis jaeger   # Jaeger UI: http://localhost:16686
```

---

## CLI

| Command | Purpose |
|---|---|
| `casefile version` | Version and implementation status |
| `casefile healthcheck --env test` | SQLite / Redis / tracer health with per-service latency |
| `casefile serve --host --port --reload` | REST API + Operations Console |
| `casefile evaluate` | Run the 30-case evaluation dataset, write JSON + Markdown reports |
| `casefile evaluate --golden` | Run the G01–G15 golden scenarios |
| `casefile evaluate --scenario CASE-004 -s G03` | Run selected scenarios |
| `casefile process-claim` / `casefile run-evaluation` | Legacy placeholders; use `serve` + `/api/v1/claims` and `evaluate` |

---

## HTTP API

Start with `casefile serve`, then everything is discoverable at `/docs`.
Routers are mounted under `/api/v1`:

```
POST /api/v1/claims                              submit a claim
GET  /api/v1/claims/{claim_id}/explain           claim decision explanation
POST /api/v1/claims/batch                        batch (catastrophe) intake
GET  /api/v1/claims/batch/{batch_id}/...         status / claims / disaster
POST /api/v1/claims/batch/{batch_id}/retry       retry failed claims
GET,POST /api/v1/claims/{claim_id}/counter-offers list / open
POST /api/v1/claims/{claim_id}/counter-offers/{offer_id}/... respond / withdraw / settle
GET  /api/v1/workflows                           list workflow runs
GET  /api/v1/workflows/{workflow_run_id}         status + budget summary
GET  /api/v1/workflows/{workflow_run_id}/history lifecycle event log
GET  /api/v1/workflows/{workflow_run_id}/explain decision explanation
GET  /api/v1/approvals                           pending human approvals
POST /api/v1/approvals/{approval_id}/decision    approve / reject
GET  /api/v1/workflows/{workflow_run_id}/checkpoints checkpoint metadata
POST /api/v1/workflows/{workflow_run_id}/replay  deterministic replay
GET  /api/v1/workflows/{workflow_run_id}/agents  agent execution telemetry
GET  /api/v1/workflows/{workflow_run_id}/tools   tool invocations + provenance
POST /api/v1/batches                             batch intake (alias of /claims/batch)
GET  /api/v1/batches/{batch_id}/...              status / claims / disaster / retry
GET  /api/v1/evaluations                         evaluation summary
GET  /api/v1/evaluations/cases/{case_id}         per-case evaluation detail
GET  /api/v1/audit                               immutable audit trail
GET  /api/v1/metrics                             operational dashboard metrics
GET  /health, /ready                             liveness / readiness
```

Authentication is environment-driven: `DevAuthProvider` locally,
`ProductionAuthProvider` when `CASEFILE_ENV=production` or
`security.auth_mode=production` (see `docs/authentication.md`).

---

## Python API

```python
from decimal import Decimal
from uuid import UUID

from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService

service = ApiService(database_url="sqlite:///./casefile.db")
try:
    ack = service.submit_claim(
        ClaimSubmissionRequest(
            policy_id="POL-12345",
            claimant_name="Ada Lovelace",
            incident_date="2026-09-15",
            claim_amount=Decimal("12450.00"),
            description="Rear-end collision at a junction; bumper and boot damage.",
            documents=["claim_form.pdf", "police_report.pdf"],
        )
    )
    status = service.get_workflow_status(UUID(ack.workflow_run_id))
    print(status.current_state, status.budget_summary.total_cost_usd)
finally:
    service.close()
```

Agents themselves are synchronous: `agent.run(payload, ctx)` returns
`(contract, AgentExecutionRecord)`. See [`AGENTS.md`](AGENTS.md) for the full
contracts, prompts and per-agent authorization matrix.

---

## Repository layout

```
casefile/
├── src/casefile/
│   ├── agents/          # Supervisor, Extractor, Investigator, Reviewer
│   ├── api/             # FastAPI app, routers, schemas, auth, middleware
│   ├── approval/        # Human approval gate, counter-offers
│   ├── budget/          # Multi-dimensional budgets and enforcement
│   ├── cache/           # Redis-backed caching, rate limiting
│   ├── checkpoint/      # Checkpoint ledger, resume, deterministic replay
│   ├── evaluation/      # Dataset, golden scenarios, reporters
│   ├── fraud/           # Fraud signals and assessments
│   ├── models/          # Pydantic contracts, workflow states
│   ├── observability/   # Tracing, metrics, structured logging
│   ├── storage/         # SQLAlchemy models, UnitOfWork repositories, engine
│   ├── tools/           # Typed tool registry (six read-only tools)
│   ├── translation/     # Multilingual claim handling
│   └── workflow/        # Engine, runner, nodes, batch orchestration, hooks
├── tests/               # unit / integration / security / evaluation
├── docs/                # Architecture, ADRs, per-phase completion reports
├── migrations/          # Alembic migrations
├── ui/                  # Operations Console (built assets served at /console)
└── infra/               # Deployment assets
```

---

## Quality gates

Every one of these runs in CI (`.github/workflows/ci.yml`):

```bash
poetry run black --check .
poetry run ruff check .
poetry run mypy src tests          # strict mode, tests included
poetry run pytest tests/unit -m unit
poetry run pytest tests/integration
```

Local notes:

- The full unit suite takes roughly 2–3 minutes; add `--no-cov` while iterating.
- `tests/conftest.py` installs a network guard that blocks non-localhost sockets,
  so a test that silently reaches the internet fails loudly.
- `poetry run casefile evaluate` runs the deterministic evaluation dataset and
  fails the process if any scenario regresses.

Coverage is configured to omit tests and migrations; reports are written to
`htmlcov/` and `coverage.xml` and uploaded as a CI artifact.

---

## Security model

- **Agent-to-tool authorization** is enforced twice: each agent's `allowed_tools`,
  then the `ToolRegistry` before execution (`docs/tools.md`).
- **Prompt injection defense**: external content is quarantined in versioned
  prompts and never concatenated into instructions.
- **LLM output validation**: malformed provider JSON is rejected by Pydantic and
  retried within a bounded budget; it never reaches workflow state.
- **Audit trail**: an immutable event log backs every decision and approval.
- **Secrets**: loaded from the environment, never logged or echoed in errors.

Details in [`docs/security.md`](docs/security.md) and
[`docs/threat-model.md`](docs/threat-model.md); reporting process in
[`SECURITY.md`](SECURITY.md).

---

## Budget enforcement

| Dimension | Default limit | Terminal state on exhaustion |
|---|---|---|
| Input tokens | 100,000 | `BUDGET_EXHAUSTED` |
| Output tokens | 20,000 | `BUDGET_EXHAUSTED` |
| Total tokens | 150,000 | `BUDGET_EXHAUSTED` |
| Cost (USD) | $5.00 | `BUDGET_EXHAUSTED` |
| Steps | 50 | `MAX_STEPS_EXCEEDED` |
| Execution time | 30 minutes | `TIMEOUT` |
| Rework cycles | 3 | `MAX_REWORK_EXCEEDED` |

Pre-flight checks reject an agent invocation before it can overshoot. See
[`docs/budget-control.md`](docs/budget-control.md).

---

## Evaluation

```bash
poetry run casefile evaluate                        # 30-case dataset → reports
poetry run casefile evaluate --golden               # G01–G15 golden scenarios
poetry run python -m casefile.evaluation            # module entry point
```

The harness replays checkpoints and compares outcomes, so a prompt or routing
change that alters a decision shows up as a scenario failure rather than as a
silent drift. See [`docs/evaluation.md`](docs/evaluation.md) and the latest
[`docs/evaluation-report.md`](docs/evaluation-report.md).

---

## Documentation

| Document | Description |
|---|---|
| [`docs/system-overview.md`](docs/system-overview.md) | End-to-end system overview |
| [`docs/architecture.md`](docs/architecture.md) | Component architecture |
| [`docs/agent-architecture.md`](docs/agent-architecture.md) / [`AGENTS.md`](AGENTS.md) | Agent design, contracts, prompts, authorization |
| [`docs/state-machine.md`](docs/state-machine.md) | States, transitions, termination guarantees |
| [`docs/tools.md`](docs/tools.md) | Tool matrix and authorization |
| [`docs/persistence.md`](docs/persistence.md) | Schema and persistence layer |
| [`docs/checkpointing.md`](docs/checkpointing.md) / [`docs/replay.md`](docs/replay.md) | Checkpoints and deterministic replay |
| [`docs/budget-control.md`](docs/budget-control.md) | Budget enforcement |
| [`docs/approval.md`](docs/approval.md) | Human approval gate |
| [`docs/observability.md`](docs/observability.md) | Tracing, metrics, logging |
| [`docs/security.md`](docs/security.md) / [`docs/threat-model.md`](docs/threat-model.md) | Security boundaries, threat model |
| [`docs/testing.md`](docs/testing.md) | Testing strategy |
| [`docs/deployment.md`](docs/deployment.md) / [`docs/operations-console.md`](docs/operations-console.md) | Deployment and operator console |
| [`docs/roadmap.md`](docs/roadmap.md) | Phase-by-phase roadmap and completion reports |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records (ADR-001 … ADR-011) |

---

## Status

Phases 0–12 are implemented and covered by the gates above: agents and tool
layer, workflow engine and batch orchestration, persistence, checkpoints and
replay, budgets, approval gate with counter-offers, fraud and translation,
observability, evaluation, REST API and Operations Console. Per-phase evidence
lives in `docs/phase-*-completion.md`.

**Not yet claimed**: production throughput and cost benchmarks. The evaluation
suite measures correctness, not load; there is no published latency or
throughput figure for this codebase.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Conventional Commits, `pre-commit`
hooks, and the tests above are expected on every PR; CI must be green.

---

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
