# CASEFILE System Overview

## What is CASEFILE?

CASEFILE is a production-oriented multi-agent AI orchestration platform for insurance claims intelligence and triage. It automates the processing of auto insurance claims through a deterministic workflow with guaranteed termination, comprehensive audit trails, and human oversight.

## Business Context

### Problem Statement

A regional auto insurer receives approximately 900 claims per day. Each claim requires:
- Document extraction (police reports, photos, repair estimates)
- Policy verification
- Previous-claim investigation
- Damage/repair-cost sanity checking
- Evidence synthesis
- Final recommendation generation
- Human approval before payout

Manual processing is:
- Slow (average 3-5 days per claim)
- Inconsistent (varies by adjuster expertise)
- Expensive (high labor cost for routine claims)
- Audit-challenged (limited traceability)

### Solution Approach

CASEFILE automates the routine analysis while:
- Maintaining human oversight for approvals
- Providing complete audit trails
- Ensuring consistent processing
- Containing costs via budget enforcement
- Enabling replay for dispute resolution

## System Capabilities

### Core Functionality

| Capability | Description |
|------------|-------------|
| Claim Intake | Accept claim documents via REST API |
| Document Extraction | Parse and structure claim documents |
| Policy Verification | Validate coverage and limits |
| History Investigation | Check for prior claims, fraud signals |
| Cost Validation | Compare repair estimates to expected ranges |
| Evidence Synthesis | Combine all findings into recommendation |
| Rework Loop | Allow reviewer to request additional investigation |
| Human Approval | Require human sign-off before payout |
| Audit Trail | Persist complete execution history |
| Replay | Resume workflows from checkpoints |

### Non-Functional Requirements

| Requirement | Target | Enforcement |
|-------------|--------|-------------|
| Latency (p50) | < 30 seconds | Measured |
| Latency (p95) | < 2 minutes | Measured |
| Throughput | 900 claims/day | Capacity planning |
| Availability | 99.5% | Health checks |
| Cost per claim | < $2.00 | Budget enforcement |
| Termination | 100% within bounds | Code enforcement |
| Audit completeness | 100% | Persistence validation |

## System Actors

```
┌─────────────────────────────────────────────────────────────────────┐
│                           ACTORS                                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐                                                   │
│  │ Claims Intake│ ──────► Submits claim documents                  │
│  │   System     │         (external system integration)            │
│  └──────────────┘                                                   │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │   Adjuster   │ ──────► Reviews recommendations                  │
│  │   (Human)    │         Approves/rejects claims                  │
│  └──────────────┘         Receives rework requests                 │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │   Auditor    │ ──────► Queries audit trail                      │
│  │   (Human)    │         Replays historical runs                  │
│  └──────────────┘         Investigates disputes                    │
│                                                                      │
│  ┌──────────────┐                                                   │
│  │  Operations  │ ──────► Monitors system health                   │
│  │   (Human)    │         Reviews cost metrics                     │
│  └──────────────┘         Investigates failures                    │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

## High-Level Data Flow

```
Claim Documents
       │
       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   CLAIM     │     │   WORKFLOW  │     │ DECISION/   │
│   INTAKE    │────►│  EXECUTION  │────►│ RECOMMEND.  │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   HUMAN     │
                    │  APPROVAL   │
                    └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │   PAYOUT    │
                    │  (external) │
                    └─────────────┘
```

## Technology Stack

### Backend

| Component | Technology | Purpose |
|-----------|------------|---------|
| API Framework | FastAPI | REST endpoints, async support |
| Orchestration | LangGraph | Workflow state machine |
| Validation | Pydantic v2 | Schema contracts |
| Database | SQLite | Persistent state (local file) |
| Cache | Redis 7 | Checkpoint cache, locks |
| Observability | OpenTelemetry | Tracing, metrics |
| Testing | pytest | Unit, integration, e2e |

### LLM Abstraction

```
┌─────────────────────────────────────────────────────────────┐
│                    CASEFILE LLM LAYER                       │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Provider Abstraction                    │   │
│  │  • Unified interface for completion calls           │   │
│  │  • Token counting abstraction                       │   │
│  │  • Cost calculation per provider                    │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│          ┌────────────────┼────────────────┐               │
│          ▼                ▼                ▼               │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │   OpenAI     │ │  Anthropic   │ │    Local     │       │
│  │   Provider   │ │   Provider   │ │   Provider   │       │
│  └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

Current default: OpenAI (gpt-4o, gpt-4o-mini)

### Infrastructure

| Component | Technology | Purpose |
|-----------|------------|---------|
| Containerization | Docker | Development, deployment |
| Orchestration | Docker Compose | Local development |
| Secrets | Environment variables | Configuration |
| Logging | Structured JSON | Observability |

## Module Organization

```
casefile/
├── api/                    # FastAPI application
│   ├── routes/            # REST endpoints
│   ├── middleware/        # Auth, logging, error handling
│   └── dependencies/      # Dependency injection
│
├── orchestration/          # LangGraph workflow
│   ├── supervisor/        # Supervisor node
│   ├── graph/             # State graph definition
│   └── transitions/       # State transition logic
│
├── agents/                 # Specialist agents
│   ├── extractor/         # Document extraction
│   ├── investigator/      # Policy, history, cost checks
│   └── reviewer/          # Evidence synthesis, rework
│
├── contracts/              # Pydantic schemas
│   ├── inputs.py          # Claim input, document schemas
│   ├── requests.py        # Agent request contracts
│   ├── results.py         # Agent result contracts
│   └── workflow.py        # Workflow state schemas
│
├── tools/                  # Mocked external tools
│   ├── policy_lookup.py
│   ├── claim_history.py
│   ├── repair_cost.py
│   └── fraud_signals.py
│
├── persistence/            # Database layer
│   ├── models/            # SQLAlchemy models
│   ├── repositories/      # Data access patterns
│   └── migrations/        # Alembic migrations
│
├── checkpoint/             # Checkpoint management
│   ├── manager.py         # Checkpoint lifecycle
│   ├── serialization.py   # State serialization
│   └── replay.py          # Replay execution
│
├── budget/                 # Budget enforcement
│   ├── tracker.py         # Token, cost, time tracking
│   ├── limits.py          # Limit definitions
│   └── enforcement.py     # Violation handling
│
├── observability/          # Telemetry
│   ├── tracing.py         # OpenTelemetry setup
│   ├── metrics.py         # Custom metrics
│   └── logging.py         # Structured logging
│
├── llm/                    # LLM abstraction
│   ├── provider.py        # Provider interface
│   ├── openai.py          # OpenAI implementation
│   ├── anthropic.py       # Anthropic implementation
│   └── local.py           # Local model implementation
│
├── evaluation/             # Evaluation harness
│   ├── runner.py          # Run executor
│   ├── recorder.py        # Result recorder
│   └── metrics.py         # Metric calculators
│
└── config/                 # Configuration
    ├── settings.py        # Pydantic settings
    └── prompts/           # Agent prompt templates
```

## Execution Model

### Synchronous Claim Processing

```
┌─────────┐     ┌─────────────┐     ┌───────────┐
│  POST   │     │  Validate   │     │  Create   │
│ /claims │────►│  Input      │────►│  Run      │
└─────────┘     └─────────────┘     └───────────┘
                                          │
                                          ▼
                                   ┌─────────────┐
                                   │   Queue     │
                                   │  (async)    │
                                   └─────────────┘
                                          │
                                          ▼
                                   ┌─────────────┐
                                   │  Workflow   │
                                   │  Execution  │
                                   └─────────────┘
                                          │
                                          ▼
                                   ┌─────────────┐
                                   │  Terminal   │
                                   │   State     │
                                   └─────────────┘
                                          │
                                          ▼
                                   ┌─────────────┐
                                   │ Notification│
                                   │  (webhook)  │
                                   └─────────────┘
```

### State Recovery

```
┌─────────────┐     ┌─────────────┐     ┌───────────┐
│ Interruption│     │   Load      │     │  Resume   │
│   Event     │────►│ Checkpoint  │────►│ Workflow  │
└─────────────┘     └─────────────┘     └───────────┘
```

## Security Overview

### Threat Model

| Threat | Mitigation |
|--------|------------|
| Prompt injection in documents | Structured output validation, no raw prompt exposure |
| Malicious claim data | Input validation, sandboxed tool execution |
| Unauthorized access | API authentication, role-based authorization |
| Data exfiltration | Audit logging, no external calls to untrusted endpoints |
| Resource exhaustion | Budget enforcement, rate limiting |

### Data Sensitivity

| Data Type | Classification | Handling |
|-----------|----------------|----------|
| Claim documents | Confidential | Encrypted at rest, audit logged |
| Policy numbers | Confidential | Masked in logs |
| PII (names, addresses) | Confidential | Retention policy enforced |
| Workflow state | Internal | Standard access controls |
| Audit logs | Internal | Immutable, long retention |

## Operational Concerns

### Monitoring

- Health check endpoint: `GET /health`
- Metrics endpoint: `GET /metrics` (Prometheus format)
- OpenTelemetry traces: Exported to collector

### Alerting (Future)

- Workflow failure rate > 5%
- Average latency > 5 minutes
- Cost per claim > $3.00
- Budget exhaustion events

### Backup and Recovery

- SQLite: Daily file snapshots
- Redis: Ephemeral coordination and caches (not durable storage)
- Configuration: Version controlled

## Success Metrics

### Phase 0 Completion

- [x] Architecture documentation complete
- [x] Domain model defined
- [x] Contracts specified
- [x] State machine designed
- [x] Repository structure established

### Phase 1 Completion

- [x] Core workflow implemented
- [x] All agents operational
- [x] Persistence layer functional
- [x] Basic observability working

### Phase 2 Completion (partial — 30 recorded production runs pending)

- [x] Evaluation harness operational
- [ ] 30 recorded claim runs
- [x] Replay functionality verified
- [x] Cost metrics accurate

### Production Readiness (Future)

- [ ] 95% workflow completion rate
- [ ] < $2.00 average cost per claim
- [ ] < 2 minute p95 latency
- [ ] 100% termination guarantee verified
