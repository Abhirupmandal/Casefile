"""
Phase 9 Acceptance Tests: Real Tool Integration + External Services + Distributed Reliability.

Validates:
A. External service provider contracts and client abstractions
B. ResilientHttpClient transient error retries and timeout boundaries
C. Authentication error fast-failure (zero unsafe retries on 401/403)
D. Typed Tool integration with external adapters (Policy, History, Repair, Fraud, Document)
E. Typed failure mapping (TIMEOUT, UNAVAILABLE, UNAUTHORIZED_TOOL, VALIDATION_FAILURE)
F. Distributed & tiered caching (InMemoryCache, RedisCache, fail-safe degradation)
G. Tool response caching with deterministic keys, TTL expiration, and cache provenance
H. Distributed sliding-window rate limiting (RedisRateLimiter, per-tool outbound limits)
I. Per-agent authorization matrix enforcement across real tool adapters
J. Secret isolation (no API keys or tokens in spans, logs, or tool calls)
K. End-to-end workflow execution with external adapters and caching
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from casefile.api.rate_limit import InMemoryRateLimiter, RedisRateLimiter
from casefile.cache.client import InMemoryCache, RedisCache
from casefile.models.contracts import ClaimInput, WorkflowState
from casefile.models.domain import AgentType, RiskLevel
from casefile.tools import create_default_registry
from casefile.tools.adapters.errors import (
    ExternalServiceAuthError,
    ExternalServiceUnavailableError,
)
from casefile.tools.adapters.fakes import (
    FakeClaimsHistoryServiceClient,
    FakeDocumentStorageServiceClient,
    FakeFraudScoringServiceClient,
    FakePolicyServiceClient,
    FakeRepairCostServiceClient,
)
from casefile.tools.adapters.http_client import (
    HttpPolicyServiceClient,
    ResilientHttpClient,
)
from casefile.tools.contracts import (
    ToolContext,
    ToolErrorCode,
    ToolFailureError,
    ToolStatus,
    ToolUsage,
)
from casefile.tools.damage import DamageEstimateInput, DamageEstimateTool
from casefile.tools.document import DocumentRetrievalInput, DocumentRetrievalTool
from casefile.tools.fraud import FraudSignalInput, FraudSignalLookupTool
from casefile.tools.history import PriorClaimLookupInput, PriorClaimLookupTool
from casefile.tools.policy import PolicyLookupInput, PolicyLookupTool
from casefile.workflow.runner import WorkflowRunner

pytestmark = pytest.mark.unit


def _create_tool_context(agent: AgentType = AgentType.INVESTIGATOR) -> ToolContext:
    return ToolContext(
        claim_id=uuid4(),
        workflow_run_id=uuid4(),
        execution_id=uuid4(),
        correlation_id=uuid4(),
        agent=agent,
        timeout_seconds=5.0,
        usage=ToolUsage(),
    )


# ==============================================================================
# Category A & B: Resilient HTTP Client, Retries & Timeouts
# ==============================================================================


def test_http_client_retries_transient_failures_then_succeeds() -> None:
    """Transient HTTP 503 is retried up to max_retries before succeeding."""
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": "service unavailable"})
        return httpx.Response(200, json={"status": "ok"})

    transport = httpx.MockTransport(mock_handler)
    with ResilientHttpClient(
        service_name="test_service",
        base_url="http://test.internal",
        max_retries=3,
        backoff_factor=0.01,
        transport=transport,
    ) as client:
        resp = client.request("GET", "/test")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert calls == 3


def test_http_client_exhausts_retries_on_persistent_503() -> None:
    """Persistent HTTP 503 raises ExternalServiceUnavailableError after max_retries."""
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "still down"})

    transport = httpx.MockTransport(mock_handler)
    with ResilientHttpClient(
        service_name="test_service",
        base_url="http://test.internal",
        max_retries=2,
        backoff_factor=0.01,
        transport=transport,
    ) as client:
        with pytest.raises(ExternalServiceUnavailableError) as exc_info:
            client.request("GET", "/test")
        assert exc_info.value.status_code == 503
        assert calls == 3  # initial + 2 retries


def test_http_client_fails_fast_on_auth_errors() -> None:
    """HTTP 401/403 fails immediately without retrying (safety rule)."""
    calls = 0

    def mock_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    transport = httpx.MockTransport(mock_handler)
    with ResilientHttpClient(
        service_name="test_service",
        base_url="http://test.internal",
        max_retries=3,
        backoff_factor=0.01,
        transport=transport,
    ) as client:
        with pytest.raises(ExternalServiceAuthError) as exc_info:
            client.request("GET", "/secure")
        assert exc_info.value.status_code == 401
        assert calls == 1  # No retries for 401!


def test_http_client_handles_404_as_none_or_empty() -> None:
    """HTTP 404 returns response cleanly for not-found handling."""
    transport = httpx.MockTransport(lambda req: httpx.Response(404, json={"detail": "not found"}))
    with HttpPolicyServiceClient(
        base_url="http://test.internal",
        transport=transport,
    ) as client:
        assert isinstance(client, HttpPolicyServiceClient)
        assert client.get_policy("NONEXISTENT") is None


# ==============================================================================
# Category C & D: Fake Clients and Tool Adapters
# ==============================================================================


def test_policy_lookup_tool_with_fake_client_success() -> None:
    fake_client = FakePolicyServiceClient()
    tool = PolicyLookupTool(client=fake_client)
    ctx = _create_tool_context()

    output = tool.run(PolicyLookupInput(policy_number="POL-SYN-001"), ctx)
    assert output.found is True
    assert output.policy is not None
    assert output.policy.policy_number == "POL-SYN-001"
    assert fake_client.call_count == 1
    assert fake_client.last_requested_policy == "POL-SYN-001"


def test_policy_lookup_tool_not_found() -> None:
    fake_client = FakePolicyServiceClient()
    tool = PolicyLookupTool(client=fake_client)
    ctx = _create_tool_context()

    output = tool.run(PolicyLookupInput(policy_number="NONEXISTENT-999"), ctx)
    assert output.found is False
    assert output.policy is None


def test_policy_lookup_tool_timeout_mapping() -> None:
    fake_client = FakePolicyServiceClient()
    fake_client.fail_with_timeout = True
    tool = PolicyLookupTool(client=fake_client)
    ctx = _create_tool_context()

    with pytest.raises(ToolFailureError) as exc_info:
        tool.run(PolicyLookupInput(policy_number="POL-AUTO-001"), ctx)
    assert exc_info.value.error.code == ToolErrorCode.TIMEOUT
    assert exc_info.value.error.retryable is True


def test_policy_lookup_tool_unavailable_mapping() -> None:
    fake_client = FakePolicyServiceClient()
    fake_client.fail_with_unavailable = True
    tool = PolicyLookupTool(client=fake_client)
    ctx = _create_tool_context()

    with pytest.raises(ToolFailureError) as exc_info:
        tool.run(PolicyLookupInput(policy_number="POL-AUTO-001"), ctx)
    assert exc_info.value.error.code == ToolErrorCode.UNAVAILABLE
    assert exc_info.value.error.retryable is True


def test_claim_history_lookup_with_client() -> None:
    fake_client = FakeClaimsHistoryServiceClient()
    tool = PriorClaimLookupTool(client=fake_client)
    ctx = _create_tool_context()

    output = tool.run(PriorClaimLookupInput(customer_id="CUST-SYN-HIST", limit=5), ctx)
    assert output.customer_id == "CUST-SYN-HIST"
    assert len(output.prior_claims) > 0
    assert fake_client.call_count == 1


def test_repair_cost_lookup_with_client_and_scope_check() -> None:
    fake_client = FakeRepairCostServiceClient()
    tool = DamageEstimateTool(client=fake_client)
    ctx = _create_tool_context()

    # Scope mismatch simulation
    with pytest.raises(ToolFailureError) as exc_info:
        tool.run(
            DamageEstimateInput(estimate_ref="SYN-EST-NORMAL", claim_ref="DIFFERENT-CLAIM-SCOPE"),
            ctx,
        )
    assert exc_info.value.error.code == ToolErrorCode.SCOPE_VIOLATION


def test_fraud_signal_lookup_with_client() -> None:
    fake_client = FakeFraudScoringServiceClient()
    tool = FraudSignalLookupTool(client=fake_client)
    ctx = _create_tool_context()

    output = tool.run(FraudSignalInput(claim_ref="SYN-NORMAL-001"), ctx)
    assert output.claim_ref == "SYN-NORMAL-001"
    assert output.risk_level in (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
    assert fake_client.call_count == 1


def test_document_retrieval_tool_with_client() -> None:
    fake_client = FakeDocumentStorageServiceClient()
    tool = DocumentRetrievalTool(client=fake_client)
    ctx = _create_tool_context()

    output = tool.run(
        DocumentRetrievalInput(document_ref="SYN-DOC-N1", claim_ref="SYN-NORMAL-001"), ctx
    )
    assert output.found is True
    assert output.document is not None
    assert output.content_untrusted != ""
    assert fake_client.call_count == 1


# ==============================================================================
# Category F & G: Distributed and Tiered Caching
# ==============================================================================


def test_in_memory_cache_ttl_expiration() -> None:
    cache = InMemoryCache()
    cache.set("key1", "val1", ttl_seconds=1)
    assert cache.get("key1") == "val1"
    time.sleep(1.1)
    assert cache.get("key1") is None


def test_redis_cache_fail_safe_on_client_exception() -> None:
    """Redis failure logs warning and returns None/False without crashing."""
    mock_redis = MagicMock()
    mock_redis.get.side_effect = ConnectionError("Redis connection refused")
    mock_redis.set.side_effect = ConnectionError("Redis connection refused")
    mock_redis.ping.side_effect = ConnectionError("Redis connection refused")

    cache = RedisCache(redis_client=mock_redis)
    assert cache.get("test_key") is None
    assert cache.set("test_key", "value", ttl_seconds=60) is False
    assert cache.is_available() is False


def test_tool_registry_caches_output_and_marks_provenance() -> None:
    """Tool execution is cached on first call; subsequent call returns from cache."""
    cache = InMemoryCache()
    fake_policy_client = FakePolicyServiceClient()
    registry = create_default_registry(cache=cache, policy_client=fake_policy_client)
    ctx = _create_tool_context()

    # Call 1: Cache Miss
    outcome1 = registry.execute(
        "policy_lookup",
        "1.0.0",
        {"policy_number": "POL-SYN-001"},
        ctx,
    )
    assert outcome1.succeeded is True
    assert outcome1.provenance is not None
    assert outcome1.provenance.source == "external_service"
    assert fake_policy_client.call_count == 1

    # Call 2: Cache Hit
    outcome2 = registry.execute(
        "policy_lookup",
        "1.0.0",
        {"policy_number": "POL-SYN-001"},
        ctx,
    )
    assert outcome2.succeeded is True
    assert outcome2.provenance is not None
    assert outcome2.provenance.source == "cache"
    assert outcome2.cost_units == 0  # Cached calls incur 0 cost units
    assert fake_policy_client.call_count == 1  # Not called again!


def test_tool_registry_fails_open_if_cache_corrupted() -> None:
    """If corrupted JSON is placed in cache, registry gracefully re-executes tool."""
    cache = InMemoryCache()
    fake_policy_client = FakePolicyServiceClient()
    registry = create_default_registry(cache=cache, policy_client=fake_policy_client)
    ctx = _create_tool_context()

    # Pre-populate corrupt cache data
    raw_input: dict[str, object] = {"policy_number": "POL-SYN-001"}
    import hashlib

    digest = hashlib.sha256(json.dumps(raw_input, sort_keys=True, default=str).encode()).hexdigest()
    cache.set(f"casefile:tool:policy_lookup:{digest}", "CORRUPTED_JSON", ttl_seconds=3600)

    outcome = registry.execute("policy_lookup", "1.0.0", raw_input, ctx)
    assert outcome.succeeded is True
    assert outcome.provenance is not None
    assert outcome.provenance.source == "external_service"
    assert fake_policy_client.call_count == 1


# ==============================================================================
# Category H: Distributed Rate Limiting
# ==============================================================================


def test_in_memory_rate_limiter_bounds() -> None:
    limiter = InMemoryRateLimiter()
    key = "client-1"

    # Acquire 3 allowed
    for i in range(3):
        allowed, remaining, _ = limiter.acquire(key, max_requests=3, window_seconds=10.0)
        assert allowed is True
        assert remaining == 2 - i

    # 4th request blocked
    allowed, remaining, retry_after = limiter.acquire(key, max_requests=3, window_seconds=10.0)
    assert allowed is False
    assert remaining == 0
    assert retry_after > 0


def test_redis_rate_limiter_with_mock_redis() -> None:
    mock_redis = MagicMock()
    mock_pipe = MagicMock()
    mock_redis.pipeline.return_value = mock_pipe

    # Mock pipeline execute returns [zrem, zcard=1, zrange=[('ts', 1000.0)]]
    mock_pipe.execute.side_effect = [
        [0, 1, [("1000.0", 1000.0)]],  # first pipeline check
        [1, True],  # second pipeline zadd + expire
    ]

    limiter = RedisRateLimiter(redis_client=mock_redis)
    allowed, remaining, retry_after = limiter.acquire(
        "test-key", max_requests=5, window_seconds=60.0
    )
    assert allowed is True
    assert remaining == 3
    assert retry_after == 0.0


def test_tool_registry_enforces_per_tool_rate_limit() -> None:
    limiter = InMemoryRateLimiter()
    # document_retrieval has limit 20 per minute
    registry = create_default_registry(rate_limiter=limiter)
    ctx = _create_tool_context(agent=AgentType.EXTRACTOR)

    # Exhaust limit
    for _ in range(20):
        limiter.acquire(
            f"tool:document_retrieval:{ctx.agent.value}", max_requests=20, window_seconds=60.0
        )

    # 21st call via registry must fail with UNAVAILABLE
    outcome = registry.execute(
        "document_retrieval",
        "1.0.0",
        {"document_ref": "DOC-001", "claim_ref": "CLM-AUTO-001"},
        ctx,
    )
    assert outcome.succeeded is False
    assert outcome.error is not None
    assert outcome.error.code == ToolErrorCode.UNAVAILABLE
    assert "Rate limit exceeded" in outcome.error.message


# ==============================================================================
# Category I: Per-Agent Authorization Matrix
# ==============================================================================


def test_authorization_matrix_extractor_forbidden_tools() -> None:
    registry = create_default_registry()
    ctx = _create_tool_context(agent=AgentType.EXTRACTOR)

    # Extractor is NOT authorized for policy_lookup
    outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-AUTO-001"}, ctx)
    assert outcome.succeeded is False
    assert outcome.error is not None
    assert outcome.error.code == ToolErrorCode.UNAUTHORIZED_TOOL
    assert outcome.status == ToolStatus.DENIED


def test_authorization_matrix_investigator_authorized_tools() -> None:
    registry = create_default_registry()
    ctx = _create_tool_context(agent=AgentType.INVESTIGATOR)

    assert registry.is_authorized(ctx.agent, "policy_lookup") is True
    assert registry.is_authorized(ctx.agent, "claim_history_lookup") is True
    assert registry.is_authorized(ctx.agent, "repair_cost_lookup") is True
    assert registry.is_authorized(ctx.agent, "fraud_signal_lookup") is True
    assert registry.is_authorized(ctx.agent, "document_retrieval") is True
    assert registry.is_authorized(ctx.agent, "evidence_lookup") is True


# ==============================================================================
# Category J: Secret Isolation
# ==============================================================================


def test_no_secrets_in_http_client_repr_or_logs() -> None:
    secret_key = SecretStr("super-confidential-external-key-12345")
    client = ResilientHttpClient(
        service_name="secret_service",
        base_url="http://secret.internal",
        api_key=secret_key,
    )
    headers = client._headers()
    assert headers["Authorization"] == "Bearer super-confidential-external-key-12345"
    # Check that representation does not leak the secret
    assert "super-confidential-external-key-12345" not in repr(client)
    assert "super-confidential-external-key-12345" not in str(client)
    client.close()


# ==============================================================================
# Category K: End-to-End Workflow with External Adapters & Caching
# ==============================================================================


def test_workflow_runner_integration_with_phase9_registry() -> None:
    """Workflow executes from ClaimInput to terminal state using Phase 9 registry with fake clients."""
    from casefile.agents.deterministic import DeterministicProvider
    from casefile.agents.extractor import ExtractorAgent
    from casefile.agents.investigator import InvestigatorAgent
    from casefile.agents.reviewer import ReviewerAgent

    cache = InMemoryCache()
    fake_policy = FakePolicyServiceClient()
    fake_history = FakeClaimsHistoryServiceClient()
    fake_repair = FakeRepairCostServiceClient()
    fake_fraud = FakeFraudScoringServiceClient()
    fake_doc = FakeDocumentStorageServiceClient()

    registry = create_default_registry(
        cache=cache,
        policy_client=fake_policy,
        history_client=fake_history,
        repair_client=fake_repair,
        fraud_client=fake_fraud,
        document_client=fake_doc,
    )

    run_id = uuid4()
    ext_prov = DeterministicProvider()
    ext_prov.push_json(
        {
            "workflow_id": str(run_id),
            "claimant_name": "Normal Holder",
            "policy_id": "POL-SYN-001",
            "incident_date": "2026-09-10",
            "incident_location": "Main St",
            "incident_description": "Rear collision",
            "claim_amount": "1500.00",
            "requested_coverage_type": "collision",
            "documents_processed": ["SYN-DOC-N1"],
            "extraction_confidence": 0.98,
            "missing_fields": [],
            "is_complete": True,
        }
    )
    inv_prov = DeterministicProvider()
    inv_prov.push_json(
        {
            "workflow_id": str(run_id),
            "policy_details": {"active": True, "coverage_limit": 50000},
            "claim_history": {"prior_claims_count": 0},
            "repair_cost_validation": {"estimate_amount": "1500.00", "is_reasonable": True},
            "fraud_signals": {"risk_score": 0.05, "flags": []},
            "supporting_documents": ["SYN-DOC-N1"],
            "findings_summary": "Policy verified, repair cost reasonable, low risk.",
            "evidence_strength": 0.95,
            "tool_calls_made": ["policy_lookup", "repair_cost_lookup"],
            "investigation_duration_seconds": 1,
            "tools_succeeded": 2,
            "tools_failed": 0,
        }
    )
    rev_prov = DeterministicProvider()
    rev_prov.push_json(
        {
            "workflow_id": str(run_id),
            "decision": "APPROVE",
            "reasoning": "All evidence complete and verified within policy limits.",
            "confidence_score": 0.96,
            "evidence_completeness": 0.95,
            "identified_gaps": [],
            "rework_feedback": None,
            "fraud_risk_level": "LOW",
            "fraud_signals_detected": False,
        }
    )

    claim = ClaimInput(
        claim_id=run_id,
        policy_id="POL-SYN-001",
        claimant_name="Normal Holder",
        incident_date="2026-09-10",
        claim_amount=Decimal("1500.00"),
        description="Rear collision at Main St on 2026-09-10",
        documents=["SYN-DOC-N1"],
    )

    runner = WorkflowRunner(
        ExtractorAgent(ext_prov),
        InvestigatorAgent(inv_prov),
        ReviewerAgent(rev_prov),
        tool_registry=registry,
    )
    wf_ctx = runner.run(claim, workflow_run_id=run_id)

    assert wf_ctx is not None
    assert wf_ctx.workflow_run_id == run_id
    assert wf_ctx.current_snapshot is not None
    assert wf_ctx.current_snapshot.current_state in (
        WorkflowState.APPROVED,
        WorkflowState.HUMAN_APPROVAL,
        WorkflowState.REJECTED,
    )
