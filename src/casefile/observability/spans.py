"""
Span-name constants and sanitized attribute builders (Phase 10 §4–§13).

One function per instrumented surface. Inputs are typed domain objects;
outputs are sanitized attribute dicts containing IDs, enums, counts,
durations, and versions only — never documents, secrets, reasoning, or
unrestricted payloads.
"""

from __future__ import annotations

from casefile.models.domain import AgentType
from casefile.observability.context import ExecMode

SPAN_WORKFLOW_RUN = "casefile.workflow.run"
SPAN_TRANSITION = "casefile.workflow.transition"
SPAN_SUPERVISOR_ROUTE = "casefile.supervisor.route"
SPAN_AGENT_RUN = "casefile.agent.run"
SPAN_TOOL_CALL = "casefile.tool.call"
SPAN_CHECKPOINT_CREATE = "casefile.checkpoint.create"
SPAN_CHECKPOINT_LOAD = "casefile.checkpoint.load"
SPAN_BUDGET_CHECK = "casefile.budget.check"
SPAN_APPROVAL_DECIDE = "casefile.approval.decide"
SPAN_REPLAY_RUN = "casefile.replay.run"
SPAN_PERSISTENCE = "casefile.persistence"

# Stable standard root/category span names (Phase 5 Part 15)
SPAN_WORKFLOW = "casefile.workflow"
SPAN_AGENT = "casefile.agent"
SPAN_TOOL = "casefile.tool"
SPAN_CHECKPOINT = "casefile.checkpoint"
SPAN_BUDGET = "casefile.budget"
SPAN_APPROVAL = "casefile.approval"


def workflow_attributes(
    *,
    state: str,
    mode: ExecMode = ExecMode.LIVE,
    terminal_state: str = "",
    termination_reason: str = "",
    duration_ms: int = 0,
    budget_version: str = "",
    provider: str = "",
    model: str = "",
    error: bool = False,
) -> dict[str, object]:
    """Root workflow span attributes."""
    attributes: dict[str, object] = {
        "casefile.workflow_state": state,
        "casefile.mode": mode.value,
    }
    if terminal_state:
        attributes["casefile.terminal_state"] = terminal_state
    if termination_reason:
        attributes["casefile.termination_reason"] = termination_reason
    if duration_ms:
        attributes["casefile.duration_ms"] = duration_ms
    if budget_version:
        attributes["casefile.budget.version"] = budget_version
    if provider:
        attributes["casefile.provider"] = provider
    if model:
        attributes["casefile.model"] = model
    if error:
        attributes["casefile.error"] = True
    return attributes


def transition_attributes(
    *,
    source_state: str,
    destination_state: str,
    trigger: str,
    actor: str,
    sequence_no: int = 0,
    duration_ms: int = 0,
    outcome: str = "success",
) -> dict[str, object]:
    """Workflow transition span attributes."""
    return {
        "casefile.source_state": source_state,
        "casefile.destination_state": destination_state,
        "casefile.trigger": trigger,
        "casefile.actor": actor,
        "casefile.sequence_no": sequence_no,
        "casefile.duration_ms": duration_ms,
        "casefile.outcome": outcome,
    }


def supervisor_attributes(
    *,
    current_state: str,
    trigger: str,
    route: str,
    reason_code: str,
    rework_cycle: int = 0,
    budget_remaining_steps: int = 0,
) -> dict[str, object]:
    """Supervisor routing decision attributes (structured only)."""
    return {
        "casefile.current_state": current_state,
        "casefile.trigger": trigger,
        "casefile.route": route,
        "casefile.reason_code": reason_code,
        "casefile.rework_cycle": rework_cycle,
        "casefile.budget_remaining_steps": budget_remaining_steps,
    }


def agent_attributes(
    *,
    agent: AgentType | str,
    prompt_version: str,
    provider: str,
    model: str,
    retry_number: int = 0,
    mode: ExecMode = ExecMode.LIVE,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: str = "",
    duration_ms: int = 0,
    success: bool = True,
    error_category: str = "",
) -> dict[str, object]:
    """Agent execution span attributes (no reasoning, no documents)."""
    name = agent.value if isinstance(agent, AgentType) else str(agent)
    attributes: dict[str, object] = {
        "casefile.agent": name,
        "casefile.prompt_version": prompt_version,
        "casefile.provider": provider,
        "casefile.model": model,
        "casefile.retry_number": retry_number,
        "casefile.mode": mode.value,
        "casefile.input_tokens": input_tokens,
        "casefile.output_tokens": output_tokens,
        "casefile.duration_ms": duration_ms,
        "casefile.success": success,
    }
    if cost_usd:
        attributes["casefile.cost_usd"] = cost_usd
    if error_category:
        attributes["casefile.error_category"] = error_category
    return attributes


def tool_attributes(
    *,
    tool_name: str,
    tool_version: str,
    agent: str,
    authorization: str,
    mode: ExecMode = ExecMode.LIVE,
    duration_ms: int = 0,
    success: bool = True,
    error_category: str = "",
    result_count: int = 0,
    tool_cost_units: int = 0,
) -> dict[str, object]:
    """Tool invocation span attributes (bounded metadata only)."""
    attributes: dict[str, object] = {
        "casefile.tool": tool_name,
        "casefile.tool_version": tool_version,
        "casefile.agent": agent,
        "casefile.authorization": authorization,
        "casefile.mode": mode.value,
        "casefile.duration_ms": duration_ms,
        "casefile.success": success,
        "casefile.result_count": result_count,
        "casefile.tool_cost_units": tool_cost_units,
    }
    if error_category:
        attributes["casefile.error_category"] = error_category
    return attributes


def checkpoint_attributes(
    *,
    operation: str,
    sequence_no: int = 0,
    kind: str = "",
    workflow_state: str = "",
    integrity_ok: bool = True,
    duration_ms: int = 0,
    success: bool = True,
) -> dict[str, object]:
    """Checkpoint operation attributes (never raw payloads)."""
    return {
        "casefile.checkpoint_operation": operation,
        "casefile.sequence_no": sequence_no,
        "casefile.checkpoint_kind": kind,
        "casefile.kind": kind,
        "casefile.workflow_state": workflow_state,
        "casefile.integrity_ok": integrity_ok,
        "casefile.duration_ms": duration_ms,
        "casefile.success": success,
    }


def budget_attributes(
    *,
    configured_steps: int = 0,
    consumed_steps: int = 0,
    remaining_steps: int = 0,
    consumed_tokens: int = 0,
    consumed_cost_usd: str = "",
    termination_reason: str = "",
    reservation_granted: bool = True,
) -> dict[str, object]:
    """Budget telemetry attributes (numeric, low cardinality)."""
    attributes: dict[str, object] = {
        "casefile.budget_configured_steps": configured_steps,
        "casefile.budget_consumed_steps": consumed_steps,
        "casefile.budget_remaining_steps": remaining_steps,
        "casefile.budget_consumed_tokens": consumed_tokens,
        "casefile.reservation_granted": reservation_granted,
    }
    if consumed_cost_usd:
        attributes["casefile.budget_consumed_cost_usd"] = consumed_cost_usd
    if termination_reason:
        attributes["casefile.termination_reason"] = termination_reason
    return attributes


def approval_attributes(
    *,
    outcome: str,
    actor_type: str,
    request_version: int = 0,
    wait_duration_ms: int = 0,
    success: bool = True,
) -> dict[str, object]:
    """Approval lifecycle attributes (no credentials, minimal identity)."""
    return {
        "casefile.approval_outcome": outcome,
        "casefile.actor_type": actor_type,
        "casefile.request_version": request_version,
        "casefile.wait_duration_ms": wait_duration_ms,
        "casefile.success": success,
    }


def persistence_attributes(
    *,
    operation: str,
    entity_type: str,
    duration_ms: int = 0,
    success: bool = True,
    error_category: str = "",
) -> dict[str, object]:
    """Persistence repository operation attributes (no raw SQL params)."""
    attrs: dict[str, object] = {
        "casefile.persistence_operation": operation,
        "casefile.entity_type": entity_type,
        "casefile.duration_ms": duration_ms,
        "casefile.success": success,
    }
    if error_category:
        attrs["casefile.error_category"] = error_category
    return attrs
