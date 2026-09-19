# CASEFILE Implementation Roadmap

**Document Version**: 1.0
**Last Updated**: 2026-09-19
**Status**: Phase 0 Complete - Ready for Phase 1

---

## Overview

This roadmap outlines the phased implementation of CASEFILE, an AI-powered insurance claim adjudication system. The project follows a structured approach: architecture design → foundation → core workflow → production hardening → optimization.

**Current Status**: ✅ Phase 0 Complete (Architecture & Design)

---

## Phase 0: Architecture & Design ✅ COMPLETE

**Duration**: 4 weeks
**Status**: ✅ Complete
**Gate**: Architecture Gate Review (PASSED 2026-09-19)

### Objectives
- Document complete system architecture
- Define all agents, contracts, and state machine
- Establish technical decisions (ADRs)
- Create comprehensive design specifications
- Validate architecture against requirements

### Deliverables ✅
- [x] 17 architecture documents (agent-architecture.md, state-machine.md, etc.)
- [x] 10 Architecture Decision Records (ADRs)
- [x] State machine definition (14 states, 8 terminal states)
- [x] Agent contracts (Pydantic models for all handoffs)
- [x] Budget enforcement strategy
- [x] Checkpoint/replay mechanism
- [x] Evaluation strategy
- [x] Security boundaries and authorization matrix
- [x] Observability plan (OpenTelemetry)
- [x] This roadmap document

### Success Criteria ✅
- ✅ All baseline requirements documented
- ✅ All elite engineering practices addressed
- ✅ No contradictions across documentation
- ✅ Architecture gate review passed

### Key Decisions
- **Orchestration**: LangGraph for workflow management (ADR-001)
- **Contracts**: Pydantic typed contracts, no free-form data (ADR-002)
- **Persistence**: PostgreSQL as system of record (ADR-003)
- **Caching**: Redis for tool responses and rate limiting (ADR-004)
- **LLM**: Provider-agnostic abstraction (ADR-005)
- **Resilience**: Checkpoint after every agent transition (ADR-006)
- **Budget**: Multi-dimensional pre-flight enforcement (ADR-007)
- **Observability**: OpenTelemetry for traces, logs, metrics (ADR-008)
- **Approval**: Human-in-the-loop approval gate (ADR-009)
- **Testing**: Checkpoint-based replay evaluation (ADR-010)

---

## Phase 1: Repository Foundation & Core Infrastructure

**Duration**: 3 weeks
**Status**: 🔜 Next Phase
**Dependencies**: Phase 0 complete

### Objectives
- Establish repository structure
- Set up development environment
- Implement core data models
- Deploy infrastructure (PostgreSQL, Redis)
- Create skeleton workflow

### Week 1: Repository Setup
- [ ] Initialize Python project (pyproject.toml with Poetry)
- [ ] Create directory structure (/src, /tests, /config, /infra, /scripts)
- [ ] Set up linting (ruff, mypy, black)
- [ ] Configure pre-commit hooks
- [ ] Set up CI/CD pipeline (GitHub Actions)
- [ ] Create .gitignore, README.md, CONTRIBUTING.md
- [ ] Document local development setup

### Week 2: Core Data Models
- [ ] Implement Pydantic contracts (agent-contracts.md)
  - ClaimInput, ExtractionRequest/Result
  - InvestigationRequest/Result, ReviewRequest/Result
  - WorkflowState enum, BudgetState, Checkpoint
- [ ] Create database schema (PostgreSQL migrations with Alembic)
  - workflow_runs, checkpoints, event_log, budget_state, evaluation_runs tables
- [ ] Implement persistence layer
  - Repository pattern for database access
  - Checkpoint storage and retrieval
  - Event log append-only writes
- [ ] Set up Redis integration
  - Connection pooling
  - Cache key patterns
  - Rate limit counters

### Week 3: Infrastructure & Skeleton Workflow
- [ ] Deploy PostgreSQL (Docker Compose for dev, RDS for production)
- [ ] Deploy Redis (Docker Compose for dev, ElastiCache for production)
- [ ] Implement LLM provider abstraction (ADR-005)
  - OpenAI provider
  - Anthropic provider
  - Token counting and cost calculation
- [ ] Create skeleton LangGraph workflow
  - Define states (14 states from state-machine.md)
  - Define edges (transitions)
  - Checkpoint configuration
- [ ] Set up OpenTelemetry instrumentation
  - Tracer and meter configuration
  - Span attributes (semantic conventions)
  - Export to Jaeger (dev) or Datadog (prod)

### Deliverables
- [ ] Python project with dependencies installed
- [ ] Database schema deployed (PostgreSQL + migrations)
- [ ] Redis cache operational
- [ ] LLM provider abstraction with OpenAI + Anthropic
- [ ] Empty LangGraph workflow (no agents yet)
- [ ] OpenTelemetry instrumentation skeleton
- [ ] CI pipeline running (lint, type check, test)

### Success Criteria
- [ ] `poetry install` completes without errors
- [ ] Database migrations apply successfully
- [ ] Redis connection tests pass
- [ ] LLM provider smoke tests pass (count tokens, estimate cost)
- [ ] Workflow state machine can transition between states
- [ ] OpenTelemetry traces exported to local Jaeger

---

## Phase 2: Agent Implementation

**Duration**: 4 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 1 complete

### Objectives
- Implement 4 agents (Supervisor, Extractor, Investigator, Reviewer)
- Integrate agents into LangGraph workflow
- Implement tool execution framework
- Create mock tools for testing

### Week 1: Supervisor & Extractor Agents
- [ ] Implement Supervisor agent
  - Workflow entry point
  - Route to Extractor
  - Budget pre-flight checks
  - State transition logic
- [ ] Implement Extractor agent
  - Prompt template for extraction
  - Structured output (ExtractionResult)
  - Pydantic validation
  - Error handling (malformed output)
- [ ] Unit tests for Supervisor and Extractor
- [ ] Integration test: RECEIVED → EXTRACTION transition

### Week 2: Investigator Agent & Tool Framework
- [ ] Implement tool execution framework
  - Tool interface (abstract base class)
  - Tool registry
  - Authorization checks (agent-to-tool permissions)
  - Retry logic (exponential backoff)
  - Tool call logging (event_log)
- [ ] Implement mock tools (5 tools)
  - policy_lookup (returns policy details)
  - claim_history_lookup (returns claim history)
  - repair_cost_lookup (returns cost estimates)
  - fraud_signal_lookup (returns fraud indicators)
  - document_retrieval (returns documents)
- [ ] Implement Investigator agent
  - Prompt template with tool descriptions
  - Tool call loop (iterative investigation)
  - Structured output (InvestigationResult)
  - Evidence aggregation
- [ ] Unit tests for tool framework and Investigator
- [ ] Integration test: EXTRACTION → INVESTIGATION transition

### Week 3: Reviewer Agent & Rework Loop
- [ ] Implement Reviewer agent
  - Prompt template for review
  - Decision logic (APPROVE/REJECT/REWORK)
  - Structured output (ReviewResult)
  - Confidence scoring
- [ ] Implement rework loop logic
  - REVIEW → REWORK_LOOP transition
  - REWORK_LOOP → INVESTIGATION transition
  - Rework counter tracking
  - Max rework limit enforcement (3 cycles)
- [ ] Unit tests for Reviewer agent
- [ ] Integration test: INVESTIGATION → REVIEW → REWORK_LOOP → INVESTIGATION

### Week 4: End-to-End Workflow
- [ ] Implement HUMAN_APPROVAL state handler
  - Create approval request
  - Block workflow (wait for decision)
  - Resume on approval/rejection
- [ ] Implement terminal state transitions
  - APPROVED, REJECTED, FAILED
  - BUDGET_EXHAUSTED, TIMEOUT, MAX_STEPS_EXCEEDED, MAX_REWORK_EXCEEDED, ESCALATION
- [ ] End-to-end integration tests
  - Happy path: RECEIVED → APPROVED
  - Rework path: RECEIVED → REWORK_LOOP → APPROVED
  - Rejection path: RECEIVED → REJECTED
- [ ] Error scenario tests
  - Agent failures, tool failures, timeout handling

### Deliverables
- [ ] 4 agents implemented (Supervisor, Extractor, Investigator, Reviewer)
- [ ] Tool execution framework with 5 mock tools
- [ ] Rework loop logic operational
- [ ] Human approval gate integrated
- [ ] All 14 workflow states reachable
- [ ] End-to-end workflow tests passing

### Success Criteria
- [ ] Workflow executes from ClaimInput to terminal state
- [ ] All agents produce valid Pydantic outputs
- [ ] Tool authorization enforced
- [ ] Rework loop executes correctly (max 3 cycles)
- [ ] Budget enforcement prevents agent invocation when limits exceeded
- [ ] 100% test coverage for agent logic

---

## Phase 3: Budget Enforcement & Termination

**Duration**: 2 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 2 complete

### Objectives
- Implement multi-dimensional budget tracking
- Enforce budget limits pre-flight
- Guarantee workflow termination
- Implement timeout handling

### Week 1: Budget Tracking & Enforcement
- [ ] Implement BudgetState tracking
  - Track tokens (input, output, total)
  - Track cost (USD)
  - Track steps, execution time, rework count
- [ ] Implement budget pre-flight checks
  - Estimate token usage before agent invocation
  - Check all dimensions (tokens, cost, steps, time, rework)
  - Reject invocation if any limit exceeded
- [ ] Implement budget post-flight updates
  - Update BudgetState after agent completion
  - Atomic database updates (transaction-protected)
  - Cache budget state in Redis (30s TTL)
- [ ] Budget exhaustion terminal states
  - BUDGET_EXHAUSTED (tokens or cost)
  - MAX_STEPS_EXCEEDED
  - MAX_REWORK_EXCEEDED

### Week 2: Timeout & Termination Guarantees
- [ ] Implement workflow timeout mechanism
  - Track execution time from workflow start
  - Check timeout before every agent invocation
  - Transition to TIMEOUT terminal state
- [ ] Implement termination guarantees
  - All workflows reach terminal state in ≤30 minutes
  - All workflows reach terminal state in ≤50 steps
  - No infinite loops possible
- [ ] Budget monitoring and alerting
  - Metrics: budget consumption percentage per dimension
  - Alerts at 75% and 90% thresholds
  - Dashboard for budget visualization
- [ ] Load testing
  - 100 concurrent workflows
  - Budget enforcement under load
  - No budget violations

### Deliverables
- [ ] Budget tracking operational (all 7 dimensions)
- [ ] Pre-flight budget checks enforced
- [ ] Timeout mechanism preventing runaway workflows
- [ ] Terminal state guarantees verified
- [ ] Budget monitoring dashboard

### Success Criteria
- [ ] No workflow exceeds token budget (150K tokens)
- [ ] No workflow exceeds cost budget ($5.00)
- [ ] No workflow exceeds 50 steps
- [ ] No workflow exceeds 30 minutes
- [ ] No workflow exceeds 3 rework cycles
- [ ] Budget pre-flight checks reject invocations correctly
- [ ] Load test: 100 workflows complete without budget violations

---

## Phase 4: Checkpoint & Replay

**Duration**: 2 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 2 complete

### Objectives
- Implement checkpoint creation after every agent transition
- Implement workflow resume from checkpoint
- Implement deterministic replay
- Verify checkpoint version compatibility

### Week 1: Checkpoint Creation & Resume
- [ ] Implement checkpoint creation
  - Serialize workflow state to Checkpoint model
  - Capture agent outputs, budget state, step number
  - Include version hash (workflow definition version)
  - Write to PostgreSQL checkpoints table
  - Async checkpoint writes (non-blocking)
- [ ] Implement checkpoint resume
  - Load checkpoint from database
  - Validate version compatibility
  - Restore workflow state
  - Resume from last completed step
- [ ] Checkpoint storage optimization
  - JSONB compression in PostgreSQL
  - Incremental checkpoints (delta encoding)
  - Checkpoint pruning (delete old checkpoints after workflow completion)

### Week 2: Deterministic Replay
- [ ] Implement replay mechanism
  - Load checkpoint
  - Replay tool calls from event log
  - Verify deterministic execution (outputs match)
  - Detect divergences
- [ ] Replay modes
  - VERIFY: Re-execute and compare
  - RESUME: Use cached outputs
- [ ] Replay testing
  - Capture 10 checkpoints from various workflow stages
  - Replay all checkpoints, verify determinism
  - Test version compatibility (reject stale checkpoints)

### Deliverables
- [ ] Checkpoint creation after every agent transition
- [ ] Resume from checkpoint operational
- [ ] Deterministic replay verified
- [ ] Checkpoint version validation

### Success Criteria
- [ ] Workflow interrupted → resume from checkpoint → complete successfully
- [ ] Replay from checkpoint produces identical results (100% determinism)
- [ ] Stale checkpoints rejected (version mismatch detected)
- [ ] Checkpoint write latency <100ms (non-blocking)

---

## Phase 5: Evaluation & Testing

**Duration**: 3 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 4 complete

### Objectives
- Create evaluation corpus (30+ scenarios)
- Implement replay-based evaluation suite
- Integrate evaluation into CI/CD
- Achieve 100% scenario pass rate

### Week 1: Evaluation Corpus Creation
- [ ] Define 30+ evaluation scenarios (evaluation.md)
  - 5 happy path scenarios
  - 5 rework scenarios
  - 5 rejection scenarios
  - 5 budget limit scenarios
  - 3 tool failure scenarios
  - 4 edge case scenarios
  - 3 escalation scenarios
- [ ] Capture checkpoints for each scenario
  - Execute scenario with mock tools
  - Save checkpoint at key workflow states
  - Validate expected terminal state reached
- [ ] Store evaluation corpus
  - Save scenarios to evaluation_runs table
  - Tag scenarios (happy_path, rework, budget, etc.)
  - Document expected outcomes

### Week 2: Evaluation Suite Implementation
- [ ] Implement evaluation runner
  - Load scenario from corpus
  - Replay from checkpoint
  - Compare actual vs expected terminal state
  - Report divergences
- [ ] Implement evaluation assertions
  - assert_terminal_state
  - assert_rework_count
  - assert_budget_within_limit
  - assert_deterministic_replay
- [ ] Parallel evaluation execution
  - Run 10 scenarios in parallel
  - Aggregate results
  - Generate HTML report

### Week 3: CI Integration & Regression Testing
- [ ] Integrate evaluation into CI/CD
  - Run evaluation suite on every PR
  - Fail CI if any scenario fails
  - Upload evaluation report as artifact
- [ ] Regression testing
  - Baseline: Current code version passes 100% of scenarios
  - Any code change must pass 100% of scenarios
  - Track evaluation pass rate over time
- [ ] Performance regression testing
  - Track token usage, cost, latency per scenario
  - Alert if metrics degrade >10%

### Deliverables
- [ ] 30+ evaluation scenarios with checkpoints
- [ ] Evaluation suite (replay-based)
- [ ] CI integration (automated regression testing)
- [ ] Evaluation dashboard

### Success Criteria
- [ ] 30+ evaluation scenarios captured
- [ ] 100% scenario pass rate
- [ ] CI fails if evaluation scenarios fail
- [ ] Evaluation suite runs in <10 minutes
- [ ] No regressions introduced by code changes

---

## Phase 6: Observability & Monitoring

**Duration**: 2 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 2 complete

### Objectives
- Deploy OpenTelemetry instrumentation
- Set up trace backend (Datadog or Honeycomb)
- Create observability dashboards
- Configure alerting

### Week 1: OpenTelemetry Deployment
- [ ] Configure OTel exporters
  - Jaeger (local dev)
  - Datadog or Honeycomb (production)
- [ ] Instrument all agents
  - Workflow span (root)
  - Agent invocation spans
  - Tool call spans
  - State transition events
- [ ] Add span attributes
  - Workflow, agent, tool, budget attributes
  - Semantic conventions (observability.md)
- [ ] Structured logging
  - Correlate logs with traces (trace_id)
  - JSON log format
  - Log levels (INFO, WARNING, ERROR)

### Week 2: Dashboards & Alerting
- [ ] Create observability dashboards
  - Workflow success rate by terminal state
  - Average workflow duration, token usage, cost
  - Tool call latency and error rates
  - Budget consumption heatmaps
  - Rework frequency
- [ ] Configure alerts
  - Workflow failure rate >5%
  - Average cost >$4 (80% of budget)
  - Average duration >10 minutes
  - Tool error rate >10%
- [ ] Cost attribution reports
  - Cost per agent (Extractor, Investigator, Reviewer)
  - Cost per workflow
  - Cost trends over time

### Deliverables
- [ ] OpenTelemetry traces exported to backend
- [ ] Observability dashboards (5+ dashboards)
- [ ] Alerting configured (Slack, PagerDuty)
- [ ] Cost attribution reports

### Success Criteria
- [ ] 100% of workflows have traces in backend
- [ ] Dashboards visualize key metrics
- [ ] Alerts fire correctly (tested with synthetic failures)
- [ ] Cost per workflow tracked accurately

---

## Phase 7: Security & Compliance

**Duration**: 2 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 2 complete

### Objectives
- Implement security boundaries
- Enforce agent-to-tool authorization
- Implement audit logging
- Validate prompt injection defenses

### Week 1: Authorization & Audit Logging
- [ ] Implement agent-to-tool authorization
  - Authorization matrix (security.md)
  - Pre-check before tool execution
  - Reject unauthorized tool calls
- [ ] Implement audit logging
  - Log all agent invocations (who, what, when, outcome)
  - Log all tool calls
  - Log all state transitions
  - Immutable audit trail (event_log table)
- [ ] Trust boundaries
  - Validate tool responses (untrusted)
  - Validate LLM outputs (Pydantic validation)
  - Sanitize inputs before including in prompts

### Week 2: Prompt Injection & Penetration Testing
- [ ] Implement prompt injection defenses
  - Sanitize tool responses
  - Separate system/user message contexts
  - Structured output enforcement
- [ ] Penetration testing
  - Attempt prompt injection attacks
  - Attempt unauthorized tool access
  - Attempt budget manipulation
- [ ] Security audit
  - Review all trust boundaries
  - Review all authorization checks
  - Review all audit logging

### Deliverables
- [ ] Agent-to-tool authorization enforced
- [ ] Complete audit trail (all actions logged)
- [ ] Prompt injection defenses validated
- [ ] Security audit report

### Success Criteria
- [ ] Unauthorized tool calls rejected (100% enforcement)
- [ ] Audit log captures all critical actions
- [ ] Prompt injection attacks mitigated
- [ ] Penetration testing report shows no critical vulnerabilities

---

## Phase 8: Production Hardening

**Duration**: 3 weeks
**Status**: 🔜 Future
**Dependencies**: Phases 1-7 complete

### Objectives
- Implement failure recovery
- Deploy to production infrastructure
- Load testing and performance optimization
- Runbook and incident response

### Week 1: Failure Recovery
- [ ] Implement retry logic
  - Transient failures: exponential backoff (max 3 retries)
  - Permanent failures: surface to agent or FAILED state
- [ ] Implement tool failure handling
  - Tool timeout → retry or continue with partial evidence
  - Tool error → log and continue
- [ ] Implement database failure handling
  - Connection pool exhaustion → retry
  - Checkpoint write failure → FAILED state
- [ ] Circuit breakers
  - Disable failing tools temporarily
  - Re-enable after cooldown period

### Week 2: Production Deployment
- [ ] Deploy to production infrastructure
  - PostgreSQL RDS (multi-AZ, automated backups)
  - Redis ElastiCache (replication enabled)
  - Application servers (ECS or Kubernetes)
  - Load balancer (ALB)
- [ ] Configure autoscaling
  - Scale workers based on workflow queue depth
  - Scale database read replicas for analytics
- [ ] Configure backups
  - PostgreSQL: automated daily backups, 30-day retention
  - Point-in-time recovery enabled
- [ ] Deploy monitoring
  - CloudWatch or Datadog for infrastructure metrics
  - OpenTelemetry for application traces

### Week 3: Load Testing & Optimization
- [ ] Load testing
  - 100 concurrent workflows
  - 1000 workflows/hour sustained
  - Measure latency, throughput, error rate
- [ ] Performance optimization
  - Database query optimization (pg_stat_statements)
  - Connection pooling tuning (PgBouncer)
  - Redis cache hit rate optimization
- [ ] Runbook creation
  - Incident response procedures
  - Escalation paths
  - Debugging guides (how to read traces, replay workflows)

### Deliverables
- [ ] Failure recovery mechanisms operational
- [ ] Production deployment complete
- [ ] Load testing report (100 concurrent workflows)
- [ ] Runbook and incident response procedures

### Success Criteria
- [ ] System handles 1000 workflows/hour sustained
- [ ] <1% error rate under load
- [ ] Mean workflow latency <5 minutes
- [ ] P95 workflow latency <10 minutes
- [ ] Database and Redis HA configurations operational
- [ ] Runbook tested with synthetic incidents

---

## Phase 9: Real Tool Integration

**Duration**: 4 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 8 complete

### Objectives
- Replace mock tools with real external APIs
- Implement tool response caching (Redis)
- Implement rate limiting
- Validate end-to-end with real data

### Week 1-2: Real Tool Implementation
- [ ] Implement policy_lookup tool
  - Integrate with policy management system API
  - Cache responses in Redis (1 hour TTL)
  - Rate limit: 100 calls/minute
- [ ] Implement claim_history_lookup tool
  - Integrate with claims database API
  - Cache responses in Redis (1 hour TTL)
  - Rate limit: 50 calls/minute
- [ ] Implement repair_cost_lookup tool
  - Integrate with repair cost database API
  - Cache responses in Redis (1 hour TTL)
  - Rate limit: 100 calls/minute
- [ ] Implement fraud_signal_lookup tool
  - Integrate with fraud detection system API
  - Cache responses in Redis (30 min TTL)
  - Rate limit: 50 calls/minute
- [ ] Implement document_retrieval tool
  - Integrate with document storage system
  - Cache responses in Redis (2 hours TTL)
  - Rate limit: 20 calls/minute

### Week 3: Testing with Real Data
- [ ] Integration testing with real APIs
  - Test all tools with production-like data
  - Validate response schemas
  - Test error handling (API timeouts, rate limits)
- [ ] End-to-end testing with real claims
  - Process 10 real claims through workflow
  - Validate terminal states match manual adjudication
  - Compare investigation results with human adjusters

### Week 4: Tool Monitoring & Optimization
- [ ] Tool performance monitoring
  - Track tool call latency
  - Track cache hit rates
  - Track rate limit violations
- [ ] Tool optimization
  - Tune cache TTLs based on data freshness requirements
  - Adjust rate limits based on API capacity
  - Optimize query parameters to reduce API latency

### Deliverables
- [ ] 5 real tools integrated (replace mocks)
- [ ] Tool response caching operational (Redis)
- [ ] Rate limiting enforced
- [ ] Real claims processed end-to-end

### Success Criteria
- [ ] All tools return valid responses for real claims
- [ ] Cache hit rate >70% for policy_lookup and claim_history_lookup
- [ ] Rate limits prevent API abuse (0 violations)
- [ ] 10 real claims processed, terminal states validated

---

## Phase 10: Human Approval UI

**Duration**: 3 weeks
**Status**: 🔜 Future
**Dependencies**: Phase 8 complete

### Objectives
- Build approval dashboard for human reviewers
- Implement approval request queue
- Integrate approval decisions with workflow
- Deploy approval UI to production

### Week 1: Approval Dashboard UI
- [ ] Build approval queue view
  - List pending approval requests
  - Sort by priority, age, claim amount
  - Filter by terminal state recommendation
- [ ] Build claim detail view
  - Display extraction, investigation, review results
  - Show evidence and tool calls
  - Display budget consumption
  - Show reviewer recommendation and reasoning

### Week 2: Approval Decision Flow
- [ ] Implement approval decision form
  - Radio buttons: APPROVE / REJECT
  - Text field: Decision reason
  - Submit button
- [ ] Integrate approval decision with workflow
  - Submit decision → resume workflow from checkpoint
  - Transition to APPROVED or REJECTED terminal state
  - Log approval decision to audit trail
- [ ] Implement timeout handling
  - Display approval deadline
  - Escalate if no decision by deadline
  - Notify escalation team

### Week 3: Deployment & Training
- [ ] Deploy approval UI to production
  - Authentication (SSO, RBAC)
  - Authorization (only approvers can approve)
- [ ] User training
  - Train human approvers on UI
  - Document approval guidelines
  - Create video walkthrough
- [ ] Approval metrics
  - Track approval latency (request → decision)
  - Track approval decision distribution (APPROVED vs REJECTED)
  - Alert on stale approvals (>24 hours)

### Deliverables
- [ ] Approval dashboard deployed
- [ ] Approval decision integration operational
- [ ] User training completed
- [ ] Approval metrics tracked

### Success Criteria
- [ ] Approvers can view pending requests
- [ ] Approvers can submit decisions (APPROVE/REJECT)
- [ ] Workflow resumes after approval decision
- [ ] 95% of approvals decided within 24 hours (SLO)

---

## Phase 11: Optimization & Scaling

**Duration**: 4 weeks
**Status**: 🔜 Future
**Dependencies**: Phases 9-10 complete

### Objectives
- Optimize costs (reduce LLM token usage)
- Improve latency (parallel tool calls)
- Scale to 10,000 workflows/hour
- Implement auto-approval for low-risk claims

### Week 1: Cost Optimization
- [ ] Prompt optimization
  - Reduce prompt length (remove redundant context)
  - Use smaller models where feasible (Claude Haiku for Extractor)
- [ ] Caching optimization
  - Increase cache TTLs where appropriate
  - Implement prompt caching (provider feature)
- [ ] Model selection optimization
  - A/B test cheaper models for non-critical agents
  - Track cost vs quality tradeoff

### Week 2: Latency Optimization
- [ ] Parallel tool calls
  - Investigator calls multiple tools concurrently
  - Reduce investigation latency by 50%
- [ ] Async agent invocations
  - Non-blocking LLM calls
  - Concurrent checkpoint writes
- [ ] Database query optimization
  - Index optimization
  - Query plan analysis (EXPLAIN ANALYZE)

### Week 3: Horizontal Scaling
- [ ] Scale to 10,000 workflows/hour
  - Horizontal scaling of application workers
  - Database read replica for analytics queries
  - Redis cluster for higher cache throughput
- [ ] Load testing at scale
  - 10,000 workflows/hour sustained
  - Measure latency, error rate, cost
- [ ] Auto-scaling configuration
  - Scale workers based on queue depth
  - Scale database connections based on load

### Week 4: Auto-Approval (Low-Risk Claims)
- [ ] Define auto-approval policy
  - Claim amount <$5,000
  - Reviewer confidence >0.95
  - No fraud signals detected
  - No rework cycles
- [ ] Implement auto-approval logic
  - Skip HUMAN_APPROVAL state if policy met
  - Transition directly: REVIEW → APPROVED
- [ ] Monitor auto-approval
  - Track auto-approval rate
  - Track false positive rate (auto-approved but should have been rejected)
  - A/B test: auto-approval vs human approval

### Deliverables
- [ ] Cost reduced by 30% (prompt optimization, model selection)
- [ ] Latency reduced by 40% (parallel tool calls, async operations)
- [ ] System scales to 10,000 workflows/hour
- [ ] Auto-approval operational for low-risk claims

### Success Criteria
- [ ] Average cost per workflow <$3.50 (down from $5.00 budget)
- [ ] Average workflow latency <3 minutes (down from 5 minutes)
- [ ] System handles 10,000 workflows/hour with <1% error rate
- [ ] Auto-approval rate 30-40% of claims, false positive rate <2%

---

## Phase 12: Advanced Features

**Duration**: Ongoing
**Status**: 🔜 Future
**Dependencies**: Phase 11 complete

### Features for Future Releases

#### Multi-Language Support
- Translate claim documents before extraction
- Support for Spanish, French, German claims

#### Advanced Fraud Detection
- Integrate ML-based fraud scoring models
- Cross-reference external fraud databases

#### Partial Approval
- Support partial claim approval (approve $10K out of $15K requested)
- Agent negotiations (Investigator proposes counter-offer)

#### Batch Processing
- Process multiple claims in batch (shared context)
- Bulk claim adjudication for disaster scenarios

#### Explainability
- Generate human-readable explanation of decision
- Highlight key evidence that influenced decision
- Counterfactual explanations ("what if X were different")

#### Active Learning
- Capture human reviewer feedback
- Fine-tune agents based on human corrections
- Continuous improvement loop

---

## Success Metrics

### System-Level Metrics
- **Throughput**: 10,000 workflows/hour sustained
- **Latency**: P50 <3 min, P95 <8 min
- **Error Rate**: <1% workflows reach FAILED state
- **Cost**: Average $3.50 per workflow
- **Availability**: 99.9% uptime

### Quality Metrics
- **Accuracy**: 95% agreement with human adjusters (on evaluation corpus)
- **Rework Rate**: <10% of workflows require rework
- **Auto-Approval Rate**: 30-40% of claims (low-risk)
- **False Positive Rate**: <2% (auto-approved but should have been rejected)

### Operational Metrics
- **Approval Latency**: 95% of approvals decided within 24 hours
- **Evaluation Pass Rate**: 100% of evaluation scenarios pass
- **Budget Compliance**: 0 workflows exceed budget limits
- **Security**: 0 unauthorized tool access attempts succeed

---

## Risk Management

### Technical Risks
| Risk | Mitigation |
|------|------------|
| LLM non-determinism breaks replay | Use fixed random seeds, cache LLM outputs in event log |
| External API changes break tools | Version tool contracts, monitor API schema changes |
| Database performance degrades | Connection pooling, read replicas, query optimization |
| Budget limits too restrictive | Track typical consumption, adjust limits based on data |
| Checkpoint storage grows unbounded | Prune old checkpoints, compress JSONB, use incremental checkpoints |

### Business Risks
| Risk | Mitigation |
|------|------------|
| Human approvers overwhelmed | Auto-approval for low-risk claims, prioritization queue |
| Incorrect adjudications | Evaluation corpus validates correctness, human approval gate |
| Regulatory non-compliance | Audit logging, human-in-the-loop, explainability |
| High operational costs | Cost optimization, model selection, caching |

---

## Dependencies

### External Dependencies
- **LLM Providers**: OpenAI, Anthropic (API access required)
- **Infrastructure**: AWS or GCP (PostgreSQL RDS, Redis ElastiCache, compute)
- **Observability**: Datadog or Honeycomb (trace backend)
- **External APIs**: Policy management, claims database, fraud detection, document storage

### Internal Dependencies
- **Phase 0 → Phase 1**: Architecture complete before implementation
- **Phase 1 → Phase 2**: Infrastructure ready before agents
- **Phase 2 → Phase 3**: Agents implemented before budget enforcement
- **Phase 4 → Phase 5**: Checkpoint/replay ready before evaluation
- **Phase 8 → Phase 9**: Production hardening before real tool integration
- **Phase 8 → Phase 10**: Production hardening before approval UI

---

## Team & Roles

### Required Roles (Phase 1-8)
- **Tech Lead**: Architecture decisions, code review
- **Backend Engineers (2)**: Agent implementation, workflow logic, persistence
- **Infrastructure Engineer**: PostgreSQL, Redis, deployment, monitoring
- **QA Engineer**: Testing, evaluation corpus creation, CI/CD
- **Product Manager**: Requirements, prioritization, stakeholder communication

### Additional Roles (Phase 9+)
- **Frontend Engineer**: Approval dashboard UI
- **Data Engineer**: Tool integration, data pipelines
- **ML Engineer**: Auto-approval policy, fraud detection models

---

## Conclusion

CASEFILE follows a structured 12-phase roadmap from architecture design to production deployment and optimization. Each phase builds on the previous, with clear deliverables and success criteria. The roadmap prioritizes:

1. **Solid foundation**: Phase 0-1 establish architecture and infrastructure
2. **Core functionality**: Phase 2-4 implement agents, budget, checkpoints
3. **Quality assurance**: Phase 5-7 evaluation, observability, security
4. **Production readiness**: Phase 8-10 hardening, real tools, approval UI
5. **Optimization**: Phase 11-12 cost, latency, scaling, advanced features

**Current Status**: Phase 0 complete, ready to begin Phase 1 implementation.

---

## References

- Architecture documents: `/docs/*.md`
- Architecture Decision Records: `/docs/adr/ADR-*.md`
- Phase 0 Completion Report: `/docs/phase-0-completion.md` (to be created)
