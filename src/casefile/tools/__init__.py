"""
CASEFILE secure typed tool layer (Phase 5, docs/tool-architecture.md).

Read-only tools behind an authorization-first registry. Agents receive
typed outputs; tools never see sessions, connections, or filesystems.
"""

from casefile.tools.authorization import (
    AUTHORIZED_TOOLS,
    check_authorization,
    is_authorized,
)
from casefile.tools.base import Tool
from casefile.tools.contracts import (
    ToolCall,
    ToolContext,
    ToolError,
    ToolErrorCode,
    ToolFailureError,
    ToolOutcome,
    ToolResultMetadata,
    ToolStatus,
    ToolUsage,
)
from casefile.tools.damage import (
    DamageEstimateInput,
    DamageEstimateOutput,
    DamageEstimateTool,
)
from casefile.tools.document import (
    DocumentRetrievalInput,
    DocumentRetrievalOutput,
    DocumentRetrievalTool,
)
from casefile.tools.errors import (
    ToolAuthorizationError,
    ToolContractError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolTimeoutError,
    ToolUnavailableError,
    ToolValidationError,
)
from casefile.tools.evidence import (
    EvidenceItem,
    EvidenceLookupInput,
    EvidenceLookupOutput,
    EvidenceLookupTool,
    EvidenceSourceType,
)
from casefile.tools.fixtures import (
    DEFAULT_STORE,
    SYNTHETIC_MARKER,
    FixtureStore,
)
from casefile.tools.fraud import (
    FraudSignalInput,
    FraudSignalLookupTool,
    FraudSignalOutput,
)
from casefile.tools.history import (
    PriorClaimLookupInput,
    PriorClaimLookupOutput,
    PriorClaimLookupTool,
)
from casefile.tools.policy import (
    PolicyLookupInput,
    PolicyLookupOutput,
    PolicyLookupTool,
)
from casefile.tools.registry import (
    ToolRegistry,
    idempotency_key_for,
)
from casefile.tools.sqlite_source import RepositoryDataSource
from casefile.workflow.hooks import EventSink


def create_default_registry(sink: EventSink | None = None) -> ToolRegistry:
    """Build the registry with all six Phase 5 read-only tools."""
    registry = ToolRegistry(sink=sink)
    registry.register(DocumentRetrievalTool())
    registry.register(EvidenceLookupTool())
    registry.register(PriorClaimLookupTool())
    registry.register(PolicyLookupTool())
    registry.register(DamageEstimateTool())
    registry.register(FraudSignalLookupTool())
    return registry


__all__ = [
    "ToolCall",
    "ToolContext",
    "ToolError",
    "ToolErrorCode",
    "ToolFailureError",
    "ToolOutcome",
    "ToolResultMetadata",
    "ToolStatus",
    "ToolUsage",
    "ToolAuthorizationError",
    "ToolContractError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolTimeoutError",
    "ToolUnavailableError",
    "ToolValidationError",
    "check_authorization",
    "is_authorized",
    "DamageEstimateInput",
    "DamageEstimateOutput",
    "DamageEstimateTool",
    "DocumentRetrievalInput",
    "DocumentRetrievalOutput",
    "DocumentRetrievalTool",
    "EvidenceItem",
    "EvidenceLookupInput",
    "EvidenceLookupOutput",
    "EvidenceLookupTool",
    "EvidenceSourceType",
    "DEFAULT_STORE",
    "SYNTHETIC_MARKER",
    "FixtureStore",
    "FraudSignalInput",
    "FraudSignalOutput",
    "FraudSignalLookupTool",
    "PriorClaimLookupInput",
    "PriorClaimLookupOutput",
    "PriorClaimLookupTool",
    "PolicyLookupInput",
    "PolicyLookupOutput",
    "PolicyLookupTool",
    "AUTHORIZED_TOOLS",
    "Tool",
    "ToolRegistry",
    "idempotency_key_for",
    "RepositoryDataSource",
    "create_default_registry",
]
