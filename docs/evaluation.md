# CASEFILE Evaluation Architecture

## Implementation Status (Phase 11)

Evaluation harness implemented in `src/casefile/evaluation/` per this document
and ADR-010:

- **Package**: `failures.py` (typed injector), `fixtures.py` (deterministic
  recipes + claim specs), `scenarios.py` (golden G01–G15), `assertions.py`
  (invariants A–P), `metrics.py` (descriptive aggregates), `reports.py`
  (JSON/markdown), `runner.py` (`ScenarioRunner`), `__main__.py`.
- **Entry points**: `python -m casefile.evaluation` and `casefile evaluate`
  (exit 0 iff overall PASS). `run-evaluation` remains the Phase 5 stub.
- **Safety**: offline, deterministic, netguard-friendly; injection disabled
  by default; no live LLM/tools; no production mutation during replay.
- **Acceptance**: unit matrix in `tests/unit/test_evaluation_core.py` and
  golden suite + CLI/module entry in
  `tests/integration/test_evaluation_scenarios.py` (all 15 scenarios PASS).

See also `docs/failure-injection.md`.

## Overview

CASEFILE includes a comprehensive evaluation harness to measure system performance, accuracy, and reliability. This document defines the evaluation framework, metrics, datasets, and execution methodology.

## Design Principles

### 1. Realistic Test Scenarios

Evaluation uses realistic claim scenarios that reflect actual production conditions, not toy examples.

### 2. Measurable Outcomes

Every evaluation has quantifiable metrics with defined success criteria.

### 3. Reproducible Results

Evaluation runs are reproducible through fixed seeds, recorded outputs, and versioned datasets.

### 4. Comprehensive Coverage

Evaluation covers:
- Normal operations
- Edge cases
- Failure scenarios
- Performance limits

### 5. Baseline Requirement

Minimum 30 recorded claim runs for baseline evaluation before production deployment.

## Evaluation Framework

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        EVALUATION FRAMEWORK                                  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      TEST DATASETS                                     │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │ Normal Claims │  │ Edge Cases    │  │ Failure       │            │  │
│  │  │               │  │               │  │ Scenarios     │            │  │
│  │  │ • Simple      │  │ • Malformed   │  │ • Timeout     │            │  │
│  │  │ • Complex     │  │ • Missing     │  │ • Budget      │            │  │
│  │  │ • Multi-party │  │   documents   │  │   exhaustion  │            │  │
│  │  │               │  │ • Policy      │  │ • Tool        │            │  │
│  │  │               │  │   mismatch    │  │   failures    │            │  │
│  │  └───────────────┘  └───────────────┘  └───────────────┘            │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                     │                                       │
│                                     ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      EVALUATION RUNNER                                 │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │   Dataset     │  │   Workflow    │  │   Result      │            │  │
│  │  │   Loader      │──►│   Executor    │──►│   Recorder    │            │  │
│  │  └───────────────┘  └───────────────┘  └───────────────┘            │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                     │                                       │
│                                     ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      METRICS ENGINE                                    │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │ Accuracy      │  │ Performance   │  │ Cost          │            │  │
│  │  │ Metrics       │  │ Metrics       │  │ Metrics       │            │  │
│  │  └───────────────┘  └───────────────┘  └───────────────┘            │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                     │                                       │
│                                     ▼                                       │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      REPORT GENERATOR                                  │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │   Summary     │  │   Detailed    │  │   Comparison  │            │  │
│  │  │   Report      │  │   Analysis    │  │   Reports     │            │  │
│  │  └───────────────┘  └───────────────┘  └───────────────┘            │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Test Dataset Design

### Dataset Categories

```python
from enum import Enum
from typing import List, Dict, Any
from uuid import UUID, uuid4
from pydantic import BaseModel, Field


class EvaluationCategory(str, Enum):
    """
    Categories for evaluation test cases.
    """

    # Normal operations
    NORMAL_SIMPLE = "NORMAL_SIMPLE"
    NORMAL_COMPLEX = "NORMAL_COMPLEX"
    NORMAL_MULTI_PARTY = "NORMAL_MULTI_PARTY"

    # Edge cases
    EDGE_MALFORMED_DOCUMENT = "EDGE_MALFORMED_DOCUMENT"
    EDGE_MISSING_INFORMATION = "EDGE_MISSING_INFORMATION"
    EDGE_POLICY_MISMATCH = "EDGE_POLICY_MISMATCH"
    EDGE_COVERAGE_LIMIT = "EDGE_COVERAGE_LIMIT"
    EDGE_HIGH_VALUE = "EDGE_HIGH_VALUE"

    # Failure scenarios
    FAILURE_EXTRACTION_ERROR = "FAILURE_EXTRACTION_ERROR"
    FAILURE_TOOL_TIMEOUT = "FAILURE_TOOL_TIMEOUT"
    FAILURE_BUDGET_EXHAUSTION = "FAILURE_BUDGET_EXHAUSTION"
    FAILURE_MAX_REWORK = "FAILURE_MAX_REWORK"
    FAILURE_SCHEMA_VALIDATION = "FAILURE_SCHEMA_VALIDATION"

    # Rework scenarios
    REWORK_SINGLE = "REWORK_SINGLE"
    REWORK_MULTIPLE = "REWORK_MULTIPLE"
    REWORK_MAX_EXCEEDED = "REWORK_MAX_EXCEEDED"

    # Performance scenarios
    PERFORMANCE_LARGE_DOCUMENTS = "PERFORMANCE_LARGE_DOCUMENTS"
    PERFORMANCE_MANY_DOCUMENTS = "PERFORMANCE_MANY_DOCUMENTS"


class EvaluationTestCase(BaseModel):
    """
    A single test case for evaluation.
    """

    # Identification
    test_case_id: UUID = Field(default_factory=uuid4)
    name: str
    category: EvaluationCategory
    description: str

    # Input
    claim_input: ClaimInput

    # Expected outcomes
    expected_terminal_state: WorkflowState
    expected_recommendation_type: Optional[RecommendationType] = None
    expected_extracted_fields: Optional[Dict[str, Any]] = None
    expected_investigation_flags: Optional[List[str]] = None

    # Constraints
    max_execution_time_seconds: int = 300
    max_cost_usd: Decimal = Decimal("5.00")

    # Mock data
    mock_policy_data: Optional[Dict[str, Any]] = None
    mock_claim_history: Optional[Dict[str, Any]] = None
    mock_repair_costs: Optional[Dict[str, Any]] = None

    # Metadata
    difficulty: str = "medium"  # "easy", "medium", "hard"
    priority: int = 1  # 1 = must pass, 2 = should pass, 3 = nice to have

    # Versioning
    schema_version: str = "1.0.0"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class EvaluationDataset(BaseModel):
    """
    Collection of test cases for evaluation.
    """

    dataset_id: UUID = Field(default_factory=uuid4)
    name: str
    description: str
    version: str

    # Test cases
    test_cases: List[EvaluationTestCase]

    # Configuration
    seed: int = 42  # For reproducibility
    shuffle: bool = False

    # Baseline requirement
    is_baseline: bool = False

    def get_cases_by_category(
        self,
        category: EvaluationCategory
    ) -> List[EvaluationTestCase]:
        """
        Get test cases for a specific category.
        """

        return [tc for tc in self.test_cases if tc.category == category]

    def get_minimum_baseline_dataset(self) -> "EvaluationDataset":
        """
        Get minimum 30 cases for baseline evaluation.
        """

        # Prioritize by priority and category coverage
        selected = []

        # Ensure category coverage
        for category in EvaluationCategory:
            cases = self.get_cases_by_category(category)
            if cases:
                selected.extend(cases[:2])  # At least 2 per category

        # Fill to 30 with priority cases
        remaining = [tc for tc in self.test_cases if tc not in selected]
        remaining.sort(key=lambda x: x.priority)

        while len(selected) < 30 and remaining:
            selected.append(remaining.pop(0))

        return EvaluationDataset(
            name=f"{self.name}_baseline_30",
            description="Minimum baseline evaluation dataset",
            version=self.version,
            test_cases=selected[:30],
            seed=self.seed
        )
```

### Baseline Test Cases

```python
# Minimum 30 test cases for baseline evaluation

BASELINE_TEST_CASES = [
    # === NORMAL OPERATIONS (10 cases) ===
    EvaluationTestCase(
        name="simple_collision_claim",
        category=EvaluationCategory.NORMAL_SIMPLE,
        description="Simple single-vehicle collision with clear documentation",
        claim_input=create_simple_collision_claim(),
        expected_terminal_state=WorkflowState.APPROVED,
        expected_recommendation_type=RecommendationType.APPROVE_FULL,
        difficulty="easy",
        priority=1
    ),

    EvaluationTestCase(
        name="comprehensive_windshield_claim",
        category=EvaluationCategory.NORMAL_SIMPLE,
        description="Windshield replacement under comprehensive coverage",
        claim_input=create_windshield_claim(),
        expected_terminal_state=WorkflowState.APPROVED,
        expected_recommendation_type=RecommendationType.APPROVE_FULL,
        difficulty="easy",
        priority=1
    ),

    EvaluationTestCase(
        name="multi_vehicle_collision",
        category=EvaluationCategory.NORMAL_COMPLEX,
        description="Multi-vehicle collision with multiple parties",
        claim_input=create_multi_vehicle_claim(),
        expected_terminal_state=WorkflowState.APPROVED,
        difficulty="medium",
        priority=1
    ),

    EvaluationTestCase(
        name="complex_injury_claim",
        category=EvaluationCategory.NORMAL_COMPLEX,
        description="Collision with injury and medical payments",
        claim_input=create_injury_claim(),
        expected_terminal_state=WorkflowState.HUMAN_APPROVAL,
        difficulty="medium",
        priority=1
    ),

    # ... 6 more normal cases

    # === EDGE CASES (10 cases) ===
    EvaluationTestCase(
        name="malformed_police_report",
        category=EvaluationCategory.EDGE_MALFORMED_DOCUMENT,
        description="Police report with incomplete sections",
        claim_input=create_claim_with_malformed_document(),
        expected_terminal_state=WorkflowState.APPROVED,  # Should still process
        difficulty="medium",
        priority=1
    ),

    EvaluationTestCase(
        name="missing_vehicle_vin",
        category=EvaluationCategory.EDGE_MISSING_INFORMATION,
        description="Claim missing vehicle VIN",
        claim_input=create_claim_missing_vin(),
        expected_terminal_state=WorkflowState.APPROVED,
        difficulty="medium",
        priority=1
    ),

    EvaluationTestCase(
        name="policy_coverage_mismatch",
        category=EvaluationCategory.EDGE_POLICY_MISMATCH,
        description="Claim type not covered by policy",
        claim_input=create_uncovered_claim(),
        expected_terminal_state=WorkflowState.REJECTED,
        difficulty="medium",
        priority=1
    ),

    EvaluationTestCase(
        name="high_value_claim",
        category=EvaluationCategory.EDGE_HIGH_VALUE,
        description="Claim approaching coverage limits",
        claim_input=create_high_value_claim(),
        expected_terminal_state=WorkflowState.HUMAN_APPROVAL,
        difficulty="hard",
        priority=1
    ),

    # ... 6 more edge cases

    # === FAILURE SCENARIOS (5 cases) ===
    EvaluationTestCase(
        name="extraction_complete_failure",
        category=EvaluationCategory.FAILURE_EXTRACTION_ERROR,
        description="All documents fail to process",
        claim_input=create_unprocessable_claim(),
        expected_terminal_state=WorkflowState.FAILED,
        difficulty="medium",
        priority=1
    ),

    EvaluationTestCase(
        name="budget_exhaustion_scenario",
        category=EvaluationCategory.FAILURE_BUDGET_EXHAUSTION,
        description="Complex claim that exhausts budget",
        claim_input=create_complex_claim(),
        expected_terminal_state=WorkflowState.BUDGET_EXHAUSTED,
        max_cost_usd=Decimal("1.00"),  # Low limit to trigger
        difficulty="hard",
        priority=2
    ),

    # ... 3 more failure cases

    # === REWORK SCENARIOS (5 cases) ===
    EvaluationTestCase(
        name="single_rework_cycle",
        category=EvaluationCategory.REWORK_SINGLE,
        description="Reviewer requests one round of rework",
        claim_input=create_rework_needed_claim(),
        expected_terminal_state=WorkflowState.APPROVED,
        difficulty="medium",
        priority=1
    ),

    EvaluationTestCase(
        name="max_rework_exceeded",
        category=EvaluationCategory.REWORK_MAX_EXCEEDED,
        description="Reviewer exceeds rework limit",
        claim_input=create_problematic_claim(),
        expected_terminal_state=WorkflowState.MAX_REWORK_EXCEEDED,
        difficulty="hard",
        priority=2
    ),

    # ... 3 more rework cases
]
```

## Evaluation Metrics

### Metric Definitions

```python
from dataclasses import dataclass
from typing import List, Dict, Optional


@dataclass
class EvaluationMetrics:
    """
    Metrics collected during evaluation.
    """

    # === COMPLETION METRICS ===
    total_runs: int
    successful_runs: int
    failed_runs: int
    exception_runs: int

    completion_rate: float  # successful / total

    # === ACCURACY METRICS ===
    extraction_accuracy: float
    investigation_accuracy: float
    recommendation_accuracy: float

    correct_terminal_state_rate: float
    correct_recommendation_type_rate: float

    # === PERFORMANCE METRICS ===
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float

    max_latency_ms: int
    min_latency_ms: int

    # === COST METRICS ===
    average_cost_usd: Decimal
    p50_cost_usd: Decimal
    p95_cost_usd: Decimal
    max_cost_usd: Decimal

    average_tokens_per_claim: int
    p95_tokens_per_claim: int

    # === STEP METRICS ===
    average_steps: float
    max_steps: int

    # === ERROR METRICS ===
    error_rate: float
    errors_by_type: Dict[str, int]
    errors_by_component: Dict[str, int]

    # === TERMINAL STATE DISTRIBUTION ===
    terminal_state_distribution: Dict[WorkflowState, int]

    # === REWORK METRICS ===
    average_rework_cycles: float
    max_rework_cycles: int
    rework_rate: float

    # === TOOL METRICS ===
    tool_call_success_rate: float
    tool_call_timeout_rate: float
    average_tool_calls_per_claim: float

    # === SCHEMA VALIDATION ===
    schema_validation_success_rate: float

    # === CATEGORY BREAKDOWN ===
    category_metrics: Dict[EvaluationCategory, "CategoryMetrics"]


@dataclass
class CategoryMetrics:
    """
    Metrics for a specific test category.
    """

    category: EvaluationCategory
    total_runs: int
    successful_runs: int
    completion_rate: float
    average_latency_ms: float
    average_cost_usd: Decimal
```

### Metric Calculation

```python
class MetricsCalculator:
    """
    Calculates evaluation metrics from results.
    """

    def calculate_metrics(
        self,
        results: List[EvaluationResult]
    ) -> EvaluationMetrics:
        """
        Calculate all metrics from evaluation results.
        """

        # Completion metrics
        total = len(results)
        successful = sum(1 for r in results if r.is_successful())
        failed = sum(1 for r in results if r.terminal_state in FAILURE_TERMINAL_STATES)
        exception = sum(1 for r in results if r.terminal_state in EXCEPTION_TERMINAL_STATES)

        # Accuracy metrics
        extraction_accuracy = self._calculate_extraction_accuracy(results)
        investigation_accuracy = self._calculate_investigation_accuracy(results)
        recommendation_accuracy = self._calculate_recommendation_accuracy(results)

        correct_terminal = sum(
            1 for r in results
            if r.terminal_state == r.test_case.expected_terminal_state
        ) / total

        correct_recommendation = self._calculate_recommendation_match_rate(results)

        # Performance metrics
        latencies = [r.execution_time_ms for r in results]
        costs = [r.total_cost for r in results]
        tokens = [r.total_tokens for r in results]
        steps = [r.step_count for r in results]

        # Error metrics
        errors = [r for r in results if r.error]
        errors_by_type = self._group_errors_by_type(errors)
        errors_by_component = self._group_errors_by_component(errors)

        # Terminal state distribution
        state_dist = self._calculate_state_distribution(results)

        # Rework metrics
        rework_cycles = [r.rework_count for r in results]
        rework_rate = sum(1 for r in results if r.rework_count > 0) / total

        # Tool metrics
        tool_metrics = self._calculate_tool_metrics(results)

        # Category breakdown
        category_metrics = self._calculate_category_metrics(results)

        return EvaluationMetrics(
            total_runs=total,
            successful_runs=successful,
            failed_runs=failed,
            exception_runs=exception,
            completion_rate=successful / total if total > 0 else 0,

            extraction_accuracy=extraction_accuracy,
            investigation_accuracy=investigation_accuracy,
            recommendation_accuracy=recommendation_accuracy,
            correct_terminal_state_rate=correct_terminal,
            correct_recommendation_type_rate=correct_recommendation,

            average_latency_ms=self._average(latencies),
            p50_latency_ms=self._percentile(latencies, 50),
            p95_latency_ms=self._percentile(latencies, 95),
            p99_latency_ms=self._percentile(latencies, 99),
            max_latency_ms=max(latencies) if latencies else 0,
            min_latency_ms=min(latencies) if latencies else 0,

            average_cost_usd=self._average_decimal(costs),
            p50_cost_usd=self._percentile_decimal(costs, 50),
            p95_cost_usd=self._percentile_decimal(costs, 95),
            max_cost_usd=max(costs) if costs else Decimal("0"),

            average_tokens_per_claim=int(self._average(tokens)),
            p95_tokens_per_claim=int(self._percentile(tokens, 95)),

            average_steps=self._average(steps),
            max_steps=max(steps) if steps else 0,

            error_rate=len(errors) / total if total > 0 else 0,
            errors_by_type=errors_by_type,
            errors_by_component=errors_by_component,

            terminal_state_distribution=state_dist,

            average_rework_cycles=self._average(rework_cycles),
            max_rework_cycles=max(rework_cycles) if rework_cycles else 0,
            rework_rate=rework_rate,

            tool_call_success_rate=tool_metrics["success_rate"],
            tool_call_timeout_rate=tool_metrics["timeout_rate"],
            average_tool_calls_per_claim=tool_metrics["average_calls"],

            schema_validation_success_rate=self._calculate_schema_validation_rate(results),

            category_metrics=category_metrics
        )

    def _average(self, values: List[float]) -> float:
        """Calculate average."""
        return sum(values) / len(values) if values else 0

    def _percentile(self, values: List[float], percentile: int) -> float:
        """Calculate percentile."""
        if not values:
            return 0

        sorted_values = sorted(values)
        index = int(len(sorted_values) * percentile / 100)
        return sorted_values[min(index, len(sorted_values) - 1)]

    def _calculate_extraction_accuracy(self, results: List[EvaluationResult]) -> float:
        """Calculate extraction accuracy."""

        correct = 0
        total = 0

        for result in results:
            if result.test_case.expected_extracted_fields:
                for field, expected in result.test_case.expected_extracted_fields.items():
                    actual = result.extracted_data.get(field)
                    if actual == expected:
                        correct += 1
                    total += 1

        return correct / total if total > 0 else 1.0
```

## Evaluation Runner

### Execution Engine

```python
class EvaluationRunner:
    """
    Runs evaluation test cases.
    """

    def __init__(
        self,
        workflow_executor: WorkflowExecutor,
        result_recorder: ResultRecorder,
        metrics_calculator: MetricsCalculator,
        report_generator: ReportGenerator
    ):
        self.executor = workflow_executor
        self.recorder = result_recorder
        self.calculator = metrics_calculator
        self.reporter = report_generator

    async def run_evaluation(
        self,
        dataset: EvaluationDataset,
        config: EvaluationConfig
    ) -> EvaluationReport:
        """
        Run evaluation for a dataset.
        """

        logger.info(
            "evaluation_started",
            dataset_name=dataset.name,
            case_count=len(dataset.test_cases)
        )

        results = []

        for i, test_case in enumerate(dataset.test_cases):
            logger.info(
                "running_test_case",
                case_name=test_case.name,
                category=test_case.category,
                progress=f"{i + 1}/{len(dataset.test_cases)}"
            )

            try:
                # Run the workflow
                result = await self._run_single_case(
                    test_case,
                    config
                )

                results.append(result)

            except Exception as e:
                logger.error(
                    "test_case_failed",
                    case_name=test_case.name,
                    error=str(e)
                )

                results.append(EvaluationResult(
                    test_case=test_case,
                    terminal_state=WorkflowState.FAILED,
                    error=ErrorDetails(
                        error_type=type(e).__name__,
                        message=str(e)
                    )
                ))

        # Calculate metrics
        metrics = self.calculator.calculate_metrics(results)

        # Generate report
        report = self.reporter.generate_report(
            dataset=dataset,
            results=results,
            metrics=metrics,
            config=config
        )

        logger.info(
            "evaluation_completed",
            completion_rate=metrics.completion_rate,
            average_cost=str(metrics.average_cost_usd)
        )

        return report

    async def _run_single_case(
        self,
        test_case: EvaluationTestCase,
        config: EvaluationConfig
    ) -> EvaluationResult:
        """
        Run a single test case.
        """

        # Set up mocks if provided
        if test_case.mock_policy_data:
            self._setup_mock_policy(test_case.mock_policy_data)

        if test_case.mock_claim_history:
            self._setup_mock_history(test_case.mock_claim_history)

        # Execute workflow
        start_time = datetime.utcnow()

        workflow_result = await self.executor.execute(
            claim_input=test_case.claim_input,
            config=WorkflowConfig(
                max_execution_time_seconds=test_case.max_execution_time_seconds,
                max_cost_usd=test_case.max_cost_usd
            )
        )

        end_time = datetime.utcnow()

        # Create result
        return EvaluationResult(
            test_case=test_case,
            workflow_run_id=workflow_result.workflow_run_id,
            terminal_state=workflow_result.terminal_state,
            termination_reason=workflow_result.termination_reason,
            step_count=workflow_result.step_count,
            rework_count=workflow_result.rework_count,
            total_tokens=workflow_result.total_tokens,
            total_cost=workflow_result.total_cost,
            execution_time_ms=int((end_time - start_time).total_seconds() * 1000),
            extracted_data=workflow_result.extracted_data,
            investigation_result=workflow_result.investigation_result,
            recommendation=workflow_result.recommendation,
            error=workflow_result.error
        )


@dataclass
class EvaluationConfig:
    """
    Configuration for evaluation runs.
    """

    # Execution
    parallel_runs: int = 1
    stop_on_failure: bool = False
    retry_failed: bool = False

    # Recording
    record_all_outputs: bool = True
    record_traces: bool = True

    # Reporting
    generate_detailed_report: bool = True
    include_individual_results: bool = True

    # Comparison
    compare_with_baseline: Optional[str] = None  # Baseline dataset ID
```

## Evaluation Report

### Report Structure

```python
class EvaluationReport(BaseModel):
    """
    Complete evaluation report.
    """

    # Identification
    report_id: UUID = Field(default_factory=uuid4)
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    # Dataset info
    dataset_name: str
    dataset_version: str
    total_cases: int

    # Summary
    summary: "EvaluationSummary"

    # Metrics
    metrics: EvaluationMetrics

    # Detailed results
    results: List[EvaluationResult]

    # Comparison (if applicable)
    baseline_comparison: Optional["BaselineComparison"] = None

    # Recommendations
    recommendations: List[str]

    def to_markdown(self) -> str:
        """
        Generate markdown report.
        """

        return f"""
# CASEFILE Evaluation Report

**Report ID:** {self.report_id}
**Generated:** {self.generated_at}
**Dataset:** {self.dataset_name} (v{self.dataset_version})

## Summary

| Metric | Value |
|--------|-------|
| Total Runs | {self.total_cases} |
| Completion Rate | {self.metrics.completion_rate:.2%} |
| Successful Runs | {self.summary.successful_runs} |
| Failed Runs | {self.summary.failed_runs} |

## Performance

| Metric | Value |
|--------|-------|
| Average Latency | {self.metrics.average_latency_ms:.0f}ms |
| P95 Latency | {self.metrics.p95_latency_ms:.0f}ms |
| Max Latency | {self.metrics.max_latency_ms}ms |

## Cost

| Metric | Value |
|--------|-------|
| Average Cost | ${self.metrics.average_cost_usd:.2f} |
| P95 Cost | ${self.metrics.p95_cost_usd:.2f} |
| Average Tokens | {self.metrics.average_tokens_per_claim} |

## Accuracy

| Metric | Value |
|--------|-------|
| Extraction Accuracy | {self.metrics.extraction_accuracy:.2%} |
| Investigation Accuracy | {self.metrics.investigation_accuracy:.2%} |
| Recommendation Accuracy | {self.metrics.recommendation_accuracy:.2%} |

## Terminal State Distribution

| State | Count | Percentage |
|-------|-------|------------|
{self._format_state_distribution()}

## Category Breakdown

{self._format_category_breakdown()}

## Recommendations

{self._format_recommendations()}
"""


@dataclass
class EvaluationSummary:
    """
    Summary of evaluation results.
    """

    successful_runs: int
    failed_runs: int
    exception_runs: int

    average_latency_ms: float
    average_cost_usd: Decimal

    key_findings: List[str]
    critical_issues: List[str]
```

## Success Criteria

### Baseline Requirements

```python
class BaselineRequirements:
    """
    Minimum requirements for baseline evaluation.
    """

    # Completion
    MIN_COMPLETION_RATE = 0.95  # 95% must complete successfully

    # Accuracy
    MIN_EXTRACTION_ACCURACY = 0.90  # 90% extraction accuracy
    MIN_INVESTIGATION_ACCURACY = 0.85  # 85% investigation accuracy
    MIN_RECOMMENDATION_ACCURACY = 0.85  # 85% recommendation accuracy

    # Performance
    MAX_AVERAGE_LATENCY_MS = 60000  # 60 seconds average
    MAX_P95_LATENCY_MS = 120000  # 2 minutes P95

    # Cost
    MAX_AVERAGE_COST_USD = Decimal("2.00")  # $2.00 average
    MAX_P95_COST_USD = Decimal("4.00")  # $4.00 P95

    # Errors
    MAX_ERROR_RATE = 0.05  # 5% max error rate

    # Schema
    MIN_SCHEMA_VALIDATION_RATE = 1.0  # 100% schema validation

    def check_requirements(
        self,
        metrics: EvaluationMetrics
    ) -> List[str]:
        """
        Check if metrics meet requirements.

        Returns list of violations.
        """

        violations = []

        if metrics.completion_rate < self.MIN_COMPLETION_RATE:
            violations.append(
                f"Completion rate {metrics.completion_rate:.2%} below minimum {self.MIN_COMPLETION_RATE:.2%}"
            )

        if metrics.extraction_accuracy < self.MIN_EXTRACTION_ACCURACY:
            violations.append(
                f"Extraction accuracy {metrics.extraction_accuracy:.2%} below minimum {self.MIN_EXTRACTION_ACCURACY:.2%}"
            )

        if metrics.average_latency_ms > self.MAX_AVERAGE_LATENCY_MS:
            violations.append(
                f"Average latency {metrics.average_latency_ms:.0f}ms exceeds maximum {self.MAX_AVERAGE_LATENCY_MS}ms"
            )

        if metrics.average_cost_usd > self.MAX_AVERAGE_COST_USD:
            violations.append(
                f"Average cost ${metrics.average_cost_usd:.2f} exceeds maximum ${self.MAX_AVERAGE_COST_USD}"
            )

        if metrics.error_rate > self.MAX_ERROR_RATE:
            violations.append(
                f"Error rate {metrics.error_rate:.2%} exceeds maximum {self.MAX_ERROR_RATE:.2%}"
            )

        if metrics.schema_validation_success_rate < self.MIN_SCHEMA_VALIDATION_RATE:
            violations.append(
                f"Schema validation rate {metrics.schema_validation_success_rate:.2%} below minimum {self.MIN_SCHEMA_VALIDATION_RATE:.2%}"
            )

        return violations
```

## Summary

The evaluation architecture provides:
- **Comprehensive test coverage**: Normal, edge, and failure scenarios
- **Quantifiable metrics**: Accuracy, performance, cost, errors
- **Baseline requirement**: Minimum 30 test cases with defined success criteria
- **Reproducibility**: Fixed seeds, recorded outputs, versioned datasets
- **Detailed reporting**: Markdown reports with actionable insights
- **Requirement verification**: Automated checking against baseline requirements
