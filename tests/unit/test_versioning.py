"""
Unit Tests: Contract versioning strategy (docs/versioning.md).

Proves version fields are present and validated, compatibility rules hold,
and malformed/unknown/incompatible versions are rejected with clear errors.
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from casefile.models.domain import Claim, ClaimType, Money
from casefile.models.versioning import (
    ARTIFACT_VERSIONS,
    IncompatibleSchemaVersionError,
    is_compatible,
    parse_version,
    validate_schema_version,
)


@pytest.mark.unit
class TestParseVersion:
    def test_valid_versions(self) -> None:
        assert parse_version("1.0.0") == (1, 0, 0)
        assert parse_version("2.11.3") == (2, 11, 3)

    def test_malformed_rejected(self) -> None:
        for bad in ["1.0", "1", "", "1.0.0.0", "a.b.c", "1.-1.0", "v1.0.0"]:
            with pytest.raises(IncompatibleSchemaVersionError):
                parse_version(bad)


@pytest.mark.unit
class TestCompatibility:
    def test_same_version_compatible(self) -> None:
        assert is_compatible("1.0.0", "1.0.0", "1.0.0") is True

    def test_patch_always_compatible(self) -> None:
        assert is_compatible("1.0.7", "1.0.0", "1.0.0") is True

    def test_major_mismatch_incompatible(self) -> None:
        assert is_compatible("2.0.0", "1.0.0", "1.0.0") is False

    def test_older_than_minimum_incompatible(self) -> None:
        assert is_compatible("1.0.0", "1.2.0", "1.1.0") is False

    def test_newer_minor_than_current_incompatible(self) -> None:
        assert is_compatible("1.3.0", "1.2.0", "1.0.0") is False


@pytest.mark.unit
class TestValidateSchemaVersion:
    def test_current_version_accepted(self) -> None:
        for artifact in ARTIFACT_VERSIONS:
            assert validate_schema_version("1.0.0", artifact_type=artifact) == "1.0.0"

    def test_unknown_artifact_rejected(self) -> None:
        with pytest.raises(IncompatibleSchemaVersionError, match="Unknown artifact type"):
            validate_schema_version("1.0.0", artifact_type="telepathy")

    def test_major_bump_rejected_with_message(self) -> None:
        with pytest.raises(IncompatibleSchemaVersionError, match="major version must match"):
            validate_schema_version("2.0.0", artifact_type="claim")

    def test_malformed_rejected(self) -> None:
        with pytest.raises(IncompatibleSchemaVersionError, match="Malformed"):
            validate_schema_version("1.0", artifact_type="claim")

    def test_models_carry_validated_versions(self) -> None:
        claim = Claim(
            external_claim_id="EXT-V",
            policy_number="POL-V",
            customer_id="C-V",
            claim_type=ClaimType.LIABILITY,
            date_of_loss=date(2026, 9, 1),
            date_reported=date(2026, 9, 2),
            description="Version probe",
            claim_amount=Money(amount=Decimal("10.00")),
        )
        assert validate_schema_version(claim.schema_version, artifact_type="claim") == "1.0.0"
        with pytest.raises(ValidationError):
            Claim.model_validate({**claim.model_dump(mode="json"), "schema_version": "nope"})
