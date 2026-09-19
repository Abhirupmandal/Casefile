# ADR-010: Evaluation Strategy

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `evaluation.md`, `testing.md`, `replay.md`, `checkpointing.md`

## Context

CASEFILE is a production AI system making financial decisions. We require rigorous evaluation to ensure:

- **Correctness**: Workflows reach expected terminal states
- **Determinism**: Replaying from checkpoints produces identical results
- **Regression prevention**: Code changes don't break existing functionality
- **Performance tracking**: Monitor latency, cost, token usage over time
- **Rework validation**: Reviewer rework cycles function correctly
- **Budget enforcement**: Limits prevent runaway costs
- **Failure handling**: System gracefully handles errors, timeouts, budget exhaustion

Requirements:
- **Comprehensive corpus**: Minimum 30 evaluation scenarios covering happy path, edge cases, failures
- **Replay-based testing**: Deterministic replay from checkpoints
- **Automated CI integration**: Evaluation runs on every code change
- **Golden datasets**: Known-good inputs and expected outputs
- **Regression detection**: Alert when evaluation results differ from baseline
- **Cost tracking**: Evaluation runs must not consume excessive LLM budget

## Decision

We will implement **checkpoint-based replay evaluation** with a curated corpus of 30+ scenarios.

### Evaluation Corpus

```python
@dataclass
class EvaluationScenario:
    """Single evaluation test case."""
    scenario_id: str
    scenario_name: str
    description: str

    # Input
    claim_input: ClaimInput

    # Expected outcome
    expected_terminal_state: WorkflowState
    expected_rework_count: int
    expected_max_cost_usd: Decimal
    expected_max_tokens: int

    # Checkpoint for replay
    checkpoint_id: UUID | None = None  # If capturing from live run

    # Tags for filtering
    tags: list[str]  # e.g., ["happy_path", "high_value", "fraud"]

# Minimum 30 scenarios required
EVALUATION_CORPUS = [
    # Happy path
    EvaluationScenario(
        scenario_id="eval-001",
        scenario_name="happy_path_simple_approval",
        description="Straightforward claim, all evidence available, Reviewer approves immediately",
        expected_terminal_state=WorkflowState.APPROVED,
        expected_rework_count=0,
        tags=["happy_path", "baseline"],
    ),

    # Rework scenarios
    EvaluationScenario(
        scenario_id="eval-002",
        scenario_name="reviewer_triggers_single_rework",
        description="Reviewer identifies missing evidence, triggers one rework cycle",
        expected_terminal_state=WorkflowState.APPROVED,
        expected_rework_count=1,
        tags=["rework", "happy_path"],
    ),

    EvaluationScenario(
        scenario_id="eval-003",
        scenario_name="max_rework_exceeded",
        description="Reviewer triggers 3 rework cycles, max rework limit reached",
        expected_terminal_state=WorkflowState.MAX_REWORK_EXCEEDED,
        expected_rework_count=3,
        tags=["rework", "limit"],
    ),

    # Budget scenarios
    EvaluationScenario(
        scenario_id="eval-004",
        scenario_name="token_budget_exhaustion",
        description="Complex claim with extensive investigation exhausts token budget",
        expected_terminal_state=WorkflowState.BUDGET_EXHAUSTED,
        expected_max_tokens=150_000,
        tags=["budget", "limit"],
    ),

    EvaluationScenario(
        scenario_id="eval-005",
        scenario_name="cost_budget_exhaustion",
        description="Multiple expensive LLM calls exhaust cost budget",
        expected_terminal_state=WorkflowState.BUDGET_EXHAUSTED,
        expected_max_cost_usd=Decimal("5.00"),
        tags=["budget", "limit"],
    ),

    # Rejection scenarios
    EvaluationScenario(
        scenario_id="eval-006",
        scenario_name="fraud_detected_rejection",
        description="Fraud signals detected, Reviewer rejects claim",
        expected_terminal_state=WorkflowState.REJECTED,
        expected_rework_count=0,
        tags=["rejection", "fraud"],
    ),

    # ... 24 more scenarios covering:
    # - Timeout scenarios
    # - Max steps exceeded
    # - Tool failure recovery
    # - Extraction errors
    # - Investigation edge cases
    # - High-value claims
    # - Complex policies
    # - Missing documentation
]
```

### Evaluation Execution

#### 1. Capture Phase (One-Time Setup)
```python
async def capture_evaluation_scenario(scenario: EvaluationScenario):
    """
    Execute scenario live, capture checkpoint for future replay.
    Run once to populate evaluation corpus.
    """
    # Execute workflow with scenario input
    workflow_result = await execute_workflow(scenario.claim_input)

    # Validate outcome matches expectations
    assert workflow_result.terminal_state == scenario.expected_terminal_state
    assert workflow_result.budget_state.rework_count == scenario.expected_rework_count

    # Save checkpoint to evaluation corpus
    checkpoint = await get_latest_checkpoint(workflow_result.workflow_id)
    scenario.checkpoint_id = checkpoint.checkpoint_id

    await save_evaluation_scenario(scenario)

    logger.info(
        "evaluation_scenario_captured",
        scenario_id=scenario.scenario_id,
        checkpoint_id=checkpoint.checkpoint_id,
        terminal_state=workflow_result.terminal_state,
    )
```

#### 2. Replay Phase (CI/CD Pipeline)
```python
async def run_evaluation_suite():
    """
    Run all evaluation scenarios via deterministic replay.
    Execute in CI on every code change.
    """
    scenarios = load_evaluation_corpus()
    results = []

    for scenario in scenarios:
        logger.info("running_evaluation", scenario_id=scenario.scenario_id)

        # Replay from checkpoint
        replay_result = await replay_from_checkpoint(
            scenario.checkpoint_id,
            mode=ReplayMode.VERIFY,
        )

        # Compare actual vs expected
        passed = (
            replay_result.terminal_state == scenario.expected_terminal_state
            and replay_result.rework_count == scenario.expected_rework_count
        )

        result = EvaluationResult(
            scenario_id=scenario.scenario_id,
            passed=passed,
            terminal_state=replay_result.terminal_state,
            expected_terminal_state=scenario.expected_terminal_state,
            divergences=replay_result.divergences,
            run_timestamp=datetime.utcnow(),
        )

        results.append(result)

        if not passed:
            logger.error(
                "evaluation_failed",
                scenario_id=scenario.scenario_id,
                expected=scenario.expected_terminal_state,
                actual=replay_result.terminal_state,
            )

    # Generate report
    report = EvaluationReport(
        total_scenarios=len(scenarios),
        passed=sum(r.passed for r in results),
        failed=sum(not r.passed for r in results),
        pass_rate=sum(r.passed for r in results) / len(scenarios),
        results=results,
    )

    return report
```

### Evaluation Assertions

```python
class EvaluationAssertions:
    """Assertions for evaluation scenarios."""

    @staticmethod
    def assert_terminal_state(result: WorkflowResult, expected: WorkflowState):
        """Workflow reached expected terminal state."""
        assert result.terminal_state == expected, \
            f"Expected {expected}, got {result.terminal_state}"

    @staticmethod
    def assert_rework_count(result: WorkflowResult, expected: int):
        """Workflow executed expected number of rework cycles."""
        assert result.budget_state.rework_count == expected, \
            f"Expected {expected} rework cycles, got {result.budget_state.rework_count}"

    @staticmethod
    def assert_budget_within_limit(result: WorkflowResult, max_cost: Decimal, max_tokens: int):
        """Workflow stayed within budget limits."""
        assert result.budget_state.total_cost_usd <= max_cost, \
            f"Cost ${result.budget_state.total_cost_usd} exceeded limit ${max_cost}"
        assert result.budget_state.total_tokens <= max_tokens, \
            f"Tokens {result.budget_state.total_tokens} exceeded limit {max_tokens}"

    @staticmethod
    def assert_deterministic_replay(replay_result: ReplayResult):
        """Replay produced identical results."""
        assert replay_result.success, \
            f"Replay diverged: {replay_result.divergences}"
```

## Consequences

### Positive
- **Regression prevention**: CI fails if evaluation scenarios break
- **Confidence in changes**: Developers know changes don't break existing functionality
- **Determinism verification**: Replay ensures reproducible execution
- **Performance tracking**: Track cost, latency, token usage over time
- **Rework validation**: Explicit scenarios test rework loops
- **Budget validation**: Scenarios verify budget enforcement
- **Comprehensive coverage**: 30+ scenarios cover happy path, edge cases, failures

### Negative
- **Maintenance burden**: Evaluation corpus must be kept up-to-date
- **Capture complexity**: Initial scenario capture requires manual setup
- **Non-determinism**: External API changes can break replay
- **Cost of capture**: Capturing scenarios consumes LLM budget
- **Slow CI**: 30+ scenarios may take 10-20 minutes to replay

### Mitigations
- **Quarterly corpus review**: Update scenarios to reflect system changes
- **Mock tools for capture**: Use deterministic tool responses during capture
- **Selective replay**: Run subset of scenarios on dev branches, full suite on main
- **Parallel execution**: Replay scenarios in parallel (10× speedup)
- **Checkpoint reuse**: Capture scenarios once, replay thousands of times (amortized cost)

## Alternatives Considered

### 1. Live API testing (no replay)
- **Pros**: Tests real system behavior
- **Rejected**: Non-deterministic (LLM responses vary), expensive (LLM costs), slow

### 2. Unit tests (mock everything)
- **Pros**: Fast, deterministic, cheap
- **Rejected**: Doesn't test end-to-end workflow, misses integration issues

### 3. Golden dataset (input → expected output)
- **Pros**: Simple comparison
- **Rejected**: LLM non-determinism makes exact output matching infeasible

### 4. Human evaluation (manual review)
- **Pros**: Catches subjective quality issues
- **Rejected**: Too slow for CI, not scalable, subjective

### 5. A/B testing in production
- **Pros**: Tests real user scenarios
- **Rejected**: Too risky for financial decisions, slow feedback

## Evaluation Scenario Coverage

### Required Scenario Categories (30+ total)

1. **Happy Path** (5 scenarios)
   - Simple approval, no rework
   - Complex claim, multiple tools, approval
   - High-value claim, approval
   - Low-value claim, approval
   - Rapid approval (minimal investigation)

2. **Rework Cycles** (5 scenarios)
   - Single rework, then approval
   - Two rework cycles, then approval
   - Max rework exceeded (3 cycles)
   - Rework after missing evidence
   - Rework after unclear reasoning

3. **Rejections** (5 scenarios)
   - Fraud detected, immediate rejection
   - Policy exclusion, rejection
   - Insufficient evidence, rejection
   - Cost exceeds coverage, rejection
   - Reviewer low confidence, rejection

4. **Budget Limits** (5 scenarios)
   - Token budget exhausted
   - Cost budget exhausted
   - Max steps exceeded
   - Timeout (30 min)
   - Combined budget pressure (multiple limits near threshold)

5. **Tool Failures** (3 scenarios)
   - Single tool failure, retry succeeds
   - Permanent tool failure, investigation continues with partial evidence
   - Multiple tool failures, workflow degrades gracefully

6. **Edge Cases** (4 scenarios)
   - Malformed claim input
   - Missing required documentation
   - Ambiguous policy language
   - Conflicting evidence sources

7. **Escalations** (3 scenarios)
   - Human approval timeout → escalation
   - Unrecoverable failure → escalation
   - Budget exceeded with incomplete investigation → escalation

## CI Integration

```yaml
# .github/workflows/evaluation.yml
name: Evaluation Suite

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  evaluate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt

      - name: Run evaluation suite
        run: |
          python -m casefile.evaluation.run_suite --parallel=10

      - name: Upload evaluation report
        if: always()
        uses: actions/upload-artifact@v3
        with:
          name: evaluation-report
          path: evaluation-report.html

      - name: Fail if evaluations failed
        run: |
          if [ -f evaluation-failures.txt ]; then
            cat evaluation-failures.txt
            exit 1
          fi
```

## Evaluation Metrics

```python
# Track evaluation metrics over time
evaluation_scenarios_total = meter.create_counter("casefile.evaluation.scenarios.total")
evaluation_scenarios_passed = meter.create_counter("casefile.evaluation.scenarios.passed")
evaluation_scenarios_failed = meter.create_counter("casefile.evaluation.scenarios.failed")

evaluation_pass_rate = meter.create_histogram("casefile.evaluation.pass_rate")
evaluation_duration_seconds = meter.create_histogram("casefile.evaluation.duration_seconds")

# SLO: 100% of evaluation scenarios pass
EVALUATION_PASS_RATE_SLO = 1.0
```

## References

- CASEFILE evaluation design: `docs/evaluation.md`
- CASEFILE testing strategy: `docs/testing.md`
- CASEFILE replay mechanism: `docs/replay.md`
- LangSmith evaluation: https://docs.smith.langchain.com/evaluation
- OpenAI Evals: https://github.com/openai/evals
