"""
Unit Tests: Package Imports & Basic Scaffolding

Validates that CASEFILE and its subpackages can be cleanly imported,
export expected symbols, and declare consistent versioning.
"""

import pytest

import casefile
from casefile import __version__


@pytest.mark.unit
def test_package_version() -> None:
    """Verify package version matches Phase 1 milestone."""
    assert __version__ == "0.1.0"
    assert casefile.__version__ == "0.1.0"


@pytest.mark.unit
def test_root_exports() -> None:
    """Verify root package exports essential contract symbols."""
    assert hasattr(casefile, "ClaimInput")
    assert hasattr(casefile, "WorkflowResult")
    assert hasattr(casefile, "WorkflowState")
    assert set(casefile.__all__) == {"__version__", "ClaimInput", "WorkflowResult", "WorkflowState"}


@pytest.mark.unit
def test_models_exports() -> None:
    """Verify casefile.models exports all typed inter-agent contracts."""
    import casefile.models

    expected_exports = {
        "ClaimInput",
        "ExtractionRequest",
        "ExtractionResult",
        "InvestigationRequest",
        "InvestigationResult",
        "ReviewRequest",
        "ReviewResult",
        "WorkflowResult",
        "WorkflowState",
        "BudgetState",
        "Checkpoint",
    }
    for model_name in expected_exports:
        assert hasattr(casefile.models, model_name)
