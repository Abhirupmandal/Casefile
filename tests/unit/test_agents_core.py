"""
Unit Tests: Supervisor, Extractor, Investigator agents.

Valid routing, structured extraction/investigation, malformed output,
validation failures, retries, tool boundaries, and failure records.
All provider I/O is deterministic; no keys, no network.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from casefile.agents.base import AgentFailedError
from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import ALLOWED_INVESTIGATOR_TOOLS, InvestigatorAgent
from casefile.agents.results import AgentErrorCategory, AgentExecutionRecord
from casefile.models.contracts import (
    BudgetState,
    ClaimInput,
    ExtractionRequest,
    ExtractionResult,
    InvestigationRequest,
    InvestigationResult,
    WorkflowState,
)
from casefile.models.domain import AgentType
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.supervisor import NodeName, RouteAction


def _claim(documents: list[str] | None = None) -> ClaimInput:
    return ClaimInput(
        policy_id="POL-AG",
        claimant_name="Agent Tester",
        incident_date="2026-09-19",
        claim_amount=Decimal("2500.00"),
        description="Agent test claim",
        documents=documents if documents is not None else ["doc-1", "doc-2"],
    )


def _extraction_result(workflow_id: UUID) -> ExtractionResult:
    return ExtractionResult(
        workflow_id=workflow_id,
        claimant_name="Agent Tester",
        policy_id="POL-AG",
        incident_date="2026-09-19",
        incident_location="Springfield",
        incident_description="Agent test claim",
        claim_amount=Decimal("2500.00"),
        requested_coverage_type="COLLISION",
        extraction_confidence=0.92,
    )


def _extraction_json(workflow_id: UUID) -> dict[str, object]:
    return {
        "workflow_id": str(workflow_id),
        "claimant_name": "Agent Tester",
        "policy_id": "POL-AG",
        "incident_date": "2026-09-19",
        "incident_location": "Springfield",
        "incident_description": "Agent test claim",
        "claim_amount": "2500.00",
        "requested_coverage_type": "COLLISION",
        "extraction_confidence": 0.92,
    }


@pytest.mark.unit
class TestSupervisorAgent:
    def test_start_claim_initializes_received(self) -> None:
        from casefile.agents.supervisor_agent import SupervisorAgent

        agent = SupervisorAgent()
        run_id, ctx, snapshot = agent.start_claim(_claim())
        assert snapshot.current_state == WorkflowState.RECEIVED
        assert snapshot.workflow_run_id == run_id
        assert ctx.workflow_run_id == run_id
        assert ctx.actor == AgentType.SUPERVISOR

    def test_route_next_dispatches(self) -> None:
        from casefile.agents.supervisor_agent import SupervisorAgent

        agent = SupervisorAgent()
        snapshot = WorkflowSnapshot(
            workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.EXTRACTION
        )
        decision = agent.route_next(snapshot, BudgetState())
        assert decision.action == RouteAction.DISPATCH
        assert decision.node == NodeName.EXTRACTOR

    def test_route_next_enforces_terminal(self) -> None:
        from casefile.agents.supervisor_agent import SupervisorAgent

        agent = SupervisorAgent()
        snapshot = WorkflowSnapshot(
            workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.APPROVED
        )
        decision = agent.route_next(snapshot, BudgetState())
        assert decision.action == RouteAction.TERMINATED
        assert decision.node == NodeName.END

    def test_route_next_budget_preflight(self) -> None:
        from casefile.agents.supervisor_agent import SupervisorAgent

        agent = SupervisorAgent()
        snapshot = WorkflowSnapshot(
            workflow_run_id=uuid4(), claim_id=uuid4(), current_state=WorkflowState.EXTRACTION
        )
        exhausted = BudgetState(steps_completed=50)
        decision = agent.route_next(snapshot, exhausted)
        assert decision.action == RouteAction.TERMINATED
        assert "exhausted" in decision.reason.lower()

    def test_supervisor_has_no_tools(self) -> None:
        from casefile.agents.supervisor_agent import SupervisorAgent

        assert SupervisorAgent.allowed_tools == frozenset()


@pytest.mark.unit
class TestExtractorAgent:
    def _ctx(self, claim: ClaimInput, run_id: UUID) -> AgentContext:
        return AgentContext(
            claim_id=claim.claim_id, workflow_run_id=run_id, agent=AgentType.EXTRACTOR
        )

    def test_valid_structured_extraction(self) -> None:
        provider = DeterministicProvider()
        run_id = uuid4()
        provider.push_json(_extraction_json(run_id))
        agent = ExtractorAgent(provider)
        claim = _claim()
        output, record = agent.run(
            ExtractionRequest(workflow_id=run_id, claim_input=claim), self._ctx(claim, run_id)
        )
        assert output.claimant_name == "Agent Tester"
        assert output.is_complete is True
        assert record.status == "SUCCESS"
        assert record.output_contract_type == "extraction_result"
        assert record.output_valid is True
        assert record.attempts == 1

    def test_malformed_provider_response_retries_then_fails(self) -> None:
        provider = DeterministicProvider()
        provider.push_text("definitely not json")
        provider.push_text("still not json")
        agent = ExtractorAgent(provider)
        claim = _claim()
        run_id = uuid4()
        ctx = AgentContext(
            claim_id=claim.claim_id,
            workflow_run_id=run_id,
            agent=AgentType.EXTRACTOR,
            max_attempts=2,
        )
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(ExtractionRequest(workflow_id=run_id, claim_input=claim), ctx)
        assert exc_info.value.error.category == AgentErrorCategory.MALFORMED_OUTPUT
        assert exc_info.value.error.retryable is True
        assert provider.remaining() == 0

    def test_schema_invalid_response_fails_fast(self) -> None:
        provider = DeterministicProvider()
        bad = _extraction_json(uuid4())
        bad["extraction_confidence"] = 7.5
        provider.push_json(bad)
        agent = ExtractorAgent(provider)
        claim = _claim()
        run_id = uuid4()
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(
                ExtractionRequest(workflow_id=run_id, claim_input=claim),
                self._ctx(claim, run_id),
            )
        assert exc_info.value.error.category == AgentErrorCategory.CONTRACT_VALIDATION
        assert exc_info.value.error.retryable is False
        assert provider.remaining() == 0

    def test_workflow_mismatch_rejected(self) -> None:
        provider = DeterministicProvider()
        provider.push_json(_extraction_json(uuid4()))
        agent = ExtractorAgent(provider)
        claim = _claim()
        run_id = uuid4()
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(
                ExtractionRequest(workflow_id=run_id, claim_input=claim),
                self._ctx(claim, run_id),
            )
        assert exc_info.value.error.code == "WORKFLOW_MISMATCH"

    def test_empty_documents_rejected(self) -> None:
        provider = DeterministicProvider()
        agent = ExtractorAgent(provider)
        claim = _claim(documents=[])
        run_id = uuid4()
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(
                ExtractionRequest(workflow_id=run_id, claim_input=claim),
                self._ctx(claim, run_id),
            )
        assert exc_info.value.error.category == AgentErrorCategory.INVALID_INPUT
        assert provider.remaining() == 0

    def test_provider_error_retry_then_success(self) -> None:
        from casefile.agents.providers import ProviderError

        provider = DeterministicProvider()
        run_id = uuid4()
        provider.push_error(ProviderError("flaky", retryable=True))
        provider.push_json(_extraction_json(run_id))
        agent = ExtractorAgent(provider)
        claim = _claim()
        output, record = agent.run(
            ExtractionRequest(workflow_id=run_id, claim_input=claim), self._ctx(claim, run_id)
        )
        assert output.claimant_name == "Agent Tester"
        assert record.attempts == 2
        assert len(record.retry_reasons) == 1

    def test_timeout_failure_record(self) -> None:
        provider = DeterministicProvider()
        provider.push_timeout()
        agent = ExtractorAgent(provider)
        claim = _claim()
        run_id = uuid4()
        ctx = AgentContext(
            claim_id=claim.claim_id,
            workflow_run_id=run_id,
            agent=AgentType.EXTRACTOR,
            max_attempts=1,
        )
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(ExtractionRequest(workflow_id=run_id, claim_input=claim), ctx)
        assert exc_info.value.error.category == AgentErrorCategory.TIMEOUT

    def test_extractor_document_tool_only(self) -> None:
        # Phase 5 matrix: extractor may use document retrieval only
        assert ExtractorAgent.allowed_tools == frozenset({"document_retrieval"})


@pytest.mark.unit
class TestInvestigatorAgent:
    def _run_valid(self, run_id: UUID) -> tuple[InvestigationResult, AgentExecutionRecord]:
        provider = DeterministicProvider()
        provider.push_json(
            {
                "workflow_id": str(run_id),
                "policy_details": {"status": "active"},
                "claim_history": {"prior": 0},
                "repair_cost_validation": {"within_range": True},
                "fraud_signals": {"risk": "low"},
                "supporting_documents": ["doc-9"],
                "findings_summary": "Policy active; costs reasonable",
                "evidence_strength": 0.8,
                "tool_calls_made": ["policy_lookup", "repair_cost_lookup"],
                "investigation_duration_seconds": 4,
                "tools_succeeded": 2,
                "tools_failed": 0,
            }
        )
        agent = InvestigatorAgent(provider)
        claim_id = uuid4()
        ctx = AgentContext(claim_id=claim_id, workflow_run_id=run_id, agent=AgentType.INVESTIGATOR)
        output, record = agent.run(
            InvestigationRequest(workflow_id=run_id, extraction_result=_extraction_result(run_id)),
            ctx,
        )
        return output, record

    def test_valid_investigation_result(self) -> None:
        output, record = self._run_valid(uuid4())
        assert output.findings_summary.startswith("Policy active")
        assert record.status == "SUCCESS"

    def test_tool_count_mismatch_rejected(self) -> None:
        provider = DeterministicProvider()
        run_id = uuid4()
        payload = {
            "workflow_id": str(run_id),
            "findings_summary": "x",
            "evidence_strength": 0.5,
            "tool_calls_made": ["policy_lookup"],
            "tools_succeeded": 5,
            "tools_failed": 0,
        }
        provider.push_json(payload)
        agent = InvestigatorAgent(provider)
        claim_id = uuid4()
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(
                InvestigationRequest(
                    workflow_id=run_id, extraction_result=_extraction_result(run_id)
                ),
                AgentContext(
                    claim_id=claim_id, workflow_run_id=run_id, agent=AgentType.INVESTIGATOR
                ),
            )
        assert exc_info.value.error.code == "TOOL_COUNT_MISMATCH"

    def test_empty_extraction_rejected(self) -> None:
        provider = DeterministicProvider()
        agent = InvestigatorAgent(provider)
        run_id = uuid4()
        bad_extraction = _extraction_result(run_id).model_copy(
            update={"incident_description": "   "}
        )
        claim_id = uuid4()
        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(
                InvestigationRequest(workflow_id=run_id, extraction_result=bad_extraction),
                AgentContext(
                    claim_id=claim_id, workflow_run_id=run_id, agent=AgentType.INVESTIGATOR
                ),
            )
        assert exc_info.value.error.category == AgentErrorCategory.INVALID_INPUT

    def test_investigator_tool_matrix(self) -> None:
        assert InvestigatorAgent.allowed_tools == ALLOWED_INVESTIGATOR_TOOLS
        assert (
            frozenset(
                {
                    "policy_lookup",
                    "claim_history_lookup",
                    "repair_cost_lookup",
                    "fraud_signal_lookup",
                    "document_retrieval",
                }
            )
            == ALLOWED_INVESTIGATOR_TOOLS
        )
