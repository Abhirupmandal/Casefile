"""
Protocols and interfaces for external service providers (Phase 9 §B).
"""

from __future__ import annotations

from typing import Any, Protocol


class PolicyServiceClient(Protocol):
    """External policy management service client interface."""

    def get_policy(self, policy_number: str) -> dict[str, Any] | None:
        """Fetch policy by policy number. Returns dict payload or None if not found."""
        ...


class ClaimsHistoryServiceClient(Protocol):
    """External claims history database service client interface."""

    def get_claim_history(self, customer_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Fetch prior claim records for a given customer."""
        ...


class RepairCostServiceClient(Protocol):
    """External repair cost estimation service client interface."""

    def get_damage_estimate(self, estimate_ref: str) -> dict[str, Any] | None:
        """Fetch damage and repair cost estimate by reference."""
        ...


class FraudScoringServiceClient(Protocol):
    """External fraud detection & signal scoring service client interface."""

    def get_fraud_signals(self, claim_ref: str) -> dict[str, Any] | None:
        """Fetch fraud indicators and risk score for a claim."""
        ...


class DocumentStorageServiceClient(Protocol):
    """External document storage service client interface."""

    def get_document(self, document_ref: str) -> dict[str, Any] | None:
        """Fetch document metadata and content by reference."""
        ...

    def get_documents_for_claim(self, claim_ref: str) -> list[dict[str, Any]]:
        """Fetch all documents associated with a claim reference."""
        ...


class TranslationServiceClient(Protocol):
    """External translation & language detection service client interface."""

    def detect_language(self, text: str) -> Any:
        """Detect language of given text."""
        ...

    def translate(self, request: Any) -> Any:
        """Translate text according to request envelope."""
        ...
