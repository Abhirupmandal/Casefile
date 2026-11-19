"""
Phase 12.3: External Fraud Intelligence Integration.

Offline deterministic test double for industry fraud database queries (NICB,
Insurance Fraud Bureau, suspicious repairer registries) using PII-safe hashes.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from casefile.fraud.contracts import (
    ExternalFraudMatch,
    FraudDatabaseQuery,
    FraudDatabaseResponse,
)
from casefile.fraud.protocols import ExternalFraudIntelligenceProtocol


def hash_identifier(value: str) -> str:
    """Compute normalized SHA-256 digest of an entity identifier."""
    cleaned = value.strip().lower()
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


# Known synthetic test records for deterministic simulation
KNOWN_SUSPICIOUS_VIN_HASHES: dict[str, dict[str, Any]] = {
    # Test VIN hash for flagged vehicle
    hash_identifier("VIN-SUSPECT-999"): {
        "source": "NICB_SIMULATED_DATABASE",
        "type": "VEHICLE_VIN_HASH",
        "masked": "VIN: *******-999",
        "confidence": 0.92,
        "reason": "Vehicle associated with 3 prior total-loss settlements within 18 months",
        "first_seen": "2024-10-15",
        "last_seen": "2026-03-20",
    },
    # Secondary flagged VIN
    hash_identifier("VIN-CLONED-101"): {
        "source": "INDUSTRY_BLACKLIST_SIMULATION",
        "type": "VEHICLE_VIN_HASH",
        "masked": "VIN: *******-101",
        "confidence": 0.88,
        "reason": "Suspected cloned vehicle identity reported by cross-carrier bureau",
        "first_seen": "2025-06-01",
        "last_seen": "2026-01-12",
    },
}

KNOWN_SUSPICIOUS_POLICY_HASHES: dict[str, dict[str, Any]] = {
    hash_identifier("POL-FRAUD-001"): {
        "source": "CARRIER_INTELLIGENCE_EXCHANGE",
        "type": "POLICY_HASH",
        "masked": "POL: *******-001",
        "confidence": 0.95,
        "reason": "Policy flagged for organized staged collision pattern",
        "first_seen": "2025-11-20",
        "last_seen": "2026-04-05",
    },
}

KNOWN_SUSPICIOUS_BODY_SHOPS: dict[str, dict[str, Any]] = {
    "SHOP-FLAGGED-77": {
        "source": "STATE_FRAUD_BUREAU_WATCHLIST",
        "type": "BODY_SHOP_ID",
        "masked": "SHOP-*******-77",
        "confidence": 0.85,
        "reason": "Repair facility under active investigation for systemic estimate inflation",
        "first_seen": "2025-01-10",
        "last_seen": "2026-05-01",
    },
}


class FakeExternalFraudDatabase(ExternalFraudIntelligenceProtocol):
    """
    Deterministic offline double for external fraud intelligence queries.

    Uses PII-safe hashes; features configurable fault injection (timeouts, 503s).
    """

    def __init__(
        self,
        *,
        custom_vin_records: dict[str, dict[str, Any]] | None = None,
        custom_policy_records: dict[str, dict[str, Any]] | None = None,
        fail_with_timeout: bool = False,
        fail_with_unavailable: bool = False,
    ) -> None:
        self.vin_records = dict(KNOWN_SUSPICIOUS_VIN_HASHES)
        if custom_vin_records:
            self.vin_records.update(custom_vin_records)

        self.policy_records = dict(KNOWN_SUSPICIOUS_POLICY_HASHES)
        if custom_policy_records:
            self.policy_records.update(custom_policy_records)

        self.body_shop_records = dict(KNOWN_SUSPICIOUS_BODY_SHOPS)

        self.fail_with_timeout = fail_with_timeout
        self.fail_with_unavailable = fail_with_unavailable
        self.query_history: list[FraudDatabaseQuery] = []

    def query_database(self, query: FraudDatabaseQuery) -> FraudDatabaseResponse:
        """Query database with PII-safe identifiers."""
        self.query_history.append(query)
        start = time.perf_counter()

        if self.fail_with_timeout:
            return FraudDatabaseResponse(
                query_id=query.query_id,
                status="TIMEOUT",
                matches=[],
                response_ms=5000.0,
                error_message="External fraud database connection timed out after 5.0s",
            )

        if self.fail_with_unavailable:
            return FraudDatabaseResponse(
                query_id=query.query_id,
                status="UNAVAILABLE",
                matches=[],
                response_ms=250.0,
                error_message="External fraud intelligence service 503 Service Unavailable",
            )

        matches: list[ExternalFraudMatch] = []

        # Check VIN hash
        if query.vin_or_vehicle_ref_hash and query.vin_or_vehicle_ref_hash in self.vin_records:
            rec = self.vin_records[query.vin_or_vehicle_ref_hash]
            matches.append(
                ExternalFraudMatch(
                    source_database=rec["source"],
                    matched_entity_type=rec["type"],
                    matched_entity_masked=rec["masked"],
                    confidence=rec["confidence"],
                    match_reason=rec["reason"],
                    first_seen=rec.get("first_seen"),
                    last_seen=rec.get("last_seen"),
                    evidence_refs=[f"ext-db:{query.vin_or_vehicle_ref_hash[:10]}"],
                    is_unverified=True,
                )
            )

        # Check Policy hash
        if query.policy_number_hash and query.policy_number_hash in self.policy_records:
            rec = self.policy_records[query.policy_number_hash]
            matches.append(
                ExternalFraudMatch(
                    source_database=rec["source"],
                    matched_entity_type=rec["type"],
                    matched_entity_masked=rec["masked"],
                    confidence=rec["confidence"],
                    match_reason=rec["reason"],
                    first_seen=rec.get("first_seen"),
                    last_seen=rec.get("last_seen"),
                    evidence_refs=[f"ext-db:{query.policy_number_hash[:10]}"],
                    is_unverified=True,
                )
            )

        # Check Body Shop ID
        if query.body_shop_ref and query.body_shop_ref in self.body_shop_records:
            rec = self.body_shop_records[query.body_shop_ref]
            matches.append(
                ExternalFraudMatch(
                    source_database=rec["source"],
                    matched_entity_type=rec["type"],
                    matched_entity_masked=rec["masked"],
                    confidence=rec["confidence"],
                    match_reason=rec["reason"],
                    first_seen=rec.get("first_seen"),
                    last_seen=rec.get("last_seen"),
                    evidence_refs=[f"ext-db:{query.body_shop_ref}"],
                    is_unverified=True,
                )
            )

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        status = "SUCCESS" if matches else "NO_MATCH"
        return FraudDatabaseResponse(
            query_id=query.query_id,
            status=status,
            matches=matches,
            response_ms=round(elapsed_ms, 2),
            error_message=None,
        )
