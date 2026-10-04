# Authentication & Role-Based Access Control (RBAC) (Phase 7)

## Authentication Abstraction

CASEFILE abstracts authentication through a clean interface:
- **`Principal`**: Strongly-typed caller identity containing `principal_id`, `role`, `display_name`, `authentication_method`, and granted `permissions`.
- **`AuthProvider`**: Protocol for token verification and principal resolution.
- **`DevAuthProvider`**: Deterministic test provider mapping pre-defined developer tokens to distinct roles and permissions. Does not store plaintext credentials or invoke external network calls.

---

## Role Hierarchy & Permissions

CASEFILE enforces 6 explicit operator roles:

```
ADMIN
  ▲
CLAIM_SUPERVISOR
  ▲
SENIOR_REVIEWER
  ▲
CLAIM_REVIEWER
  ▲
OPERATOR
  ▲
VIEWER
```

### Authorization Matrix

| Permission | VIEWER | OPERATOR | CLAIM_REVIEWER | SENIOR_REVIEWER | CLAIM_SUPERVISOR | ADMIN |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| `WORKFLOW_READ` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `WORKFLOW_HISTORY_READ` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `EVALUATION_READ` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `METRICS_READ` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `CLAIM_SUBMIT` | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| `TOOL_TELEMETRY_READ` | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| `AGENT_TELEMETRY_READ` | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| `CHECKPOINT_READ` | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| `REPLAY_EXECUTE` | ❌ | ✅ | ❌ | ❌ | ❌ | ✅ |
| `APPROVAL_READ` | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ |
| `APPROVAL_DECIDE` | ❌ | ❌ | ✅ | ✅ | ✅ | ✅ |
| `AUDIT_READ` | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## Security Invariants

1. **Server-Side Enforcement**: The frontend is treated as completely untrusted. All role checks and approval validation occur in the backend service layer and FastAPI dependencies.
2. **Exclusion of Autonomous Agents**: Autonomous agent identities (e.g. `extractor`, `investigator`, `reviewer`) are strictly barred from the `APPROVAL_DECIDE` permission. Human-in-the-loop gates cannot be decided by automated agents.
3. **Self-Approval Protection**: A requester cannot approve their own claim if recorded as the submitting operator.
4. **Expiration & Concurrency Guard**: Stale approvals and approvals exceeding their deadline are rejected at the domain boundary with typed error codes (`EXPIRED_REQUEST`, `CONFLICT`).
