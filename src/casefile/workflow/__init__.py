"""
CASEFILE deterministic workflow/state engine (Phase 3).

Public surface: state categories, triggers, transition table, clock-aware
run context, deterministic engine, supervisor router, LangGraph graph with
typed placeholder nodes, SQLite store boundary, observability hooks.

Lazy exports (PEP 562): importing this package must not pull the LangGraph
graph, stores, or providers as a side effect, so leaf modules (context,
triggers, states) stay importable from anywhere without cycles.
"""

from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "Clock": "casefile.workflow.context",
    "FixedClock": "casefile.workflow.context",
    "RunContext": "casefile.workflow.context",
    "SystemClock": "casefile.workflow.context",
    "WorkflowContext": "casefile.workflow.context",
    "WorkflowSnapshot": "casefile.workflow.context",
    "ApplyResult": "casefile.workflow.engine",
    "WorkflowEngine": "casefile.workflow.engine",
    "idempotency_key_for": "casefile.workflow.engine",
    "ContractValidationError": "casefile.workflow.errors",
    "MalformedOutputError": "casefile.workflow.errors",
    "RetryExhaustedError": "casefile.workflow.errors",
    "WorkflowError": "casefile.workflow.errors",
    "WorkflowExecutionError": "casefile.workflow.errors",
    "GRAPH_NODES": "casefile.workflow.graph",
    "build_agent_graph": "casefile.workflow.graph",
    "build_graph": "casefile.workflow.graph",
    "route_from_supervisor": "casefile.workflow.graph",
    "EventSink": "casefile.workflow.hooks",
    "HookPayload": "casefile.workflow.hooks",
    "NullSink": "casefile.workflow.hooks",
    "RecordingSink": "casefile.workflow.hooks",
    "WorkflowHookEvent": "casefile.workflow.hooks",
    "GraphState": "casefile.workflow.nodes",
    "NodeOutcome": "casefile.workflow.nodes",
    "NodeStatus": "casefile.workflow.nodes",
    "extractor_node": "casefile.workflow.nodes",
    "human_approval_node": "casefile.workflow.nodes",
    "investigator_node": "casefile.workflow.nodes",
    "reviewer_node": "casefile.workflow.nodes",
    "supervisor_node": "casefile.workflow.nodes",
    "WorkflowRunner": "casefile.workflow.runner",
    "ACTIVE_STATES": "casefile.workflow.states",
    "APPROVAL_STATES": "casefile.workflow.states",
    "FAILURE_STATES": "casefile.workflow.states",
    "TERMINAL_STATES": "casefile.workflow.states",
    "WAITING_STATES": "casefile.workflow.states",
    "StateKind": "casefile.workflow.states",
    "is_active": "casefile.workflow.states",
    "is_failure_state": "casefile.workflow.states",
    "is_terminal": "casefile.workflow.states",
    "is_waiting": "casefile.workflow.states",
    "requires_human_approval": "casefile.workflow.states",
    "state_kind": "casefile.workflow.states",
    "SqliteWorkflowStore": "casefile.workflow.store",
    "WorkflowStore": "casefile.workflow.store",
    "ROUTABLE_STATES": "casefile.workflow.supervisor",
    "NodeName": "casefile.workflow.supervisor",
    "RouteAction": "casefile.workflow.supervisor",
    "RoutingDecision": "casefile.workflow.supervisor",
    "SupervisorRouter": "casefile.workflow.supervisor",
    "ActorNotAllowedError": "casefile.workflow.transitions",
    "InvalidTransitionError": "casefile.workflow.transitions",
    "ReworkLimitError": "casefile.workflow.transitions",
    "StateDeclaration": "casefile.workflow.transitions",
    "TerminalStateError": "casefile.workflow.transitions",
    "TransitionEvent": "casefile.workflow.transitions",
    "TransitionRecord": "casefile.workflow.transitions",
    "TransitionRule": "casefile.workflow.transitions",
    "destinations_from": "casefile.workflow.transitions",
    "incoming_sources": "casefile.workflow.transitions",
    "incoming_triggers": "casefile.workflow.transitions",
    "is_allowed": "casefile.workflow.transitions",
    "rule_for": "casefile.workflow.transitions",
    "state_declaration": "casefile.workflow.transitions",
    "triggers_from": "casefile.workflow.transitions",
    "Trigger": "casefile.workflow.triggers",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Lazily resolve public names to keep package import side-effect free."""
    if name in _EXPORTS:
        from importlib import import_module

        module = import_module(_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
