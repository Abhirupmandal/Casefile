"""Tests for the Phase 6 deterministic evaluation dataset.

Validates that the 30-case dataset covers all 10 required behavioral classes
with deterministic identifiers, valid Pydantic models, and explicit expectations.
"""

from __future__ import annotations

import re

from casefile.evaluation.dataset import (
    DATASET_CASES,
    EVALUATION_DATASET,
    get_evaluation_case,
)
from casefile.evaluation.model import EvaluationCase


def test_dataset_size_at_least_thirty() -> None:
    """The evaluation dataset must contain at least 30 deterministic cases."""
    assert len(EVALUATION_DATASET) >= 30
    assert len(DATASET_CASES) >= 30


def test_case_identifiers_deterministic_pattern() -> None:
    """All case IDs must strictly match the CASE-XXX pattern."""
    pattern = re.compile(r"^CASE-\d{3}$")
    for case in EVALUATION_DATASET:
        assert pattern.match(case.case_id), f"Invalid ID format: {case.case_id}"
        assert case.case_id in DATASET_CASES
        fetched = get_evaluation_case(case.case_id)
        assert fetched.case_id == case.case_id


def test_behavioral_classes_distribution() -> None:
    """Dataset must cover all 10 required behavioral categories with minimum counts."""
    categories: dict[str, int] = {}
    for case in EVALUATION_DATASET:
        categories[case.category] = categories.get(case.category, 0) + 1

    # A. Nominal claims: at least 5
    assert categories.get("NOMINAL", 0) >= 5
    # B. Reviewer rework: at least 4
    assert categories.get("REWORK", 0) >= 4
    # C. Tool failures: at least 4
    assert categories.get("TOOL_FAILURE", 0) >= 4
    # D. Provider failures/retries: at least 3
    assert categories.get("PROVIDER_FAILURE", 0) >= 3
    # E. Conflicting evidence: at least 3
    assert categories.get("CONFLICTING_EVIDENCE", 0) >= 3
    # F. Missing policy/evidence: at least 2
    assert categories.get("MISSING_EVIDENCE", 0) >= 2
    # G. Fraud/risk signals: at least 2
    assert categories.get("FRAUD_SIGNALS", 0) >= 2
    # H. Budget/step exhaustion: at least 2
    assert categories.get("BUDGET_EXHAUSTION", 0) >= 2
    # I. Human rejection/expiration: at least 2
    assert categories.get("HUMAN_APPROVAL", 0) >= 2
    # J. Checkpoint/resume/replay scenarios: at least 3
    assert categories.get("CHECKPOINT_REPLAY", 0) >= 3


def test_case_models_have_explicit_expectations() -> None:
    """Each case must declare explicit expectations rather than implicit defaults."""
    for case in EVALUATION_DATASET:
        assert isinstance(case, EvaluationCase)
        assert case.scenario_name
        assert case.description
        assert case.claim_fixture.policy_id
        assert case.scenario.scenario_id == case.case_id
