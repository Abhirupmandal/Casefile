"""CASEFILE evaluation package (Phase 6).

Deterministic, offline evaluation harness: typed scenarios and cases, controlled
failure injection, invariant engine A–P, golden suite G01–G15, 30-case dataset
CASE-001–CASE-030, regression baselines, and sanitized reporting.
"""

from casefile.evaluation.assertions import (
    INVARIANT_LETTERS,
    ApprovalFact,
    BudgetFact,
    InvariantContext,
    InvariantResult,
    InvariantStatus,
    TransitionFact,
    evaluate_invariants,
    invariant_pass_rate,
)
from casefile.evaluation.baseline import (
    DEFAULT_PHASE6_BASELINE,
    BaselineComparisonResult,
    BaselineMetricComparison,
    BaselineThresholds,
    compare_against_baseline,
)
from casefile.evaluation.classifier import (
    FailureCategory,
    FailureClassification,
    FailureStage,
    classify_failure,
)
from casefile.evaluation.dataset import (
    DATASET_CASES,
    EVALUATION_DATASET,
    get_evaluation_case,
)
from casefile.evaluation.failures import (
    FailureInjection,
    FailurePoint,
    FailureType,
    InjectedFailureError,
    RecoveryBehavior,
    TriggeredInjection,
)
from casefile.evaluation.injector import (
    FailureInjector,
    FailurePlan,
    FailureRule,
    InjectedFailure,
)
from casefile.evaluation.metrics import EvaluationMetrics, ScenarioCounters, compute_metrics
from casefile.evaluation.model import (
    EvaluationCase,
    EvaluationMetric,
    EvaluationOutcome,
    EvaluationRun,
    EvaluationSummary,
    ExpectedApprovalOutcome,
    ExpectedBudgetOutcome,
    ExpectedCheckpointBehavior,
    ExpectedFailureClassification,
    ExpectedNodePath,
    ExpectedTerminalState,
    FaultType,
    ReplayExpectation,
)
from casefile.evaluation.reporter import (
    export_evaluation_run_json,
    format_markdown_report,
    sanitize_evaluation_data,
    write_markdown_report,
)
from casefile.evaluation.reports import (
    EvaluationReport,
    EvaluationResult,
    ReplayComparison,
    build_report,
    report_to_json,
    report_to_markdown,
    write_report,
)
from casefile.evaluation.runner import ScenarioRunner
from casefile.evaluation.scenarios import (
    GOLDEN_SCENARIOS,
    EvaluationScenario,
    ExpectedBehavior,
    ScenarioInitial,
    get_scenario,
)

__all__ = [
    "INVARIANT_LETTERS",
    "ApprovalFact",
    "BudgetFact",
    "InvariantContext",
    "InvariantResult",
    "InvariantStatus",
    "TransitionFact",
    "evaluate_invariants",
    "invariant_pass_rate",
    "FailureInjection",
    "FailureInjector",
    "FailurePoint",
    "FailureType",
    "InjectedFailureError",
    "RecoveryBehavior",
    "TriggeredInjection",
    "EvaluationMetrics",
    "ScenarioCounters",
    "compute_metrics",
    "EvaluationReport",
    "EvaluationResult",
    "ReplayComparison",
    "build_report",
    "report_to_json",
    "report_to_markdown",
    "write_report",
    "ScenarioRunner",
    "GOLDEN_SCENARIOS",
    "EvaluationScenario",
    "ExpectedBehavior",
    "ScenarioInitial",
    "get_scenario",
    # Phase 6 exports
    "FaultType",
    "ExpectedTerminalState",
    "ExpectedNodePath",
    "ExpectedBudgetOutcome",
    "ExpectedApprovalOutcome",
    "ExpectedCheckpointBehavior",
    "ReplayExpectation",
    "ExpectedFailureClassification",
    "EvaluationCase",
    "EvaluationOutcome",
    "EvaluationMetric",
    "EvaluationSummary",
    "EvaluationRun",
    "FailureRule",
    "FailurePlan",
    "InjectedFailure",
    "FailureCategory",
    "FailureStage",
    "FailureClassification",
    "classify_failure",
    "EVALUATION_DATASET",
    "DATASET_CASES",
    "get_evaluation_case",
    "BaselineThresholds",
    "BaselineMetricComparison",
    "BaselineComparisonResult",
    "DEFAULT_PHASE6_BASELINE",
    "compare_against_baseline",
    "export_evaluation_run_json",
    "format_markdown_report",
    "sanitize_evaluation_data",
    "write_markdown_report",
]
