# CASEFILE Testing Strategy

## Overview

CASEFILE requires a comprehensive testing strategy to ensure correctness, reliability, and safety. This document defines the testing approach, test categories, and specific test cases needed to verify the system behaves correctly under all conditions.

## Testing Philosophy

### 1. Test Pyramid

```
                    ┌─────────────┐
                    │    E2E      │
                    │   Tests     │  (Few, Expensive)
                    └─────────────┘
                  ┌───────────────────┐
                  │   Integration     │
                  │     Tests         │  (Some)
                  └───────────────────┘
            ┌─────────────────────────────┐
            │       Unit Tests            │  (Many, Fast)
            └─────────────────────────────┘
```

### 2. Test Categories

| Category | Speed | Coverage | Purpose |
|----------|-------|----------|---------|
| Unit | Fast | High | Verify individual components |
| Contract | Fast | High | Verify Pydantic schemas |
| Integration | Medium | Medium | Verify component interactions |
| State Machine | Fast | High | Verify workflow transitions |
| Agent | Medium | Medium | Verify agent behavior |
| Tool | Fast | High | Verify tool contracts |
| Persistence | Medium | Medium | Verify database operations |
| Replay | Medium | Medium | Verify checkpoint/replay |
| Budget | Fast | High | Verify budget enforcement |
| Failure Injection | Medium | Medium | Verify error handling |
| End-to-End | Slow | Low | Verify complete workflows |

### 3. Test Requirements

- Every contract has schema tests
- Every state transition has tests
- Every budget limit has enforcement tests
- Every failure mode has handling tests
- Every tool has mock-based tests

## Unit Tests

### Contract/Schema Tests

```python
import pytest
from pydantic import ValidationError


class TestClaimInputContract:
    """Tests for ClaimInput schema validation."""

    def test_valid_claim_input(self):
        """Valid claim input should parse successfully."""
        claim = ClaimInput(
            external_claim_id="CLM-12345",
            policy_number="POL-ABC-123",
            customer_id="CUST-456",
            claim_type=ClaimType.COLLISION,
            date_of_loss=date(2024, 1, 15),
            date_reported=date(2024, 1, 16),
            description="Vehicle collision at intersection",
            documents=[
                Document(
                    document_type=DocumentType.POLICE_REPORT,
                    content="base64encodedcontent",
                    content_type=DocumentContentType.PDF,
                    source="police_department"
                )
            ]
        )
        assert claim.external_claim_id == "CLM-12345"
        assert claim.policy_number == "POL-ABC-123"
        assert len(claim.documents) == 1

    def test_policy_number_normalized(self):
        """Policy number should be normalized to uppercase."""
        claim = ClaimInput(
            policy_number="pol-abc-123",
            # ... other required fields
        )
        assert claim.policy_number == "POL-ABC-123"

    def test_empty_policy_number_rejected(self):
        """Empty policy number should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ClaimInput(policy_number="   ", documents=[])
        assert "cannot be empty" in str(exc_info.value)


class TestBudgetStateContract:
    """Tests for BudgetState schema validation."""

    def test_total_tokens_computed(self):
        """Total tokens should be computed from input + output."""
        state = BudgetState(
            workflow_run_id=uuid4(),
            input_tokens_used=1000,
            output_tokens_used=500,
            limits=BudgetLimits()
        )
        assert state.total_tokens_used == 1500

    def test_budget_exhaustion_detection(self):
        """Budget exhaustion should be detected."""
        state = BudgetState(
            workflow_run_id=uuid4(),
            input_tokens_used=100000,
            output_tokens_used=50000,
            limits=BudgetLimits(max_total_tokens=150000)
        )
        reason = state.check_limits()
        assert state.is_exhausted
        assert reason == "MAX_TOKENS_EXCEEDED"
```

### State Transition Tests

```python
class TestStateMachineTransitions:
    """Tests for state machine transitions."""

    @pytest.mark.parametrize("source,destination", ALLOWED_TRANSITIONS)
    def test_allowed_transition_succeeds(self, source, destination):
        """Every allowed transition should succeed with valid data."""
        validator = TransitionValidator()
        state_data = create_valid_state_data(source)
        result = validator.validate(source, destination, state_data)
        assert result.is_valid

    @pytest.mark.parametrize("source,destination", get_forbidden_transitions())
    def test_forbidden_transition_fails(self, source, destination):
        """Forbidden transitions should be rejected."""
        validator = TransitionValidator()
        state_data = create_valid_state_data(source)
        result = validator.validate(source, destination, state_data)
        assert not result.is_valid
        assert "not allowed" in result.violations[0].lower()

    def test_terminal_state_is_terminal(self):
        """Terminal states cannot transition to any other state."""
        validator = TransitionValidator()
        for terminal in TERMINAL_STATES:
            for destination in WorkflowState:
                if destination != terminal:
                    result = validator.validate(terminal, destination, {})
                    assert not result.is_valid


class TestReworkTransition:
    """Tests for rework cycle management."""

    def test_rework_allowed_under_limit(self):
        """Rework should be allowed when under max cycles."""
        context = ExecutionContext(
            current_state=WorkflowState.REVIEW,
            rework_count=2,
            budget_state=BudgetState(limits=BudgetLimits(max_rework_cycles=3))
        )
        decision = supervisor.decide_next_state(
            context.current_state,
            ReviewResult(decision=ReviewDecision.REQUEST_REWORK)
        )
        assert decision == WorkflowState.REWORK_LOOP

    def test_rework_blocked_at_limit(self):
        """Rework should be blocked when at max cycles."""
        context = ExecutionContext(
            current_state=WorkflowState.REVIEW,
            rework_count=3,
            budget_state=BudgetState(limits=BudgetLimits(max_rework_cycles=3))
        )
        decision = supervisor.decide_next_state(
            context.current_state,
            ReviewResult(decision=ReviewDecision.REQUEST_REWORK)
        )
        assert decision == WorkflowState.MAX_REWORK_EXCEEDED
```

### Budget Enforcement Tests

```python
class TestBudgetEnforcement:
    """Tests for budget limit enforcement."""

    def test_token_limit_enforced(self):
        """Token limit should be enforced before LLM call."""
        tracker = BudgetTracker(BudgetLimits(max_total_tokens=1000))
        state = BudgetState(
            workflow_run_id=uuid4(),
            input_tokens_used=800,
            output_tokens_used=100,
            limits=BudgetLimits(max_total_tokens=1000)
        )
        # Requesting 200 tokens should fail (would exceed 1000)
        can_proceed, reason = tracker.check_can_proceed(state, requested_tokens=200)
        assert not can_proceed
        assert reason == "INSUFFICIENT_TOKEN_BUDGET"

    def test_cost_limit_enforced(self):
        """Cost limit should be enforced."""
        tracker = BudgetTracker(BudgetLimits(max_cost_usd=Decimal("1.00")))
        state = BudgetState(
            workflow_run_id=uuid4(),
            estimated_cost_usd=Decimal("0.90"),
            limits=BudgetLimits(max_cost_usd=Decimal("1.00"))
        )
        # Adding $0.20 should fail (would exceed $1.00)
        state = tracker.record_usage(
            state,
            TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150),
            Decimal("0.20")
        )
        assert state.is_exhausted
        assert state.exhaustion_reason == "MAX_COST_EXCEEDED"

    def test_step_limit_enforced(self):
        """Step limit should be enforced."""
        tracker = BudgetTracker(BudgetLimits(max_steps=10))
        state = BudgetState(
            workflow_run_id=uuid4(),
            steps_completed=9,
            limits=BudgetLimits(max_steps=10)
        )
        # Incrementing to 10 should be allowed
        state = tracker.record_step(state)
        assert not state.is_exhausted
        # Incrementing to 11 should exhaust
        state = tracker.record_step(state)
        assert state.is_exhausted
        assert state.exhaustion_reason == "MAX_STEPS_EXCEEDED"
```

## Integration Tests

### Agent Integration Tests

```python
class TestExtractorIntegration:
    """Integration tests for Extractor agent."""

    @pytest.fixture
    def extractor(self):
        """Create extractor with mock LLM."""
        mock_llm = MockLLMProvider()
        return ExtractorAgent(llm_provider=mock_llm)

    async def test_extraction_from_police_report(self, extractor):
        """Extractor should parse police report correctly."""
        request = ExtractionRequest(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            documents=[
                Document(
                    document_type=DocumentType.POLICE_REPORT,
                    content=create_police_report_content(),
                    content_type=DocumentContentType.PDF
                )
            ]
        )
        result = await extractor.execute(request)
        assert result.status == ExtractionStatus.SUCCESS
        assert result.extracted_data.vehicle.make is not None
        assert result.extracted_data.driver.name is not None

    async def test_extraction_with_malformed_document(self, extractor):
        """Extractor should handle malformed documents gracefully."""
        request = ExtractionRequest(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            documents=[
                Document(
                    document_type=DocumentType.POLICE_REPORT,
                    content="",  # Empty content
                    content_type=DocumentContentType.PDF
                )
            ]
        )
        result = await extractor.execute(request)
        # Should fail gracefully, not crash
        assert result.status in [ExtractionStatus.FAILURE, ExtractionStatus.PARTIAL_SUCCESS]


class TestInvestigatorIntegration:
    """Integration tests for Investigator agent."""

    @pytest.fixture
    def investigator(self):
        """Create investigator with mock tools."""
        mock_tools = create_mock_tool_registry()
        return InvestigatorAgent(tool_registry=mock_tools)

    async def test_investigation_calls_all_tools(self, investigator):
        """Investigator should call all required tools."""
        request = InvestigationRequest(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            extracted_data=create_sample_extracted_data(),
            policy_number="POL-123"
        )
        result = await investigator.execute(request)
        assert result.status == InvestigationStatus.SUCCESS
        assert result.policy_verification is not None
        assert result.claim_history is not None
        assert result.cost_analysis is not None
        assert result.fraud_assessment is not None
```

## Failure Injection Tests

```python
class TestFailureInjection:
    """Tests for failure handling."""

    async def test_llm_timeout_handling(self):
        """System should handle LLM timeouts."""
        mock_llm = MockLLMProvider(should_timeout=True)
        agent = ExtractorAgent(llm_provider=mock_llm)
        result = await agent.execute(create_valid_extraction_request())
        assert result.status == ExtractionStatus.TIMEOUT
        assert result.error is not None

    async def test_llm_malformed_output_handling(self):
        """System should handle malformed LLM output."""
        mock_llm = MockLLMProvider(should_return_malformed=True)
        agent = ExtractorAgent(llm_provider=mock_llm)
        result = await agent.execute(create_valid_extraction_request())
        assert result.status == ExtractionStatus.FAILURE

    async def test_tool_timeout_handling(self):
        """System should handle tool timeouts."""
        mock_tool = MockTool(should_timeout=True)
        executor = ToolExecutor(tools={"test_tool": mock_tool})
        with pytest.raises(ToolTimeoutError):
            await executor.execute("test_tool", create_tool_input(), create_context())

    async def test_database_failure_handling(self):
        """System should handle database failures."""
        mock_db = MockDatabase(should_fail=True)
        repo = WorkflowRunRepository(mock_db)
        with pytest.raises(DatabaseError):
            await repo.save(create_workflow())

    async def test_retry_exhaustion_handling(self):
        """System should handle retry exhaustion."""
        mock_tool = MockTool(should_fail=True, fail_count=10)
        executor = ToolExecutor(tools={"test_tool": mock_tool})
        with pytest.raises(ToolExecutionError) as exc_info:
            await executor.execute("test_tool", create_tool_input(), create_context())
        assert "failed after" in str(exc_info.value).lower()
```

## End-to-End Tests

```python
class TestEndToEndWorkflows:
    """End-to-end workflow tests."""

    @pytest.fixture
    async def system(self):
        """Create complete system for testing."""
        system = await create_test_system()
        yield system
        await system.cleanup()

    async def test_simple_claim_approval(self, system):
        """Simple claim should be approved end-to-end."""
        claim = create_simple_claim()
        result = await system.workflow_executor.execute(claim)
        assert result.terminal_state == WorkflowState.APPROVED
        assert result.recommendation is not None

    async def test_complex_claim_with_rework(self, system):
        """Complex claim with rework should complete."""
        claim = create_complex_claim()
        result = await system.workflow_executor.execute(claim)
        assert result.terminal_state in [WorkflowState.APPROVED, WorkflowState.REJECTED]
        assert result.rework_count > 0

    async def test_workflow_termination_guaranteed(self, system):
        """Workflow should always terminate."""
        claim = create_problematic_claim()
        config = WorkflowConfig(max_steps=10, max_execution_time_seconds=60)
        result = await system.workflow_executor.execute(claim, config)
        assert result.terminal_state in TERMINAL_STATES
        assert result.step_count <= 10
```

## Test Utilities

### Mock Implementations

```python
class MockLLMProvider:
    """Mock LLM provider for testing."""

    def __init__(self, should_timeout=False, should_fail=False, should_return_malformed=False):
        self.should_timeout = should_timeout
        self.should_fail = should_fail
        self.should_return_malformed = should_return_malformed
        self.call_count = 0

    async def complete(self, prompt, response_model, **kwargs):
        """Return mock response."""
        self.call_count += 1
        if self.should_timeout:
            await asyncio.sleep(100)
        if self.should_fail:
            raise LLMError("Mock LLM failure")
        if self.should_return_malformed:
            return {"invalid": "data"}
        return create_mock_response(response_model)


class MockTool:
    """Mock tool for testing."""

    def __init__(self, should_fail=False, should_timeout=False, fail_count=0):
        self.should_fail = should_fail
        self.should_timeout = should_timeout
        self.fail_count = fail_count
        self.call_count = 0

    async def execute(self, input_data, context):
        """Return mock result."""
        self.call_count += 1
        if self.should_timeout:
            await asyncio.sleep(100)
        if self.should_fail or self.call_count <= self.fail_count:
            raise ToolExecutionError("Mock tool failure")
        return create_mock_result(input_data)
```

## Summary

The testing strategy provides:
- Contract tests: Every Pydantic schema validated
- State machine tests: Every transition tested
- Budget tests: Every limit enforced
- Failure tests: Every failure mode handled
- Integration tests: Component interactions verified
- E2E tests: Complete workflows verified
- Mock utilities: Deterministic testing
