"""
Unit Tests: Individual tool behavior, bounds, and determinism.

Each tool is exercised for valid output, not-found handling, input
rejection, scope isolation, result bounds, and repeat-call determinism.
"""

from uuid import uuid4

import pytest

from casefile.models.domain import AgentType
from casefile.tools import create_default_registry
from casefile.tools.contracts import ToolContext, ToolOutcome, ToolStatus


def _ctx(agent: AgentType = AgentType.INVESTIGATOR) -> ToolContext:
    return ToolContext(claim_id=uuid4(), workflow_run_id=uuid4(), agent=agent)


def _run(
    tool_name: str, raw: dict[str, object], agent: AgentType = AgentType.INVESTIGATOR
) -> ToolOutcome:
    registry = create_default_registry()
    return registry.execute(tool_name, "1.0.0", raw, _ctx(agent))


@pytest.mark.unit
class TestDocumentRetrieval:
    def test_valid_retrieval(self) -> None:
        from casefile.tools.document import DocumentRetrievalOutput

        outcome = _run(
            "document_retrieval",
            {"document_ref": "SYN-DOC-N1", "claim_ref": "SYN-NORMAL-001"},
            AgentType.EXTRACTOR,
        )
        assert outcome.succeeded is True
        assert isinstance(outcome.output, DocumentRetrievalOutput)
        assert outcome.output.found is True
        assert outcome.output.document is not None
        assert outcome.output.document.trusted_source is False
        assert "Main St" in outcome.output.content_untrusted

    def test_not_found_is_typed(self) -> None:
        outcome = _run(
            "document_retrieval",
            {"document_ref": "SYN-DOC-NOPE", "claim_ref": "SYN-NORMAL-001"},
            AgentType.EXTRACTOR,
        )
        # Not-found is a typed SUCCESS result (found=False), never an error
        assert outcome.status == ToolStatus.SUCCESS
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.found is False  # type: ignore[attr-defined]

    def test_malformed_ref_rejected(self) -> None:
        outcome = _run(
            "document_retrieval",
            {"document_ref": "../../etc/passwd", "claim_ref": "SYN-NORMAL-001"},
            AgentType.EXTRACTOR,
        )
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code.value == "INVALID_INPUT"

    def test_cross_claim_scope_rejected(self) -> None:
        outcome = _run(
            "document_retrieval",
            {"document_ref": "SYN-DOC-N1", "claim_ref": "SYN-FRAUD-001"},
            AgentType.EXTRACTOR,
        )
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code.value == "SCOPE_VIOLATION"


@pytest.mark.unit
class TestPolicyLookup:
    def test_valid_policy(self) -> None:
        from casefile.tools.policy import PolicyLookupOutput

        outcome = _run("policy_lookup", {"policy_number": "pol-syn-001"})
        assert outcome.succeeded is True
        assert isinstance(outcome.output, PolicyLookupOutput)
        assert outcome.output.found is True
        assert outcome.output.policy is not None
        assert outcome.output.policy.policy_number == "POL-SYN-001"

    def test_unknown_policy_not_found(self) -> None:
        outcome = _run("policy_lookup", {"policy_number": "POL-NOPE"})
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.found is False  # type: ignore[attr-defined]

    def test_empty_policy_rejected(self) -> None:
        outcome = _run("policy_lookup", {"policy_number": "  "})
        assert outcome.status == ToolStatus.FAILURE


@pytest.mark.unit
class TestPriorClaimLookup:
    def test_history_newest_first(self) -> None:
        from casefile.tools.history import PriorClaimLookupOutput

        outcome = _run("claim_history_lookup", {"customer_id": "CUST-SYN-HIST"})
        assert outcome.succeeded is True
        assert isinstance(outcome.output, PriorClaimLookupOutput)
        assert outcome.output.total_count == 3
        dates = [claim.claim_date for claim in outcome.output.prior_claims]
        assert dates == sorted(dates, reverse=True)

    def test_limit_bounds_results(self) -> None:
        outcome = _run("claim_history_lookup", {"customer_id": "CUST-SYN-HIST", "limit": 2})
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert len(outcome.output.prior_claims) == 2  # type: ignore[attr-defined]
        assert outcome.output.total_count == 3  # type: ignore[attr-defined]

    def test_empty_history(self) -> None:
        outcome = _run("claim_history_lookup", {"customer_id": "CUST-SYN-001"})
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.total_count == 0  # type: ignore[attr-defined]

    def test_scope_isolation(self) -> None:
        outcome = _run("claim_history_lookup", {"customer_id": "CUST-SYN-001"})
        assert outcome.succeeded is True
        refs = [c.claim_reference for c in outcome.output.prior_claims]  # type: ignore[union-attr]
        assert "SYN-OLD-1" not in refs


@pytest.mark.unit
class TestDamageEstimate:
    def test_consistent_estimate(self) -> None:
        from casefile.tools.damage import DamageEstimateOutput

        outcome = _run(
            "repair_cost_lookup", {"estimate_ref": "SYN-EST-NORMAL", "claim_ref": "SYN-NORMAL-001"}
        )
        assert outcome.succeeded is True
        assert isinstance(outcome.output, DamageEstimateOutput)
        assert outcome.output.found is True
        assert outcome.output.consistent is True
        assert outcome.output.estimate is not None
        assert outcome.output.findings == ()

    def test_inconsistent_estimate_flagged(self) -> None:
        outcome = _run(
            "repair_cost_lookup",
            {"estimate_ref": "SYN-EST-BROKEN", "claim_ref": "SYN-ESTIMATE-001"},
        )
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.consistent is False  # type: ignore[attr-defined]
        assert outcome.output.estimate is None  # type: ignore[attr-defined]
        assert len(outcome.output.findings) > 0  # type: ignore[attr-defined]
        assert outcome.output.declared_total is not None  # type: ignore[attr-defined]
        assert outcome.output.computed_total is not None  # type: ignore[attr-defined]

    def test_cross_claim_scope_rejected(self) -> None:
        outcome = _run(
            "repair_cost_lookup", {"estimate_ref": "SYN-EST-NORMAL", "claim_ref": "SYN-FRAUD-001"}
        )
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code.value == "SCOPE_VIOLATION"


@pytest.mark.unit
class TestEvidenceLookup:
    def test_normalized_items_with_provenance(self) -> None:
        from casefile.tools.evidence import EvidenceLookupOutput

        outcome = _run("evidence_lookup", {"claim_ref": "SYN-NORMAL-001"})
        assert outcome.succeeded is True
        assert isinstance(outcome.output, EvidenceLookupOutput)
        assert outcome.output.total_count > 0
        for item in outcome.output.items:
            assert item.claim_ref == "SYN-NORMAL-001"
            assert item.evidence_id is not None
            assert item.provenance.startswith("fixture:")

    def test_deterministic_ordering(self) -> None:
        first = _run("evidence_lookup", {"claim_ref": "SYN-NORMAL-001"})
        second = _run("evidence_lookup", {"claim_ref": "SYN-NORMAL-001"})
        assert first.output is not None and second.output is not None

        # Semantic equality: provenance timestamps excluded (collected_at is
        # per-call metadata, not normalized result content)
        def _key(output: object) -> list[tuple[str, str, str]]:
            items = getattr(output, "items", ())
            return [(str(item.evidence_id), item.source, item.value) for item in items]

        assert _key(first.output) == _key(second.output)
        assert first.output.total_count == second.output.total_count  # type: ignore[attr-defined]

    def test_source_filter_and_limit(self) -> None:
        outcome = _run(
            "evidence_lookup",
            {"claim_ref": "SYN-NORMAL-001", "source_types": ["DOCUMENT"], "limit": 1},
        )
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert len(outcome.output.items) == 1  # type: ignore[attr-defined]


@pytest.mark.unit
class TestFraudLookup:
    def test_clean_claim_low_risk(self) -> None:
        from casefile.tools.fraud import FraudSignalOutput

        outcome = _run("fraud_signal_lookup", {"claim_ref": "SYN-NORMAL-001"})
        assert outcome.succeeded is True
        assert isinstance(outcome.output, FraudSignalOutput)
        assert outcome.output.risk_score == 0.0
        assert outcome.output.risk_level.value == "LOW"
        assert outcome.output.requires_manual_review is False

    def test_fraud_profile_scores(self) -> None:
        outcome = _run("fraud_signal_lookup", {"claim_ref": "SYN-FRAUD-001"})
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.risk_score == 0.8  # type: ignore[attr-defined]
        assert outcome.output.risk_level.value == "HIGH"  # type: ignore[attr-defined]
        assert outcome.output.requires_manual_review is True  # type: ignore[attr-defined]
        assert len(outcome.output.indicators) == 2  # type: ignore[attr-defined]
