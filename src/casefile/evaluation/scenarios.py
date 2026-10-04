"""
Evaluation scenario models and the golden suite (G01–G15).

A scenario fully describes initial state (claim, budget envelope, agent
scripts, injections, harness flags) and expected behavior (terminal state,
path, approval/replay/budget/failure expectations, invariant set). Golden
scenarios are versioned and deterministic — same inputs, same report.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from casefile.budget.envelope import BudgetEnvelope
from casefile.evaluation.failures import FailureInjection
from casefile.evaluation.fixtures import ClaimSpec, PayloadRecipe
from casefile.models.contracts import WorkflowState
from casefile.models.versioning import SchemaVersion


class ScriptKind(str, Enum):
    """What one scripted provider step serves."""

    PAYLOAD = "payload"
    TEXT = "text"
    ERROR = "error"
    TIMEOUT = "timeout"


class ScriptedStep(BaseModel):
    """One queued provider outcome. Frozen."""

    model_config = {"frozen": True}

    kind: ScriptKind = ScriptKind.PAYLOAD
    recipe: PayloadRecipe | None = None
    text: str = ""
    error_retryable: bool = True
    input_tokens: int = Field(default=100, ge=0)
    output_tokens: int = Field(default=50, ge=0)
    schema_version: SchemaVersion = "1.0.0"


class AgentScript(BaseModel):
    """Ordered provider steps for one agent. Frozen."""

    model_config = {"frozen": True}

    agent: Literal["extractor", "investigator", "reviewer"]
    steps: tuple[ScriptedStep, ...] = ()


class ExpectedBudgetBehavior(BaseModel):
    """Budget assertions for one scenario. Frozen."""

    model_config = {"frozen": True}

    steps_consumed: int | None = Field(default=None, ge=0)
    tool_calls: int | None = Field(default=None, ge=0)
    rework_cycles: int | None = Field(default=None, ge=0)
    cost_nonzero: bool = False
    terminal_reason: str | None = None
    schema_version: SchemaVersion = "1.0.0"


class ExpectedReplayBehavior(BaseModel):
    """Replay assertions for one scenario. Frozen."""

    model_config = {"frozen": True}

    runs: bool = False
    mode: Literal["REPLAY"] | None = None
    terminal_matches_live: bool = False
    expect_missing_artifact: bool = False
    expect_no_mutation: bool = False
    schema_version: SchemaVersion = "1.0.0"


class ExpectedFailureBehavior(BaseModel):
    """Failure-injection / error assertions. Frozen."""

    model_config = {"frozen": True}

    expect_error: bool = False
    expect_error_code: str | None = None
    min_retries: int = Field(default=0, ge=0)
    max_retries: int | None = Field(default=None, ge=0)
    min_agent_executions: int | None = Field(default=None, ge=0)
    schema_version: SchemaVersion = "1.0.0"


class ApprovalExpectation(BaseModel):
    """Human approval assertions. Frozen."""

    model_config = {"frozen": True}

    outcome: Literal["granted", "rejected", "pending", "none", "race_one_winner"] = "none"
    schema_version: SchemaVersion = "1.0.0"


class ConcurrencyMode(str, Enum):
    """Optional concurrency driver for a scenario."""

    NONE = "NONE"
    BUDGET_RACE = "BUDGET_RACE"
    APPROVAL_RACE = "APPROVAL_RACE"


class ScenarioInitial(BaseModel):
    """Everything the runner needs to set up one scenario. Frozen."""

    model_config = {"frozen": True}

    claim: ClaimSpec = Field(default_factory=ClaimSpec)
    envelope: BudgetEnvelope = Field(default_factory=BudgetEnvelope)
    scripts: tuple[AgentScript, ...] = ()
    injections: tuple[FailureInjection, ...] = ()
    max_attempts: int = Field(default=2, ge=1, le=5)
    tool_max_attempts: int = Field(default=2, ge=1, le=5)
    restart_at: WorkflowState | None = None
    corrupt_checkpoint: bool = False
    omit_replay_artifacts: bool = False
    broken_telemetry: bool = False
    concurrency: ConcurrencyMode = ConcurrencyMode.NONE
    approval_action: Literal["approve", "reject", "none"] = "approve"
    race_contenders: int = Field(default=6, ge=2, le=32)
    untrusted_claim_marker: bool = False
    max_iterations: int = Field(default=64, ge=1, le=256)
    schema_version: SchemaVersion = "1.0.0"


class ExpectedBehavior(BaseModel):
    """Assertions the runner evaluates after the drive loop. Frozen."""

    model_config = {"frozen": True}

    terminal_state: WorkflowState | None = None
    terminal_reason: str | None = None
    transition_path: tuple[WorkflowState, ...] = ()
    approval: ApprovalExpectation = Field(default_factory=ApprovalExpectation)
    budget: ExpectedBudgetBehavior = Field(default_factory=ExpectedBudgetBehavior)
    replay: ExpectedReplayBehavior = Field(default_factory=ExpectedReplayBehavior)
    failure: ExpectedFailureBehavior = Field(default_factory=ExpectedFailureBehavior)
    rework_count: int | None = Field(default=None, ge=0)
    agent_executions: int | None = Field(default=None, ge=0)
    race_winners: int | None = Field(default=None, ge=1)
    require_invariants: tuple[str, ...] = ()
    schema_version: SchemaVersion = "1.0.0"


class EvaluationScenario(BaseModel):
    """One versioned, deterministic evaluation scenario. Frozen."""

    model_config = {"frozen": True}

    scenario_id: str = Field(pattern=r"^(G\d{2}|CASE-\d{3})$")
    name: str
    description: str = ""
    version: str = "1.0.0"
    initial: ScenarioInitial
    expected: ExpectedBehavior
    schema_version: SchemaVersion = "1.0.0"


def _approve_scripts(
    *,
    review: PayloadRecipe = PayloadRecipe.REVIEW_APPROVE,
    rework_then_approve: bool = False,
    malformed_then_ok: bool = False,
    provider_errors: int = 0,
) -> tuple[AgentScript, ...]:
    """Common happy-path script shapes."""
    extractor_steps: list[ScriptedStep] = []
    for _ in range(provider_errors):
        extractor_steps.append(
            ScriptedStep(
                kind=ScriptKind.ERROR, error_retryable=True, input_tokens=0, output_tokens=0
            )
        )
    if malformed_then_ok:
        extractor_steps.append(
            ScriptedStep(
                kind=ScriptKind.TEXT, text="not-json {{", input_tokens=80, output_tokens=20
            )
        )
    extractor_steps.append(
        ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.EXTRACTION_OK)
    )
    investigator_steps = [
        ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.INVESTIGATION_OK)
    ]
    if rework_then_approve:
        investigator_steps.append(
            ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.INVESTIGATION_OK)
        )
    reviewer_steps: list[ScriptedStep] = []
    if rework_then_approve:
        reviewer_steps.append(
            ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=PayloadRecipe.REVIEW_REWORK)
        )
    reviewer_steps.append(ScriptedStep(kind=ScriptKind.PAYLOAD, recipe=review))
    return (
        AgentScript(agent="extractor", steps=tuple(extractor_steps)),
        AgentScript(agent="investigator", steps=tuple(investigator_steps)),
        AgentScript(agent="reviewer", steps=tuple(reviewer_steps)),
    )


_NOMINAL_PATH = (
    WorkflowState.RECEIVED,
    WorkflowState.EXTRACTION,
    WorkflowState.INVESTIGATION,
    WorkflowState.REVIEW,
    WorkflowState.HUMAN_APPROVAL,
    WorkflowState.APPROVED,
)


def _golden() -> tuple[EvaluationScenario, ...]:
    """Build the golden suite G01–G15 (deterministic, offline)."""
    from casefile.evaluation.failures import (
        FailureInjection,
        FailurePoint,
        FailureType,
        RecoveryBehavior,
    )

    tool_injection = FailureInjection(
        failure_id="G03-TOOL-001",
        point=FailurePoint.BEFORE_TOOL,
        type=FailureType.UNAVAILABLE_DEPENDENCY,
        target_component="tool:policy_lookup",
        max_triggers=1,
        expected_recovery_behavior=RecoveryBehavior.TERMINATE_NODE,
    )
    return (
        EvaluationScenario(
            scenario_id="G01",
            name="Nominal full path",
            description="Claim validates through extraction, investigation, review, human approve.",
            initial=ScenarioInitial(scripts=_approve_scripts(), untrusted_claim_marker=True),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.APPROVED,
                transition_path=_NOMINAL_PATH,
                approval=ApprovalExpectation(outcome="granted"),
                budget=ExpectedBudgetBehavior(cost_nonzero=True, steps_consumed=5),
                agent_executions=3,
                rework_count=0,
                require_invariants=("A", "B", "C", "D", "E", "F", "G", "N", "O"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G02",
            name="Transient provider failure recovers",
            description="Malformed first response then valid payload; bounded retry succeeds.",
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
        EvaluationScenario(
            scenario_id="G03",
            name="Tool failure terminates node",
            description="policy_lookup unavailable; bounded tool retries then INVESTIGATION_FAILED.",
            initial=ScenarioInitial(
                scripts=_approve_scripts(),
                injections=(tool_injection,),
                tool_max_attempts=2,
            ),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.FAILED,
                transition_path=(
                    WorkflowState.RECEIVED,
                    WorkflowState.EXTRACTION,
                    WorkflowState.INVESTIGATION,
                    WorkflowState.FAILED,
                ),
                failure=ExpectedFailureBehavior(expect_error=True),
                require_invariants=("A", "B", "D", "E"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G04",
            name="Single rework loop then approve",
            description="Reviewer requests rework once; second investigation approves.",
            initial=ScenarioInitial(scripts=_approve_scripts(rework_then_approve=True)),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.APPROVED,
                transition_path=(
                    WorkflowState.RECEIVED,
                    WorkflowState.EXTRACTION,
                    WorkflowState.INVESTIGATION,
                    WorkflowState.REVIEW,
                    WorkflowState.REWORK_LOOP,
                    WorkflowState.INVESTIGATION,
                    WorkflowState.REVIEW,
                    WorkflowState.HUMAN_APPROVAL,
                    WorkflowState.APPROVED,
                ),
                approval=ApprovalExpectation(outcome="granted"),
                rework_count=1,
                agent_executions=5,
                require_invariants=("A", "B", "D", "E", "G"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G05",
            name="Human grants approval",
            description="Full path with explicit human APPROVE decision.",
            initial=ScenarioInitial(scripts=_approve_scripts(), approval_action="approve"),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.APPROVED,
                transition_path=_NOMINAL_PATH,
                approval=ApprovalExpectation(outcome="granted"),
                require_invariants=("A", "F", "G"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G06",
            name="Human rejects claim",
            description="Full path with explicit human REJECT decision.",
            initial=ScenarioInitial(scripts=_approve_scripts(), approval_action="reject"),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.REJECTED,
                transition_path=(
                    WorkflowState.RECEIVED,
                    WorkflowState.EXTRACTION,
                    WorkflowState.INVESTIGATION,
                    WorkflowState.REVIEW,
                    WorkflowState.HUMAN_APPROVAL,
                    WorkflowState.REJECTED,
                ),
                approval=ApprovalExpectation(outcome="rejected"),
                require_invariants=("A", "F", "G"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G07",
            name="Retry budget exhausted",
            description="Provider always errors; retry guard denies beyond max_agent_retries.",
            initial=ScenarioInitial(
                scripts=_approve_scripts(provider_errors=5),
                envelope=BudgetEnvelope(max_agent_retries=1),
                max_attempts=5,
            ),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.FAILED,
                transition_path=(
                    WorkflowState.RECEIVED,
                    WorkflowState.EXTRACTION,
                    WorkflowState.FAILED,
                ),
                failure=ExpectedFailureBehavior(
                    expect_error=True,
                    expect_error_code="MAX_AGENT_RETRIES_EXCEEDED",
                ),
                require_invariants=("A", "B", "D", "E"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G08",
            name="Max steps budget stop",
            description="Envelope max_steps=2; third transition denied then terminal stop.",
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
        EvaluationScenario(
            scenario_id="G09",
            name="Restart mid-investigation",
            description="Drop process objects at INVESTIGATION; resume from checkpoint to APPROVED.",
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
        EvaluationScenario(
            scenario_id="G10",
            name="Replay equivalence",
            description="Replay from RECEIVED checkpoint reproduces the live path without mutation.",
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
        EvaluationScenario(
            scenario_id="G11",
            name="Corrupt checkpoint rejected",
            description="Tampered snapshot_json fails integrity; resume fails closed.",
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
        EvaluationScenario(
            scenario_id="G12",
            name="Budget final-unit race",
            description="Concurrent last tool-call reservation: exactly one winner.",
            initial=ScenarioInitial(
                scripts=_approve_scripts(),
                concurrency=ConcurrencyMode.BUDGET_RACE,
                envelope=BudgetEnvelope(max_tool_calls=1),
                race_contenders=6,
            ),
            expected=ExpectedBehavior(
                terminal_state=None,
                race_winners=1,
                require_invariants=("D", "E", "M"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G13",
            name="Approval decision race",
            description="Same verdict, different decision keys: one DECIDED, rest CONFLICT.",
            initial=ScenarioInitial(
                scripts=_approve_scripts(),
                concurrency=ConcurrencyMode.APPROVAL_RACE,
                approval_action="approve",
                race_contenders=6,
            ),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.APPROVED,
                approval=ApprovalExpectation(outcome="race_one_winner"),
                race_winners=1,
                require_invariants=("A", "F", "M"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G14",
            name="Missing replay artifact fails loudly",
            description="Omit recorded agents; ReplayAgentProvider refuses live calls.",
            initial=ScenarioInitial(
                scripts=_approve_scripts(),
                omit_replay_artifacts=True,
            ),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.APPROVED,
                replay=ExpectedReplayBehavior(
                    runs=True,
                    mode="REPLAY",
                    expect_missing_artifact=True,
                    expect_no_mutation=True,
                ),
                require_invariants=("H", "I"),
            ),
        ),
        EvaluationScenario(
            scenario_id="G15",
            name="Broken telemetry does not break correctness",
            description="Sink/exporter failures counted; workflow still reaches APPROVED.",
            initial=ScenarioInitial(
                scripts=_approve_scripts(),
                broken_telemetry=True,
            ),
            expected=ExpectedBehavior(
                terminal_state=WorkflowState.APPROVED,
                transition_path=_NOMINAL_PATH,
                approval=ApprovalExpectation(outcome="granted"),
                require_invariants=("P", "B"),
            ),
        ),
    )


GOLDEN_SCENARIOS: dict[str, EvaluationScenario] = {
    scenario.scenario_id: scenario for scenario in _golden()
}


def get_scenario(scenario_id: str) -> EvaluationScenario:
    """Fetch one golden scenario or raise a clear error."""
    try:
        return GOLDEN_SCENARIOS[scenario_id]
    except KeyError:
        known = ", ".join(sorted(GOLDEN_SCENARIOS))
        raise KeyError(f"Unknown scenario {scenario_id!r}; known: {known}") from None
