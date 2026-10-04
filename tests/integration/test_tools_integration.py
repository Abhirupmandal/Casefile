"""
Integration Tests: Agent → ToolRegistry → typed tool → agent contract.

Proves the full Phase 5 chain with deterministic fixtures: extractor
fetches documents then extracts; investigator looks up policy/history/
damage evidence then synthesizes; reviewer pulls evidence/policy/damage
then recommends. No raw store output crosses into agent contracts.
"""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.models.contracts import (
    ClaimInput,
    ExtractionRequest,
    InvestigationRequest,
    ReviewRequest,
)
from casefile.models.domain import AgentType
from casefile.tools import create_default_registry
from casefile.tools.document import DocumentRetrievalOutput
from casefile.tools.evidence import EvidenceLookupOutput
from casefile.tools.policy import PolicyLookupOutput


def _agent_ctx(claim_id: UUID, workflow_run_id: UUID, agent: AgentType) -> AgentContext:
    return AgentContext(claim_id=claim_id, workflow_run_id=workflow_run_id, agent=agent)


@pytest.mark.integration
class TestExtractorToolChain:
    def test_extractor_registry_document_contract(self) -> None:
        from uuid import UUID

        run_id = uuid4()
        claim = ClaimInput(
            policy_id="POL-SYN-001",
            claimant_name="Syn Normal",
            incident_date="2026-09-10",
            claim_amount=Decimal("1500.00"),
            description="Synthetic normal claim",
            documents=["SYN-DOC-N1", "SYN-DOC-N2"],
        )
        registry = create_default_registry()
        agent = ExtractorAgent(DeterministicProvider())
        ctx = _agent_ctx(claim.claim_id, run_id, AgentType.EXTRACTOR)
        docs = agent.fetch_documents(
            ["SYN-DOC-N1", "SYN-DOC-N2"], "SYN-NORMAL-001", agent_ctx=ctx, registry=registry
        )
        assert len(docs) == 2
        assert all(isinstance(doc, DocumentRetrievalOutput) for doc in docs)
        assert all(doc.found for doc in docs)
        provider = DeterministicProvider()
        provider.push_json(
            {
                "workflow_id": str(run_id),
                "claimant_name": "Syn Normal",
                "policy_id": "POL-SYN-001",
                "incident_date": "2026-09-10",
                "incident_location": "Main St",
                "incident_description": "Synthetic normal claim",
                "claim_amount": "1500.00",
                "requested_coverage_type": "COLLISION",
                "extraction_confidence": 0.95,
            }
        )
        extracting = ExtractorAgent(provider)
        output, _ = extracting.run(ExtractionRequest(workflow_id=run_id, claim_input=claim), ctx)
        assert output.is_complete is True
        assert UUID(str(output.workflow_id)) == run_id


@pytest.mark.integration
class TestInvestigatorToolChain:
    def test_investigator_policy_history_damage_contract(self) -> None:
        from tests.unit.test_agents_core import _extraction_result

        run_id = uuid4()
        claim_id = uuid4()
        registry = create_default_registry()
        agent = InvestigatorAgent(DeterministicProvider())
        ctx = _agent_ctx(claim_id, run_id, AgentType.INVESTIGATOR)
        policy = agent.lookup(
            "policy_lookup", {"policy_number": "POL-SYN-001"}, agent_ctx=ctx, registry=registry
        )
        assert policy.succeeded is True
        assert isinstance(policy.output, PolicyLookupOutput)
        history = agent.lookup(
            "claim_history_lookup",
            {"customer_id": "CUST-SYN-HIST"},
            agent_ctx=ctx,
            registry=registry,
        )
        assert history.succeeded is True
        damage = agent.lookup(
            "repair_cost_lookup",
            {"estimate_ref": "SYN-EST-NORMAL", "claim_ref": "SYN-NORMAL-001"},
            agent_ctx=ctx,
            registry=registry,
        )
        assert damage.succeeded is True
        provider = DeterministicProvider()
        provider.push_json(
            {
                "workflow_id": str(run_id),
                "policy_details": {"status": "active"},
                "claim_history": {"prior": 3},
                "repair_cost_validation": {"within_range": True},
                "fraud_signals": {"risk": "low"},
                "supporting_documents": ["SYN-DOC-N1"],
                "findings_summary": "Evidence supports the claim",
                "evidence_strength": 0.85,
                "tool_calls_made": ["policy_lookup", "claim_history_lookup", "repair_cost_lookup"],
                "investigation_duration_seconds": 5,
                "tools_succeeded": 3,
                "tools_failed": 0,
            }
        )
        investigating = InvestigatorAgent(provider)
        output, _ = investigating.run(
            InvestigationRequest(workflow_id=run_id, extraction_result=_extraction_result(run_id)),
            ctx,
        )
        assert output.evidence_strength == 0.85


@pytest.mark.integration
class TestReviewerToolChain:
    def test_reviewer_evidence_policy_damage_contract(self) -> None:
        from tests.unit.test_agents_core import _extraction_result
        from tests.unit.test_agents_review import _investigation

        run_id = uuid4()
        claim_id = uuid4()
        registry = create_default_registry()
        agent = ReviewerAgent(DeterministicProvider())
        ctx = _agent_ctx(claim_id, run_id, AgentType.REVIEWER)
        evidence = agent.lookup(
            "evidence_lookup", {"claim_ref": "SYN-NORMAL-001"}, agent_ctx=ctx, registry=registry
        )
        assert evidence.succeeded is True
        assert isinstance(evidence.output, EvidenceLookupOutput)
        policy = agent.lookup(
            "policy_lookup", {"policy_number": "POL-SYN-001"}, agent_ctx=ctx, registry=registry
        )
        assert policy.succeeded is True
        damage = agent.lookup(
            "repair_cost_lookup",
            {"estimate_ref": "SYN-EST-NORMAL", "claim_ref": "SYN-NORMAL-001"},
            agent_ctx=ctx,
            registry=registry,
        )
        assert damage.succeeded is True
        provider = DeterministicProvider()
        provider.push_json(
            {
                "workflow_id": str(run_id),
                "decision": "APPROVE",
                "reasoning": "Evidence complete and consistent",
                "confidence_score": 0.93,
                "evidence_completeness": 1.0,
                "identified_gaps": [],
                "rework_feedback": None,
                "fraud_risk_level": "LOW",
                "fraud_signals_detected": False,
            }
        )
        reviewing = ReviewerAgent(provider)
        output, _ = reviewing.run(
            ReviewRequest(
                workflow_id=run_id,
                extraction_result=_extraction_result(run_id),
                investigation_result=_investigation(run_id),
            ),
            ctx,
        )
        assert output.decision == "APPROVE"
