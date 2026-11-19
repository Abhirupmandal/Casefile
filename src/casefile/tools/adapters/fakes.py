"""
Deterministic test doubles for external services (Phase 9 §B, §I).

Provides offline, predictable implementations with configurable failure modes
(timeouts, 503 unavailability, transient errors, authentication errors).
"""

from __future__ import annotations

import json
from typing import Any, cast

from casefile.tools.adapters.errors import (
    ExternalServiceAuthError,
    ExternalServiceResponseError,
    ExternalServiceTimeoutError,
    ExternalServiceUnavailableError,
)
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore


class FakePolicyServiceClient:
    """Deterministic fake Policy Service client."""

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE
        self.call_count = 0
        self.last_requested_policy: str | None = None
        self.fail_with_timeout = False
        self.fail_with_unavailable = False
        self.fail_with_auth = False
        self.fail_with_malformed = False
        self.transient_failures_remaining = 0

    def get_policy(self, policy_number: str) -> dict[str, Any] | None:
        self.call_count += 1
        self.last_requested_policy = policy_number

        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise ExternalServiceUnavailableError("Temporary network glitch", "policy_service", 503)

        if self.fail_with_timeout:
            raise ExternalServiceTimeoutError("Policy service timed out", "policy_service")
        if self.fail_with_unavailable:
            raise ExternalServiceUnavailableError("Policy service down", "policy_service", 503)
        if self.fail_with_auth:
            raise ExternalServiceAuthError("Invalid API key", "policy_service", 401)
        if self.fail_with_malformed:
            return {"corrupted": "not-a-valid-policy"}

        policy = self._store.get_policy(policy_number)
        if policy is None:
            return None
        return cast(dict[str, Any], json.loads(policy.model_dump_json()))


class FakeClaimsHistoryServiceClient:
    """Deterministic fake Claims History Service client."""

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE
        self.call_count = 0
        self.last_requested_customer: str | None = None
        self.fail_with_timeout = False
        self.fail_with_unavailable = False
        self.fail_with_auth = False
        self.fail_with_malformed = False
        self.transient_failures_remaining = 0

    def get_claim_history(self, customer_id: str, limit: int = 10) -> list[dict[str, Any]]:
        self.call_count += 1
        self.last_requested_customer = customer_id

        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise ExternalServiceUnavailableError("Service busy", "claims_history_service", 503)

        if self.fail_with_timeout:
            raise ExternalServiceTimeoutError("History service timed out", "claims_history_service")
        if self.fail_with_unavailable:
            raise ExternalServiceUnavailableError(
                "History service unavailable", "claims_history_service", 503
            )
        if self.fail_with_auth:
            raise ExternalServiceAuthError("Unauthorized", "claims_history_service", 403)
        if self.fail_with_malformed:
            raise ExternalServiceResponseError("Invalid JSON returned", "claims_history_service")

        priors = self._store.priors_for_customer(customer_id)
        return [
            {
                "claim_id": str(p.claim_reference),
                "incident_date": p.claim_date.isoformat(),
                "claim_amount": str(p.amount.amount),
                "currency": p.amount.currency,
                "status": p.status,
                "claim_type": p.claim_type.value,
                "description": f"Prior claim {p.claim_reference}",
            }
            for p in priors[:limit]
        ]


class FakeRepairCostServiceClient:
    """Deterministic fake Repair Estimating Service client."""

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE
        self.call_count = 0
        self.last_requested_estimate: str | None = None
        self.fail_with_timeout = False
        self.fail_with_unavailable = False
        self.fail_with_auth = False
        self.fail_with_malformed = False
        self.transient_failures_remaining = 0

    def get_damage_estimate(self, estimate_ref: str) -> dict[str, Any] | None:
        self.call_count += 1
        self.last_requested_estimate = estimate_ref

        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise ExternalServiceUnavailableError(
                "Estimating service overloaded", "repair_cost_service", 503
            )

        if self.fail_with_timeout:
            raise ExternalServiceTimeoutError("Estimating service timed out", "repair_cost_service")
        if self.fail_with_unavailable:
            raise ExternalServiceUnavailableError(
                "Estimating service down", "repair_cost_service", 502
            )
        if self.fail_with_auth:
            raise ExternalServiceAuthError("Forbidden", "repair_cost_service", 403)
        if self.fail_with_malformed:
            return {"random_key": 999}

        parts = self._store.get_estimate(estimate_ref)
        if parts is None:
            return None
        return {
            "estimate_ref": estimate_ref,
            "claim_ref": parts.claim_ref,
            "source_facility": parts.source,
            "parts_total": str(parts.declared_total.amount),
            "declared_total": str(parts.declared_total.amount),
            "labor_hours": str(parts.labor_hours),
            "currency": parts.declared_total.currency,
        }


class FakeFraudScoringServiceClient:
    """Deterministic fake Fraud Scoring Service client."""

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE
        self.call_count = 0
        self.last_requested_claim: str | None = None
        self.fail_with_timeout = False
        self.fail_with_unavailable = False
        self.fail_with_auth = False
        self.fail_with_malformed = False
        self.transient_failures_remaining = 0

    def get_fraud_signals(self, claim_ref: str) -> dict[str, Any] | None:
        self.call_count += 1
        self.last_requested_claim = claim_ref

        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise ExternalServiceUnavailableError(
                "Scoring engine cold start", "fraud_scoring_service", 504
            )

        if self.fail_with_timeout:
            raise ExternalServiceTimeoutError("Scoring service timed out", "fraud_scoring_service")
        if self.fail_with_unavailable:
            raise ExternalServiceUnavailableError(
                "Scoring engine down", "fraud_scoring_service", 503
            )
        if self.fail_with_auth:
            raise ExternalServiceAuthError("Invalid credentials", "fraud_scoring_service", 401)
        if self.fail_with_malformed:
            return {"unexpected": "data"}

        profile = self._store.fraud_profile(claim_ref)
        if profile is None:
            return None
        from casefile.tools.fraud import SEVERITY_WEIGHTS

        raw = profile.indicators
        score = min(1.0, sum(SEVERITY_WEIGHTS.get(severity, 0.1) for _, _, severity in raw))
        level = "LOW" if score < 0.2 else ("MEDIUM" if score < 0.5 else "HIGH")
        return {
            "claim_ref": claim_ref,
            "fraud_score": score,
            "risk_level": level,
            "reasons": [text for _, text, _ in raw],
        }


class FakeDocumentStorageServiceClient:
    """Deterministic fake Document Storage Service client."""

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE
        self.call_count = 0
        self.last_requested_doc: str | None = None
        self.fail_with_timeout = False
        self.fail_with_unavailable = False
        self.fail_with_auth = False
        self.fail_with_malformed = False
        self.transient_failures_remaining = 0

    def get_document(self, document_ref: str) -> dict[str, Any] | None:
        self.call_count += 1
        self.last_requested_doc = document_ref

        if self.transient_failures_remaining > 0:
            self.transient_failures_remaining -= 1
            raise ExternalServiceUnavailableError(
                "Storage connection reset", "document_storage_service", 503
            )

        if self.fail_with_timeout:
            raise ExternalServiceTimeoutError("Storage read timed out", "document_storage_service")
        if self.fail_with_unavailable:
            raise ExternalServiceUnavailableError(
                "Storage unavailable", "document_storage_service", 500
            )
        if self.fail_with_auth:
            raise ExternalServiceAuthError("Access denied", "document_storage_service", 403)
        if self.fail_with_malformed:
            return {"bad": "structure"}

        doc = self._store.get_document(document_ref)
        if doc is None:
            return None
        return {
            "document_ref": doc.document_ref,
            "claim_ref": doc.claim_ref,
            "document_type": doc.document_type.value,
            "filename": doc.filename,
            "content_type": doc.content_type.value,
            "content": doc.content,
            "source": doc.source,
            "trusted_source": doc.trusted_source,
        }

    def get_documents_for_claim(self, claim_ref: str) -> list[dict[str, Any]]:
        self.call_count += 1
        docs = self._store.documents_for_claim(claim_ref)
        return [
            {
                "document_ref": d.document_ref,
                "claim_ref": d.claim_ref,
                "document_type": d.document_type.value,
                "filename": d.filename,
                "content_type": d.content_type.value,
                "content": d.content,
                "source": d.source,
                "trusted_source": d.trusted_source,
            }
            for d in docs
        ]
