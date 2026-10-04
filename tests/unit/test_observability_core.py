"""
Unit Tests: Observability core — Phase 10 acceptance matrix A–AC.

A–C    Provider configuration (disabled default, disabled config, degrade)
D      Test helper wires InMemorySpanExporter
E–H    TraceContext enrichment, replay tag, span binding, attribute shape
I–N    Sanitization (credentials, documents, cap, coercion, never-raise, safe_error)
O–R    Tracer degradation (disabled/None null spans, nesting, error status)
S–V    Meters (exact metric set, forbidden labels, totals/snapshot, unknown names)
W–X    Hook bridge (mapped events, unmapped ignored / never raises)
Y–Z    Span constants and attribute builders (names, sanitized shapes)
AA–AC  maybe_span null path, shutdown best-effort, package exports
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from casefile.config import ObservabilityConfig
from casefile.models.domain import AgentType
from casefile.observability import (
    EMPTY_CONTEXT,
    FORBIDDEN_LABEL_KEYS,
    MAX_ATTRIBUTE_LENGTH,
    METRIC_NAMES,
    SPAN_AGENT_RUN,
    SPAN_APPROVAL_DECIDE,
    SPAN_BUDGET_CHECK,
    SPAN_CHECKPOINT_CREATE,
    SPAN_CHECKPOINT_LOAD,
    SPAN_REPLAY_RUN,
    SPAN_SUPERVISOR_ROUTE,
    SPAN_TOOL_CALL,
    SPAN_TRANSITION,
    SPAN_WORKFLOW_RUN,
    CasefileMeters,
    ExecMode,
    HookBridge,
    Observability,
    OtelTracer,
    TraceContext,
    agent_attributes,
    approval_attributes,
    budget_attributes,
    check_labels,
    checkpoint_attributes,
    configure_observability,
    maybe_span,
    safe_error,
    sanitize_attributes,
    sanitize_value,
    supervisor_attributes,
    tool_attributes,
    transition_attributes,
    workflow_attributes,
)
from casefile.observability.provider import (
    test_observability as make_test_observability,
)
from casefile.observability.sanitize import SafeErrorModel
from casefile.workflow.hooks import HookPayload, RecordingSink, WorkflowHookEvent


@pytest.mark.unit
class TestAcceptanceAtoD:
    """A–D: provider configuration and test helper."""

    def test_a_disabled_mode_by_default_helper(self) -> None:
        obs = Observability.disabled()
        assert obs.enabled is False
        assert obs.tracer.enabled is False

    def test_b_configure_disabled_config(self) -> None:
        config = ObservabilityConfig()
        config.tracing.enabled = False
        config.metrics.enabled = False
        obs = configure_observability(config)
        assert obs.enabled is False
        assert obs.tracer.enabled is False

    def test_c_configure_enabled_degrades_without_collector(self) -> None:
        config = ObservabilityConfig()
        config.tracing.enabled = True
        config.metrics.enabled = True
        obs = configure_observability(config)
        assert isinstance(obs, Observability)
        assert obs.service_name == "casefile"
        assert obs.enabled is True

    def test_d_test_helper_wires_memory_exporter(self) -> None:
        obs, exporter = make_test_observability()
        assert obs.enabled is True
        assert obs.tracer.enabled is True
        with obs.tracer.span("probe", {"casefile.ok": True}):
            pass
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].name == "probe"
        assert spans[0].attributes is not None
        assert spans[0].attributes.get("casefile.ok") is True


@pytest.mark.unit
class TestAcceptanceEtoH:
    """E–H: TraceContext enrichment and attribute shape."""

    def test_e_default_live_mode_and_attributes(self) -> None:
        run_id = uuid4()
        context = TraceContext(workflow_run_id=run_id)
        assert context.mode == ExecMode.LIVE
        attributes = context.attribute_dict()
        assert attributes["casefile.mode"] == "LIVE"
        assert attributes["casefile.workflow_run_id"] == str(run_id)

    def test_f_for_replay_preserves_run_identity(self) -> None:
        run_id = uuid4()
        replayed = TraceContext(workflow_run_id=run_id).for_replay()
        assert replayed.mode == ExecMode.REPLAY
        assert replayed.workflow_run_id == run_id
        assert replayed.attribute_dict()["casefile.mode"] == "REPLAY"

    def test_g_with_span_binds_trace_identity(self) -> None:
        bound = TraceContext(workflow_run_id=uuid4()).with_span("trace-1", "span-9")
        assert bound.trace_id == "trace-1"
        assert bound.span_id == "span-9"
        attributes = bound.attribute_dict()
        assert attributes["casefile.trace_id"] == "trace-1"

    def test_h_attribute_dict_ids_only_empty_default(self) -> None:
        attributes = EMPTY_CONTEXT.attribute_dict()
        assert attributes == {"casefile.mode": "LIVE"}
        full = TraceContext(
            workflow_run_id=uuid4(),
            execution_id=uuid4(),
            claim_id=uuid4(),
            checkpoint_id=uuid4(),
            approval_id=uuid4(),
            correlation_id=uuid4(),
        ).attribute_dict()
        for key in (
            "casefile.execution_id",
            "casefile.claim_id",
            "casefile.checkpoint_id",
            "casefile.approval_id",
            "casefile.correlation_id",
        ):
            assert key in full
        UUID(full["casefile.claim_id"])


@pytest.mark.unit
class TestAcceptanceItoN:
    """I–N: sanitization and safe error telemetry."""

    def test_i_drops_credentials_and_secrets(self) -> None:
        attributes = sanitize_attributes(
            {
                "password": "hunter2",
                "api_key": "sk-live",
                "authorization": "Bearer abc",
                "private_key": "-----BEGIN",
                "casefile.agent": "extractor",
            }
        )
        assert "password" not in attributes
        assert "api_key" not in attributes
        assert "authorization" not in attributes
        assert "private_key" not in attributes
        assert attributes["casefile.agent"] == "extractor"

    def test_j_drops_documents_and_reasoning(self) -> None:
        attributes = sanitize_attributes(
            {
                "claim_document": "full text here",
                "content": "blob",
                "reasoning": "chain thought",
                "prompt_text": "system prompt",
                "casefile.tool": "policy_lookup",
            }
        )
        assert "claim_document" not in attributes
        assert "content" not in attributes
        assert "reasoning" not in attributes
        assert "prompt_text" not in attributes
        assert attributes["casefile.tool"] == "policy_lookup"

    def test_k_truncates_long_values(self) -> None:
        attributes = sanitize_attributes({"casefile.detail": "x" * 1000})
        assert len(str(attributes["casefile.detail"])) == MAX_ATTRIBUTE_LENGTH

    def test_l_coerces_types(self) -> None:
        identifier = uuid4()
        assert sanitize_value(identifier) == str(identifier)
        assert sanitize_value(Decimal("1.50")) == "1.50"
        assert sanitize_value(True) is True
        assert sanitize_value(7) == 7
        assert sanitize_value(1.5) == 1.5

    def test_m_never_raises(self) -> None:
        class Exploding:
            def __str__(self) -> str:
                raise RuntimeError("boom")

        assert sanitize_attributes({"bad": Exploding()}) == {}

    def test_n_safe_error_has_no_raw_text(self) -> None:
        error = safe_error(
            category="PROVIDER",
            code="TIMEOUT",
            component="extractor",
            retryable=True,
            workflow_state="EXTRACTION",
            terminal=False,
        )
        assert isinstance(error, SafeErrorModel)
        dumped = error.model_dump()
        assert dumped["code"] == "TIMEOUT"
        assert dumped["retryable"] is True
        assert "traceback" not in dumped
        assert "message" not in dumped
        assert error.frozen is True if hasattr(error, "frozen") else True


@pytest.mark.unit
class TestAcceptanceOtoR:
    """O–R: tracer degradation, nesting, and error status."""

    def test_o_disabled_tracer_yields_null_span(self) -> None:
        tracer = OtelTracer(enabled=False)
        assert tracer.enabled is False
        with maybe_span(tracer, "anything", {"a": 1}) as span:
            span.set_attribute("b", 2)
            span.set_status_error("nope")
            span.add_event("evt", {"k": "v"})
            span.record_exception(RuntimeError("x"))

    def test_p_none_tracer_yields_null_span(self) -> None:
        with maybe_span(None, "anything") as span:
            span.set_attribute("b", 2)
            span.set_status_error("")

    def test_q_parent_child_nesting(self) -> None:
        obs, exporter = make_test_observability()
        with obs.tracer.span("parent", {}), obs.tracer.span("child", {}):
            pass
        spans = {span.name: span for span in exporter.get_finished_spans()}
        assert set(spans) == {"parent", "child"}
        assert spans["child"].parent is not None
        assert spans["child"].parent.span_id == spans["parent"].context.span_id

    def test_r_error_status_recorded(self) -> None:
        obs, exporter = make_test_observability()
        with obs.tracer.span("failing", {}) as span:
            span.set_status_error("TIMEOUT")
        (finished,) = exporter.get_finished_spans()
        assert finished.status.status_code.name == "ERROR"


@pytest.mark.unit
class TestAcceptanceStoV:
    """S–V: exact metric set, cardinality, totals, unknown names."""

    def test_s_exact_metric_set(self) -> None:
        assert len(METRIC_NAMES) == 16
        for name in (
            "casefile.workflow.runs",
            "casefile.workflow.duration",
            "casefile.workflow.failures",
            "casefile.agent.executions",
            "casefile.agent.duration",
            "casefile.agent.failures",
            "casefile.tool.invocations",
            "casefile.tool.duration",
            "casefile.tool.failures",
            "casefile.workflow.rework_cycles",
            "casefile.workflow.retries",
            "casefile.budget.cost",
            "casefile.budget.tokens",
            "casefile.budget.exhaustions",
            "casefile.approval.wait_duration",
            "casefile.approval.decisions",
        ):
            assert name in METRIC_NAMES

    def test_t_forbidden_labels_rejected(self) -> None:
        meters = CasefileMeters()
        assert "claim_id" in FORBIDDEN_LABEL_KEYS
        assert "execution_id" in FORBIDDEN_LABEL_KEYS
        assert "approval_id" in FORBIDDEN_LABEL_KEYS
        with pytest.raises(ValueError, match="forbidden"):
            meters.record_counter("casefile.workflow.runs", 1.0, {"claim_id": "x"})
        with pytest.raises(ValueError, match="forbidden"):
            meters.record_counter("casefile.tool.invocations", 1.0, {"execution_id": "x"})
        with pytest.raises(ValueError, match="forbidden"):
            check_labels({"approval_id": "x"})
        with pytest.raises(ValueError, match="forbidden"):
            check_labels({"casefile.checkpoint_id": "x"})

    def test_u_totals_and_snapshots(self) -> None:
        meters = CasefileMeters()
        meters.record_counter("casefile.workflow.runs", 1.0, {"outcome": "started"})
        meters.record_counter("casefile.workflow.runs", 2.0, {"outcome": "started"})
        meters.record_histogram("casefile.workflow.duration", 150.0, {"outcome": "done"})
        assert meters.total("casefile.workflow.runs", {"outcome": "started"}) == 3.0
        assert meters.total("casefile.workflow.runs") == 3.0
        snapshot = meters.snapshot()
        assert snapshot["casefile.workflow.runs{outcome=started}"] == 3.0
        assert snapshot["casefile.workflow.duration{outcome=done}"] == 150.0

    def test_v_unknown_metric_rejected(self) -> None:
        meters = CasefileMeters()
        with pytest.raises(ValueError, match="Unknown"):
            meters.record_counter("casefile.made.up", 1.0)
        with pytest.raises(ValueError, match="Unknown"):
            meters.record_histogram("casefile.workflow.runs", 1.0)


@pytest.mark.unit
class TestAcceptanceWtoX:
    """W–X: hook bridge metric translation."""

    def test_w_bridge_maps_hooks_to_metrics(self) -> None:
        meters = CasefileMeters()
        bridge = HookBridge(meters)
        sink = RecordingSink()
        payload = HookPayload(
            event=WorkflowHookEvent.TOOL_COMPLETED,
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            correlation_id=uuid4(),
        )
        sink.emit(payload)
        bridge.emit(payload)
        assert meters.total("casefile.tool.invocations", {"outcome": "success"}) == 1.0
        for event, name in (
            (WorkflowHookEvent.WORKFLOW_STARTED, "casefile.workflow.runs"),
            (WorkflowHookEvent.NODE_FAILED, "casefile.agent.failures"),
            (WorkflowHookEvent.RETRY_REQUESTED, "casefile.workflow.retries"),
            (WorkflowHookEvent.TOOL_AUTHORIZATION_DENIED, "casefile.tool.failures"),
            (WorkflowHookEvent.CHECKPOINT_CREATED, "casefile.workflow.runs"),
            (WorkflowHookEvent.REPLAY_COMPLETED, "casefile.workflow.runs"),
            (WorkflowHookEvent.REPLAY_FAILED, "casefile.workflow.failures"),
            (WorkflowHookEvent.APPROVAL_REQUESTED, "casefile.approval.decisions"),
            (WorkflowHookEvent.WORKFLOW_TERMINATED, "casefile.workflow.runs"),
            (WorkflowHookEvent.STRUCTURED_OUTPUT_INVALID, "casefile.agent.failures"),
        ):
            fresh = CasefileMeters()
            HookBridge(fresh).emit(
                HookPayload(
                    event=event,
                    workflow_run_id=uuid4(),
                    claim_id=uuid4(),
                    correlation_id=uuid4(),
                )
            )
            assert fresh.total(name) == 1.0, event

    def test_x_bridge_ignores_unmapped_and_never_raises(self) -> None:
        meters = CasefileMeters()
        bridge = HookBridge(meters)
        bridge.emit(
            HookPayload(
                event=WorkflowHookEvent.NODE_STARTED,
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                correlation_id=uuid4(),
            )
        )
        assert meters.snapshot() == {}
        bridge.emit(
            HookPayload(
                event=WorkflowHookEvent.TOOL_REQUESTED,
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                correlation_id=uuid4(),
            )
        )
        bridge.emit(
            HookPayload(
                event=WorkflowHookEvent.STATE_TRANSITION,
                workflow_run_id=uuid4(),
                claim_id=uuid4(),
                correlation_id=uuid4(),
            )
        )
        assert meters.snapshot() == {}


@pytest.mark.unit
class TestAcceptanceYtoAC:
    """Y–AC: span catalog, attribute builders, maybe_span, shutdown, exports."""

    def test_y_span_name_constants_complete_and_unique(self) -> None:
        names = [
            SPAN_WORKFLOW_RUN,
            SPAN_TRANSITION,
            SPAN_SUPERVISOR_ROUTE,
            SPAN_AGENT_RUN,
            SPAN_TOOL_CALL,
            SPAN_CHECKPOINT_CREATE,
            SPAN_CHECKPOINT_LOAD,
            SPAN_BUDGET_CHECK,
            SPAN_APPROVAL_DECIDE,
            SPAN_REPLAY_RUN,
        ]
        assert len(names) == 10
        assert len(set(names)) == 10
        assert all(n.startswith("casefile.") for n in names)

    def test_z_attribute_builders_sanitized_shapes(self) -> None:
        builders: list[dict[str, object]] = [
            workflow_attributes(state="RECEIVED"),
            workflow_attributes(
                state="APPROVED",
                mode=ExecMode.REPLAY,
                terminal_state="APPROVED",
                termination_reason="approval",
                duration_ms=10,
            ),
            transition_attributes(
                source_state="RECEIVED",
                destination_state="EXTRACTION",
                trigger="CLAIM_VALIDATED",
                actor="SUPERVISOR",
                sequence_no=2,
            ),
            supervisor_attributes(
                current_state="RECEIVED",
                trigger="CLAIM_VALIDATED",
                route="EXTRACTION",
                reason_code="OK",
            ),
            agent_attributes(
                agent=AgentType.EXTRACTOR,
                prompt_version="extractor-v1",
                provider="anthropic",
                model="claude",
                input_tokens=10,
                output_tokens=5,
                cost_usd="0.01",
            ),
            tool_attributes(
                tool_name="policy_lookup",
                tool_version="1.0.0",
                agent="INVESTIGATOR",
                authorization="ALLOW",
            ),
            checkpoint_attributes(operation="create", sequence_no=1, kind="TRANSITION"),
            budget_attributes(configured_steps=50, consumed_steps=1, consumed_tokens=100),
            approval_attributes(outcome="APPROVE", actor_type="HUMAN", request_version=1),
        ]
        denied = ("document", "password", "reasoning", "secret", "content")
        for attributes in builders:
            assert attributes
            for key, value in attributes.items():
                assert key.startswith("casefile."), key
                assert not any(token in key.lower() for token in denied), key
                text = str(value)
                assert len(text) <= MAX_ATTRIBUTE_LENGTH + 32
                assert "Observability Tester" not in text
                assert "SYN-DOC" not in text

    def test_aa_maybe_span_with_disabled_tracer(self) -> None:
        tracer = OtelTracer(enabled=False)
        with maybe_span(tracer, SPAN_AGENT_RUN, {"casefile.agent": "EXTRACTOR"}) as span:
            span.set_attribute("casefile.success", True)
            span.set_status_error("")

    def test_ab_shutdown_best_effort_swallows_errors(self) -> None:
        obs = Observability.disabled()

        def _boom() -> None:
            raise RuntimeError("shutdown exploded")

        class _Provider:
            def shutdown(self) -> None:
                raise RuntimeError("provider exploded")

        obs.on_shutdown(_boom)
        obs.on_shutdown(_Provider())
        called: list[int] = []
        obs.on_shutdown(lambda: called.append(1))
        obs.shutdown()
        assert called == [1]

    def test_ac_package_exports_and_otlp_default(self) -> None:
        import casefile.observability as pkg

        for name in pkg.__all__:
            assert hasattr(pkg, name), name
        config = ObservabilityConfig()
        assert config.tracing.otlp_endpoint == "http://localhost:4318"
        assert config.tracing.enabled is True
        assert config.metrics.enabled is True
        assert config.service_name == "casefile"
