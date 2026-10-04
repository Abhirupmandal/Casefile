"""
Agent base class: bounded execution with structured-output enforcement.

Pipeline per invocation: validate input → build prompt → provider call →
parse JSON → Pydantic validation → domain checks → typed contract. Any
stage may produce a typed AgentError instead of output; malformed output is
never coerced into success. Retries are bounded, reasoned, and keep the
same execution identity. Lifecycle hooks flow to the Phase 3 event sink.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Generic, NoReturn, TypeVar

from pydantic import BaseModel, ValidationError

from casefile.agents.context import AgentContext
from casefile.agents.prompts import PromptTemplate, RenderedPrompt
from casefile.agents.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    ProviderError,
    ProviderTimeoutError,
)
from casefile.agents.results import AgentError, AgentErrorCategory, AgentExecutionRecord
from casefile.models.domain import AgentType
from casefile.tools.contracts import ToolContext, ToolOutcome
from casefile.tools.registry import ToolRegistry
from casefile.workflow.hooks import EventSink, HookPayload, NullSink, WorkflowHookEvent

if TYPE_CHECKING:
    from casefile.observability.provider import Observability

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentFailedError(Exception):
    """Terminal agent failure carrying the typed error (and record)."""

    def __init__(self, error: AgentError, record: AgentExecutionRecord | None = None) -> None:
        super().__init__(f"{error.category.value}: {error.message}")
        self.error = error
        self.record = record


class BaseAgent(ABC, Generic[InputT, OutputT]):
    """Shared execution machinery; subclasses define I/O and prompts."""

    agent_type: AgentType
    output_contract_type: str = ""
    allowed_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        provider: LLMProvider,
        prompt: PromptTemplate,
        *,
        model: str,
        temperature: float = 0.0,
        sink: EventSink | None = None,
        retry_guard: Callable[[int], bool] | None = None,
    ) -> None:
        self._provider = provider
        self._prompt = prompt
        self._model = model
        self._temperature = temperature
        self._sink: EventSink = sink or NullSink()
        self._retry_guard = retry_guard

    @property
    def provider_name(self) -> str:
        """Underlying provider identifier (for records, never SDK objects)."""
        return self._provider.name

    @abstractmethod
    def validate_input(self, payload: InputT, ctx: AgentContext) -> None:
        """Raise AgentFailedError(INVALID_INPUT) for unacceptable input."""
        ...

    @abstractmethod
    def build_render(
        self, payload: InputT, ctx: AgentContext
    ) -> tuple[RenderedPrompt, dict[str, object]]:
        """Render the prompt; second value carries parse-time metadata."""
        ...

    @abstractmethod
    def parse_output(
        self, raw: str, payload: InputT, ctx: AgentContext, render_meta: dict[str, object]
    ) -> OutputT:
        """Parse provider text into the typed contract or raise AgentFailedError."""
        ...

    def run(
        self, payload: InputT, ctx: AgentContext, obs: Observability | None = None
    ) -> tuple[OutputT, AgentExecutionRecord]:
        """Execute with bounded retries; returns (contract, record)."""
        from casefile.observability.context import ExecMode, TraceContext
        from casefile.observability.spans import SPAN_AGENT_RUN, agent_attributes
        from casefile.observability.tracer import maybe_span

        trace = TraceContext(
            workflow_run_id=ctx.workflow_run_id,
            execution_id=ctx.execution_id,
            claim_id=ctx.claim_id,
            correlation_id=ctx.correlation_id,
            mode=ExecMode.LIVE,
        )
        attributes = agent_attributes(
            agent=self.agent_type,
            prompt_version=self._prompt.version,
            provider=self._provider.name,
            model=self._model,
        )
        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_AGENT_RUN, attributes, trace) as span:
            try:
                output, record = self._run_inner(payload, ctx)
            except Exception as exc:
                span.set_status_error(type(exc).__name__)
                span.set_attribute("casefile.success", False)
                self._record_agent_metrics(obs, ctx, False, 0, 0.0, 0)
                raise
            span.set_attribute("casefile.success", record.error is None)
            span.set_attribute("casefile.input_tokens", record.input_tokens)
            span.set_attribute("casefile.output_tokens", record.output_tokens)
            span.set_attribute("casefile.retry_number", max(0, record.attempts - 1))
            self._record_agent_metrics(
                obs,
                ctx,
                record.error is None,
                record.input_tokens + record.output_tokens,
                record.latency_ms,
                record.attempts,
            )
            return output, record

    def _run_inner(
        self, payload: InputT, ctx: AgentContext
    ) -> tuple[OutputT, AgentExecutionRecord]:
        """Uninstrumented execution body (logic unchanged)."""
        started = datetime.now(UTC)
        self._emit(WorkflowHookEvent.NODE_STARTED, ctx, "")
        try:
            self.validate_input(payload, ctx)
        except AgentFailedError as exc:
            return self._fail(ctx, started, exc.error, attempts=1, reasons=())
        output, record = self._attempt_loop(payload, ctx, started)
        return output, record

    def _attempt_loop(
        self, payload: InputT, ctx: AgentContext, started: datetime
    ) -> tuple[OutputT, AgentExecutionRecord]:
        reasons: list[str] = []
        last_error: AgentError | None = None
        for attempt in range(1, ctx.max_attempts + 1):
            try:
                rendered, meta = self.build_render(payload, ctx)
                response = self._provider.complete(
                    LLMRequest(
                        correlation_id=ctx.correlation_id,
                        model=self._model,
                        messages=(
                            LLMMessage(role="system", content=rendered.system),
                            LLMMessage(role="user", content=rendered.user),
                        ),
                        temperature=self._temperature,
                        response_schema_name=self._prompt.output_contract,
                    )
                )
                output = self.parse_output(response.content, payload, ctx, meta)
                record = AgentExecutionRecord(
                    execution_id=ctx.execution_id,
                    workflow_run_id=ctx.workflow_run_id,
                    agent=self.agent_type,
                    started_at=started,
                    input_correlation_id=ctx.correlation_id,
                    input_contract_version=(
                        payload.schema_version if hasattr(payload, "schema_version") else "1.0.0"
                    ),
                    output_contract_type=self.output_contract_type,
                    contract_version=(
                        output.schema_version if hasattr(output, "schema_version") else "1.0.0"
                    ),
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    latency_ms=response.latency_ms,
                    provider_name=response.provider or self._provider.name,
                    model_name=response.model or self._model,
                    attempts=attempt,
                    retry_reasons=tuple(reasons),
                    output_valid=True,
                    error=None,
                )
                self._emit(WorkflowHookEvent.NODE_COMPLETED, ctx, self.output_contract_type)
                return output, record
            except AgentFailedError as exc:
                last_error = exc.error
                if not exc.error.retryable or attempt >= ctx.max_attempts:
                    return self._fail(ctx, started, exc.error, attempts=attempt, reasons=reasons)
                guarded = self._check_retry_guard(attempt + 1)
                if guarded is not None:
                    return self._fail(ctx, started, guarded, attempts=attempt, reasons=reasons)
                reasons.append(f"attempt-{attempt}: {exc.error.code}")
                self._emit_retry(ctx, exc.error)
            except (ProviderTimeoutError, ProviderError) as exc:
                mapped = AgentError(
                    category=(
                        AgentErrorCategory.TIMEOUT
                        if isinstance(exc, ProviderTimeoutError)
                        else AgentErrorCategory.PROVIDER
                    ),
                    code=type(exc).__name__,
                    message=str(exc),
                    retryable=exc.retryable,
                    attempt=attempt,
                )
                last_error = mapped
                if not mapped.retryable or attempt >= ctx.max_attempts:
                    return self._fail(ctx, started, mapped, attempts=attempt, reasons=reasons)
                guarded = self._check_retry_guard(attempt + 1)
                if guarded is not None:
                    return self._fail(ctx, started, guarded, attempts=attempt, reasons=reasons)
                reasons.append(f"attempt-{attempt}: {mapped.code}")
                self._emit_retry(ctx, mapped)
        assert last_error is not None
        return self._fail(ctx, started, last_error, attempts=ctx.max_attempts, reasons=reasons)

    def _check_retry_guard(self, next_attempt: int) -> AgentError | None:
        """Consult the optional budget retry guard; returns an error when spent."""
        if self._retry_guard is None:
            return None
        if self._retry_guard(next_attempt):
            return None
        return AgentError(
            category=AgentErrorCategory.POLICY_VIOLATION,
            code="MAX_AGENT_RETRIES_EXCEEDED",
            message="Retry budget exhausted; refusing further attempts",
            retryable=False,
            attempt=next_attempt,
        )

    def _record_agent_metrics(
        self,
        obs: Observability | None,
        ctx: AgentContext,
        success: bool,
        total_tokens: int,
        latency_ms: float,
        attempts: int,
    ) -> None:
        """Record agent metrics; safe when observability is absent."""
        if obs is None:
            return
        labels = {
            "agent": self.agent_type.value,
            "provider": self._provider.name,
            "model": self._model,
            "mode": "LIVE",
        }
        meters = obs.meters
        meters.record_counter("casefile.agent.executions", 1.0, dict(labels))
        meters.record_histogram("casefile.agent.duration", float(latency_ms), dict(labels))
        if not success:
            meters.record_counter("casefile.agent.failures", 1.0, dict(labels, outcome="failure"))
        if total_tokens:
            meters.record_histogram("casefile.budget.tokens", float(total_tokens), dict(labels))
        if attempts > 1:
            meters.record_counter("casefile.workflow.retries", float(attempts - 1), dict(labels))

    def _fail(
        self,
        ctx: AgentContext,
        started: datetime,
        error: AgentError,
        *,
        attempts: int,
        reasons: tuple[str, ...] | list[str],
    ) -> NoReturn:
        record = AgentExecutionRecord(
            execution_id=ctx.execution_id,
            workflow_run_id=ctx.workflow_run_id,
            agent=self.agent_type,
            started_at=started,
            status="FAILURE",
            input_correlation_id=ctx.correlation_id,
            input_contract_version="1.0.0",
            output_contract_type=self.output_contract_type,
            attempts=attempts,
            retry_reasons=tuple(reasons),
            output_valid=False,
            error=error,
        )
        self._emit(agent_hook_event_for(error), ctx, error.code)
        raise AgentFailedError(error, record)

    def _emit(self, event: WorkflowHookEvent, ctx: AgentContext, detail: str) -> None:
        self._sink.emit(
            HookPayload(
                event=event,
                workflow_run_id=ctx.workflow_run_id,
                claim_id=ctx.claim_id,
                correlation_id=ctx.correlation_id,
                node=self.agent_type.value.lower(),
                detail=detail,
            )
        )

    def _emit_retry(self, ctx: AgentContext, error: AgentError) -> None:
        self._sink.emit(
            HookPayload(
                event=WorkflowHookEvent.RETRY_REQUESTED,
                workflow_run_id=ctx.workflow_run_id,
                claim_id=ctx.claim_id,
                correlation_id=ctx.correlation_id,
                node=self.agent_type.value.lower(),
                detail=f"{error.code}: {error.message}",
            )
        )

    @staticmethod
    def parse_json_object(raw: str, *, contract: str) -> dict[str, object]:
        """Parse provider text as a JSON object or raise MALFORMED_OUTPUT."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.MALFORMED_OUTPUT,
                    code="NOT_JSON",
                    message=f"{contract} must be JSON: {exc}",
                    retryable=True,
                )
            ) from exc
        if not isinstance(data, dict):
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.MALFORMED_OUTPUT,
                    code="NOT_OBJECT",
                    message=f"{contract} must be a JSON object",
                    retryable=True,
                )
            )
        return data

    @staticmethod
    def validate_contract(
        model: type[OutputT], data: dict[str, object], *, contract: str
    ) -> OutputT:
        """Pydantic-validate parsed JSON or raise CONTRACT_VALIDATION."""
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="SCHEMA_MISMATCH",
                    message=f"{contract} failed validation: {exc.errors()[0]['msg']}",
                    retryable=False,
                )
            ) from exc


def build_tool_context(agent_ctx: AgentContext, agent: AgentType) -> ToolContext:
    """Derive a tool invocation context from an agent context.

    Identities flow through; sessions, connections, and filesystems never do.
    """
    return ToolContext(
        claim_id=agent_ctx.claim_id,
        workflow_run_id=agent_ctx.workflow_run_id,
        execution_id=agent_ctx.execution_id,
        correlation_id=agent_ctx.correlation_id,
        agent=agent,
        timeout_seconds=float(agent_ctx.timeout_seconds),
    )


def lookup_tool(
    registry: ToolRegistry,
    allowed: frozenset[str],
    tool_name: str,
    raw_input: dict[str, object],
    agent_ctx: AgentContext,
    agent: AgentType,
) -> ToolOutcome:
    """Agent-side tool call with defense-in-depth authorization.

    The agent allowlist is checked first; the registry re-checks before
    execution. Unauthorized calls fail before any tool code runs.
    """
    if tool_name not in allowed:
        raise AgentFailedError(
            AgentError(
                category=AgentErrorCategory.POLICY_VIOLATION,
                code="TOOL_NOT_ALLOWED",
                message=f"Agent {agent.value} may not call {tool_name!r}",
                retryable=False,
            )
        )
    version = registry.version_of(tool_name) or "1.0.0"
    return registry.execute(tool_name, version, raw_input, build_tool_context(agent_ctx, agent))


def agent_hook_event_for(error: AgentError) -> WorkflowHookEvent:
    """Map an agent error to its lifecycle hook (validation failures included)."""
    if error.category == AgentErrorCategory.MALFORMED_OUTPUT:
        return WorkflowHookEvent.STRUCTURED_OUTPUT_INVALID
    return WorkflowHookEvent.NODE_FAILED
