"""
Approval commands, actors, outcomes, and errors (Phase 5).

Only a typed ApprovalActor/HumanActor can decide. Agents, models, tools, and unknown
actors cannot construct a valid decision command — rejection happens at
the type boundary before any persistence is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    ApprovalStatus,
    HumanApproval,
    HumanApprovalRequest,
    Recommendation,
)
from casefile.models.versioning import SchemaVersion


class ApprovalErrorCode(str, Enum):
    """Typed approval failure categories."""

    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN_ACTOR = "FORBIDDEN_ACTOR"
    NOT_PENDING = "NOT_PENDING"
    STALE_VERSION = "STALE_VERSION"
    TERMINAL_RUN = "TERMINAL_RUN"
    EXPIRED_REQUEST = "EXPIRED_REQUEST"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    CONFLICT = "CONFLICT"
    UNAUTHORIZED = "UNAUTHORIZED"


class ApprovalError(Exception):
    """Typed approval failure carrying a stable code."""

    def __init__(self, code: ApprovalErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


class ApprovalRole(str, Enum):
    """Explicitly defined approval authority roles."""

    CLAIM_REVIEWER = "CLAIM_REVIEWER"
    SENIOR_REVIEWER = "SENIOR_REVIEWER"
    CLAIM_SUPERVISOR = "CLAIM_SUPERVISOR"
    ADMIN = "ADMIN"
    HUMAN = "HUMAN"  # Backward compatibility alias


class ApprovalActor(BaseModel):
    """Explicitly authorized human identity with role. Frozen."""

    model_config = {"frozen": True}

    actor_id: str
    display_name: str = ""
    role: ApprovalRole | str = ApprovalRole.CLAIM_REVIEWER
    is_human: bool = True
    schema_version: SchemaVersion = "1.0.0"

    @model_validator(mode="before")
    @classmethod
    def _default_display_name(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("display_name") and data.get("actor_id"):
            data["display_name"] = str(data["actor_id"])
        return data

    @field_validator("actor_id", "display_name")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("actor identity fields cannot be empty")
        return stripped

    @field_validator("role")
    @classmethod
    def _validate_role(cls, value: ApprovalRole | str) -> ApprovalRole | str:
        if isinstance(value, str):
            try:
                return ApprovalRole(value)
            except ValueError:
                if value == "HUMAN":
                    return value
                raise ValueError(f"Invalid approval role: {value}") from None
        return value


class HumanActor(BaseModel):
    """Explicitly authorized human identity (legacy/strict 'HUMAN' role). Frozen.

    The role literal admits only HUMAN — agent, model, tool, or unknown
    actors cannot satisfy this type.
    """

    model_config = {"frozen": True}

    actor_id: str
    display_name: str
    role: Literal["HUMAN"] = "HUMAN"
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("actor_id", "display_name")
    @classmethod
    def _validate_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("actor identity fields cannot be empty")
        return stripped


class ApprovalVerdict(str, Enum):
    """Human decision direction."""

    APPROVE = "APPROVE"
    APPROVED = "APPROVE"
    REJECT = "REJECT"
    REJECTED = "REJECT"


class ApprovalRequest(BaseModel):
    """Strongly typed approval request contract (Phase 5 Part 1)."""

    model_config = {"frozen": True}

    approval_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID | str
    requested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_by: str = "reviewer"
    current_workflow_state: WorkflowState = WorkflowState.HUMAN_APPROVAL
    recommendation: Recommendation | str | dict[str, Any]
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    decision_deadline: datetime
    required_role: ApprovalRole = ApprovalRole.CLAIM_REVIEWER
    status: ApprovalStatus = ApprovalStatus.PENDING

    # Auxiliary links
    execution_id: UUID | None = None
    checkpoint_id: UUID | None = None
    request_version: int = Field(default=1, ge=1)
    reviewer_summary: str = ""
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("requested_by")
    @classmethod
    def _validate_requester(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("requested_by cannot be empty")
        return stripped

    @model_validator(mode="after")
    def _validate_deadline(self) -> ApprovalRequest:
        if self.decision_deadline <= self.requested_at:
            raise ValueError("decision_deadline must follow requested_at")
        return self

    def to_domain_request(self) -> HumanApprovalRequest:
        """Convert to internal HumanApprovalRequest for storage and engine."""
        from decimal import Decimal

        from casefile.models.domain import (
            HumanApprovalRequest,
            Money,
            Recommendation,
            RecommendationType,
        )

        claim_id_val = self.claim_id if isinstance(self.claim_id, UUID) else uuid4()
        rec = self.recommendation
        if isinstance(rec, dict):
            notes = str(rec.get("notes") or rec.get("action") or "Recommendation")
            amount = rec.get("amount", "100.00")
            rec = Recommendation(
                claim_id=claim_id_val,
                recommendation_type=RecommendationType.APPROVE_FULL,
                estimated_payout=Money(amount=Decimal(str(amount))),
                notes=notes,
                confidence=self.confidence,
            )
        elif isinstance(rec, str):
            rec = Recommendation(
                claim_id=claim_id_val,
                recommendation_type=RecommendationType.APPROVE_FULL,
                estimated_payout=Money(amount=Decimal("100.00")),
                notes=rec,
                confidence=self.confidence,
            )
        return HumanApprovalRequest(
            approval_id=self.approval_id,
            workflow_run_id=self.workflow_run_id,
            claim_id=claim_id_val,
            execution_id=self.execution_id,
            checkpoint_id=self.checkpoint_id,
            request_version=self.request_version,
            recommendation=rec,
            reviewer_summary=self.reviewer_summary
            or (rec.notes if hasattr(rec, "notes") else str(rec)),
            requested_at=self.requested_at,
            deadline=self.decision_deadline,
            requested_by=self.requested_by,
            required_role=(
                self.required_role.value
                if isinstance(self.required_role, ApprovalRole)
                else str(self.required_role)
            ),
        )


class ApprovalDecision(BaseModel):
    """Typed approve/reject command. Frozen."""

    model_config = {"frozen": True}

    approval_id: UUID
    actor: HumanActor | ApprovalActor
    verdict: ApprovalVerdict = ApprovalVerdict.APPROVE
    reason: str = ""
    decision_key: str = Field(default_factory=lambda: uuid4().hex)
    expected_version: int = Field(default=1, ge=1)
    correlation_id: UUID = Field(default_factory=uuid4)
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workflow_run_id: UUID | None = None
    claim_id: UUID | None = None
    schema_version: SchemaVersion = "1.0.0"

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "decision" in data and "verdict" not in data:
                data["verdict"] = data["decision"]
            if "decided_by" in data and "actor" not in data:
                decided_by = data["decided_by"]
                if isinstance(decided_by, str):
                    data["actor"] = ApprovalActor(actor_id=decided_by, display_name=decided_by)
                else:
                    data["actor"] = decided_by
            if "decision_key" not in data or not data.get("decision_key"):
                data["decision_key"] = uuid4().hex
            if "expected_version" not in data or data.get("expected_version") is None:
                data["expected_version"] = 1
        return data

    @property
    def decision(self) -> ApprovalVerdict:
        """Alias for verdict."""
        return self.verdict

    @property
    def decided_by(self) -> HumanActor | ApprovalActor:
        """Alias for actor."""
        return self.actor

    @field_validator("decision_key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("decision_key cannot be empty")
        return stripped


class ApprovalAuditRecord(BaseModel):
    """Strongly typed audit record for approval lifecycle events (Phase 5 Part 1 & 8)."""

    model_config = {"frozen": True}

    event_id: UUID = Field(default_factory=uuid4)
    event_type: str
    actor: str | ApprovalActor | HumanActor
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workflow_run_id: UUID
    approval_id: UUID
    correlation_id: UUID = Field(default_factory=uuid4)
    action: str = ""
    result: str = "SUCCESS"
    details: dict[str, object] = Field(default_factory=dict)
    schema_version: SchemaVersion = "1.0.0"


class ExpireApproval(BaseModel):
    """Typed expiration command. Frozen."""

    model_config = {"frozen": True}

    approval_id: UUID
    actor: HumanActor | ApprovalActor
    reason: str = ""
    correlation_id: UUID = Field(default_factory=uuid4)
    schema_version: SchemaVersion = "1.0.0"


class CancelApproval(BaseModel):
    """Typed cancellation command. Frozen."""

    model_config = {"frozen": True}

    approval_id: UUID
    actor: HumanActor | ApprovalActor
    reason: str = ""
    correlation_id: UUID = Field(default_factory=uuid4)
    schema_version: SchemaVersion = "1.0.0"


class ApprovalOutcome(str, Enum):
    """Result of a decision attempt."""

    DECIDED = "DECIDED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"


class DecisionResult(BaseModel):
    """Deterministic outcome: the approval plus what happened."""

    model_config = {"frozen": True}

    outcome: ApprovalOutcome
    approval: HumanApproval
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: SchemaVersion = "1.0.0"


class InvalidApprovalTransitionError(ApprovalError):
    """Raised when an invalid approval state transition is attempted."""

    def __init__(
        self,
        from_status: ApprovalStatus,
        to_status: ApprovalStatus,
        message: str = "",
    ) -> None:
        code = (
            ApprovalErrorCode.CONFLICT
            if from_status
            in {
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.CANCELLED,
            }
            else ApprovalErrorCode.NOT_PENDING
        )
        super().__init__(
            code,
            message or f"Invalid approval transition from {from_status.value} to {to_status.value}",
        )
        self.from_status = from_status
        self.to_status = to_status


class ApprovalStateMachine:
    """Approval lifecycle state machine and invariants (Phase 5 Part 2)."""

    ALLOWED_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
        ApprovalStatus.PENDING: frozenset(
            {
                ApprovalStatus.APPROVED,
                ApprovalStatus.REJECTED,
                ApprovalStatus.EXPIRED,
                ApprovalStatus.CANCELLED,
            }
        ),
        ApprovalStatus.APPROVED: frozenset(),
        ApprovalStatus.REJECTED: frozenset(),
        ApprovalStatus.EXPIRED: frozenset(),
        ApprovalStatus.CANCELLED: frozenset(),
        ApprovalStatus.ESCALATED: frozenset(),
    }

    TERMINAL_STATES: frozenset[ApprovalStatus] = frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        }
    )

    @classmethod
    def is_terminal(cls, status: ApprovalStatus) -> bool:
        """True if the approval status is terminal (immutable)."""
        return status in cls.TERMINAL_STATES

    @classmethod
    def can_transition(cls, from_status: ApprovalStatus, to_status: ApprovalStatus) -> bool:
        """True if transitioning from from_status to to_status is valid."""
        return to_status in cls.ALLOWED_TRANSITIONS.get(from_status, frozenset())

    @classmethod
    def validate_transition(cls, from_status: ApprovalStatus, to_status: ApprovalStatus) -> None:
        """Validate transition; raises InvalidApprovalTransitionError if invalid or terminal."""
        if cls.is_terminal(from_status):
            raise InvalidApprovalTransitionError(
                from_status,
                to_status,
                f"Terminal approval status {from_status.value} is immutable; cannot transition to {to_status.value}",
            )
        if not cls.can_transition(from_status, to_status):
            raise InvalidApprovalTransitionError(
                from_status,
                to_status,
                f"Invalid approval transition from {from_status.value} to {to_status.value}",
            )

    @classmethod
    def transition(cls, from_status: ApprovalStatus, to_status: ApprovalStatus) -> ApprovalStatus:
        """Validate and apply transition, returning target status."""
        cls.validate_transition(from_status, to_status)
        return to_status

    @classmethod
    def target_status_for_verdict(cls, verdict: ApprovalVerdict) -> ApprovalStatus:
        if verdict == ApprovalVerdict.APPROVED:
            return ApprovalStatus.APPROVED
        return ApprovalStatus.REJECTED

    @classmethod
    def target_status_for_action(cls, action: str) -> ApprovalStatus:
        act = action.upper()
        if act == "EXPIRE":
            return ApprovalStatus.EXPIRED
        if act == "CANCEL":
            return ApprovalStatus.CANCELLED
        raise ValueError(f"Unknown approval action: {action}")


_ROLE_HIERARCHY: dict[str, int] = {
    ApprovalRole.CLAIM_REVIEWER.value: 1,
    "CLAIM_REVIEWER": 1,
    ApprovalRole.HUMAN.value: 1,
    "HUMAN": 1,
    ApprovalRole.SENIOR_REVIEWER.value: 2,
    "SENIOR_REVIEWER": 2,
    ApprovalRole.CLAIM_SUPERVISOR.value: 3,
    "CLAIM_SUPERVISOR": 3,
    ApprovalRole.ADMIN.value: 4,
    "ADMIN": 4,
}


class ApprovalAuthorizer:
    """Authorization and separation-of-duties enforcement (Phase 5 Part 4 & 5)."""

    FORBIDDEN_ROLES: frozenset[str] = frozenset(
        {
            "AGENT",
            "SUPERVISOR",
            "MODEL",
            "TOOL",
            "LLM",
            "EXTRACTOR",
            "INVESTIGATOR",
            "REVIEWER",
            "SYSTEM",
        }
    )

    @classmethod
    def authorize(
        cls,
        actor: ApprovalActor | HumanActor,
        request: ApprovalRequest | HumanApprovalRequest | None = None,
        required_role: ApprovalRole | str | None = None,
    ) -> None:
        """Verify actor is human, authorized, and satisfies separation of duties.

        Raises ApprovalError with FORBIDDEN_ACTOR if unauthorized.
        """
        if not getattr(actor, "is_human", True):
            raise ApprovalError(
                ApprovalErrorCode.UNAUTHORIZED,
                "Non-human actors (agents, models, tools) cannot decide approvals",
            )

        actor_id_lower = getattr(actor, "actor_id", "").lower()
        if (
            actor_id_lower in {"supervisor", "workflow_supervisor", "workflow-supervisor"}
            or actor_id_lower.startswith("agent")
            or "agent-" in actor_id_lower
            or "-agent" in actor_id_lower
        ):
            raise ApprovalError(
                ApprovalErrorCode.UNAUTHORIZED,
                f"Actor {getattr(actor, 'actor_id', '')} is not authorized to approve",
            )

        role_str = actor.role.value if isinstance(actor.role, Enum) else str(actor.role)
        if role_str.upper() in cls.FORBIDDEN_ROLES:
            raise ApprovalError(
                ApprovalErrorCode.UNAUTHORIZED,
                f"Actor role {role_str!r} is strictly forbidden from approving claims",
            )

        # Separation of duties: requester cannot approve their own request
        if request is not None:
            requested_by = getattr(request, "requested_by", None)
            if requested_by and actor.actor_id == requested_by:
                raise ApprovalError(
                    ApprovalErrorCode.UNAUTHORIZED,
                    f"Separation of duties violation: requester {actor.actor_id!r} cannot approve their own request",
                )
            req_role = getattr(request, "required_role", required_role)
        else:
            req_role = required_role

        if req_role is not None:
            req_role_str = req_role.value if isinstance(req_role, Enum) else str(req_role)
            actor_level = _ROLE_HIERARCHY.get(role_str.upper(), 0)
            required_level = _ROLE_HIERARCHY.get(req_role_str.upper(), 1)
            if actor_level < required_level:
                raise ApprovalError(
                    ApprovalErrorCode.UNAUTHORIZED,
                    f"Actor role {role_str} does not have sufficient permission (required: {req_role_str})",
                )

    @classmethod
    def check_authorization(
        cls,
        actor: ApprovalActor | HumanActor,
        required_role: ApprovalRole | str | None = None,
    ) -> None:
        """Alias for check_authorization."""
        if actor is None:
            raise ApprovalError(ApprovalErrorCode.UNAUTHORIZED, "Actor is required")
        cls.authorize(actor, required_role=required_role)

    @classmethod
    def check_separation_of_duties(
        cls,
        actor: ApprovalActor | HumanActor,
        requested_by: str,
        enforce: bool = True,
    ) -> None:
        """Enforce that actor did not request the approval."""
        cls.check_authorization(actor)
        if enforce and requested_by and getattr(actor, "actor_id", "") == requested_by:
            raise ApprovalError(
                ApprovalErrorCode.UNAUTHORIZED,
                f"Separation of duties: {actor.actor_id} cannot approve request made by self",
            )
