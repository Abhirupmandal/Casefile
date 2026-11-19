"""
Policy lookup tool: read-only structured policy retrieval.

Returns the typed Policy domain model or a typed not-found result.
Agents receive domain objects, never database rows. Policies cannot be
modified through this tool.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, field_validator

from casefile.models.domain import Policy
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool


class PolicyLookupInput(BaseModel):
    """Policy number to look up (normalized to upper case)."""

    model_config = {"frozen": True}

    policy_number: str

    @field_validator("policy_number")
    @classmethod
    def _normalize(cls, value: str) -> str:
        stripped = value.strip().upper()
        if not stripped:
            raise ValueError("policy_number cannot be empty")
        if len(stripped) > 64:
            raise ValueError("policy_number exceeds 64 characters")
        return stripped


class PolicyLookupOutput(BaseModel):
    """Structured policy result or typed not-found."""

    model_config = {"frozen": True}

    found: bool
    policy: Policy | None = None
    schema_version: SchemaVersion = "1.0.0"


class PolicyLookupTool(Tool[PolicyLookupInput, PolicyLookupOutput]):
    """Look up policy identity, coverage, dates, limits, and constraints."""

    name = "policy_lookup"
    version = "1.0.0"
    description = "Look up insurance policy details including coverage and limits"
    timeout_seconds = 5.0
    cache_ttl = 3600

    input_model = PolicyLookupInput
    output_model = PolicyLookupOutput

    def __init__(
        self,
        store: FixtureStore | None = None,
        client: Any = None,
    ) -> None:
        self._store = store or DEFAULT_STORE
        self._client = client

    def run(self, tool_input: PolicyLookupInput, ctx: ToolContext) -> PolicyLookupOutput:
        """Return the typed policy or a not-found result."""
        if self._client is not None:
            from casefile.tools.adapters.errors import ExternalServiceError
            from casefile.tools.adapters.mapping import map_external_error_to_tool_failure

            try:
                data = self._client.get_policy(tool_input.policy_number)
                if data is None:
                    return PolicyLookupOutput(found=False)
                policy = Policy.model_validate(data)
                return PolicyLookupOutput(found=True, policy=policy)
            except ExternalServiceError as exc:
                raise map_external_error_to_tool_failure(exc, self.name) from exc
            except Exception as exc:
                from casefile.tools.contracts import ToolError, ToolErrorCode, ToolFailureError

                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.VALIDATION_FAILURE,
                        message=f"Failed to parse policy data from external service: {exc}",
                        retryable=False,
                    )
                ) from exc

        stored_policy: Policy | None = self._store.get_policy(tool_input.policy_number)
        if stored_policy is None:
            return PolicyLookupOutput(found=False)
        return PolicyLookupOutput(found=True, policy=stored_policy)
