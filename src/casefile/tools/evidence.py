"""
Normalized evidence retrieval: structured items with provenance.

Each item carries identity, source, claim scope, quality, value,
provenance, and version. No free-form blobs as the only representation.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from casefile.models.domain import EvidenceItem, EvidenceSourceType
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool

__all__ = [
    "EvidenceItem",
    "EvidenceSourceType",
    "EvidenceLookupInput",
    "EvidenceLookupOutput",
    "EvidenceLookupTool",
]


class EvidenceLookupInput(BaseModel):
    """Claim-scoped evidence query with bounded result count."""

    model_config = {"frozen": True}

    claim_ref: str
    source_types: tuple[EvidenceSourceType, ...] = ()
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("claim_ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim_ref cannot be empty")
        if len(stripped) > 64:
            raise ValueError("claim_ref exceeds 64 characters")
        return stripped


class EvidenceLookupOutput(BaseModel):
    """Deterministically ordered evidence items for one claim."""

    model_config = {"frozen": True}

    claim_ref: str
    items: tuple[EvidenceItem, ...] = ()
    total_count: int = Field(default=0, ge=0)
    schema_version: SchemaVersion = "1.0.0"


class EvidenceLookupTool(Tool[EvidenceLookupInput, EvidenceLookupOutput]):
    """Assemble normalized evidence for a claim from fixture records."""

    name = "evidence_lookup"
    version = "1.0.0"
    description = "Retrieve normalized, provenance-tagged evidence for a claim"
    timeout_seconds = 5.0

    input_model = EvidenceLookupInput
    output_model = EvidenceLookupOutput

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE

    def run(self, tool_input: EvidenceLookupInput, ctx: ToolContext) -> EvidenceLookupOutput:
        """Collect document/policy/estimate/history/fraud evidence in order."""
        from casefile.tools.fixtures import stable_digest

        items: list[EvidenceItem] = []
        wanted = set(tool_input.source_types) or set(EvidenceSourceType)

        def add(
            source_type: EvidenceSourceType,
            source: str,
            value: str,
            confidence: float,
            provenance: str,
        ) -> None:
            if source_type not in wanted:
                return
            seed = f"{tool_input.claim_ref}:{source_type.value}:{source}:{value}"
            items.append(
                EvidenceItem(
                    evidence_id=UUID(hex=stable_digest(seed)[:32]),
                    source=source,
                    source_type=source_type,
                    claim_ref=tool_input.claim_ref,
                    confidence=confidence,
                    value=value,
                    provenance=provenance,
                )
            )

        for doc in self._store.documents_for_claim(tool_input.claim_ref):
            add(
                EvidenceSourceType.DOCUMENT,
                doc.document_ref,
                f"{doc.document_type.value}: {doc.filename}",
                0.9,
                f"fixture:{doc.source}",
            )
        scenario = self._store.scenario(tool_input.claim_ref)
        if scenario is not None:
            policy = self._store.get_policy(scenario["policy"])
            if policy is not None:
                add(
                    EvidenceSourceType.POLICY_RECORD,
                    policy.policy_number,
                    f"Policy {policy.policy_number} active={policy.is_active}",
                    1.0,
                    "fixture:policies",
                )
        for est in self._store.estimates_for_claim(tool_input.claim_ref):
            add(
                EvidenceSourceType.ESTIMATE_RECORD,
                est.estimate_ref,
                f"Estimate {est.estimate_ref} declared={est.declared_total.amount}",
                0.85,
                f"fixture:{est.source}",
            )
        profile = self._store.fraud_profile(tool_input.claim_ref)
        if profile is not None and profile.indicators:
            add(
                EvidenceSourceType.FRAUD_PROFILE,
                tool_input.claim_ref,
                f"{len(profile.indicators)} fraud indicators",
                0.7,
                "fixture:fraud-profiles",
            )
        ordered = sorted(items, key=lambda item: (item.source_type.value, item.source))
        selected = ordered[: tool_input.limit]
        return EvidenceLookupOutput(
            claim_ref=tool_input.claim_ref,
            items=tuple(selected),
            total_count=len(ordered),
        )
