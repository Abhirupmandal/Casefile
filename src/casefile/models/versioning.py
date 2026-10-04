"""
CASEFILE contract versioning strategy implementation (docs/versioning.md).

Semantic versions (MAJOR.MINOR.PATCH) on every contract. Compatibility rules:
- Major version must match
- Stored minor must be >= minimum-compatible minor and <= current minor
- Patch versions are always compatible within major.minor
Malformed or incompatible versions raise with understandable messages.
Never silently accept a breaking version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

SchemaVersion = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]

CURRENT_SCHEMA_VERSION = "1.0.0"


class IncompatibleSchemaVersionError(ValueError):
    """Raised when a schema version is malformed or incompatible."""


@dataclass(frozen=True)
class ArtifactVersions:
    """Current and minimum-compatible versions for one artifact type."""

    current: str
    min_compatible: str


ARTIFACT_VERSIONS: dict[str, ArtifactVersions] = {
    "claim": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "claim_input": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "workflow_run": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "workflow_state": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "audit_event": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "checkpoint": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "extraction_request": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "extraction_result": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "investigation_request": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "investigation_result": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "review_request": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "review_result": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "human_approval_request": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
    "human_approval": ArtifactVersions(current="1.0.0", min_compatible="1.0.0"),
}


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse a MAJOR.MINOR.PATCH string or raise a clear error."""
    parts = version.split(".")
    if len(parts) != 3:
        raise IncompatibleSchemaVersionError(
            f"Malformed schema version {version!r}: expected MAJOR.MINOR.PATCH"
        )
    try:
        major, minor, patch = (int(part) for part in parts)
    except ValueError:
        raise IncompatibleSchemaVersionError(
            f"Malformed schema version {version!r}: components must be integers"
        ) from None
    if major < 0 or minor < 0 or patch < 0:
        raise IncompatibleSchemaVersionError(
            f"Malformed schema version {version!r}: components must be non-negative"
        )
    return (major, minor, patch)


def is_compatible(stored_version: str, current_version: str, min_compatible: str) -> bool:
    """Check stored data compatibility per docs/versioning.md rules."""
    stored = parse_version(stored_version)
    current = parse_version(current_version)
    minimum = parse_version(min_compatible)
    if stored[0] != current[0]:
        return False
    if stored[1] < minimum[1]:
        return False
    return stored[1] <= current[1]


def validate_schema_version(schema_version: str, *, artifact_type: str) -> str:
    """Validate a contract's schema version against the registry.

    Returns the version unchanged when compatible; raises
    IncompatibleSchemaVersionError with an understandable message otherwise.
    """
    versions = ARTIFACT_VERSIONS.get(artifact_type)
    if versions is None:
        raise IncompatibleSchemaVersionError(f"Unknown artifact type {artifact_type!r}")
    parsed = parse_version(schema_version)
    current = parse_version(versions.current)
    if parsed[0] != current[0]:
        raise IncompatibleSchemaVersionError(
            f"Incompatible schema version {schema_version!r} for {artifact_type!r}: "
            f"major version must match current {versions.current!r}"
        )
    if not is_compatible(schema_version, versions.current, versions.min_compatible):
        raise IncompatibleSchemaVersionError(
            f"Incompatible schema version {schema_version!r} for {artifact_type!r}: "
            f"minimum compatible version is {versions.min_compatible!r}"
        )
    return schema_version
