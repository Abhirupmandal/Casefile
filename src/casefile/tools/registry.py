"""
Tool registry: explicit registration, versioned resolution, authorization
enforcement, and bounded execution (AGENTS.md authorization matrix,
docs/tool-architecture.md).

Flow: Agent → ToolRegistry.execute → authorization (deny BEFORE execution)
→ typed input validation → bounded tool run → typed outcome + hooks.
Agents never import tool implementations; tools never see sessions,
connections, or filesystems.
"""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from pydantic import BaseModel

from casefile.models.domain import AgentType
from casefile.tools.authorization import AUTHORIZED_TOOLS
from casefile.tools.base import InputT, OutputT
from casefile.tools.base import Tool as Tool
from casefile.tools.contracts import (
    ToolCall,
    ToolContext,
    ToolError,
    ToolErrorCode,
    ToolFailureError,
    ToolOutcome,
    ToolResultMetadata,
    ToolStatus,
)
from casefile.workflow.hooks import EventSink, HookPayload, NullSink, WorkflowHookEvent

if TYPE_CHECKING:
    from casefile.observability.provider import Observability


def idempotency_key_for(
    workflow_run_id: UUID, execution_id: UUID, tool_name: str, input_json: str
) -> str:
    """Stable key: same deterministic request ⇒ same key (sha256, never hash())."""
    digest = hashlib.sha256(input_json.encode("utf-8")).hexdigest()[:16]
    return f"{workflow_run_id}:{execution_id}:{tool_name}:{digest}"


class ToolRegistry:
    """Explicit registry with authorization-first execution."""

    def __init__(self, sink: EventSink | None = None) -> None:
        # Any is justified: the registry holds heterogeneous Tool[I, O]
        # implementations behind one stable-name map; every access goes
        # through typed input_model/output_model validation.
        self._tools: dict[str, Tool[Any, Any]] = {}
        self._sink: EventSink = sink or NullSink()
        self._calls: list[ToolCall] = []

    @property
    def recorded_calls(self) -> list[ToolCall]:
        """Return a copy of all recorded tool invocations."""
        return list(self._calls)

    def clear_recorded_calls(self) -> None:
        """Clear recorded tool invocations."""
        self._calls.clear()

    def register(self, tool: Tool[InputT, OutputT]) -> None:
        """Register a tool under its stable name (duplicates rejected)."""
        if not tool.name:
            raise ValueError("Tools must declare a non-empty name")
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def list_tools(self) -> list[str]:
        """Registered tool names, sorted."""
        return sorted(self._tools)

    def metadata(self, tool_name: str) -> dict[str, str]:
        """Version/description metadata for a registered tool."""
        tool = self._resolve(tool_name)
        return {"name": tool.name, "version": tool.version, "description": tool.description}

    def version_of(self, tool_name: str) -> str | None:
        """Registered version for a tool, or None when unknown."""
        tool = self._tools.get(tool_name)
        return tool.version if tool is not None else None

    def tools_for(self, agent: AgentType) -> list[str]:
        """Tool names the matrix authorizes for an agent (unknown ⇒ none)."""
        return sorted(AUTHORIZED_TOOLS.get(agent, frozenset()) & set(self._tools))

    def is_authorized(self, agent: AgentType, tool_name: str) -> bool:
        """Matrix check used before any execution."""
        return tool_name in AUTHORIZED_TOOLS.get(agent, frozenset())

    def execute(
        self,
        tool_name: str,
        tool_version: str,
        raw_input: dict[str, object],
        ctx: ToolContext,
        obs: Observability | None = None,
    ) -> ToolOutcome:
        """Authorize → validate → run bounded → outcome. Never raises for
        tool-level failures; they return as typed ToolOutcome errors."""
        from casefile.observability.context import ExecMode, TraceContext
        from casefile.observability.spans import SPAN_TOOL_CALL
        from casefile.observability.tracer import maybe_span

        invocation_id = uuid4()
        started = datetime.now(UTC)
        self._emit(WorkflowHookEvent.TOOL_REQUESTED, ctx, tool_name, "")
        trace = TraceContext(
            workflow_run_id=ctx.workflow_run_id,
            execution_id=ctx.execution_id,
            claim_id=ctx.claim_id,
            correlation_id=ctx.correlation_id,
            mode=ExecMode.LIVE,
        )
        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_TOOL_CALL, {"casefile.tool": tool_name}, trace) as span:
            outcome = self._execute_inner(
                tool_name, tool_version, raw_input, ctx, invocation_id, started
            )
            span.set_attribute("casefile.success", outcome.succeeded)
            span.set_attribute("casefile.tool_version", outcome.tool_version)
            span.set_attribute("casefile.duration_ms", outcome.duration_ms)
            if outcome.error is not None:
                span.set_attribute("casefile.error_category", outcome.error.code.value)
            self._record_tool_metrics(obs, ctx, tool_name, outcome)
            return outcome

    def _execute_inner(
        self,
        tool_name: str,
        tool_version: str,
        raw_input: dict[str, object],
        ctx: ToolContext,
        invocation_id: UUID,
        started: datetime,
    ) -> ToolOutcome:
        """Uninstrumented execution body (logic unchanged)."""
        try:
            tool = self._resolve(tool_name)
            self._check_version(tool, tool_version)
            self._check_authorization(tool_name, ctx)
            parsed = self._parse_input(tool, raw_input, tool_name)
            key = idempotency_key_for(
                ctx.workflow_run_id,
                ctx.execution_id,
                tool_name,
                json.dumps(raw_input, sort_keys=True, default=str),
            )
            call = ToolCall(
                invocation_id=invocation_id,
                tool_name=tool_name,
                tool_version=tool.version,
                claim_id=ctx.claim_id,
                workflow_run_id=ctx.workflow_run_id,
                execution_id=ctx.execution_id,
                correlation_id=ctx.correlation_id,
                requesting_agent=ctx.agent,
                input_json=json.dumps(raw_input, sort_keys=True, default=str),
                status=ToolStatus.RUNNING,
                started_at=started,
                idempotency_key=key,
            )
            self._emit(WorkflowHookEvent.TOOL_STARTED, ctx, tool_name, str(invocation_id))
            output, duration_ms = self._run_bounded(tool, parsed, ctx)
            finished = datetime.now(UTC)
            call.status = ToolStatus.SUCCESS
            call.finished_at = finished
            call.duration_ms = duration_ms
            call.output_json = output.model_dump_json()
            self._bump_usage(ctx, duration_ms, tool.cost_units)
            self._emit(WorkflowHookEvent.TOOL_COMPLETED, ctx, tool_name, str(invocation_id))
            fixture_val = (
                getattr(parsed, "claim_ref", None)
                or getattr(parsed, "policy_number", None)
                or getattr(parsed, "document_ref", None)
                or getattr(parsed, "customer_id", None)
                or getattr(parsed, "estimate_ref", None)
            )
            provenance = ToolResultMetadata(
                source="fixture" if getattr(tool, "_store", None) else "repository",
                source_version="1.0.0",
                retrieved_at=finished,
                fixture_id=str(fixture_val) if fixture_val is not None else None,
                tool_name=tool_name,
                tool_version=tool.version,
                correlation_id=ctx.correlation_id,
            )
            outcome = ToolOutcome(
                invocation_id=invocation_id,
                tool_name=tool_name,
                tool_version=tool.version,
                status=ToolStatus.SUCCESS,
                output=output,
                duration_ms=duration_ms,
                cost_units=tool.cost_units,
                idempotency_key=key,
                call=call,
                provenance=provenance,
            )
            self._calls.append(call)
            return outcome
        except ToolFailureError as failure:
            return self._failed_outcome(
                failure.error, tool_name, tool_version, ctx, invocation_id, started
            )

    def _record_tool_metrics(
        self,
        obs: Observability | None,
        ctx: ToolContext,
        tool_name: str,
        outcome: ToolOutcome,
    ) -> None:
        """Record tool metrics; safe when observability is absent."""
        if obs is None:
            return
        labels = {"tool": tool_name, "agent": ctx.agent.value, "mode": "LIVE"}
        meters = obs.meters
        meters.record_counter("casefile.tool.invocations", 1.0, dict(labels))
        meters.record_histogram("casefile.tool.duration", float(outcome.duration_ms), dict(labels))
        if not outcome.succeeded:
            meters.record_counter("casefile.tool.failures", 1.0, dict(labels, outcome="failure"))

    def _resolve(self, tool_name: str) -> Tool[Any, Any]:
        try:
            return self._tools[tool_name]
        except KeyError:
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.UNKNOWN_TOOL,
                    message=f"Tool {tool_name!r} is not registered",
                )
            ) from None

    @staticmethod
    def _check_version(tool: Tool[Any, Any], tool_version: str) -> None:
        if tool_version != tool.version:
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.VERSION_MISMATCH,
                    message=f"Tool {tool.name!r} version {tool_version!r} "
                    f"does not match registered {tool.version!r}",
                )
            )

    def _check_authorization(self, tool_name: str, ctx: ToolContext) -> None:
        if not self.is_authorized(ctx.agent, tool_name):
            self._emit(WorkflowHookEvent.TOOL_AUTHORIZATION_DENIED, ctx, tool_name, ctx.agent.value)
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.UNAUTHORIZED_TOOL,
                    message=f"Agent {ctx.agent.value} is not authorized for {tool_name!r}",
                )
            )

    @staticmethod
    def _parse_input(
        tool: Tool[Any, Any], raw_input: dict[str, object], tool_name: str
    ) -> BaseModel:
        try:
            parsed: BaseModel = tool.input_model.model_validate(raw_input)
            return parsed
        except Exception as exc:
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.INVALID_INPUT,
                    message=f"Invalid input for {tool_name!r}: {exc}",
                )
            ) from exc

    def _run_bounded(
        self, tool: Tool[Any, Any], parsed: BaseModel, ctx: ToolContext
    ) -> tuple[BaseModel, int]:
        timeout = min(ctx.timeout_seconds, tool.timeout_seconds)
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="casefile-tool") as pool:
            future: Future[BaseModel] = pool.submit(tool.run, parsed, ctx)
            try:
                output = future.result(timeout=timeout)
            except TimeoutError as exc:
                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.TIMEOUT,
                        message=f"Tool {tool.name!r} exceeded {timeout}s",
                        retryable=True,
                    )
                ) from exc
            except ToolFailureError:
                raise
            except Exception as exc:
                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.INTERNAL_FAILURE,
                        message=f"Tool {tool.name!r} failed: {exc}",
                    )
                ) from exc
        duration_ms = int((time.perf_counter() - started) * 1000)
        if not isinstance(output, tool.output_model):
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.VALIDATION_FAILURE,
                    message=f"Tool {tool.name!r} returned the wrong output type",
                )
            )
        return output, duration_ms

    def _failed_outcome(
        self,
        error: ToolError,
        tool_name: str,
        tool_version: str,
        ctx: ToolContext,
        invocation_id: UUID,
        started: datetime,
    ) -> ToolOutcome:
        finished = datetime.now(UTC)
        duration_ms = int((finished - started).total_seconds() * 1000)
        status = ToolStatus.TIMEOUT if error.code == ToolErrorCode.TIMEOUT else ToolStatus.FAILURE
        if error.code == ToolErrorCode.UNAUTHORIZED_TOOL:
            status = ToolStatus.DENIED
        call = ToolCall(
            invocation_id=invocation_id,
            tool_name=tool_name,
            tool_version=tool_version,
            claim_id=ctx.claim_id,
            workflow_run_id=ctx.workflow_run_id,
            execution_id=ctx.execution_id,
            correlation_id=ctx.correlation_id,
            requesting_agent=ctx.agent,
            status=status,
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            error=error,
        )
        event = (
            WorkflowHookEvent.TOOL_FAILED
            if status == ToolStatus.FAILURE or status == ToolStatus.TIMEOUT
            else WorkflowHookEvent.TOOL_AUTHORIZATION_DENIED
        )
        self._emit(event, ctx, tool_name, error.code.value)
        self._calls.append(call)
        return ToolOutcome(
            invocation_id=invocation_id,
            tool_name=tool_name,
            tool_version=tool_version,
            status=status,
            output=None,
            error=error,
            duration_ms=duration_ms,
            idempotency_key=call.idempotency_key,
            call=call,
        )

    @staticmethod
    def _bump_usage(ctx: ToolContext, duration_ms: int, cost_units: int) -> None:
        ctx.usage.calls_made += 1
        ctx.usage.total_latency_ms += duration_ms
        ctx.usage.cost_units += cost_units

    def _emit(
        self, event: WorkflowHookEvent, ctx: ToolContext, tool_name: str, detail: str
    ) -> None:
        self._sink.emit(
            HookPayload(
                event=event,
                workflow_run_id=ctx.workflow_run_id,
                claim_id=ctx.claim_id,
                correlation_id=ctx.correlation_id,
                node=f"tool:{tool_name}",
                detail=detail,
            )
        )
