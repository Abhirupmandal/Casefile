# CASEFILE Phase 6 Evaluation Report

- **Run ID**: `a50833f8-36e2-45f0-98f7-d659f15c340e`
- **Timestamp**: 2026-10-04T19:00:11.408711+00:00
- **Evaluation Version**: 1.0.0
- **Total Scenarios**: 30
- **Passed**: 30
- **Failed**: 0
- **Pass Rate**: **100.00%**

## Summary Metrics

| Metric | Value |
|---|---|
| `approval_safety_rate` | 1.0000 |
| `average_retries_per_claim` | 0.1000 |
| `average_rework_cycles` | 0.1667 |
| `average_steps_per_claim` | 5.7667 |
| `budget_enforcement_rate` | 1.0000 |
| `budget_utilization` | 0.0007 |
| `checkpoint_resume_success_rate` | 1.0000 |
| `estimated_cost_per_claim` | 0.0006 |
| `evaluation_pass_rate` | 1.0000 |
| `failure_containment_rate` | 1.0000 |
| `failure_recovery_rate` | 1.0000 |
| `invariant_pass_rate` | 1.0000 |
| `invariant_results` | 119 |
| `invariants_failed` | 0 |
| `invariants_passed` | 119 |
| `invariants_skipped` | 0 |
| `maximum_rework_enforcement_rate` | 1.0000 |
| `maximum_step_enforcement_rate` | 1.0000 |
| `mean_agent_executions` | 2.9000 |
| `mean_retries` | 0.1000 |
| `mean_rework` | 0.1667 |
| `mean_steps` | 5.7667 |
| `mean_tool_invocations` | 2.0000 |
| `path_accuracy` | 1.0000 |
| `persistence_consistency_rate` | 1.0000 |
| `recovery_rate` | 1.0000 |
| `replay_consistency_rate` | 1.0000 |
| `replay_determinism_rate` | 1.0000 |
| `retry_success_rate` | 1.0000 |
| `rework_success_rate` | 1.0000 |
| `scenario_count` | 30 |
| `scenario_pass_rate` | 1.0000 |
| `scenarios_errored` | 0 |
| `scenarios_failed` | 0 |
| `scenarios_passed` | 30 |
| `schema_version` | 1.0.0 |
| `source` | evaluation |
| `telemetry_isolation_rate` | 1.0000 |
| `terminal_reason_distribution` | {'APPROVAL_GRANTED': 14, 'REWORK_EXHAUSTED': 1, 'INVESTIGATION_FAILED': 4, 'REVIEW_REJECTED': 6, 'MAX_COST_EXCEEDED': 1, 'MAX_STEPS_EXCEEDED': 1, 'APPROVAL_REJECTED': 2, 'NONE': 1} |
| `terminal_state_accuracy` | 1.0000 |
| `tool_authorization_safety_rate` | 1.0000 |
| `total_cost_usd` | 0.0171 |
| `total_input_tokens` | 8480 |
| `total_output_tokens` | 4220 |
| `unauthorized_approval_rejection_rate` | 1.0000 |

## Scenario Execution & Exact Node Path Results

| Case ID | Name | Status | Terminal State | Path Match | Replay Match | Retries | Rework | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| `CASE-001` | Nominal collision claim | **PASS** | `APPROVED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-002` | Nominal comprehensive claim | **PASS** | `APPROVED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-003` | Nominal property damage claim | **PASS** | `APPROVED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-004` | Nominal windshield replacement | **PASS** | `APPROVED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-005` | Nominal hit-and-run with police statement | **PASS** | `APPROVED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-006` | Reviewer rework: missing police statement | **PASS** | `APPROVED` | YES | N/A | 0 | 1 | `$0.0010` |
| `CASE-007` | Reviewer rework: inconsistent repair estimate | **PASS** | `APPROVED` | YES | N/A | 0 | 1 | `$0.0010` |
| `CASE-008` | Reviewer rework: prior history clarification | **PASS** | `APPROVED` | YES | N/A | 0 | 1 | `$0.0010` |
| `CASE-009` | Reviewer rework exhaustion (max rework limit) | **PASS** | `MAX_REWORK_EXCEEDED` | YES | N/A | 0 | 1 | `$0.0010` |
| `CASE-010` | Policy lookup tool failure terminates node safely | **PASS** | `FAILED` | YES | N/A | 0 | 0 | `$0.0002` |
| `CASE-011` | Policy lookup tool timeout | **PASS** | `FAILED` | YES | N/A | 0 | 0 | `$0.0002` |
| `CASE-012` | Fraud lookup service unavailable | **PASS** | `FAILED` | YES | N/A | 0 | 0 | `$0.0002` |
| `CASE-013` | Fraud signal tool exception | **PASS** | `FAILED` | YES | N/A | 0 | 0 | `$0.0002` |
| `CASE-014` | Extractor provider transient error recovery | **PASS** | `APPROVED` | YES | N/A | 1 | 0 | `$0.0004` |
| `CASE-015` | Investigator provider retry on transient timeout | **PASS** | `APPROVED` | YES | N/A | 1 | 0 | `$0.0006` |
| `CASE-016` | Malformed agent output recovers on retry | **PASS** | `APPROVED` | YES | N/A | 1 | 0 | `$0.0005` |
| `CASE-017` | Claim amount exceeds vehicle market value | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-018` | Police report conflicts with claimant statement | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-019` | Conflicting repair labor rates resolved via rework | **PASS** | `APPROVED` | YES | N/A | 0 | 1 | `$0.0010` |
| `CASE-020` | Policy lapsed prior to incident date | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-021` | Missing essential proof of loss documents | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-022` | High fraud score: multiple recent total-loss claims | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-023` | Staged accident indicators flagged | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-024` | Cost budget ceiling exhausted | **PASS** | `BUDGET_EXHAUSTED` | YES | N/A | 0 | 0 | `$0.0002` |
| `CASE-025` | Maximum workflow steps exhausted | **PASS** | `MAX_STEPS_EXCEEDED` | YES | N/A | 0 | 0 | `$0.0004` |
| `CASE-026` | Human adjuster rejects claim recommendation | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-027` | Human adjuster denies dubious commercial loss | **PASS** | `REJECTED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-028` | Process restart and resume from checkpoint | **PASS** | `APPROVED` | YES | N/A | 0 | 0 | `$0.0006` |
| `CASE-029` | Deterministic replay reproduces live trajectory | **PASS** | `APPROVED` | YES | MATCH | 0 | 0 | `$0.0006` |
| `CASE-030` | Corrupted checkpoint detected and rejected | **PASS** | `NONE` | YES | N/A | 0 | 0 | `$0.0002` |

## Detailed Exact Node Paths

### CASE-001: Nominal collision claim
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-002: Nominal comprehensive claim
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-003: Nominal property damage claim
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-004: Nominal windshield replacement
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-005: Nominal hit-and-run with police statement
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-006: Reviewer rework: missing police statement
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REWORK_LOOP -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-007: Reviewer rework: inconsistent repair estimate
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REWORK_LOOP -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-008: Reviewer rework: prior history clarification
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REWORK_LOOP -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-009: Reviewer rework exhaustion (max rework limit)
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REWORK_LOOP -> INVESTIGATION -> REVIEW -> MAX_REWORK_EXCEEDED`
- **Failure Classification**: Category=`REWORK_EXHAUSTION`, Stage=`REVIEW`, Type=`MAX_REWORK_EXCEEDED`, Contained=`True`

### CASE-010: Policy lookup tool failure terminates node safely
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> FAILED`
- **Errors/Notes**: `injected-tool:policy_lookup`
- **Failure Classification**: Category=`TOOL_FAILURE`, Stage=`INVESTIGATION`, Type=`TOOL_ERROR`, Contained=`True`

### CASE-011: Policy lookup tool timeout
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> FAILED`
- **Errors/Notes**: `injected-tool:policy_lookup`
- **Failure Classification**: Category=`TOOL_FAILURE`, Stage=`INVESTIGATION`, Type=`TOOL_ERROR`, Contained=`True`

### CASE-012: Fraud lookup service unavailable
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> FAILED`
- **Errors/Notes**: `injected-tool:fraud_signal_lookup`
- **Failure Classification**: Category=`TOOL_FAILURE`, Stage=`INVESTIGATION`, Type=`TOOL_ERROR`, Contained=`True`

### CASE-013: Fraud signal tool exception
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> FAILED`
- **Errors/Notes**: `injected-tool:fraud_signal_lookup`
- **Failure Classification**: Category=`TOOL_FAILURE`, Stage=`INVESTIGATION`, Type=`TOOL_ERROR`, Contained=`True`

### CASE-014: Extractor provider transient error recovery
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-015: Investigator provider retry on transient timeout
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-016: Malformed agent output recovers on retry
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-017: Claim amount exceeds vehicle market value
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-018: Police report conflicts with claimant statement
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-019: Conflicting repair labor rates resolved via rework
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REWORK_LOOP -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-020: Policy lapsed prior to incident date
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-021: Missing essential proof of loss documents
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-022: High fraud score: multiple recent total-loss claims
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-023: Staged accident indicators flagged
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-024: Cost budget ceiling exhausted
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> BUDGET_EXHAUSTED`
- **Failure Classification**: Category=`BUDGET_EXHAUSTION`, Stage=`BUDGET`, Type=`BUDGET_EXHAUSTION`, Contained=`True`

### CASE-025: Maximum workflow steps exhausted
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> MAX_STEPS_EXCEEDED`
- **Failure Classification**: Category=`STEP_EXHAUSTION`, Stage=`SUPERVISOR`, Type=`MAX_STEPS_EXCEEDED`, Contained=`True`

### CASE-026: Human adjuster rejects claim recommendation
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-027: Human adjuster denies dubious commercial loss
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> REJECTED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-028: Process restart and resume from checkpoint
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-029: Deterministic replay reproduces live trajectory
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL -> APPROVED`
- **Failure Classification**: Category=`NOMINAL`, Stage=`SUPERVISOR`, Type=`NONE`, Contained=`True`

### CASE-030: Corrupted checkpoint detected and rejected
- **Status**: PASS
- **Actual Path**: `RECEIVED -> EXTRACTION -> INVESTIGATION`
- **Errors/Notes**: `CheckpointCorruptError`
- **Failure Classification**: Category=`CHECKPOINT_FAILURE`, Stage=`CHECKPOINT`, Type=`CHECKPOINT_WRITE_ERROR`, Contained=`True`
