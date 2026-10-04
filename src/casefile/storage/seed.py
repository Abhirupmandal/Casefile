"""
Controlled synthetic seed dataset for integration tests (Phase 6 §23).

Mirrors the Phase 5 fixture scenarios into durable rows: normal,
high-value, prior-history, inconsistent-evidence, suspicious/fraud,
rework-needed, and estimate-inconsistency claims. All identifiers are
SYNTH-* synthetic; no real personal information. Deterministic UUIDs via
uuid5 so re-seeding is idempotent (merge semantics).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import NAMESPACE_DNS, UUID, uuid5

from casefile.models.domain import (
    Claim,
    ClaimDocumentReference,
    ClaimStatus,
    ClaimType,
    DamageEstimate,
    FraudIndicator,
    Money,
    PriorClaim,
)
from casefile.models.persistence import DamageEstimateRecord, DamageLineItemRecord
from casefile.storage.unit_of_work import UnitOfWork
from casefile.tools.fixtures import (
    CLAIM_SCENARIOS,
    DOCUMENTS,
    ESTIMATES,
    FRAUD_PROFILES,
    POLICIES,
    PRIORS,
    FixtureDocument,
    FixtureEstimateParts,
    FixtureFraudProfile,
    FixturePrior,
)

SCENARIO_AMOUNTS: dict[str, str] = {
    "SYN-NORMAL-001": "1500.00",
    "SYN-HIGH-001": "95000.00",
    "SYN-HISTORY-001": "2100.00",
    "SYN-GAPS-001": "500.00",
    "SYN-FRAUD-001": "3000.00",
    "SYN-REWORK-001": "750.00",
    "SYN-ESTIMATE-001": "500.00",
}


def synthetic_uuid(namespace: str, ref: str) -> UUID:
    """Deterministic UUID for a synthetic reference."""
    return uuid5(NAMESPACE_DNS, f"casefile:{namespace}:{ref}")


def seed_synthetic_dataset(uow: UnitOfWork) -> dict[str, int]:
    """Write all synthetic scenarios through repository mappings.

    Idempotent: rows merge on primary key, so re-seeding is safe.
    Returns per-table row counts written.
    """
    counts = {"policies": 0, "priors": 0, "estimates": 0, "documents": 0, "fraud": 0, "claims": 0}
    for policy in POLICIES.values():
        uow.policies.save(policy)
        counts["policies"] += 1
    for customer_priors in PRIORS.values():
        for prior in customer_priors:
            uow.priors.save(_prior_claim(prior), prior.customer_id, prior.policy_number)
            counts["priors"] += 1
    for parts in ESTIMATES.values():
        if parts.estimate_ref == "SYN-EST-BROKEN":
            _seed_inconsistent_estimate(uow, parts)
        else:
            uow.estimates.save(_estimate_from_parts(parts), parts.claim_ref, parts.estimate_ref)
        counts["estimates"] += 1
    for doc in DOCUMENTS.values():
        uow.documents.save(_doc_ref(doc), doc.claim_ref, doc.content)
        counts["documents"] += 1
    for profile in FRAUD_PROFILES.values():
        uow.fraud.save_all(profile.claim_ref, _fraud_indicators(profile))
        counts["fraud"] += len(profile.indicators)
    for claim_ref, scenario in CLAIM_SCENARIOS.items():
        claim = Claim(
            claim_id=synthetic_uuid("claim", claim_ref),
            external_claim_id=claim_ref,
            policy_number=scenario["policy"],
            customer_id=scenario["customer"],
            claim_type=ClaimType.COLLISION,
            status=ClaimStatus.SUBMITTED,
            date_of_loss=date(2026, 9, 10),
            date_reported=date(2026, 9, 12),
            description=f"Synthetic {scenario['note']} ({claim_ref})",
            claim_amount=Money(amount=Decimal(SCENARIO_AMOUNTS[claim_ref])),
        )
        uow.claims.save(claim)
        counts["claims"] += 1
    return counts


def _seed_inconsistent_estimate(uow: UnitOfWork, parts: FixtureEstimateParts) -> None:
    """Write deliberately unreconciled rows for the sanity tool to flag.

    Bypasses domain validation on purpose: the validated model cannot
    represent this state, which is exactly what the tool must detect.
    """
    session = uow.session
    session.merge(
        DamageEstimateRecord(
            estimate_ref=parts.estimate_ref,
            estimate_id=str(synthetic_uuid("estimate", parts.estimate_ref)),
            claim_ref=parts.claim_ref,
            source=parts.source,
            estimate_date=parts.estimate_date.isoformat(),
            labor_hours=parts.labor_hours,
            declared_total=parts.declared_total.amount,
            declared_currency=parts.declared_total.currency,
        )
    )
    for index, item in enumerate(parts.line_items):
        session.merge(
            DamageLineItemRecord(
                line_id=f"{parts.estimate_ref}:{index}",
                estimate_ref=parts.estimate_ref,
                description=item.description,
                quantity=item.quantity,
                unit_amount=item.unit_cost.amount,
                unit_currency=item.unit_cost.currency,
                total_amount=item.total_cost.amount,
                total_currency=item.total_cost.currency,
            )
        )
    session.flush()


def _estimate_from_parts(parts: FixtureEstimateParts) -> DamageEstimate:
    """Rebuild a validated estimate from raw fixture components."""

    return DamageEstimate(
        source=parts.source,
        estimate_date=parts.estimate_date,
        line_items=list(parts.line_items),
        labor_hours=parts.labor_hours,
        total_cost=parts.declared_total,
    )


def _doc_ref(doc: FixtureDocument) -> ClaimDocumentReference:
    """Rebuild a document reference from a fixture row."""

    return ClaimDocumentReference(
        document_id=synthetic_uuid("doc", doc.document_ref),
        document_type=doc.document_type,
        filename=doc.filename,
        content_type=doc.content_type,
        content_hash=doc.content_hash,
        source=doc.source,
        trusted_source=False,
    )


def _prior_claim(prior: FixturePrior) -> PriorClaim:
    """Rebuild a validated prior claim from a fixture row."""
    return PriorClaim(
        claim_reference=prior.claim_reference,
        claim_date=prior.claim_date,
        claim_type=prior.claim_type,
        amount=prior.amount,
        status=prior.status,
        at_fault=prior.at_fault,
    )


def _fraud_indicators(profile: FixtureFraudProfile) -> list[FraudIndicator]:
    """Rebuild fraud indicators from a fixture profile."""

    return [
        FraudIndicator(indicator_type=kind, description=text, severity=severity)
        for kind, text, severity in profile.indicators
    ]


__all__ = [
    "SCENARIO_AMOUNTS",
    "seed_synthetic_dataset",
    "synthetic_uuid",
]
