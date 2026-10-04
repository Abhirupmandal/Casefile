"""
CASEFILE durable storage package (Phase 6).

Typed repository protocols + SQLAlchemy implementations, explicit
domain/database mappings, unit-of-work transactions, a repository-backed
tool data source, and the synthetic seed dataset. Agents and tools never
see sessions, connections, or SQL.
"""

from casefile.storage.errors import (
    PersistenceError,
    PersistenceErrorCode,
    map_mapping_failure,
    map_transaction_failure,
    map_unavailable,
)
from casefile.storage.repositories import (
    AgentExecutionRepository,
    ApprovalRepository,
    AuditRepository,
    BudgetRepository,
    ClaimRepository,
    DamageEstimateRepository,
    DocumentRepository,
    EvidenceRepository,
    FraudRepository,
    PolicyRepository,
    PriorClaimRepository,
    SqlAgentExecutionRepository,
    SqlApprovalRepository,
    SqlAuditRepository,
    SqlBudgetRepository,
    SqlClaimRepository,
    SqlDamageEstimateRepository,
    SqlDocumentRepository,
    SqlEvidenceRepository,
    SqlFraudRepository,
    SqlPolicyRepository,
    SqlPriorClaimRepository,
    SqlWorkflowRunRepository,
    WorkflowRunRepository,
)
from casefile.storage.seed import SCENARIO_AMOUNTS, seed_synthetic_dataset, synthetic_uuid
from casefile.storage.unit_of_work import UnitOfWork

__all__ = [
    "PersistenceError",
    "PersistenceErrorCode",
    "map_mapping_failure",
    "map_transaction_failure",
    "map_unavailable",
    "AgentExecutionRepository",
    "ApprovalRepository",
    "AuditRepository",
    "BudgetRepository",
    "ClaimRepository",
    "DamageEstimateRepository",
    "DocumentRepository",
    "EvidenceRepository",
    "FraudRepository",
    "PolicyRepository",
    "PriorClaimRepository",
    "SqlAgentExecutionRepository",
    "SqlApprovalRepository",
    "SqlAuditRepository",
    "SqlBudgetRepository",
    "SqlClaimRepository",
    "SqlDamageEstimateRepository",
    "SqlDocumentRepository",
    "SqlEvidenceRepository",
    "SqlFraudRepository",
    "SqlPolicyRepository",
    "SqlPriorClaimRepository",
    "SqlWorkflowRunRepository",
    "WorkflowRunRepository",
    "SCENARIO_AMOUNTS",
    "seed_synthetic_dataset",
    "synthetic_uuid",
    "UnitOfWork",
]
