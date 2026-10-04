"""
CASEFILE contract envelope: the single typed boundary for agent-to-agent
handoff (AGENTS.md §Agent Handoffs, docs/agent-contracts.md §No Free-Form
Communication).

Eight handoffs are registered, each bound to one payload model and one
sender → recipient route. `wrap()` builds an envelope, `unwrap()` recovers
the payload only when the contract type, route, payload type, and schema
version all check out. Free-form dicts and wrong-type payloads are rejected.

Round-trip guarantee: envelope → JSON → envelope preserves semantic
equality (validated by tests).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, SerializeAsAny, model_validator

from casefile.models.contracts import (
    ExtractionRequest,
    ExtractionResult,
    InvestigationRequest,
    InvestigationResult,
    ReviewRequest,
    ReviewResult,
)
from casefile.models.domain import AgentType, HumanApproval, HumanApprovalRequest
from casefile.models.versioning import (
    SchemaVersion,
    validate_schema_version,
)

_T = TypeVar("_T", bound=BaseModel)

ContractType = Literal[
    "extraction_request",
    "extraction_result",
    "investigation_request",
    "investigation_result",
    "review_request",
    "review_result",
    "human_approval_request",
    "human_approval",
]


class UnknownContractTypeError(ValueError):
    """Raised when a contract type is not in the handoff registry."""


class ContractPayloadMismatchError(ValueError):
    """Raised when a payload does not match its declared contract type."""


@dataclass(frozen=True)
class HandoffRoute:
    """Sender, recipient, and payload model for one handoff."""

    sender: AgentType
    recipient: AgentType
    payload_model: type[BaseModel]


HANDOFF_ROUTES: dict[str, HandoffRoute] = {
    "extraction_request": HandoffRoute(
        sender=AgentType.SUPERVISOR,
        recipient=AgentType.EXTRACTOR,
        payload_model=ExtractionRequest,
    ),
    "extraction_result": HandoffRoute(
        sender=AgentType.EXTRACTOR,
        recipient=AgentType.SUPERVISOR,
        payload_model=ExtractionResult,
    ),
    "investigation_request": HandoffRoute(
        sender=AgentType.SUPERVISOR,
        recipient=AgentType.INVESTIGATOR,
        payload_model=InvestigationRequest,
    ),
    "investigation_result": HandoffRoute(
        sender=AgentType.INVESTIGATOR,
        recipient=AgentType.SUPERVISOR,
        payload_model=InvestigationResult,
    ),
    "review_request": HandoffRoute(
        sender=AgentType.SUPERVISOR,
        recipient=AgentType.REVIEWER,
        payload_model=ReviewRequest,
    ),
    "review_result": HandoffRoute(
        sender=AgentType.REVIEWER,
        recipient=AgentType.SUPERVISOR,
        payload_model=ReviewResult,
    ),
    "human_approval_request": HandoffRoute(
        sender=AgentType.REVIEWER,
        recipient=AgentType.HUMAN,
        payload_model=HumanApprovalRequest,
    ),
    "human_approval": HandoffRoute(
        sender=AgentType.HUMAN,
        recipient=AgentType.SUPERVISOR,
        payload_model=HumanApproval,
    ),
}


class ContractEnvelope(BaseModel):
    """Typed wrapper for every agent-to-agent handoff."""

    contract_type: ContractType
    schema_version: SchemaVersion = "1.0.0"
    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    sender: AgentType
    recipient: AgentType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID = Field(default_factory=uuid4)
    idempotency_key: str | None = None
    payload: SerializeAsAny[BaseModel]

    @model_validator(mode="before")
    @classmethod
    def _rehydrate_payload(cls, data: object) -> object:
        """Re-validate a raw payload dict against the registry model so JSON
        round-trips recover the concrete payload type instead of BaseModel."""
        if isinstance(data, dict):
            contract_type = data.get("contract_type")
            payload = data.get("payload")
            if isinstance(contract_type, str) and isinstance(payload, dict):
                route = HANDOFF_ROUTES.get(contract_type)
                if route is None:
                    raise UnknownContractTypeError(f"Unknown contract type {contract_type!r}")
                data = {**data, "payload": route.payload_model.model_validate(payload)}
        return data

    @model_validator(mode="after")
    def _validate_route_and_version(self) -> ContractEnvelope:
        route = HANDOFF_ROUTES.get(self.contract_type)
        if route is None:
            raise UnknownContractTypeError(f"Unknown contract type {self.contract_type!r}")
        if self.sender != route.sender or self.recipient != route.recipient:
            raise ContractPayloadMismatchError(
                f"{self.contract_type!r} must travel "
                f"{route.sender.value}->{route.recipient.value}, got "
                f"{self.sender.value}->{self.recipient.value}"
            )
        if not isinstance(self.payload, route.payload_model):
            raise ContractPayloadMismatchError(
                f"{self.contract_type!r} requires payload "
                f"{route.payload_model.__name__}, got {type(self.payload).__name__}"
            )
        payload_version = getattr(self.payload, "schema_version", None)
        if isinstance(payload_version, str):
            validate_schema_version(payload_version, artifact_type=self.contract_type)
        if self.idempotency_key is None:
            self.idempotency_key = f"{self.workflow_run_id}:{self.execution_id}"
        return self

    @classmethod
    def wrap(
        cls,
        contract_type: ContractType,
        payload: BaseModel,
        *,
        claim_id: UUID,
        workflow_run_id: UUID,
    ) -> ContractEnvelope:
        """Build an envelope for a handoff, deriving route from the registry."""
        route = HANDOFF_ROUTES.get(contract_type)
        if route is None:
            raise UnknownContractTypeError(f"Unknown contract type {contract_type!r}")
        return cls(
            contract_type=contract_type,
            claim_id=claim_id,
            workflow_run_id=workflow_run_id,
            sender=route.sender,
            recipient=route.recipient,
            payload=payload,
        )

    def unwrap(self, expected_model: type[_T]) -> _T:
        """Recover the payload, enforcing the registry type for the contract."""
        route = HANDOFF_ROUTES[self.contract_type]
        if not isinstance(self.payload, route.payload_model) or not isinstance(
            self.payload, expected_model
        ):
            raise ContractPayloadMismatchError(
                f"Cannot unwrap {self.contract_type!r} as {expected_model.__name__}"
            )
        return self.payload
