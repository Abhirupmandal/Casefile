"""
Repository-backed tool data source (Phase 6 §17).

ToolRegistry → Typed Tool → Repository → SQLite. Subclasses FixtureStore
so tools run unchanged against either source: fixtures for isolated unit
tests, this class for SQLite integration. Read-only: one short session
per accessor and only Fixture* types cross outward — rows, sessions, and
SQL never escape. Agents never see repositories.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from casefile.models.domain import (
    ClaimType,
    DamageLineItem,
    DocumentContentType,
    DocumentType,
    FraudSeverity,
    Money,
    Policy,
)
from casefile.models.persistence import (
    ClaimDocumentRecord,
    ClaimRecord,
    DamageEstimateRecord,
    DamageLineItemRecord,
    FraudIndicatorRecord,
    PriorClaimRecord,
)
from casefile.storage.repositories import SqlPolicyRepository
from casefile.tools.fixtures import (
    FixtureDocument,
    FixtureEstimateParts,
    FixtureFraudProfile,
    FixturePrior,
    FixtureStore,
)


class RepositoryDataSource(FixtureStore):
    """FixtureStore interface served from durable SQLite rows."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    def get_policy(self, policy_number: str) -> Policy | None:
        from casefile.storage.errors import PersistenceError

        with self._factory() as session:
            try:
                return SqlPolicyRepository(session).get(policy_number.strip().upper())
            except PersistenceError:
                return None

    def get_document(self, document_ref: str) -> FixtureDocument | None:
        ref = document_ref.strip()
        try:
            UUID(ref)
        except ValueError:
            return None
        with self._factory() as session:
            row = session.get(ClaimDocumentRecord, ref)
            if row is None:
                return None
            return self._to_fixture_document(row, ref)

    def documents_for_claim(self, claim_ref: str) -> list[FixtureDocument]:
        with self._factory() as session:
            rows = (
                session.query(ClaimDocumentRecord)
                .filter_by(claim_ref=claim_ref)
                .order_by(ClaimDocumentRecord.document_id)
                .all()
            )
            return [self._to_fixture_document(row, row.document_id) for row in rows]

    @staticmethod
    def _to_fixture_document(row: ClaimDocumentRecord, ref: str) -> FixtureDocument:
        return FixtureDocument(
            document_ref=ref,
            claim_ref=row.claim_ref,
            document_type=DocumentType(row.document_type),
            filename=row.filename or "",
            content_type=DocumentContentType(row.content_type),
            content=row.content,
            source=row.source,
            trusted_source=row.trusted_source,
        )

    def priors_for_customer(self, customer_id: str) -> list[FixturePrior]:
        with self._factory() as session:
            rows = (
                session.query(PriorClaimRecord)
                .filter_by(customer_id=customer_id)
                .order_by(PriorClaimRecord.claim_date.desc(), PriorClaimRecord.claim_reference)
                .all()
            )
            return [
                FixturePrior(
                    claim_reference=row.claim_reference,
                    claim_date=date.fromisoformat(row.claim_date),
                    claim_type=ClaimType(row.claim_type),
                    amount=Money(amount=Decimal(row.amount), currency=row.currency),
                    status=row.status,
                    at_fault=row.at_fault,
                    customer_id=row.customer_id,
                    policy_number=row.policy_number,
                )
                for row in rows
            ]

    def get_estimate(self, estimate_ref: str) -> FixtureEstimateParts | None:
        with self._factory() as session:
            header = session.get(DamageEstimateRecord, estimate_ref.strip())
            if header is None:
                return None
            lines = (
                session.query(DamageLineItemRecord)
                .filter_by(estimate_ref=header.estimate_ref)
                .order_by(DamageLineItemRecord.line_id)
                .all()
            )
            return self._to_fixture_estimate(header, lines)

    def estimates_for_claim(self, claim_ref: str) -> list[FixtureEstimateParts]:
        with self._factory() as session:
            headers = (
                session.query(DamageEstimateRecord)
                .filter_by(claim_ref=claim_ref)
                .order_by(DamageEstimateRecord.estimate_ref)
                .all()
            )
            parts: list[FixtureEstimateParts] = []
            for header in headers:
                lines = (
                    session.query(DamageLineItemRecord)
                    .filter_by(estimate_ref=header.estimate_ref)
                    .order_by(DamageLineItemRecord.line_id)
                    .all()
                )
                parts.append(self._to_fixture_estimate(header, lines))
            return parts

    @staticmethod
    def _to_fixture_estimate(
        header: DamageEstimateRecord, lines: list[DamageLineItemRecord]
    ) -> FixtureEstimateParts:
        return FixtureEstimateParts(
            estimate_ref=header.estimate_ref,
            claim_ref=header.claim_ref,
            source=header.source,
            estimate_date=date.fromisoformat(header.estimate_date),
            line_items=tuple(
                DamageLineItem(
                    description=row.description,
                    quantity=row.quantity,
                    unit_cost=Money(amount=Decimal(row.unit_amount), currency=row.unit_currency),
                    total_cost=Money(amount=Decimal(row.total_amount), currency=row.total_currency),
                )
                for row in lines
            ),
            declared_total=Money(
                amount=Decimal(header.declared_total), currency=header.declared_currency
            ),
            labor_hours=header.labor_hours,
        )

    def fraud_profile(self, claim_ref: str) -> FixtureFraudProfile | None:
        with self._factory() as session:
            rows = (
                session.query(FraudIndicatorRecord)
                .filter_by(claim_ref=claim_ref.strip())
                .order_by(FraudIndicatorRecord.indicator_type)
                .all()
            )
            if not rows:
                return None
            return FixtureFraudProfile(
                claim_ref=claim_ref,
                indicators=tuple(
                    (row.indicator_type, row.description, FraudSeverity(row.severity))
                    for row in rows
                ),
            )

    def scenario(self, claim_ref: str) -> dict[str, str] | None:
        with self._factory() as session:
            row = session.query(ClaimRecord).filter_by(external_claim_id=claim_ref.strip()).first()
            if row is None:
                return None
            return {"policy": row.policy_number, "customer": row.customer_id, "note": "seeded"}
