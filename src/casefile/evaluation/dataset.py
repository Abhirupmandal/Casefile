"""Deterministic evaluation dataset containing 30 recorded claim scenarios (Phase 6).

Covers 10 formal behavioral categories:
- A. Nominal claims (CASE-001 through CASE-005)
- B. Reviewer rework (CASE-006 through CASE-009)
- C. Tool failures (CASE-010 through CASE-013)
- D. Provider failures/retries (CASE-014 through CASE-016)
- E. Conflicting evidence (CASE-017 through CASE-019)
- F. Missing policy/evidence (CASE-020 through CASE-021)
- G. Fraud/risk signals (CASE-022 through CASE-023)
- H. Budget/step exhaustion (CASE-024 through CASE-025)
- I. Human rejection/expiration (CASE-026 through CASE-027)
- J. Checkpoint/resume/replay scenarios (CASE-028 through CASE-030)
"""

from __future__ import annotations

from decimal import Decimal

from casefile.budget.envelope import BudgetEnvelope
from casefile.evaluation.failures import (
    FailureInjection,
    FailurePoint,
    FailureType,
)
from casefile.evaluation.fixtures import ClaimSpec, PayloadRecipe
from casefile.evaluation.model import (
    EvaluationCase,
    ExpectedApprovalOutcome,
    ExpectedBudgetOutcome,
    ExpectedCheckpointBehavior,
    ExpectedFailureClassification,
    ReplayExpectation,
)
from casefile.evaluation.scenarios import (
    AgentScript,
    ApprovalExpectation,
    EvaluationScenario,
    ExpectedBehavior,
    ExpectedBudgetBehavior,
    ExpectedFailureBehavior,
    ExpectedReplayBehavior,
    ScenarioInitial,
    ScriptedStep,
    ScriptKind,
    _approve_scripts,
)
from casefile.models.contracts import WorkflowState

_NOMINAL_PATH: tuple[WorkflowState, ...] = (
    WorkflowState.RECEIVED,
    WorkflowState.EXTRACTION,
    WorkflowState.INVESTIGATION,
    WorkflowState.REVIEW,
    WorkflowState.HUMAN_APPROVAL,
    WorkflowState.APPROVED,
)

_REJECT_PATH: tuple[WorkflowState, ...] = (
    WorkflowState.RECEIVED,
    WorkflowState.EXTRACTION,
    WorkflowState.INVESTIGATION,
    WorkflowState.REVIEW,
    WorkflowState.REJECTED,
)

_REWORK_APPROVE_PATH: tuple[WorkflowState, ...] = (
    WorkflowState.RECEIVED,
    WorkflowState.EXTRACTION,
    WorkflowState.INVESTIGATION,
    WorkflowState.REVIEW,
    WorkflowState.REWORK_LOOP,
    WorkflowState.INVESTIGATION,
    WorkflowState.REVIEW,
    WorkflowState.HUMAN_APPROVAL,
    WorkflowState.APPROVED,
)

_HUMAN_REJECT_PATH: tuple[WorkflowState, ...] = (
    WorkflowState.RECEIVED,
    WorkflowState.EXTRACTION,
    WorkflowState.INVESTIGATION,
    WorkflowState.REVIEW,
    WorkflowState.HUMAN_APPROVAL,
    WorkflowState.REJECTED,
)

_TOOL_FAILED_PATH: tuple[WorkflowState, ...] = (
    WorkflowState.RECEIVED,
    WorkflowState.EXTRACTION,
    WorkflowState.INVESTIGATION,
    WorkflowState.FAILED,
)


def _reject_scripts() -> tuple[AgentScript, ...]:
    return (
        AgentScript(
            agent="extractor",
            steps=(ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.EXTRACTION_OK),),
        ),
        AgentScript(
            agent="investigator",
            steps=(ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.INVESTIGATION_OK),),
        ),
        AgentScript(
            agent="reviewer",
            steps=(ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.REVIEW_REJECT),),
        ),
    )


def _rework_exhaust_scripts() -> tuple[AgentScript, ...]:
    return (
        AgentScript(
            agent="extractor",
            steps=(ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.EXTRACTION_OK),),
        ),
        AgentScript(
            agent="investigator",
            steps=(
                ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.INVESTIGATION_OK),
                ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.INVESTIGATION_OK),
            ),
        ),
        AgentScript(
            agent="reviewer",
            steps=(
                ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.REVIEW_REWORK),
                ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.REVIEW_REWORK),
            ),
        ),
    )


def _build_cases() -> tuple[EvaluationCase, ...]:
    cases: list[EvaluationCase] = []

    # ------------------------------------------------------------------
    # Category A: Nominal claims (CASE-001 to CASE-005)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-001",
            scenario_name="Nominal collision claim",
            category="NOMINAL",
            description="Happy path: collision claim with all documents verified and approved.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-001",
                claimant_name="Alice Smith",
                claim_amount=Decimal("1500.00"),
                description="Rear bumper damage in parking lot",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-001",
                name="Nominal collision claim",
                description="Nominal collision claim approved",
                initial=ScenarioInitial(scripts=_approve_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    agent_executions=3,
                    rework_count=0,
                    require_invariants=("A", "B", "C", "D", "E", "F", "G", "N", "O"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(
                outcome="granted", actor_role="human"
            ),
            expected_rework_count=0,
            expected_retry_count=0,
            expected_budget_outcome=ExpectedBudgetOutcome(
                cost_ceiling=Decimal("0.50"), rework_cycles=0
            ),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-002",
            scenario_name="Nominal comprehensive claim",
            category="NOMINAL",
            description="Comprehensive coverage claim with photo proof and clear liability.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-002",
                claimant_name="Bob Jones",
                claim_amount=Decimal("2500.00"),
                description="Fallen tree limb on parked vehicle",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-002",
                name="Nominal comprehensive claim",
                description="Comprehensive claim approved",
                initial=ScenarioInitial(scripts=_approve_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    agent_executions=3,
                    rework_count=0,
                    require_invariants=("A", "B", "D", "E", "F"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=0,
            expected_retry_count=0,
            expected_budget_outcome=ExpectedBudgetOutcome(cost_ceiling=Decimal("0.50")),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-003",
            scenario_name="Nominal property damage claim",
            category="NOMINAL",
            description="Property damage claim with repair invoice attached.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-003",
                claimant_name="Carol White",
                claim_amount=Decimal("3800.00"),
                description="Damage to garage door by insured vehicle",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-003",
                name="Nominal property damage claim",
                description="Property damage claim approved",
                initial=ScenarioInitial(scripts=_approve_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    agent_executions=3,
                    rework_count=0,
                    require_invariants=("A", "B", "D", "E", "F"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=0,
            expected_retry_count=0,
            expected_budget_outcome=ExpectedBudgetOutcome(cost_ceiling=Decimal("0.50")),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-004",
            scenario_name="Nominal windshield replacement",
            category="NOMINAL",
            description="Glass coverage claim for front windshield replacement.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-004",
                claimant_name="David Brown",
                claim_amount=Decimal("450.00"),
                description="Highway gravel cracked windshield",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-004",
                name="Nominal windshield replacement",
                description="Windshield replacement approved",
                initial=ScenarioInitial(scripts=_approve_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    agent_executions=3,
                    rework_count=0,
                    require_invariants=("A", "B", "D", "E", "F"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=0,
            expected_retry_count=0,
            expected_budget_outcome=ExpectedBudgetOutcome(cost_ceiling=Decimal("0.50")),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-005",
            scenario_name="Nominal hit-and-run with police statement",
            category="NOMINAL",
            description="Hit-and-run claim supported by verified police report.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-005",
                claimant_name="Eve Davis",
                claim_amount=Decimal("4200.00"),
                description="Hit-and-run collision while parked overnight",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-005",
                name="Nominal hit-and-run claim",
                description="Hit-and-run approved",
                initial=ScenarioInitial(scripts=_approve_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    agent_executions=3,
                    rework_count=0,
                    require_invariants=("A", "B", "D", "E", "F"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=0,
            expected_retry_count=0,
            expected_budget_outcome=ExpectedBudgetOutcome(cost_ceiling=Decimal("0.50")),
        )
    )

    # ------------------------------------------------------------------
    # Category B: Reviewer rework (CASE-006 to CASE-009)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-006",
            scenario_name="Reviewer rework: missing police statement",
            category="REWORK",
            description="Reviewer asks for missing police statement; re-investigation finds it; approved.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-006",
                claimant_name="Frank Miller",
                claim_amount=Decimal("3100.00"),
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-006",
                name="Reviewer rework: missing police statement",
                description="Single rework cycle resolved to approval",
                initial=ScenarioInitial(scripts=_approve_scripts(rework_then_approve=True)),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_REWORK_APPROVE_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    rework_count=1,
                    agent_executions=5,
                    require_invariants=("A", "B", "D", "E", "G"),
                ),
            ),
            expected_state_path=_REWORK_APPROVE_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=1,
            expected_budget_outcome=ExpectedBudgetOutcome(
                rework_cycles=1, cost_ceiling=Decimal("0.80")
            ),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-007",
            scenario_name="Reviewer rework: inconsistent repair estimate",
            category="REWORK",
            description="Reviewer identifies estimate discrepancy; second investigation clarifies; approved.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-007",
                claimant_name="Grace Hopper",
                claim_amount=Decimal("5100.00"),
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-007",
                name="Reviewer rework: inconsistent repair estimate",
                description="Estimate discrepancy resolved",
                initial=ScenarioInitial(scripts=_approve_scripts(rework_then_approve=True)),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_REWORK_APPROVE_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    rework_count=1,
                    agent_executions=5,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REWORK_APPROVE_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=1,
            expected_budget_outcome=ExpectedBudgetOutcome(rework_cycles=1),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-008",
            scenario_name="Reviewer rework: prior history clarification",
            category="REWORK",
            description="Prior claim history needs checking; re-investigation verifies no overlap; approved.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-008",
                claimant_name="Henry Ford",
                claim_amount=Decimal("2900.00"),
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-008",
                name="Reviewer rework: prior history clarification",
                description="Prior history checked on rework",
                initial=ScenarioInitial(scripts=_approve_scripts(rework_then_approve=True)),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_REWORK_APPROVE_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    rework_count=1,
                    agent_executions=5,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REWORK_APPROVE_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_rework_count=1,
            expected_budget_outcome=ExpectedBudgetOutcome(rework_cycles=1),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-009",
            scenario_name="Reviewer rework exhaustion (max rework limit)",
            category="REWORK",
            description="Reviewer requests rework repeatedly until envelope max_rework_cycles=1 is exceeded.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-009",
                claimant_name="Ian Malcolm",
                claim_amount=Decimal("6500.00"),
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-009",
                name="Reviewer rework exhaustion",
                description="Max rework ceiling terminates loop",
                initial=ScenarioInitial(
                    scripts=_rework_exhaust_scripts(),
                    envelope=BudgetEnvelope(max_rework_cycles=1),
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.MAX_REWORK_EXCEEDED,
                    transition_path=(
                        WorkflowState.RECEIVED,
                        WorkflowState.EXTRACTION,
                        WorkflowState.INVESTIGATION,
                        WorkflowState.REVIEW,
                        WorkflowState.REWORK_LOOP,
                        WorkflowState.INVESTIGATION,
                        WorkflowState.REVIEW,
                        WorkflowState.MAX_REWORK_EXCEEDED,
                    ),
                    rework_count=1,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=(
                WorkflowState.RECEIVED,
                WorkflowState.EXTRACTION,
                WorkflowState.INVESTIGATION,
                WorkflowState.REVIEW,
                WorkflowState.REWORK_LOOP,
                WorkflowState.INVESTIGATION,
                WorkflowState.REVIEW,
                WorkflowState.MAX_REWORK_EXCEEDED,
            ),
            expected_terminal_state=WorkflowState.MAX_REWORK_EXCEEDED,
            expected_rework_count=1,
            expected_failure_classification=ExpectedFailureClassification(
                category="REWORK_EXHAUSTION",
                stage="REVIEW",
                failure_type="MAX_REWORK_EXCEEDED",
                contained=True,
            ),
        )
    )

    # ------------------------------------------------------------------
    # Category C: Tool failures (CASE-010 to CASE-013)
    # ------------------------------------------------------------------
    tool_injection_policy = FailureInjection(
        failure_id="case010-policy-fail",
        point=FailurePoint.BEFORE_TOOL,
        type=FailureType.EXCEPTION,
        target_component="policy_lookup",
        max_triggers=5,
    )
    cases.append(
        EvaluationCase(
            case_id="CASE-010",
            scenario_name="Policy lookup tool failure terminates node safely",
            category="TOOL_FAILURE",
            description="Policy lookup fails repeatedly; bounded tool retries fail closed to FAILED.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-010"),
            scenario=EvaluationScenario(
                scenario_id="CASE-010",
                name="Policy lookup tool failure",
                description="Tool error safely contained",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    injections=(tool_injection_policy,),
                    tool_max_attempts=2,
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.FAILED,
                    transition_path=_TOOL_FAILED_PATH,
                    failure=ExpectedFailureBehavior(expect_error=True),
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_TOOL_FAILED_PATH,
            expected_terminal_state=WorkflowState.FAILED,
            expected_tool_failures=1,
            expected_failure_classification=ExpectedFailureClassification(
                category="TOOL_FAILURE",
                stage="INVESTIGATION",
                failure_type="TOOL_ERROR",
                contained=True,
            ),
        )
    )

    tool_injection_repair = FailureInjection(
        failure_id="case011-policy-timeout",
        point=FailurePoint.BEFORE_TOOL,
        type=FailureType.TIMEOUT,
        target_component="tool:policy_lookup",
        max_triggers=5,
    )
    cases.append(
        EvaluationCase(
            case_id="CASE-011",
            scenario_name="Policy lookup tool timeout",
            category="TOOL_FAILURE",
            description="Policy lookup tool times out; investigation node safely halts.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-011"),
            scenario=EvaluationScenario(
                scenario_id="CASE-011",
                name="Policy lookup timeout",
                description="Tool timeout handled safely",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    injections=(tool_injection_repair,),
                    tool_max_attempts=2,
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.FAILED,
                    transition_path=_TOOL_FAILED_PATH,
                    failure=ExpectedFailureBehavior(expect_error=True),
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_TOOL_FAILED_PATH,
            expected_terminal_state=WorkflowState.FAILED,
            expected_tool_failures=1,
            expected_failure_classification=ExpectedFailureClassification(
                category="TOOL_FAILURE",
                stage="INVESTIGATION",
                failure_type="TOOL_TIMEOUT",
                contained=True,
            ),
        )
    )

    tool_injection_fraud = FailureInjection(
        failure_id="case012-fraud-unavail",
        point=FailurePoint.BEFORE_TOOL,
        type=FailureType.UNAVAILABLE_DEPENDENCY,
        target_component="tool:fraud_signal_lookup",
        max_triggers=5,
    )
    cases.append(
        EvaluationCase(
            case_id="CASE-012",
            scenario_name="Fraud lookup service unavailable",
            category="TOOL_FAILURE",
            description="External fraud signal service unavailable; bounded failure containment.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-012"),
            scenario=EvaluationScenario(
                scenario_id="CASE-012",
                name="Fraud lookup unavailable",
                description="Service unavailability handled safely",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    injections=(tool_injection_fraud,),
                    tool_max_attempts=2,
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.FAILED,
                    transition_path=_TOOL_FAILED_PATH,
                    failure=ExpectedFailureBehavior(expect_error=True),
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_TOOL_FAILED_PATH,
            expected_terminal_state=WorkflowState.FAILED,
            expected_tool_failures=1,
            expected_failure_classification=ExpectedFailureClassification(
                category="TOOL_FAILURE",
                stage="INVESTIGATION",
                failure_type="TOOL_UNAVAILABLE",
                contained=True,
            ),
        )
    )

    tool_injection_doc = FailureInjection(
        failure_id="case013-fraud-fail",
        point=FailurePoint.BEFORE_TOOL,
        type=FailureType.EXCEPTION,
        target_component="tool:fraud_signal_lookup",
        max_triggers=5,
    )
    cases.append(
        EvaluationCase(
            case_id="CASE-013",
            scenario_name="Fraud signal tool exception",
            category="TOOL_FAILURE",
            description="Fraud signal lookup failure during investigation halts the node gracefully.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-013"),
            scenario=EvaluationScenario(
                scenario_id="CASE-013",
                name="Fraud lookup exception",
                description="Tool error handled safely",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    injections=(tool_injection_doc,),
                    tool_max_attempts=2,
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.FAILED,
                    transition_path=_TOOL_FAILED_PATH,
                    failure=ExpectedFailureBehavior(expect_error=True),
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_TOOL_FAILED_PATH,
            expected_terminal_state=WorkflowState.FAILED,
            expected_tool_failures=1,
            expected_failure_classification=ExpectedFailureClassification(
                category="TOOL_FAILURE",
                stage="INVESTIGATION",
                failure_type="TOOL_ERROR",
                contained=True,
            ),
        )
    )

    # ------------------------------------------------------------------
    # Category D: Provider failures / retries (CASE-014 to CASE-016)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-014",
            scenario_name="Extractor provider transient error recovery",
            category="PROVIDER_FAILURE",
            description="Provider returns 500 on first invocation; retry succeeds.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-014"),
            scenario=EvaluationScenario(
                scenario_id="CASE-014",
                name="Extractor provider error recovers",
                description="Provider error recovered on retry",
                initial=ScenarioInitial(scripts=_approve_scripts(provider_errors=1)),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    failure=ExpectedFailureBehavior(min_retries=1),
                    agent_executions=3,
                    require_invariants=("B", "D", "E"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_retry_count=1,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-015",
            scenario_name="Investigator provider retry on transient timeout",
            category="PROVIDER_FAILURE",
            description="Investigator provider times out once; retry successfully produces payload.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-015"),
            scenario=EvaluationScenario(
                scenario_id="CASE-015",
                name="Investigator provider timeout recovers",
                description="Investigator retry succeeds",
                initial=ScenarioInitial(
                    scripts=(
                        AgentScript(
                            agent="extractor",
                            steps=(
                                ScriptedStep(
                                    kind=ScriptKind.PAYLOAD,
                                    recipe=PayloadRecipe.EXTRACTION_OK,
                                ),
                            ),
                        ),
                        AgentScript(
                            agent="investigator",
                            steps=(
                                ScriptedStep(
                                    kind=ScriptKind.TIMEOUT,
                                    error_retryable=True,
                                ),
                                ScriptedStep(
                                    kind=ScriptKind.PAYLOAD,
                                    recipe=PayloadRecipe.INVESTIGATION_OK,
                                ),
                            ),
                        ),
                        AgentScript(
                            agent="reviewer",
                            steps=(
                                ScriptedStep(
                                    kind=ScriptKind.PAYLOAD,
                                    recipe=PayloadRecipe.REVIEW_APPROVE,
                                ),
                            ),
                        ),
                    ),
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    failure=ExpectedFailureBehavior(min_retries=1),
                    agent_executions=3,
                    require_invariants=("B", "D", "E"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_retry_count=1,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-016",
            scenario_name="Malformed agent output recovers on retry",
            category="PROVIDER_FAILURE",
            description="LLM serves malformed non-JSON string; contract retry produces valid extraction.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-016"),
            scenario=EvaluationScenario(
                scenario_id="CASE-016",
                name="Malformed output retry",
                description="Contract violation retried and resolved",
                initial=ScenarioInitial(scripts=_approve_scripts(malformed_then_ok=True)),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    failure=ExpectedFailureBehavior(min_retries=1),
                    agent_executions=3,
                    require_invariants=("B", "D", "E"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_retry_count=1,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
        )
    )

    # ------------------------------------------------------------------
    # Category E: Conflicting evidence (CASE-017 to CASE-019)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-017",
            scenario_name="Claim amount exceeds vehicle market value",
            category="CONFLICTING_EVIDENCE",
            description="Claimed damage exceeds vehicle actual cash value; Reviewer recommends REJECT.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-017",
                claim_amount=Decimal("15000.00"),
                description="Old sedan repair estimate exceeds market value",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-017",
                name="Claim exceeds vehicle value",
                description="Reviewer rejects total loss discrepancy",
                initial=ScenarioInitial(scripts=_reject_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_REJECT_PATH,
                    agent_executions=3,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="none"),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-018",
            scenario_name="Police report conflicts with claimant statement",
            category="CONFLICTING_EVIDENCE",
            description="Claimant asserts green light; police citation establishes red light violation.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-018",
                description="Disputed red light intersection collision",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-018",
                name="Police report conflict",
                description="Reviewer rejects contradictory claim",
                initial=ScenarioInitial(scripts=_reject_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_REJECT_PATH,
                    agent_executions=3,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="none"),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-019",
            scenario_name="Conflicting repair labor rates resolved via rework",
            category="CONFLICTING_EVIDENCE",
            description="Shop labor rates conflict with regional standard; reworked estimate approved.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-019",
                claim_amount=Decimal("4800.00"),
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-019",
                name="Labor rate conflict resolved",
                description="Rate conflict reconciled in rework loop",
                initial=ScenarioInitial(scripts=_approve_scripts(rework_then_approve=True)),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_REWORK_APPROVE_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    rework_count=1,
                    agent_executions=5,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REWORK_APPROVE_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_rework_count=1,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
        )
    )

    # ------------------------------------------------------------------
    # Category F: Missing policy / evidence (CASE-020 to CASE-021)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-020",
            scenario_name="Policy lapsed prior to incident date",
            category="MISSING_EVIDENCE",
            description="Investigation reveals policy cancelled 15 days before incident; claim rejected.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-020-LAPSED",
                description="Incident occurred after policy cancellation",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-020",
                name="Policy lapsed rejection",
                description="Reviewer rejects lapsed policy",
                initial=ScenarioInitial(scripts=_reject_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_REJECT_PATH,
                    agent_executions=3,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="none"),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-021",
            scenario_name="Missing essential proof of loss documents",
            category="MISSING_EVIDENCE",
            description="Claimant failed to provide photo or repair estimate evidence; rejected.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-021",
                documents=(),
                description="No documentation provided",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-021",
                name="Missing proof of loss",
                description="Reviewer rejects unverified claim",
                initial=ScenarioInitial(scripts=_reject_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_REJECT_PATH,
                    agent_executions=3,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="none"),
        )
    )

    # ------------------------------------------------------------------
    # Category G: Fraud / risk signals (CASE-022 to CASE-023)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-022",
            scenario_name="High fraud score: multiple recent total-loss claims",
            category="FRAUD_SIGNALS",
            description="Fraud signal lookup detects 3 total-loss claims within 90 days; rejected.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-022",
                description="Suspicious repeat total loss claim",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-022",
                name="High fraud score rejection",
                description="Reviewer rejects high risk claim",
                initial=ScenarioInitial(scripts=_reject_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_REJECT_PATH,
                    agent_executions=3,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="none"),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-023",
            scenario_name="Staged accident indicators flagged",
            category="FRAUD_SIGNALS",
            description="Damage geometry inconsistent with described stationary collision; rejected.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-023",
                description="Inconsistent impact angle suggests staged event",
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-023",
                name="Staged accident rejection",
                description="Reviewer rejects staged event indicators",
                initial=ScenarioInitial(scripts=_reject_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_REJECT_PATH,
                    agent_executions=3,
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="none"),
        )
    )

    # ------------------------------------------------------------------
    # Category H: Budget / step exhaustion (CASE-024 to CASE-025)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-024",
            scenario_name="Cost budget ceiling exhausted",
            category="BUDGET_EXHAUSTION",
            description="Envelope cost ceiling set to $0.0001; halts at BUDGET_EXHAUSTED.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-024"),
            scenario=EvaluationScenario(
                scenario_id="CASE-024",
                name="Cost ceiling exhausted",
                description="Cost limit latches terminal budget stop",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    envelope=BudgetEnvelope(max_cost_usd=Decimal("0.0001")),
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.BUDGET_EXHAUSTED,
                    budget=ExpectedBudgetBehavior(terminal_reason="MAX_COST_EXCEEDED"),
                    require_invariants=("A", "B", "E"),
                ),
            ),
            expected_state_path=(),  # Path ends in BUDGET_EXHAUSTED
            expected_terminal_state=WorkflowState.BUDGET_EXHAUSTED,
            expected_budget_outcome=ExpectedBudgetOutcome(
                cost_ceiling=Decimal("0.0001"),
                terminal_reason="MAX_COST_EXCEEDED",
            ),
            expected_failure_classification=ExpectedFailureClassification(
                category="BUDGET_EXHAUSTION",
                stage="BUDGET",
                failure_type="BUDGET_EXHAUSTION",
                contained=True,
            ),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-025",
            scenario_name="Maximum workflow steps exhausted",
            category="BUDGET_EXHAUSTION",
            description="Envelope max_steps=2; transition past investigation denied to MAX_STEPS_EXCEEDED.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-025"),
            scenario=EvaluationScenario(
                scenario_id="CASE-025",
                name="Max steps exhausted",
                description="Step ceiling terminates workflow safely",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    envelope=BudgetEnvelope(max_steps=2),
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.MAX_STEPS_EXCEEDED,
                    transition_path=(
                        WorkflowState.RECEIVED,
                        WorkflowState.EXTRACTION,
                        WorkflowState.INVESTIGATION,
                        WorkflowState.MAX_STEPS_EXCEEDED,
                    ),
                    budget=ExpectedBudgetBehavior(
                        steps_consumed=2,
                        terminal_reason="MAX_STEPS_EXCEEDED",
                    ),
                    require_invariants=("A", "B", "D", "E"),
                ),
            ),
            expected_state_path=(
                WorkflowState.RECEIVED,
                WorkflowState.EXTRACTION,
                WorkflowState.INVESTIGATION,
                WorkflowState.MAX_STEPS_EXCEEDED,
            ),
            expected_terminal_state=WorkflowState.MAX_STEPS_EXCEEDED,
            expected_budget_outcome=ExpectedBudgetOutcome(
                steps_consumed=2,
                terminal_reason="MAX_STEPS_EXCEEDED",
            ),
            expected_failure_classification=ExpectedFailureClassification(
                category="STEP_EXHAUSTION",
                stage="SUPERVISOR",
                failure_type="MAX_STEPS_EXCEEDED",
                contained=True,
            ),
        )
    )

    # ------------------------------------------------------------------
    # Category I: Human rejection / expiration (CASE-026 to CASE-027)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-026",
            scenario_name="Human adjuster rejects claim recommendation",
            category="HUMAN_APPROVAL",
            description="Reviewer recommended approval, but human adjuster exercises veto to REJECT.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-026"),
            scenario=EvaluationScenario(
                scenario_id="CASE-026",
                name="Human adjuster veto",
                description="Explicit human reject decision",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    approval_action="reject",
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_HUMAN_REJECT_PATH,
                    approval=ApprovalExpectation(outcome="rejected"),
                    require_invariants=("A", "F", "G"),
                ),
            ),
            expected_state_path=_HUMAN_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(
                outcome="rejected", actor_role="human"
            ),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-027",
            scenario_name="Human adjuster denies dubious commercial loss",
            category="HUMAN_APPROVAL",
            description="High value commercial loss denied at human gate.",
            claim_fixture=ClaimSpec(
                policy_id="POL-SYN-027",
                claim_amount=Decimal("9500.00"),
            ),
            scenario=EvaluationScenario(
                scenario_id="CASE-027",
                name="Human gate commercial denial",
                description="Commercial loss denied by human",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    approval_action="reject",
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.REJECTED,
                    transition_path=_HUMAN_REJECT_PATH,
                    approval=ApprovalExpectation(outcome="rejected"),
                    require_invariants=("A", "F", "G"),
                ),
            ),
            expected_state_path=_HUMAN_REJECT_PATH,
            expected_terminal_state=WorkflowState.REJECTED,
            expected_approval_behavior=ExpectedApprovalOutcome(
                outcome="rejected", actor_role="human"
            ),
        )
    )

    # ------------------------------------------------------------------
    # Category J: Checkpoint / resume / replay (CASE-028 to CASE-030)
    # ------------------------------------------------------------------
    cases.append(
        EvaluationCase(
            case_id="CASE-028",
            scenario_name="Process restart and resume from checkpoint",
            category="CHECKPOINT_REPLAY",
            description="Process memory wiped at INVESTIGATION; resumed from checkpoint to APPROVED.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-028"),
            scenario=EvaluationScenario(
                scenario_id="CASE-028",
                name="Restart mid-investigation",
                description="Resume from checkpoint to completion",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    restart_at=WorkflowState.INVESTIGATION,
                ),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    agent_executions=3,
                    require_invariants=("A", "B", "G", "K"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_checkpoint_behavior=ExpectedCheckpointBehavior(
                checkpoint_created=True,
                checkpoint_restored=True,
            ),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-029",
            scenario_name="Deterministic replay reproduces live trajectory",
            category="CHECKPOINT_REPLAY",
            description="Replay against checkpoint produces identical terminal state and node path.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-029"),
            scenario=EvaluationScenario(
                scenario_id="CASE-029",
                name="Replay equivalence",
                description="Replay matches live trajectory without side-effects",
                initial=ScenarioInitial(scripts=_approve_scripts()),
                expected=ExpectedBehavior(
                    terminal_state=WorkflowState.APPROVED,
                    transition_path=_NOMINAL_PATH,
                    approval=ApprovalExpectation(outcome="granted"),
                    replay=ExpectedReplayBehavior(
                        runs=True,
                        mode="REPLAY",
                        terminal_matches_live=True,
                        expect_no_mutation=True,
                    ),
                    require_invariants=("H", "I"),
                ),
            ),
            expected_state_path=_NOMINAL_PATH,
            expected_terminal_state=WorkflowState.APPROVED,
            expected_approval_behavior=ExpectedApprovalOutcome(outcome="granted"),
            expected_replay_behavior=ReplayExpectation(
                runs=True,
                terminal_matches=True,
                expect_no_mutation=True,
            ),
        )
    )

    cases.append(
        EvaluationCase(
            case_id="CASE-030",
            scenario_name="Corrupted checkpoint detected and rejected",
            category="CHECKPOINT_REPLAY",
            description="Tampered snapshot hash fails integrity check; resume fails closed.",
            claim_fixture=ClaimSpec(policy_id="POL-SYN-030"),
            scenario=EvaluationScenario(
                scenario_id="CASE-030",
                name="Corrupt checkpoint rejected",
                description="Tampered snapshot fails closed",
                initial=ScenarioInitial(
                    scripts=_approve_scripts(),
                    restart_at=WorkflowState.INVESTIGATION,
                    corrupt_checkpoint=True,
                ),
                expected=ExpectedBehavior(
                    terminal_state=None,
                    failure=ExpectedFailureBehavior(
                        expect_error=True,
                        expect_error_code="CheckpointCorruptError",
                    ),
                    require_invariants=("J",),
                ),
            ),
            expected_state_path=(),
            expected_terminal_state=None,
            expected_checkpoint_behavior=ExpectedCheckpointBehavior(
                checkpoint_created=True,
                checkpoint_restored=False,
                integrity_verified=False,
            ),
            expected_failure_classification=ExpectedFailureClassification(
                category="CHECKPOINT_FAILURE",
                stage="CHECKPOINT",
                failure_type="CHECKPOINT_WRITE_ERROR",
                contained=True,
            ),
        )
    )

    return tuple(cases)


EVALUATION_DATASET: tuple[EvaluationCase, ...] = _build_cases()

DATASET_CASES: dict[str, EvaluationCase] = {case.case_id: case for case in EVALUATION_DATASET}


def get_evaluation_case(case_id: str) -> EvaluationCase:
    """Retrieve an evaluation case by deterministic CASE-XXX identifier."""
    try:
        return DATASET_CASES[case_id]
    except KeyError:
        known = ", ".join(sorted(DATASET_CASES))
        raise KeyError(f"Unknown case_id {case_id!r}; known: {known}") from None
