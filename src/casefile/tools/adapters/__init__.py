"""
External service adapters and test doubles (Phase 9 §B).
"""

from casefile.tools.adapters.errors import (
    ExternalServiceAuthError,
    ExternalServiceError,
    ExternalServiceResponseError,
    ExternalServiceTimeoutError,
    ExternalServiceUnavailableError,
)
from casefile.tools.adapters.fakes import (
    FakeClaimsHistoryServiceClient,
    FakeDocumentStorageServiceClient,
    FakeFraudScoringServiceClient,
    FakePolicyServiceClient,
    FakeRepairCostServiceClient,
)
from casefile.tools.adapters.http_client import (
    HttpClaimsHistoryServiceClient,
    HttpDocumentStorageServiceClient,
    HttpFraudScoringServiceClient,
    HttpPolicyServiceClient,
    HttpRepairCostServiceClient,
    ResilientHttpClient,
)
from casefile.tools.adapters.protocols import (
    ClaimsHistoryServiceClient,
    DocumentStorageServiceClient,
    FraudScoringServiceClient,
    PolicyServiceClient,
    RepairCostServiceClient,
)

__all__ = [
    "ClaimsHistoryServiceClient",
    "DocumentStorageServiceClient",
    "ExternalServiceAuthError",
    "ExternalServiceError",
    "ExternalServiceResponseError",
    "ExternalServiceTimeoutError",
    "ExternalServiceUnavailableError",
    "FakeClaimsHistoryServiceClient",
    "FakeDocumentStorageServiceClient",
    "FakeFraudScoringServiceClient",
    "FakePolicyServiceClient",
    "FakeRepairCostServiceClient",
    "FraudScoringServiceClient",
    "HttpClaimsHistoryServiceClient",
    "HttpDocumentStorageServiceClient",
    "HttpFraudScoringServiceClient",
    "HttpPolicyServiceClient",
    "HttpRepairCostServiceClient",
    "PolicyServiceClient",
    "RepairCostServiceClient",
    "ResilientHttpClient",
]
