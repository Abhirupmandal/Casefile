# CASEFILE Versioning Strategy

## Overview

CASEFILE requires comprehensive versioning to support evolution without breaking stored workflows, checkpoints, and evaluation datasets. This document defines the versioning approach for all versioned artifacts.

## Versioning Philosophy

### 1. Semantic Versioning

All versioned components use semantic versioning (MAJOR.MINOR.PATCH):
- MAJOR: Breaking changes
- MINOR: New features, backward compatible
- PATCH: Bug fixes, backward compatible

### 2. Version Everything

Every artifact that can change independently is versioned:
- Workflow definitions
- State schemas
- Agent contracts
- Prompts
- Model configurations
- Tool contracts
- Evaluation datasets

### 3. Backward Compatibility

Stored data (checkpoints, audit logs) must remain readable after upgrades.

### 4. Explicit Migration

When breaking changes are necessary, explicit migration paths are provided.

## Versioned Artifacts

### Artifact Registry

```python
from typing import Dict, Type
from dataclasses import dataclass


@dataclass
class VersionedArtifact:
    """
    Base class for versioned artifacts.
    """

    artifact_type: str
    schema_version: str
    min_compatible_version: str
    deprecated: bool = False
    deprecation_date: Optional[datetime] = None
    removal_date: Optional[datetime] = None


VERSIONED_ARTIFACTS: Dict[str, VersionedArtifact] = {
    # Core schemas
    "claim_input": VersionedArtifact(
        artifact_type="claim_input",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "workflow_state": VersionedArtifact(
        artifact_type="workflow_state",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "checkpoint": VersionedArtifact(
        artifact_type="checkpoint",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "audit_event": VersionedArtifact(
        artifact_type="audit_event",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),

    # Agent contracts
    "extraction_request": VersionedArtifact(
        artifact_type="extraction_request",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "extraction_result": VersionedArtifact(
        artifact_type="extraction_result",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "investigation_request": VersionedArtifact(
        artifact_type="investigation_request",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "investigation_result": VersionedArtifact(
        artifact_type="investigation_result",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "review_request": VersionedArtifact(
        artifact_type="review_request",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "review_result": VersionedArtifact(
        artifact_type="review_result",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),

    # Tool contracts
    "policy_lookup_input": VersionedArtifact(
        artifact_type="policy_lookup_input",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "policy_lookup_output": VersionedArtifact(
        artifact_type="policy_lookup_output",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),

    # Evaluation
    "evaluation_dataset": VersionedArtifact(
        artifact_type="evaluation_dataset",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
    "evaluation_test_case": VersionedArtifact(
        artifact_type="evaluation_test_case",
        schema_version="1.0.0",
        min_compatible_version="1.0.0"
    ),
}
```

## Schema Version Compatibility

### Compatibility Matrix

```python
class SchemaCompatibilityChecker:
    """
    Checks compatibility between schema versions.
    """

    @staticmethod
    def is_compatible(
        stored_version: str,
        current_version: str,
        min_compatible: str
    ) -> bool:
        """
        Check if stored data is compatible with current schema.

        Rules:
        - Major version must match
        - Stored minor version must be >= min_compatible minor
        - Patch versions are always compatible within major.minor
        """

        stored = parse_version(stored_version)
        current = parse_version(current_version)
        minimum = parse_version(min_compatible)

        # Major version must match
        if stored[0] != current[0]:
            return False

        # Must be at least minimum compatible version
        if stored[1] < minimum[1]:
            return False

        # Minor version can be older or same
        if stored[1] > current[1]:
            return False

        return True

    @staticmethod
    def requires_migration(
        stored_version: str,
        current_version: str
    ) -> bool:
        """
        Check if stored data requires migration.

        Returns True if migration is needed before use.
        """

        stored = parse_version(stored_version)
        current = parse_version(current_version)

        # Same version - no migration
        if stored == current:
            return False

        # Minor version difference - may need migration
        if stored[0] == current[0] and stored[1] != current[1]:
            return True

        return False


def parse_version(version: str) -> tuple:
    """Parse semantic version string."""
    parts = version.split('.')
    return (int(parts[0]), int(parts[1]), int(parts[2]))
```

## Migration Framework

### Migration Interface

```python
from abc import ABC, abstractmethod
from typing import Any, Dict


class Migration(ABC):
    """
    Base class for schema migrations.
    """

    @property
    @abstractmethod
    def from_version(self) -> str:
        """Version migrating from."""
        ...

    @property
    @abstractmethod
    def to_version(self) -> str:
        """Version migrating to."""
        ...

    @property
    @abstractmethod
    def artifact_type(self) -> str:
        """Artifact type being migrated."""
        ...

    @abstractmethod
    def migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate data from from_version to to_version.

        Args:
            data: Data in from_version format

        Returns:
            Data in to_version format
        """
        ...

    def validate(self, data: Dict[str, Any]) -> bool:
        """
        Validate data is in expected from_version format.
        """
        ...
```

### Migration Registry

```python
class MigrationRegistry:
    """
    Registry of all available migrations.
    """

    def __init__(self):
        self._migrations: Dict[str, List[Migration]] = {}

    def register(self, migration: Migration) -> None:
        """Register a migration."""

        key = f"{migration.artifact_type}:{migration.from_version}:{migration.to_version}"

        if migration.artifact_type not in self._migrations:
            self._migrations[migration.artifact_type] = []

        self._migrations[migration.artifact_type].append(migration)

    def get_migration_path(
        self,
        artifact_type: str,
        from_version: str,
        to_version: str
    ) -> List[Migration]:
        """
        Get the migration path from one version to another.

        Returns list of migrations to apply in order.
        """

        migrations = []
        current = from_version

        while current != to_version:
            # Find next migration
            for migration in self._migrations.get(artifact_type, []):
                if migration.from_version == current:
                    migrations.append(migration)
                    current = migration.to_version
                    break
            else:
                raise MigrationNotFoundError(
                    f"No migration path from {current} to {to_version} for {artifact_type}"
                )

        return migrations

    def migrate(
        self,
        artifact_type: str,
        data: Dict[str, Any],
        from_version: str,
        to_version: str
    ) -> Dict[str, Any]:
        """
        Migrate data through the migration path.
        """

        path = self.get_migration_path(artifact_type, from_version, to_version)

        result = data
        for migration in path:
            result = migration.migrate(result)

        return result


# Example migrations
class ExtractionResultV1_0_0_toV1_1_0(Migration):
    """
    Migrate ExtractionResult from 1.0.0 to 1.1.0.

    Changes:
    - Add 'confidence_by_field' to ExtractionConfidence
    - Add 'processing_notes' field
    """

    @property
    def from_version(self) -> str:
        return "1.0.0"

    @property
    def to_version(self) -> str:
        return "1.1.0"

    @property
    def artifact_type(self) -> str:
        return "extraction_result"

    def migrate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Perform migration."""

        # Add new fields with defaults
        if "confidence" in data:
            data["confidence"]["confidence_by_field"] = {}

        data["processing_notes"] = None

        # Update schema version
        data["schema_version"] = "1.1.0"

        return data
```

## Application Versioning

### Application Version

```python
import importlib.metadata


class ApplicationVersion:
    """
    Application version information.
    """

    def __init__(self):
        self.version = self._get_version()
        self.version_tuple = parse_version(self.version)

    def _get_version(self) -> str:
        """Get application version from package metadata."""
        try:
            return importlib.metadata.version("casefile")
        except importlib.metadata.PackageNotFoundError:
            return "0.0.0-dev"

    def __str__(self) -> str:
        return self.version

    def __repr__(self) -> str:
        return f"ApplicationVersion({self.version})"


# Global instance
VERSION = ApplicationVersion()
```

### Version in Checkpoints

```python
class CheckpointWithVersion(Checkpoint):
    """
    Checkpoint with application version for compatibility checking.
    """

    application_version: str = Field(
        default_factory=lambda: str(VERSION),
        description="Application version that created this checkpoint"
    )

    def is_compatible_with_current(self) -> bool:
        """Check if checkpoint is compatible with current application."""

        # Parse versions
        checkpoint_app = parse_version(self.application_version)
        current_app = VERSION.version_tuple

        # Major version must match
        if checkpoint_app[0] != current_app[0]:
            return False

        return True
```

## Prompt Versioning

### Prompt Registry

```python
class PromptVersion(BaseModel):
    """
    Versioned prompt template.
    """

    prompt_id: str
    version: str
    template: str
    variables: List[str]
    description: str

    # Metadata
    created_at: datetime
    author: str
    deprecated: bool = False
    deprecation_message: Optional[str] = None


class PromptRegistry:
    """
    Registry of versioned prompts.
    """

    def __init__(self):
        self._prompts: Dict[str, Dict[str, PromptVersion]] = {}

    def register(self, prompt: PromptVersion) -> None:
        """Register a prompt version."""

        if prompt.prompt_id not in self._prompts:
            self._prompts[prompt.prompt_id] = {}

        self._prompts[prompt.prompt_id][prompt.version] = prompt

    def get(
        self,
        prompt_id: str,
        version: Optional[str] = None
    ) -> PromptVersion:
        """
        Get a prompt by ID and version.

        If version is None, returns the latest non-deprecated version.
        """

        if prompt_id not in self._prompts:
            raise PromptNotFoundError(f"Prompt '{prompt_id}' not found")

        if version:
            return self._prompts[prompt_id][version]

        # Get latest non-deprecated
        versions = sorted(
            self._prompts[prompt_id].values(),
            key=lambda p: parse_version(p.version),
            reverse=True
        )

        for prompt in versions:
            if not prompt.deprecated:
                return prompt

        raise NoValidPromptVersionError(f"No valid version for '{prompt_id}'")


# Example prompt definitions
EXTRACTION_PROMPT_V1 = PromptVersion(
    prompt_id="extraction_synthesis",
    version="1.0.0",
    template="""
Extract the following information from the claim documents:

Documents:
{documents}

Extract:
1. Vehicle information (make, model, year, VIN)
2. Driver information (name, license)
3. Accident details (date, location, description)
4. Damages (location, type, severity)
5. Witnesses (names, contact)

Respond in JSON format.
""",
    variables=["documents"],
    description="Initial extraction prompt",
    created_at=datetime(2024, 1, 1),
    author="system"
)

EXTRACTION_PROMPT_V2 = PromptVersion(
    prompt_id="extraction_synthesis",
    version="1.1.0",
    template="""
Extract the following information from the claim documents:

Documents:
{documents}

Extract:
1. Vehicle information (make, model, year, VIN, license plate)
2. Driver information (name, DOB, license number, address)
3. Accident details (date, time, location, description, weather)
4. Damages (location, type, severity, estimated cost)
5. Witnesses (names, contact, statement summary)
6. Repair estimates (source, date, amount, breakdown)

Respond in JSON format with confidence scores for each field.
""",
    variables=["documents"],
    description="Enhanced extraction with confidence scores",
    created_at=datetime(2024, 2, 1),
    author="system"
)
```

## Model Configuration Versioning

```python
class ModelConfiguration(BaseModel):
    """
    Versioned LLM model configuration.
    """

    config_id: str
    version: str

    # Provider settings
    provider: str
    model_name: str

    # Model parameters
    temperature: float = 0.0
    max_tokens: int = 4096
    top_p: float = 1.0

    # Budget settings
    cost_per_1k_input_tokens: Decimal
    cost_per_1k_output_tokens: Decimal

    # Metadata
    created_at: datetime
    deprecated: bool = False


class ModelConfigRegistry:
    """
    Registry of model configurations.
    """

    CONFIGS = {
        "openai-gpt4o-v1": ModelConfiguration(
            config_id="default-extraction",
            version="1.0.0",
            provider="openai",
            model_name="gpt-4o",
            temperature=0.0,
            max_tokens=4096,
            cost_per_1k_input_tokens=Decimal("0.005"),
            cost_per_1k_output_tokens=Decimal("0.015"),
            created_at=datetime(2024, 1, 1)
        ),
        "openai-gpt4o-mini-v1": ModelConfiguration(
            config_id="default-investigation",
            version="1.0.0",
            provider="openai",
            model_name="gpt-4o-mini",
            temperature=0.0,
            max_tokens=2048,
            cost_per_1k_input_tokens=Decimal("0.00015"),
            cost_per_1k_output_tokens=Decimal("0.0006"),
            created_at=datetime(2024, 1, 1)
        ),
    }
```

## Evaluation Dataset Versioning

```python
class EvaluationDatasetVersion(BaseModel):
    """
    Versioned evaluation dataset.
    """

    dataset_id: str
    version: str
    name: str
    description: str

    # Test cases
    test_case_count: int
    checksum: str  # Hash of all test cases

    # Metadata
    created_at: datetime
    baseline: bool = False

    # Compatibility
    min_application_version: str
    schema_versions: Dict[str, str]  # artifact_type -> version


class DatasetRegistry:
    """
    Registry of evaluation datasets.
    """

    def get_dataset(
        self,
        dataset_id: str,
        version: Optional[str] = None
    ) -> EvaluationDataset:
        """Get a specific version of a dataset."""
        ...

    def get_latest_baseline(self) -> EvaluationDataset:
        """Get the latest baseline dataset."""
        ...
```

## Version Compatibility Matrix

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    VERSION COMPATIBILITY MATRIX                              │
│                                                                              │
│  Application │ Schema │ Checkpoint │ Prompt │ Model Config │ Dataset       │
│  Version     │ Version │ Compatible │ Ver.   │ Ver.         │ Ver.          │
│──────────────────────────────────────────────────────────────────────────────│
│  1.0.0       │ 1.0.0   │ ✓ Yes      │ 1.0.x  │ 1.0.x        │ 1.0.x         │
│  1.1.0       │ 1.1.0   │ ✓ Yes*     │ 1.0.x  │ 1.0.x        │ 1.0.x, 1.1.x  │
│  2.0.0       │ 2.0.0   │ ✗ No**     │ 2.0.x  │ 1.0.x, 2.0.x │ 2.0.x         │
│                                                                              │
│  * Requires migration                                                        │
│  ** Breaking change - cannot load old checkpoints                            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Deprecation Policy

### Deprecation Timeline

```python
class DeprecationPolicy:
    """
    Policy for deprecating versioned artifacts.
    """

    # Timeline for deprecation
    DEPRECATION_NOTICE_PERIOD_DAYS = 90  # 3 months
    DEPRECATION_REMOVAL_PERIOD_DAYS = 180  # 6 months

    @staticmethod
    def deprecate_artifact(
        artifact_type: str,
        version: str,
        reason: str
    ) -> None:
        """
        Mark an artifact as deprecated.
        """

        artifact = VERSIONED_ARTIFACTS[artifact_type]
        artifact.deprecated = True
        artifact.deprecation_date = datetime.utcnow()
        artifact.removal_date = datetime.utcnow() + timedelta(
            days=DeprecationPolicy.DEPRECATION_REMOVAL_PERIOD_DAYS
        )

        # Log deprecation
        logger.warning(
            "artifact_deprecated",
            artifact_type=artifact_type,
            version=version,
            reason=reason,
            removal_date=artifact.removal_date
        )

        # Emit event
        observability.emit_deprecation_event(
            artifact_type=artifact_type,
            version=version,
            reason=reason,
            removal_date=artifact.removal_date
        )
```

## Summary

The versioning strategy provides:
- Semantic versioning for all artifacts
- Schema compatibility checking
- Migration framework for upgrades
- Versioned prompts and model configurations
- Evaluation dataset versioning
- Deprecation policy and timeline
- Compatibility matrix documentation
