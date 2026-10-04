"""
Authentication and Role-Based Access Control (RBAC) boundary (Phase 7).

Provides:
- Strongly-typed operator roles (VIEWER, OPERATOR, CLAIM_REVIEWER, SENIOR_REVIEWER, CLAIM_SUPERVISOR, ADMIN)
- Explicit permissions matrix
- Principal abstraction carrying identity, role, and granted permissions
- Pluggable AuthProvider with deterministic dev/test provider
- FastAPI dependencies for authentication and authorization enforcement
- Strict server-side security: human approval requires human roles, agents are excluded
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from typing import Annotated, Protocol

from fastapi import Depends, Header, HTTPException, status
from pydantic import BaseModel, Field


class Role(str, Enum):
    """Operator and user roles for CASEFILE system access."""

    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    CLAIM_REVIEWER = "CLAIM_REVIEWER"
    SENIOR_REVIEWER = "SENIOR_REVIEWER"
    CLAIM_SUPERVISOR = "CLAIM_SUPERVISOR"
    ADMIN = "ADMIN"


class Permission(str, Enum):
    """Granular system permissions."""

    READ_WORKFLOW = "workflow:read"
    READ_HISTORY = "history:read"
    READ_EVALUATION = "evaluation:read"
    READ_HEALTH = "health:read"
    READ_METRICS = "metrics:read"
    SUBMIT_CLAIM = "claim:submit"
    INSPECT_COMPONENTS = "components:inspect"
    REPLAY_WORKFLOW = "workflow:replay"
    VIEW_APPROVALS = "approval:view"
    DECIDE_APPROVAL = "approval:decide"
    VIEW_AUDIT = "audit:view"
    ADMIN_OPERATIONS = "admin:all"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.READ_WORKFLOW,
        Permission.READ_HISTORY,
        Permission.READ_EVALUATION,
        Permission.READ_HEALTH,
        Permission.READ_METRICS,
    },
    Role.OPERATOR: {
        Permission.READ_WORKFLOW,
        Permission.READ_HISTORY,
        Permission.READ_EVALUATION,
        Permission.READ_HEALTH,
        Permission.READ_METRICS,
        Permission.SUBMIT_CLAIM,
        Permission.INSPECT_COMPONENTS,
        Permission.REPLAY_WORKFLOW,
        Permission.VIEW_APPROVALS,
        Permission.VIEW_AUDIT,
    },
    Role.CLAIM_REVIEWER: {
        Permission.READ_WORKFLOW,
        Permission.READ_HISTORY,
        Permission.READ_EVALUATION,
        Permission.READ_HEALTH,
        Permission.READ_METRICS,
        Permission.VIEW_APPROVALS,
        Permission.DECIDE_APPROVAL,
    },
    Role.SENIOR_REVIEWER: {
        Permission.READ_WORKFLOW,
        Permission.READ_HISTORY,
        Permission.READ_EVALUATION,
        Permission.READ_HEALTH,
        Permission.READ_METRICS,
        Permission.VIEW_APPROVALS,
        Permission.DECIDE_APPROVAL,
    },
    Role.CLAIM_SUPERVISOR: {
        Permission.READ_WORKFLOW,
        Permission.READ_HISTORY,
        Permission.READ_EVALUATION,
        Permission.READ_HEALTH,
        Permission.READ_METRICS,
        Permission.SUBMIT_CLAIM,
        Permission.INSPECT_COMPONENTS,
        Permission.REPLAY_WORKFLOW,
        Permission.VIEW_APPROVALS,
        Permission.DECIDE_APPROVAL,
        Permission.VIEW_AUDIT,
    },
    Role.ADMIN: set(Permission),
}

# Role hierarchy for approval authority
APPROVAL_ROLE_LEVELS: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.OPERATOR: 0,
    Role.CLAIM_REVIEWER: 10,
    Role.SENIOR_REVIEWER: 20,
    Role.CLAIM_SUPERVISOR: 30,
    Role.ADMIN: 100,
}

REQUIRED_ROLE_LEVELS: dict[str, int] = {
    "HUMAN": 10,
    "CLAIM_REVIEWER": 10,
    "SENIOR_REVIEWER": 20,
    "CLAIM_SUPERVISOR": 30,
    "ADMIN": 100,
}


class Principal(BaseModel):
    """Authenticated operator principal."""

    model_config = {"frozen": True}

    principal_id: str = Field(..., description="Unique principal identifier")
    role: Role = Field(..., description="Assigned authorization role")
    display_name: str = Field(default="", description="Human-readable name")
    auth_method: str = Field(default="token", description="Authentication mechanism")
    permissions: set[str] = Field(default_factory=set, description="Granted permissions")

    def has_permission(self, permission: Permission | str) -> bool:
        perm_val = permission.value if isinstance(permission, Permission) else str(permission)
        return perm_val in self.permissions or Permission.ADMIN_OPERATIONS.value in self.permissions

    def can_decide_approval(self, required_role: str | None = None) -> bool:
        """Verify the principal has approval authority meeting or exceeding the required level."""
        if not self.has_permission(Permission.DECIDE_APPROVAL):
            return False
        if not required_role:
            return True
        req_level = REQUIRED_ROLE_LEVELS.get(required_role.upper(), 10)
        curr_level = APPROVAL_ROLE_LEVELS.get(self.role, 0)
        return curr_level >= req_level


class AuthProvider(Protocol):
    """Abstract authentication provider."""

    def authenticate(
        self,
        authorization: str | None = None,
        x_principal_id: str | None = None,
        x_principal_role: str | None = None,
        x_api_key: str | None = None,
    ) -> Principal | None: ...


# Known pre-configured tokens for development / tests
DEV_TOKENS: dict[str, tuple[str, Role, str]] = {
    "dev-admin-token": ("admin-user", Role.ADMIN, "System Administrator"),
    "dev-reviewer-token": ("reviewer-alice", Role.CLAIM_REVIEWER, "Alice Reviewer"),
    "dev-senior-token": ("reviewer-bob", Role.SENIOR_REVIEWER, "Bob Senior Reviewer"),
    "dev-operator-token": ("operator-charlie", Role.OPERATOR, "Charlie Operator"),
    "dev-viewer-token": ("viewer-dave", Role.VIEWER, "Dave Viewer"),
    "dev-supervisor-token": ("supervisor-eve", Role.CLAIM_SUPERVISOR, "Eve Supervisor"),
}


class ProductionAuthProvider:
    """Production authentication provider.

    Rejects hardcoded development tokens and strictly prohibits arbitrary
    header-based role spoofing (X-Principal-Role / X-Principal-Id).
    Authenticates only verified production bearer tokens or API keys.
    """

    def __init__(
        self,
        token_principals: dict[str, tuple[str, Role, str]] | None = None,
    ) -> None:
        self.token_principals = token_principals or {}

    def authenticate(
        self,
        authorization: str | None = None,
        x_principal_id: str | None = None,
        x_principal_role: str | None = None,
        x_api_key: str | None = None,
    ) -> Principal | None:
        # Header spoofing is strictly prohibited in production
        token: str | None = None
        if authorization:
            parts = authorization.strip().split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]
        elif x_api_key:
            token = x_api_key.strip()

        if not token:
            return None

        # Disallow development tokens
        if token.startswith("dev-") or token in DEV_TOKENS:
            return None

        if token in self.token_principals:
            pid, role, name = self.token_principals[token]
            perms = {p.value for p in ROLE_PERMISSIONS[role]}
            return Principal(
                principal_id=pid,
                role=role,
                display_name=name,
                auth_method="production_token",
                permissions=perms,
            )
        return None


class DevAuthProvider:
    """Deterministic development and test authentication provider."""

    def authenticate(
        self,
        authorization: str | None = None,
        x_principal_id: str | None = None,
        x_principal_role: str | None = None,
        x_api_key: str | None = None,
    ) -> Principal | None:
        import os

        if os.environ.get("CASEFILE_ENV", "").lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Security violation: DevAuthProvider is prohibited in production mode.",
            )

        # 1. Direct Bearer token check
        if authorization:
            parts = authorization.strip().split()
            if len(parts) == 2 and parts[0].lower() == "bearer":
                token = parts[1]
                if token in DEV_TOKENS:
                    pid, role, name = DEV_TOKENS[token]
                    perms = {p.value for p in ROLE_PERMISSIONS[role]}
                    return Principal(
                        principal_id=pid,
                        role=role,
                        display_name=name,
                        auth_method="bearer_token",
                        permissions=perms,
                    )
                # Unrecognized bearer token
                return None

        # 2. API Key header
        if x_api_key:
            if x_api_key in DEV_TOKENS:
                pid, role, name = DEV_TOKENS[x_api_key]
                perms = {p.value for p in ROLE_PERMISSIONS[role]}
                return Principal(
                    principal_id=pid,
                    role=role,
                    display_name=name,
                    auth_method="api_key",
                    permissions=perms,
                )
            return None

        # 3. Explicit Header identity (used in tests and internal service mesh)
        if x_principal_id and x_principal_role:
            try:
                role_enum = Role(x_principal_role.upper())
            except ValueError:
                return None
            perms = {p.value for p in ROLE_PERMISSIONS[role_enum]}
            return Principal(
                principal_id=x_principal_id.strip(),
                role=role_enum,
                display_name=x_principal_id.strip(),
                auth_method="header_identity",
                permissions=perms,
            )

        return None


# Global active auth provider (can be replaced in test harnesses)
_GLOBAL_AUTH_PROVIDER: AuthProvider = DevAuthProvider()


def get_auth_provider() -> AuthProvider:
    return _GLOBAL_AUTH_PROVIDER


def set_auth_provider(provider: AuthProvider) -> None:
    global _GLOBAL_AUTH_PROVIDER
    _GLOBAL_AUTH_PROVIDER = provider


def get_current_principal(
    authorization: Annotated[str | None, Header()] = None,
    x_principal_id: Annotated[str | None, Header()] = None,
    x_principal_role: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    """FastAPI dependency: resolves and authenticates the requesting principal."""
    provider = get_auth_provider()
    principal = provider.authenticate(
        authorization=authorization,
        x_principal_id=x_principal_id,
        x_principal_role=x_principal_role,
        x_api_key=x_api_key,
    )
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_roles(*allowed_roles: Role) -> Callable[[Principal], Principal]:
    """FastAPI dependency factory: enforces one of the allowed roles."""

    def _role_checker(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        if principal.role not in allowed_roles and principal.role != Role.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal with role '{principal.role.value}' is not authorized for this operation",
            )
        return principal

    return _role_checker


def require_permission(permission: Permission) -> Callable[[Principal], Principal]:
    """FastAPI dependency factory: enforces a specific granular permission."""

    def _perm_checker(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> Principal:
        if not principal.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Principal does not possess required permission '{permission.value}'",
            )
        return principal

    return _perm_checker
