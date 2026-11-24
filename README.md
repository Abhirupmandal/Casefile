<div align="center">

# CASEFILE

**Multi-agent insurance claim adjudication where every decision is budgeted, checkpointed and replayable.**

[![CI](https://github.com/Abhirupmandal/Casefile/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Abhirupmandal/Casefile/actions/workflows/ci.yml)
[![Coverage: 83%](https://img.shields.io/badge/coverage-83%25-brightgreen.svg)](#quality-gates)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Types: mypy strict](https://img.shields.io/badge/types-mypy%20strict-informational.svg)](pyproject.toml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

</div>

CASEFILE takes a claim from intake to a human-signed decision: documents are
parsed, evidence is gathered through typed read-only tools, a reviewer
recommends approve / reject / rework, and a human approval gate has the final
say. Agents talk to each other only through Pydantic contracts; the workflow
runs under multi-dimensional budgets; every state transition is written to an
append-only checkpoint ledger, so a finished run can be replayed step for step.

---

## Not another LLM wrapper

Giving a model a tool loop is easy. Making the loop safe to run in an insurance
process takes structure. Each risk below is handled by a specific mechanism, and
each mechanism is covered by tests and by the evaluation harness.

| Risk | Mechanism | Where |
|---|---|---|
| Agents call tools they should not | Per-agent `allowed_tools`, re-checked by `ToolRegistry` before execution | `src/casefile/agents/`, `src/casefile/tools/` |
| External text hijacks the prompt | Untrusted content quarantined in versioned prompt templates, never concatenated into instructions | `src/casefile/agents/prompts.py` |
| Malformed model output reaches state | Provider JSON → Pydantic validation → domain consistency checks, bounded retries | `src/casefile/agents/base.py` |
| A loop burns tokens and dollars | Pre-flight budget check before every invocation across 7 dimensions; exhaustion is a terminal state | `src/casefile/budget/` |
| A run cannot be explained afterwards | Append-only event log, checkpoint ledger, checkpoint-derived replay | `src/casefile/checkpoint/`, `src/casefile/workflow/` |
| The model, not a human, approves | Reviewer only *recommends*; approval gate decides, and counter-offers are negotiated explicitly | `src/casefile/approval/` |

**Tests never touch a real provider.** The whole suite runs on
`DeterministicProvider`; the hosted LangChain adapter is constructed only when a
provider key is configured.

---

## At a glance

| | |
|---|---|
| **Agents** | Supervisor + Extractor, Investigator, Reviewer (supervisor–worker) |
| **State machine** | 14 states, 8 of them terminal, with termination guarantees |
| **Tools** | 6 read-only tools behind one typed registry (authorization, cache, rate limits) |
| **Surface** | 13 routers / 37 OpenAPI routes under `/api/v1`, plus `/docs` and `/console` |
| **Budgets** | 7 dimensions: input/output/total tokens, cost, steps, wall clock, rework |
| **Evaluation** | 30-case dataset + G01–G15 golden scenarios, checkpoint replay |
| **Tests** | 1,195 across unit, integration, security and evaluation suites; 83% line coverage |

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

- **Orchestration**: LangGraph (`docs/adr/ADR-001-langgraph-orchestration.md`)
- **Persistence**: SQLite as the system of record — checkpoints, event log, budget state
- **Cache / rate limiting**: Redis, optional in tests and local runs
- **Observability**: OpenTelemetry spans per agent invocation and tool call, metrics, structured logs

The supervisor performs no domain logic: it routes, enforces budgets and
guarantees that the workflow terminates. Specialists own the judgment.

### What one claim actually does

1. `POST /api/v1/claims` validates the payload and opens a workflow run (`RECEIVED`).
2. Supervisor takes a budget pre-flight, transitions to `EXTRACTION` and calls the Extractor, which fetches documents through `document_retrieval` only.
3. Investigator calls its authorized tools (`policy_lookup`, `claim_history_lookup`, `repair_cost_lookup`, `fraud_signal_lookup`, `document_retrieval`, `evidence_lookup`) and synthesizes findings.
4. Reviewer evaluates evidence completeness and returns `APPROVE` / `REJECT` / `REWORK`. Rework loops back to the Investigator with written feedback, at most 3 times.
5. Approval is queued for a human adjuster; the decision lands in the audit trail.
6. Every hop wrote a checkpoint, so `POST /api/v1/workflows/{id}/replay` can re-execute the run and report any divergence.

Two real trajectories from the evaluation run — one with a rework cycle, one
where a tool failure is contained instead of corrupting state:

```
CASE-006  RECEIVED → EXTRACTION → INVESTIGATION → REVIEW → REWORK_LOOP
          → INVESTIGATION → REVIEW → HUMAN_APPROVAL → APPROVED

CASE-010  RECEIVED → EXTRACTION → INVESTIGATION → FAILED   (injected policy_lookup error)
```

---

## Quick start

**Prerequisites**: Python 3.11+, Poetry 2.4+, Docker (optional, for Redis and Jaeger).

```bash
git clone https://github.com/Abhirupmandal/Casefile.git
cd Casefile

poetry install
cp .env.example .env          # add provider keys only if you want live model calls
poetry run alembic upgrade head

poetry run casefile healthcheck
poetry run casefile serve --reload   # API on http://127.0.0.1:8000, console at /console
```

Redis and Jaeger are optional for local work:

```bash
docker compose up -d redis jaeger   # Jaeger UI: http://localhost:16686
```

Submit a claim and follow it:

```bash
curl -s localhost:8000/api/v1/claims -H 'content-type: application/json' -d '{
  "policy_id": "POL-12345", "claimant_name": "Ada Lovelace",
  "incident_date": "2026-09-15", "claim_amount": "12450.00",
  "description": "Rear-end collision at a junction; bumper and boot damage.",
  "documents": ["claim_form.pdf", "police_report.pdf"]
}'
```

---

## CLI

| Command | Purpose |
|---|---|
| `casefile version` | Version and implementation status |
| `casefile healthcheck --env test` | SQLite / Redis / tracer health with per-service latency |
| `casefile serve --host --port --reload` | REST API + Operations Console |
| `casefile evaluate` | Run the 30-case dataset, write JSON + Markdown reports |
| `casefile evaluate --golden` | Run the G01–G15 golden scenarios |
| `casefile evaluate --scenario CASE-004 -s G03` | Run selected scenarios |
| `casefile process-claim` / `casefile run-evaluation` | Legacy placeholders; use `serve` + `/api/v1/claims` and `evaluate` |

---

## HTTP API

Everything is discoverable at `/docs` once the server is up. Routers are
mounted under `/api/v1`.

<details>
<summary><b>Full route list</b> (37 routes across 13 routers)</summary>

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

</details>

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

Agents are synchronous: `agent.run(payload, ctx)` returns
`(contract, AgentExecutionRecord)`. [`AGENTS.md`](AGENTS.md) has the full
contracts, prompts and the per-agent authorization matrix.

---

## Budget enforcement

Pre-flight checks reject an agent invocation *before* it can overshoot, and
exhaustion is a terminal state rather than an exception.

| Dimension | Default limit | Terminal state on exhaustion |
|---|---|---|
| Input tokens | 100,000 | `BUDGET_EXHAUSTED` |
| Output tokens | 20,000 | `BUDGET_EXHAUSTED` |
| Total tokens | 150,000 | `BUDGET_EXHAUSTED` |
| Cost (USD) | $5.00 | `BUDGET_EXHAUSTED` |
| Steps | 50 | `MAX_STEPS_EXCEEDED` |
| Execution time | 30 minutes | `TIMEOUT` |
| Rework cycles | 3 | `MAX_REWORK_EXCEEDED` |

See [`docs/budget-control.md`](docs/budget-control.md).

---

## Evaluation

```bash
poetry run casefile evaluate            # 30-case dataset → JSON + Markdown reports
poetry run casefile evaluate --golden   # G01–G15 golden scenarios
poetry run python -m casefile.evaluation
```

The harness replays checkpoints and compares outcomes, so a prompt or routing
change that alters a decision surfaces as a scenario failure instead of silent
drift. The last committed run (deterministic provider, `docs/evaluation-report.md`):

| Measure | Result |
|---|---|
| Scenarios | 30 / 30 pass, 0 failed |
| Invariants | 119 / 119 pass |
| Terminal state & path accuracy | 1.00 |
| Replay determinism | 1.00 |
| Mean steps / agent runs / tool calls per claim | 5.77 / 2.90 / 2.00 |
| Mean rework cycles / retries per claim | 0.17 / 0.10 |
| Simulated cost per claim | $0.0006 |

These numbers measure **orchestration conformance** — routing, budgets,
checkpoints, replay, approval safety, failure containment — against a scripted
harness. They are not a model-accuracy claim, and no accuracy figure has been
measured against a labelled adjudication corpus. See
[`docs/evaluation.md`](docs/evaluation.md).

---

## Quality gates

Every gate below runs in CI (`.github/workflows/ci.yml`) on `main` and `develop`:

```bash
poetry run black --check .
poetry run ruff check .
poetry run mypy src tests          # strict mode, tests included
poetry run pytest tests/unit -m unit
poetry run pytest tests/integration
```

Measured on the current `main` (Python 3.11):

| Suite | Result |
|---|---|
| Unit | 941 passed |
| Integration | 139 passed, 2 skipped |
| Security + evaluation | 115 passed |
| Golden scenarios G01–G15 | 15 / 15 |
| Line coverage | 83% (13,657 statements, 2,271 missed) |

Local notes:

- The full unit suite takes roughly 2–3 minutes; add `--no-cov` while iterating.
- `tests/conftest.py` installs a network guard that blocks non-localhost sockets, so a test that silently reaches the internet fails loudly.
- `poetry run casefile evaluate` fails the process if any scenario regresses.
- Coverage omits tests and migrations; reports go to `htmlcov/` and `coverage.xml` and are uploaded as a CI artifact.

---

## Security model

- **Agent-to-tool authorization** is enforced twice: each agent's `allowed_tools`, then `ToolRegistry` before execution ([`docs/tools.md`](docs/tools.md)).
- **Prompt injection defense**: external content is quarantined in versioned prompts and never concatenated into instructions.
- **LLM output validation**: malformed provider JSON is rejected by Pydantic and retried within a bounded budget; it never reaches workflow state.
- **Audit trail**: an immutable event log backs every decision and approval.
- **Secrets**: loaded from the environment, never logged or echoed in errors.

Details in [`docs/security.md`](docs/security.md) and
[`docs/threat-model.md`](docs/threat-model.md); reporting process in
[`SECURITY.md`](SECURITY.md).

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
| [`docs/failure-modes.md`](docs/failure-modes.md) / [`docs/failure-injection.md`](docs/failure-injection.md) | Failure taxonomy and injection harness |
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

**Not claimed**: production throughput, latency and cost benchmarks. The
evaluation suite measures correctness under a deterministic provider, not load,
so there is no published latency or throughput figure for this codebase. Fraud
signals are a deterministic weighted heuristic (`DeterministicFraudScorer`),
not a trained classifier.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Conventional Commits, `pre-commit`
hooks, and the gates above are expected on every PR; CI must be green.

---

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).
