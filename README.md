# CASEFILE

**AI-Powered Insurance Claim Adjudication System**

CASEFILE is a production-grade multi-agent system that automates insurance claim adjudication using LLM-powered agents with strict budgets, checkpointing, and human-in-the-loop approval.

---

## 📋 Overview

CASEFILE orchestrates four specialized AI agents to process insurance claims:

1. **Extractor**: Parses claim documents and extracts structured data
2. **Investigator**: Gathers evidence using external tools (policy lookup, fraud detection, etc.)
3. **Reviewer**: Evaluates investigation completeness and recommends approve/reject/rework
4. **Supervisor**: Coordinates agents, enforces budgets, manages state transitions

Every workflow includes:
- ✅ **Typed contracts** (Pydantic models for all agent handoffs)
- ✅ **Budget enforcement** (token, cost, step, time, rework limits)
- ✅ **Checkpointing** (resume from any point, deterministic replay)
- ✅ **Human approval** (human-in-the-loop before final decision)
- ✅ **Observability** (OpenTelemetry traces, logs, metrics)
- ✅ **Security** (agent-to-tool authorization, audit logging, prompt injection defense)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Supervisor                           │
│            (Orchestration, Budget Enforcement)              │
└───────┬─────────────────────────────────┬──────────────────┘
        │                                 │
        ▼                                 ▼
┌───────────────┐              ┌──────────────────┐
│   Extractor   │──────────────▶│  Investigator    │
│ (Parse claim) │              │ (Gather evidence)│
└───────────────┘              └────────┬─────────┘
                                        │
                                        ▼
                               ┌────────────────┐
                               │    Reviewer    │
                               │ (Evaluate, QA) │
                               └────────┬───────┘
                                        │
                        ┌───────────────┼───────────────┐
                        ▼               ▼               ▼
                  ┌─────────┐   ┌──────────┐   ┌──────────┐
                  │ APPROVE │   │  REWORK  │   │  REJECT  │
                  └─────────┘   └──────────┘   └──────────┘
                        │               │               │
                        ▼               │               ▼
                ┌──────────────┐        │       ┌──────────┐
                │HUMAN_APPROVAL│        │       │ REJECTED │
                └──────┬───────┘        │       └──────────┘
                       │                │
                ┌──────┴────────┐       │
                ▼               ▼       ▼
          ┌─────────┐    ┌──────────┐  (back to Investigator)
          │APPROVED │    │ REJECTED │
          └─────────┘    └──────────┘
```

**State Machine**: 14 states, 8 terminal states
**Workflow Orchestration**: LangGraph
**Persistence**: PostgreSQL (checkpoints, event log, budget state)
**Caching**: Redis (tool responses, rate limiting)
**Observability**: OpenTelemetry (traces, logs, metrics)

See [`docs/architecture.md`](docs/architecture.md) for complete architecture documentation.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Poetry 2.4+
- Docker & Docker Compose (for PostgreSQL 15, Redis 7, Jaeger)
- OpenAI and/or Anthropic API keys (Phase 2+)

### Installation

```bash
# Clone repository
git clone https://github.com/your-org/casefile.git
cd casefile

# Install dependencies
poetry install

# Set up environment
cp .env.example .env
# Edit .env with your API keys and database credentials

# Start infrastructure services (PostgreSQL, Redis, Jaeger)
docker-compose up -d

# Verify environment health
poetry run casefile healthcheck
```

### CLI

```bash
# Display version
poetry run casefile --version

# Check infrastructure health (Phase 1+)
poetry run casefile healthcheck

# Submit a claim workflow (Phase 2+)
poetry run casefile submit --claim-id CLM-2024-001 --policy-id POL-12345
```

### Python API (Phase 2+)

```python
from casefile.models.contracts import ClaimInput
from casefile.workflow.executor import execute_workflow  # available Phase 2

claim = ClaimInput(
    claim_id="CLM-2024-001",
    policy_id="POL-12345",
    incident_date="2024-09-15",
    claim_amount=12450.00,
    description="Vehicle damage from collision",
    documents=["claim_form.pdf", "police_report.pdf"],
)

result = await execute_workflow(claim)
print(f"Terminal state: {result.terminal_state}")
print(f"Cost: ${result.budget_state.total_cost_usd}")
```

---

## 📂 Project Structure

```
casefile/
├── docs/                    # Architecture documentation
│   ├── adr/                 # Architecture Decision Records (ADRs)
│   ├── architecture.md      # System architecture
│   ├── agent-architecture.md
│   ├── state-machine.md
│   └── ...
├── src/casefile/            # Source code
│   ├── agents/              # Agent implementations (Extractor, Investigator, etc.)
│   ├── models/              # Pydantic models (contracts, state)
│   ├── persistence/         # Database repositories, migrations
│   ├── tools/               # Tool implementations (policy lookup, fraud detection)
│   ├── workflow/            # LangGraph workflow definition
│   └── observability/       # OpenTelemetry instrumentation
├── tests/                   # Tests
│   ├── unit/                # Unit tests
│   ├── integration/         # Integration tests
│   └── fixtures/            # Test fixtures, evaluation corpus
├── config/                  # Configuration files
├── scripts/                 # Utility scripts (migrations, seeding)
├── infra/                   # Infrastructure as code (Terraform, Docker)
├── pyproject.toml           # Python project configuration
└── README.md                # This file
```

---

## 🔧 Development

### Code Quality

```bash
# Linting
poetry run ruff check .

# Formatting
poetry run black .

# Type checking (src + tests, strict)
poetry run mypy src tests

# All checks (pre-commit)
poetry run pre-commit run --all-files
```

### Testing

```bash
# Run all tests
poetry run pytest

# Run only unit tests (fast)
poetry run pytest -m unit

# Run with coverage
poetry run pytest --cov=casefile --cov-report=html

# Run integration tests (requires services)
docker-compose up -d
poetry run pytest -m integration
```

### Database Migrations

```bash
# Create migration
poetry run alembic revision --autogenerate -m "Add checkpoints table"

# Apply migrations
poetry run alembic upgrade head

# Rollback migration
poetry run alembic downgrade -1
```

---

## 📊 Observability

### OpenTelemetry Traces

CASEFILE instruments every agent invocation, tool call, and state transition with OpenTelemetry spans.

**View traces locally:**

```bash
# Start Jaeger
docker-compose up -d jaeger

# Open Jaeger UI
open http://localhost:16686
```

**Trace hierarchy:**

```
workflow_run (root span)
├── agent_invocation (Extractor)
├── agent_invocation (Investigator)
│   ├── tool_call (policy_lookup)
│   ├── tool_call (claim_history_lookup)
│   └── tool_call (fraud_signal_lookup)
├── agent_invocation (Reviewer)
└── state_transition (REVIEW → HUMAN_APPROVAL)
```

### Metrics

Key metrics tracked:
- `casefile.agent.invocations` - Agent invocation count
- `casefile.agent.latency` - Agent invocation latency (histogram)
- `casefile.agent.tokens` - Token usage per agent (histogram)
- `casefile.workflow.cost` - Total cost per workflow (histogram)
- `casefile.workflows.active` - Active workflow count (gauge)

---

## 🔐 Security

CASEFILE implements defense-in-depth security:

- **Agent-to-tool authorization**: Only authorized agents can call specific tools
- **Prompt injection defense**: Tool responses sanitized before inclusion in prompts
- **Audit logging**: All actions logged to immutable event log
- **Trust boundaries**: External data validated (Pydantic), LLM outputs validated
- **Secrets management**: API keys loaded from environment, never logged

See [`docs/security.md`](docs/security.md) for complete security documentation.

---

## 💰 Budget Enforcement

CASEFILE enforces multi-dimensional budgets to prevent runaway costs:

| Budget Dimension | Default Limit | Terminal State on Exhaustion |
|-----------------|---------------|------------------------------|
| Input tokens    | 100,000       | BUDGET_EXHAUSTED            |
| Output tokens   | 20,000        | BUDGET_EXHAUSTED            |
| Total tokens    | 150,000       | BUDGET_EXHAUSTED            |
| Cost (USD)      | $5.00         | BUDGET_EXHAUSTED            |
| Steps           | 50            | MAX_STEPS_EXCEEDED          |
| Execution time  | 30 minutes    | TIMEOUT                     |
| Rework cycles   | 3             | MAX_REWORK_EXCEEDED         |

**Pre-flight checks** prevent agent invocations that would exceed budget.

See [`docs/budget-control.md`](docs/budget-control.md) for details.

---

## 🔄 Checkpoint & Replay

CASEFILE checkpoints workflow state after every agent transition.

### Resume from Checkpoint

```python
from casefile.persistence.checkpoints import load_checkpoint
from casefile.workflow.replay import resume_from_checkpoint

# Load checkpoint
checkpoint = await load_checkpoint(checkpoint_id)

# Resume workflow
result = await resume_from_checkpoint(checkpoint)
```

### Deterministic Replay

```python
from casefile.workflow.replay import replay_from_checkpoint, ReplayMode

# Replay and verify determinism
result = await replay_from_checkpoint(
    checkpoint_id,
    mode=ReplayMode.VERIFY,
)

if result.divergences:
    print(f"Replay diverged: {result.divergences}")
```

See [`docs/checkpointing.md`](docs/checkpointing.md) and [`docs/replay.md`](docs/replay.md).

---

## 🧪 Evaluation

CASEFILE uses checkpoint-based replay for evaluation:

```bash
# Run evaluation suite (30+ scenarios)
poetry run python -m casefile.evaluation.run_suite

# Run specific scenario
poetry run python -m casefile.evaluation.run_scenario --scenario eval-001

# Generate evaluation report
poetry run python -m casefile.evaluation.report
```

Evaluation corpus includes:
- ✅ Happy path (no rework, approval)
- ✅ Reviewer triggers rework
- ✅ Budget exhaustion scenarios
- ✅ Timeout scenarios
- ✅ Rejection scenarios
- ✅ Tool failure scenarios

See [`docs/evaluation.md`](docs/evaluation.md) for details.

---

## 📖 Documentation

### Architecture Documentation

| Document | Description |
|----------|-------------|
| [`architecture.md`](docs/architecture.md) | High-level system architecture |
| [`agent-architecture.md`](docs/agent-architecture.md) | Agent design and responsibilities |
| [`agent-contracts.md`](docs/agent-contracts.md) | Pydantic contract definitions |
| [`state-machine.md`](docs/state-machine.md) | Workflow states and transitions |
| [`tool-architecture.md`](docs/tool-architecture.md) | Tool contracts and execution |
| [`persistence.md`](docs/persistence.md) | Database schema and persistence layer |
| [`checkpointing.md`](docs/checkpointing.md) | Checkpoint strategy |
| [`replay.md`](docs/replay.md) | Replay mechanism |
| [`budget-control.md`](docs/budget-control.md) | Budget enforcement |
| [`termination.md`](docs/termination.md) | Termination guarantees |
| [`observability.md`](docs/observability.md) | OpenTelemetry instrumentation |
| [`security.md`](docs/security.md) | Security boundaries and authorization |
| [`testing.md`](docs/testing.md) | Testing strategy |
| [`evaluation.md`](docs/evaluation.md) | Evaluation corpus and strategy |
| [`failure-modes.md`](docs/failure-modes.md) | Failure handling and recovery |
| [`versioning.md`](docs/versioning.md) | Schema and workflow versioning |

### Architecture Decision Records (ADRs)

| ADR | Title |
|-----|-------|
| [ADR-001](docs/adr/ADR-001-langgraph-orchestration.md) | LangGraph for Workflow Orchestration |
| [ADR-002](docs/adr/ADR-002-pydantic-typed-contracts.md) | Pydantic for Typed Inter-Agent Contracts |
| [ADR-003](docs/adr/ADR-003-postgresql-persistence.md) | PostgreSQL as System of Record |
| [ADR-004](docs/adr/ADR-004-redis-boundaries.md) | Redis for Caching and Rate Limiting |
| [ADR-005](docs/adr/ADR-005-provider-agnostic-llm.md) | Provider-Agnostic LLM Abstraction |
| [ADR-006](docs/adr/ADR-006-checkpoint-replay.md) | Checkpoint and Replay Strategy |
| [ADR-007](docs/adr/ADR-007-budget-enforcement.md) | Multi-Dimensional Budget Enforcement |
| [ADR-008](docs/adr/ADR-008-opentelemetry-observability.md) | OpenTelemetry for Observability |
| [ADR-009](docs/adr/ADR-009-human-approval-gate.md) | Human Approval Gate |
| [ADR-010](docs/adr/ADR-010-evaluation-strategy.md) | Evaluation Strategy |

### Agent Documentation

See [`AGENTS.md`](AGENTS.md) for detailed agent specifications.

---

## 🗺️ Roadmap

See [`docs/roadmap.md`](docs/roadmap.md) for the complete implementation roadmap.

**Current Status**: Phase 0 complete (Architecture & Design)
**Next Phase**: Phase 1 (Repository Foundation & Core Infrastructure) - 3 weeks

---

## 🤝 Contributing

### Development Workflow

1. Create feature branch: `git checkout -b feature/my-feature`
2. Make changes, write tests
3. Run code quality checks: `poetry run pre-commit run --all-files`
4. Run tests: `poetry run pytest`
5. Commit changes: `git commit -m "feat: add my feature"`
6. Push and create PR: `git push origin feature/my-feature`

### Commit Message Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `refactor:` Code refactoring
- `test:` Test changes
- `chore:` Build/tooling changes

---

## 📄 License

Proprietary - All Rights Reserved

---

## 🙋 Support

- **Documentation**: [`docs/`](docs/)
- **Issues**: Create GitHub issue
- **Discussions**: GitHub Discussions
- **Runbook**: [`docs/runbook.md`](docs/runbook.md) (coming in Phase 8)

---

## ⚡ Performance

### Benchmarks (Target Metrics)

| Metric | Target | Current |
|--------|--------|---------|
| Throughput | 1,000 workflows/hour | TBD (Phase 8) |
| Latency (P50) | <3 minutes | TBD (Phase 8) |
| Latency (P95) | <8 minutes | TBD (Phase 8) |
| Cost per workflow | $3.50 | TBD (Phase 8) |
| Error rate | <1% | TBD (Phase 8) |
| Availability | 99.9% | TBD (Phase 8) |

---

## 🏆 Acknowledgments

Built with:
- [LangGraph](https://github.com/langchain-ai/langgraph) - Workflow orchestration
- [Pydantic](https://github.com/pydantic/pydantic) - Data validation
- [PostgreSQL](https://www.postgresql.org/) - Persistence
- [Redis](https://redis.io/) - Caching
- [OpenTelemetry](https://opentelemetry.io/) - Observability
- [Poetry](https://python-poetry.org/) - Dependency management

---

**CASEFILE** - Production-grade AI-powered claim adjudication
