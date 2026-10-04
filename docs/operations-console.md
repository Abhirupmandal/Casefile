# Operations Console Architecture (Phase 7)

## Overview

The CASEFILE Operations Console is an operator-facing control surface engineered for transparency, state machine inspection, and human-in-the-loop decisioning. It is built as a modern, zero-dependency vanilla CSS + React + TypeScript single-page application and served from the FastAPI backend at `/console`.

---

## Views & Capabilities

### 1. Operations Control Surface (Dashboard)
- **Real-Time KPIs**: Live counters for active workflows, completed claims, failed runs, budget-exhausted workflows, and pending human approvals.
- **Cost & Duration**: Average workflow cost (USD) and duration tracking.
- **Benchmark Pass Rate**: Visual pass rate indicator pulled from the evaluation report.
- **Claim Submission**: Integrated intake modal triggering multi-agent adjudication.

### 2. Workflows & Claims Registry
- Tabular registry showing `workflow_run_id`, `claim_id`, `current_state`, step count, rework count, cost, and created timestamp.
- Real-time search by ID and filtering by state category (`ACTIVE`, `WAITING`, `APPROVED`, `REJECTED`, `FAILED`).

### 3. Workflow Detail Inspector
- **State Machine Progression Stepper**: Visual stage-by-stage status indicator (`RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED/REJECTED`).
- **Append-Only Timeline**: Chronological history of state transitions with triggering event, acting agent, and transition rationale.
- **Specialist Agent Telemetry**: Execution cards for Extractor, Investigator, and Reviewer (latency, tokens in/out, retry count, failure category, sanitized contract output).
- **External Tools Log**: Invocations of `policy_lookup`, `claim_history_lookup`, `fraud_signal_lookup`, and `repair_cost_lookup` with execution timing and sanitized arguments.
- **Checkpoints**: Sequence numbers, durable checkpoint hashes, resumability flags, and integrity verification.
- **Budget Meters**: Progress gauges for token limits (150k limit) and cost ceiling ($5.00 limit).

### 4. Human-in-the-Loop Approval Queue
- Shows pending claims requiring authoritative human adjudication.
- Detail modal displaying the specialist agent's recommendation summary, recommended payout, confidence score, and adjudication deadline.
- **Role Enforcement**: The UI highlights the active user's permissions and disables decision buttons if the active role lacks authorization.
- Required reason input for audit compliance.

### 5. Deterministic Replay Simulator
- Prominent **REPLAY / SIMULATION MODE** safety banner.
- Selection of historical workflow runs.
- Execution of safe simulation replay: verifies path matching and deterministic state matching without external side effects or state mutation.

### 6. Evaluation & Failure Engineering Benchmark
- Summary metrics: 30 scenarios, pass rate (100%), path accuracy (100%), replay determinism (100%), approval safety (100%), failure containment (100%).
- Full scenario test matrix table with filters for nominal scenarios vs injected failure scenarios.

### 7. Operational Health Probes
- Process liveness (`GET /health`) status pill.
- Dependency readiness (`GET /ready`) verifying database connectivity, UnitOfWork transaction isolation, and checkpoint store health.

---

## Local Development & Build

```bash
# Install dependencies
cd ui
npm install

# Run Vite dev server with proxy to backend (port 3000 -> 8000)
npm run dev

# Run Vitest component tests
npm test

# Build production bundle for FastAPI static mounting
npm run build
```
