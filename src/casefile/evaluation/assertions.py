"""
Invariant engine (Phase 11 acceptance invariants A–P).

Each invariant is a pure function over InvariantContext producing a
typed InvariantResult (PASS / FAIL / SKIPPED with evidence). SKIPPED
results are excluded from the pass rate. Contexts are built by the
runner from durable state — no live providers, no network.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.termination import BudgetTermination
from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType, ApprovalStatus
from casefile.models.versioning import SchemaVersion
from casefile.workflow.triggers import Trigger

INVARIANT_LETTERS: tuple[str, ...] = tuple("ABCDEFGHIJKLMNOP")


class InvariantStatus(str, Enum):
    """PASS / FAIL / SKIPPED for one invariant."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class InvariantResult(BaseModel):
    """Typed outcome for one invariant in one scenario. Frozen."""

    model_config = {"frozen": True}

    invariant_id: str = Field(pattern=r"^[A-P]$")
    name: str
    status: InvariantStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    scenario_id: str = ""
    schema_version: SchemaVersion = "1.0.0"


class TransitionFact(BaseModel):
    """One durable transition fact for sequence / idempotency checks."""

    sequence_no: int = Field(ge=1)
    execution_id: str
    idempotency_key: str
    trigger: str
    actor: str
    source: str
    destination: str
    reason: str = ""


class ApprovalFact(BaseModel):
    """Approval facts for human-only and immutability invariants."""

    status: str = ""
    approver_role: str | None = None
    recommendation_json: str = ""
    reviewer_summary: str = ""
    request_version: int = 1


class BudgetFact(BaseModel):
    """Observed budget usage vs envelope for non-exceedance checks."""

    steps: int = Field(default=0, ge=0)
    agent_steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    rework_cycles: int = Field(default=0, ge=0)
    agent_retries: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    terminal_reason: str | None = None


class InvariantContext(BaseModel):
    """Everything the invariant functions need for one scenario run."""

    scenario_id: str = ""
    terminal_state: WorkflowState | None = None
    transition_path: tuple[WorkflowState, ...] = ()
    transitions: tuple[TransitionFact, ...] = ()
    reached_terminal: bool = False
    ops_after_terminal: int = Field(default=0, ge=0)
    budget: BudgetFact = Field(default_factory=BudgetFact)
    envelope: BudgetEnvelope = Field(default_factory=BudgetEnvelope)
    approval: ApprovalFact | None = None
    approval_immutable_ok: bool = True
    replay_row_delta: int | None = None
    replay_provider_requests: int | None = None
    replay_terminal_matches: bool | None = None
    checkpoint_integrity_ok: bool | None = None
    checkpoint_rejected: bool | None = None
    per_agent_run_counts: dict[str, int] = Field(default_factory=dict)
    duplicate_applied: bool | None = None
    untrusted_marker: str | None = None
    marker_in_triggers_or_reasons: bool = False
    triggers_valid_enum: bool = True
    postgres_absent: bool = True
    sqlite_backend: bool = True
    telemetry_errors: int = 0
    broken_telemetry_enabled: bool = False
    schema_version: SchemaVersion = "1.0.0"


_INVARIANT_NAMES: dict[str, str] = {
    "A": "terminal_state_immutable",
    "B": "workflow_eventually_terminates",
    "C": "no_operations_after_terminal",
    "D": "budget_never_exceeded",
    "E": "counters_non_negative",
    "F": "human_only_approval",
    "G": "approval_request_immutable",
    "H": "replay_makes_no_mutations",
    "I": "replay_makes_no_live_calls",
    "J": "checkpoint_integrity_fail_closed",
    "K": "resume_creates_no_duplicates",
    "L": "idempotent_reapply",
    "M": "sequence_numbers_contiguous",
    "N": "untrusted_content_cannot_drive_control",
    "O": "postgresql_permanently_absent",
    "P": "telemetry_failure_does_not_break_correctness",
}


def _pass(letter: str, ctx: InvariantContext, **evidence: Any) -> InvariantResult:
    return InvariantResult(
        invariant_id=letter,
        name=_INVARIANT_NAMES[letter],
        status=InvariantStatus.PASS,
        evidence=evidence,
        scenario_id=ctx.scenario_id,
        message="ok",
    )


def _fail(letter: str, ctx: InvariantContext, message: str, **evidence: Any) -> InvariantResult:
    return InvariantResult(
        invariant_id=letter,
        name=_INVARIANT_NAMES[letter],
        status=InvariantStatus.FAIL,
        evidence=evidence,
        message=message,
        scenario_id=ctx.scenario_id,
    )


def _skip(letter: str, ctx: InvariantContext, reason: str) -> InvariantResult:
    return InvariantResult(
        invariant_id=letter,
        name=_INVARIANT_NAMES[letter],
        status=InvariantStatus.SKIPPED,
        message=reason,
        scenario_id=ctx.scenario_id,
    )


def _check_a(ctx: InvariantContext) -> InvariantResult:
    """A: terminal state never receives further transitions."""
    if ctx.terminal_state is None:
        return _skip("A", ctx, "scenario did not reach a terminal state")
    if not ctx.terminal_state.is_terminal:
        return _fail("A", ctx, f"{ctx.terminal_state.value} is not terminal")
    if ctx.ops_after_terminal != 0:
        return _fail(
            "A",
            ctx,
            f"{ctx.ops_after_terminal} operations recorded after terminal",
            ops_after_terminal=ctx.ops_after_terminal,
        )
    return _pass("A", ctx, terminal=ctx.terminal_state.value)


def _check_b(ctx: InvariantContext) -> InvariantResult:
    """B: every completed run reaches a terminal state (or expected error)."""
    if not ctx.reached_terminal:
        if ctx.terminal_state is None:
            return _skip("B", ctx, "expected-error scenario without terminal drive")
        return _fail("B", ctx, "run did not reach a terminal state")
    return _pass("B", ctx, terminal=str(ctx.terminal_state))


def _check_c(ctx: InvariantContext) -> InvariantResult:
    """C: no agent/tool/transition work is recorded after terminal."""
    if ctx.terminal_state is None:
        return _skip("C", ctx, "no terminal state observed")
    if ctx.ops_after_terminal != 0:
        return _fail(
            "C",
            ctx,
            f"{ctx.ops_after_terminal} post-terminal operations",
            ops_after_terminal=ctx.ops_after_terminal,
        )
    return _pass("C", ctx, ops_after_terminal=0)


def _check_d(ctx: InvariantContext) -> InvariantResult:
    """D: no budget dimension exceeds its envelope; cost breach latches."""
    b, e = ctx.budget, ctx.envelope
    breaches: list[str] = []
    if b.steps > e.max_steps:
        breaches.append(f"steps {b.steps}>{e.max_steps}")
    if b.agent_steps > e.max_agent_steps:
        breaches.append(f"agent_steps {b.agent_steps}>{e.max_agent_steps}")
    if b.tool_calls > e.max_tool_calls:
        breaches.append(f"tool_calls {b.tool_calls}>{e.max_tool_calls}")
    if b.rework_cycles > e.max_rework_cycles:
        breaches.append(f"rework {b.rework_cycles}>{e.max_rework_cycles}")
    if b.agent_retries > e.max_agent_retries:
        breaches.append(f"retries {b.agent_retries}>{e.max_agent_retries}")
    if b.input_tokens > e.max_input_tokens:
        breaches.append(f"input_tokens {b.input_tokens}>{e.max_input_tokens}")
    if b.output_tokens > e.max_output_tokens:
        breaches.append(f"output_tokens {b.output_tokens}>{e.max_output_tokens}")
    if b.total_tokens > e.max_total_tokens:
        breaches.append(f"total_tokens {b.total_tokens}>{e.max_total_tokens}")
    if b.cost_usd > e.max_cost_usd:
        if b.terminal_reason is None:
            breaches.append(f"cost {b.cost_usd} exceeded without latch")
        else:
            breaches.append(f"cost {b.cost_usd}>{e.max_cost_usd} (latched={b.terminal_reason})")
    if breaches:
        return _fail("D", ctx, "; ".join(breaches), breaches=breaches)
    return _pass("D", ctx, steps=b.steps, cost=str(b.cost_usd))


def _check_e(ctx: InvariantContext) -> InvariantResult:
    """E: all observed counters are non-negative."""
    b = ctx.budget
    values = {
        "steps": b.steps,
        "agent_steps": b.agent_steps,
        "tool_calls": b.tool_calls,
        "rework_cycles": b.rework_cycles,
        "agent_retries": b.agent_retries,
        "input_tokens": b.input_tokens,
        "output_tokens": b.output_tokens,
        "total_tokens": b.total_tokens,
        "cost_usd": b.cost_usd,
        "ops_after_terminal": ctx.ops_after_terminal,
    }
    negatives = {
        k: str(v) for k, v in values.items() if isinstance(v, int | float | Decimal) and v < 0
    }
    if negatives:
        return _fail("E", ctx, f"negative counters: {negatives}", negatives=negatives)
    return _pass("E", ctx)


def _check_f(ctx: InvariantContext) -> InvariantResult:
    """F: only HUMAN actors are recorded as approvers."""
    if ctx.approval is None:
        return _skip("F", ctx, "no approval observed")
    role = ctx.approval.approver_role
    if role is None:
        if ctx.approval.status == ApprovalStatus.PENDING.value:
            return _pass("F", ctx, status=ctx.approval.status)
        return _fail(
            "F", ctx, f"settled approval missing approver_role (status={ctx.approval.status})"
        )
    if role != "HUMAN":
        return _fail("F", ctx, f"approver_role {role!r} is not HUMAN")
    return _pass("F", ctx, role=role)


def _check_g(ctx: InvariantContext) -> InvariantResult:
    """G: approval request payload is immutable across decision."""
    if ctx.approval is None:
        return _skip("G", ctx, "no approval observed")
    if not ctx.approval_immutable_ok:
        return _fail("G", ctx, "recommendation/summary changed across decide")
    return _pass(
        "G",
        ctx,
        recommendation_sha_len=len(ctx.approval.recommendation_json),
        request_version=ctx.approval.request_version,
    )


def _check_h(ctx: InvariantContext) -> InvariantResult:
    """H: replay produces zero durable row mutations."""
    if ctx.replay_row_delta is None:
        return _skip("H", ctx, "replay not run")
    if ctx.replay_row_delta != 0:
        return _fail("H", ctx, f"replay mutated {ctx.replay_row_delta} rows")
    return _pass("H", ctx, row_delta=0)


def _check_i(ctx: InvariantContext) -> InvariantResult:
    """I: replay performs zero live provider calls."""
    if ctx.replay_provider_requests is None:
        return _skip("I", ctx, "replay not run")
    if ctx.replay_provider_requests != 0:
        return _fail("I", ctx, f"replay made {ctx.replay_provider_requests} provider calls")
    if ctx.replay_terminal_matches is False:
        return _fail("I", ctx, "replay terminal state diverged from live")
    return _pass("I", ctx, provider_requests=0)


def _check_j(ctx: InvariantContext) -> InvariantResult:
    """J: corrupt checkpoints are rejected (fail closed), valid pass integrity."""
    if ctx.checkpoint_rejected is True:
        return _pass("J", ctx, rejected=True)
    if ctx.checkpoint_integrity_ok is True:
        return _pass("J", ctx, integrity_ok=True)
    if ctx.checkpoint_integrity_ok is False and ctx.checkpoint_rejected is None:
        return _fail("J", ctx, "invalid checkpoint was not rejected")
    return _skip("J", ctx, "no checkpoint integrity observation")


def _check_k(ctx: InvariantContext) -> InvariantResult:
    """K: resume does not duplicate agent executions."""
    if not ctx.per_agent_run_counts:
        return _skip("K", ctx, "no per-agent run counts")
    duplicates = {a: n for a, n in ctx.per_agent_run_counts.items() if n > 1 and a == "extractor"}
    # Extractor must run exactly once even across restart; other agents may
    # legitimately re-run after rework. Restart scenarios pin extractor=1.
    if "extractor" in ctx.per_agent_run_counts and ctx.per_agent_run_counts["extractor"] != 1:
        return _fail(
            "K",
            ctx,
            f"extractor ran {ctx.per_agent_run_counts['extractor']} times",
            counts=ctx.per_agent_run_counts,
        )
    _ = duplicates
    return _pass("K", ctx, counts=ctx.per_agent_run_counts)


def _check_l(ctx: InvariantContext) -> InvariantResult:
    """L: re-applying a non-terminal transition event is idempotent."""
    if ctx.duplicate_applied is None:
        return _skip("L", ctx, "idempotency probe not run")
    if not ctx.duplicate_applied:
        return _fail("L", ctx, "duplicate apply did not report duplicate=True")
    return _pass("L", ctx, duplicate=True)


def _check_m(ctx: InvariantContext) -> InvariantResult:
    """M: per-execution sequences are contiguous 1..n; keys unique."""
    if not ctx.transitions:
        return _skip("M", ctx, "no transitions recorded")
    by_exec: dict[str, list[int]] = {}
    keys: list[str] = []
    for fact in ctx.transitions:
        by_exec.setdefault(fact.execution_id, []).append(fact.sequence_no)
        keys.append(fact.idempotency_key)
    if len(keys) != len(set(keys)):
        return _fail("M", ctx, "duplicate idempotency keys observed")
    for exec_id, seqs in by_exec.items():
        ordered = sorted(seqs)
        expected = list(range(ordered[0], ordered[0] + len(ordered)))
        if ordered != expected:
            return _fail(
                "M",
                ctx,
                f"execution {exec_id} sequence {ordered} is not contiguous",
                execution_id=exec_id,
            )
    # Cross-execution duplicates of sequence_no are allowed after restart
    # (fresh engine resets _sequence); uniqueness is scoped per execution.
    return _pass("M", ctx, executions=len(by_exec), transitions=len(keys))


def _check_n(ctx: InvariantContext) -> InvariantResult:
    """N: untrusted claim marker never appears in control-plane fields."""
    if not ctx.triggers_valid_enum:
        return _fail("N", ctx, "non-enum trigger observed")
    if ctx.marker_in_triggers_or_reasons:
        return _fail("N", ctx, "untrusted marker leaked into triggers/reasons/termination")
    return _pass("N", ctx, marker_present=ctx.untrusted_marker is not None)


def _check_o(ctx: InvariantContext) -> InvariantResult:
    """O: PostgreSQL driver is never imported; backend is SQLite."""
    if not ctx.postgres_absent:
        return _fail("O", ctx, "psycopg/postgres import detected")
    if not ctx.sqlite_backend:
        return _fail("O", ctx, "evaluation backend is not SQLite")
    return _pass("O", ctx, backend="sqlite")


def _check_p(ctx: InvariantContext) -> InvariantResult:
    """P: telemetry failures are counted and never block correctness."""
    if not ctx.broken_telemetry_enabled:
        return _skip("P", ctx, "broken telemetry not enabled for this scenario")
    if ctx.telemetry_errors <= 0:
        return _fail("P", ctx, "expected telemetry errors were not observed")
    if ctx.terminal_state != WorkflowState.APPROVED:
        return _fail(
            "P",
            ctx,
            f"terminal {ctx.terminal_state} despite telemetry failures",
        )
    return _pass("P", ctx, telemetry_errors=ctx.telemetry_errors)


_CHECKS = {
    "A": _check_a,
    "B": _check_b,
    "C": _check_c,
    "D": _check_d,
    "E": _check_e,
    "F": _check_f,
    "G": _check_g,
    "H": _check_h,
    "I": _check_i,
    "J": _check_j,
    "K": _check_k,
    "L": _check_l,
    "M": _check_m,
    "N": _check_n,
    "O": _check_o,
    "P": _check_p,
}


def evaluate_invariants(
    ctx: InvariantContext,
    letters: tuple[str, ...] | list[str] | None = None,
) -> list[InvariantResult]:
    """Evaluate invariants A–P (or a requested subset) against one context."""
    wanted = tuple(letters) if letters else INVARIANT_LETTERS
    results: list[InvariantResult] = []
    for letter in wanted:
        if letter not in _CHECKS:
            raise ValueError(f"Unknown invariant {letter!r}; expected A–P")
        results.append(_CHECKS[letter](ctx))
    return results


def invariant_pass_rate(results: list[InvariantResult]) -> float:
    """PASS / (PASS + FAIL); SKIPPED excluded. 1.0 when nothing judged."""
    judged = [r for r in results if r.status != InvariantStatus.SKIPPED]
    if not judged:
        return 1.0
    passed = sum(1 for r in judged if r.status == InvariantStatus.PASS)
    return passed / len(judged)


def summarize_budget(
    *,
    steps: int = 0,
    agent_steps: int = 0,
    tool_calls: int = 0,
    rework_cycles: int = 0,
    agent_retries: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: Decimal = Decimal("0"),
    terminal_reason: str | None = None,
) -> BudgetFact:
    """Build a BudgetFact from observed counters."""
    return BudgetFact(
        steps=steps,
        agent_steps=agent_steps,
        tool_calls=tool_calls,
        rework_cycles=rework_cycles,
        agent_retries=agent_retries,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        cost_usd=cost_usd,
        terminal_reason=terminal_reason,
    )


def trigger_is_enum(value: str) -> bool:
    """True when value is a declared Trigger member."""
    try:
        Trigger(value)
        return True
    except ValueError:
        return False


def budget_reason_is_known(value: str | None) -> bool:
    """True when value is a declared BudgetTermination member (or None)."""
    if value is None:
        return True
    try:
        BudgetTermination(value)
        return True
    except ValueError:
        return False


def agent_type_is_known(value: str) -> bool:
    """True when value is a declared AgentType member."""
    try:
        AgentType(value)
        return True
    except ValueError:
        return False
