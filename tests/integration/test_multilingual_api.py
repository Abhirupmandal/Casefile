"""
Phase 12.2 Production Wiring Integration Tests.

Verifies ApiService dependency injection of translation_client and end-to-end
claim execution through ApiService.submit_claim():
1. Spanish claim with injected translation client normalizes to English and reaches human approval.
2. French and German claims with injected client normalize and proceed safely.
3. English claim without translation client functions backward-compatibly.
4. Non-English claim without translation client fails closed (no silent English fallback, no approval).
5. Translation client raising timeout or unavailable error fails closed safely.
6. Translation provenance and dual hashes survive workflow handoffs.
7. Raw document text and PII are not emitted in telemetry or audit logs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService
from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.translation.contracts import compute_sha256
from casefile.translation.fake import FakeTranslationServiceClient


@pytest.fixture
def api_service_factory() -> Iterator[Callable[..., ApiService]]:
    """Factory creating isolated in-memory ApiService instances."""
    services: list[ApiService] = []

    def _create(client: FakeTranslationServiceClient | None = None) -> ApiService:
        svc = ApiService(
            database_url=f"sqlite:///:memory:?cache=shared_{uuid4().hex}",
            translation_client=client,
        )
        services.append(svc)
        return svc

    yield _create

    for svc in services:
        svc.close()


def test_api_service_spanish_claim_with_injected_client(
    api_service_factory: Callable[..., ApiService],
) -> None:
    trans_client = FakeTranslationServiceClient()
    svc: ApiService = api_service_factory(trans_client)

    req = ClaimSubmissionRequest(
        policy_id="POL-ES-201",
        claimant_name="Mateo Silva",
        incident_date="2026-05-15",
        claim_amount=Decimal("1800.00"),
        description="Choque en intersección con daño frontal",
        documents=["Informe de accidente: Choque con daño en el parachoques delantero y faro."],
        language="es",
    )

    resp = svc.submit_claim(req)
    # Must proceed past EXTRACTION to HUMAN_APPROVAL
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Verify translation occurred
    assert trans_client.call_count >= 1
    assert trans_client.last_request is not None
    assert trans_client.last_request.source_language == "es"

    # Verify audit events and workflow state in database
    with svc._uow() as uow:
        run_record = uow.runs.get(UUID(resp.workflow_run_id))
        assert run_record is not None
        assert run_record.current_state == WorkflowState.HUMAN_APPROVAL.value

        # Check recorded agent executions
        executions = uow.agent_executions.list_for_run(UUID(resp.workflow_run_id))
        extractor_exec = next(e for e in executions if e.agent == "EXTRACTOR")
        assert extractor_exec.status == "SUCCESS"


def test_api_service_french_and_german_claims_with_injected_client(
    api_service_factory: Callable[..., ApiService],
) -> None:
    trans_client = FakeTranslationServiceClient()
    svc: ApiService = api_service_factory(trans_client)

    # French claim
    fr_req = ClaimSubmissionRequest(
        policy_id="POL-FR-301",
        claimant_name="Claire Dubois",
        incident_date="2026-06-10",
        claim_amount=Decimal("2200.00"),
        description="Collision sur autoroute avec dommage au pare-chocs",
        documents=["Constat d'accident: dommage au pare-chocs avant."],
        language="fr",
    )
    fr_resp = svc.submit_claim(fr_req)
    assert fr_resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # German claim
    de_req = ClaimSubmissionRequest(
        policy_id="POL-DE-401",
        claimant_name="Stefan Weber",
        incident_date="2026-07-01",
        claim_amount=Decimal("3100.00"),
        description="Fahrzeugkollision mit erheblichem Schaden",
        documents=["Polizeibericht: Fahrzeugunfall mit Schaden an der Stoßstange."],
        language="de",
    )
    de_resp = svc.submit_claim(de_req)
    assert de_resp.current_state == WorkflowState.HUMAN_APPROVAL.value


def test_api_service_english_claim_without_translation_client(
    api_service_factory: Callable[..., ApiService],
) -> None:
    # No translation client injected
    svc: ApiService = api_service_factory(None)
    assert svc.translation_client is None

    req = ClaimSubmissionRequest(
        policy_id="POL-EN-501",
        claimant_name="John Doe",
        incident_date="2026-04-01",
        claim_amount=Decimal("1200.00"),
        description="Fender bender in parking lot",
        documents=["Police report: Minor bumper damage."],
        language="en",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value


def test_api_service_non_english_claim_without_translation_client_fails_closed(
    api_service_factory: Callable[..., ApiService],
) -> None:
    # No translation client configured
    svc: ApiService = api_service_factory(None)

    req = ClaimSubmissionRequest(
        policy_id="POL-ES-601",
        claimant_name="Gabriel Garcia",
        incident_date="2026-05-20",
        claim_amount=Decimal("2500.00"),
        description="Golpe en el lateral",
        documents=["Informe de accidente sin traductor disponible"],
        language="es",
    )

    resp = svc.submit_claim(req)
    # Must fail closed to FAILED - never continue silently as English, never approve
    assert resp.current_state == WorkflowState.FAILED.value

    with svc._uow() as uow:
        run_record = uow.runs.get(UUID(resp.workflow_run_id))
        assert run_record is not None
        assert run_record.current_state == WorkflowState.FAILED.value


def test_api_service_translation_error_fails_closed(
    api_service_factory: Callable[..., ApiService],
) -> None:
    trans_client = FakeTranslationServiceClient()
    trans_client.fail_with_timeout = True
    svc: ApiService = api_service_factory(trans_client)

    req = ClaimSubmissionRequest(
        policy_id="POL-ES-701",
        claimant_name="Lucia Santos",
        incident_date="2026-05-25",
        claim_amount=Decimal("1500.00"),
        description="Daño por choque",
        documents=["Documento que provocará timeout en traductor"],
        language="es",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.FAILED.value


def test_api_service_translation_provenance_and_hashes_retained(
    api_service_factory: Callable[..., ApiService],
) -> None:
    trans_client = FakeTranslationServiceClient()
    svc: ApiService = api_service_factory(trans_client)

    raw_doc_text = "Informe de accidente: Siniestro con daño en el capó."
    orig_hash = compute_sha256(raw_doc_text)

    req = ClaimSubmissionRequest(
        policy_id="POL-ES-801",
        claimant_name="Rosa Mendez",
        incident_date="2026-06-01",
        claim_amount=Decimal("1900.00"),
        description="Siniestro en avenida",
        documents=[raw_doc_text],
        language="es",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Verify provenance survives in latest checkpoint
    latest_cp = svc.checkpoint_repo.get_latest(UUID(resp.workflow_run_id))
    assert latest_cp is not None
    extractor_rec = next(a for a in latest_cp.recorded_agents if a.agent == AgentType.EXTRACTOR)
    output = json.loads(extractor_rec.output_json)
    assert output.get("was_translated") is True
    assert output.get("source_language") == "es"
    prov = output.get("translation_provenance", {})
    assert "document-0" in prov
    doc_prov = prov["document-0"]
    assert doc_prov["original_content_hash"] == orig_hash
    assert doc_prov["translated_content_hash"] != orig_hash


def test_api_service_no_pii_in_audit_logs(
    api_service_factory: Callable[..., ApiService],
) -> None:
    trans_client = FakeTranslationServiceClient()
    svc: ApiService = api_service_factory(trans_client)

    req = ClaimSubmissionRequest(
        policy_id="POL-ES-901",
        claimant_name="SecretClaimantName",
        incident_date="2026-06-05",
        claim_amount=Decimal("3000.00"),
        description="Descripción altamente confidencial con PII",
        documents=["Informe confidencial que no debe aparecer en logs."],
        language="es",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Inspect audit events in DB
    with svc._uow() as uow:
        audit_events = uow.audit.list_for_run(UUID(resp.workflow_run_id))
        assert len(audit_events) > 0
        for ev in audit_events:
            # Audit detail/payload must not leak raw document text or sensitive document description
            ev_str = f"{ev.summary} {ev.action} {ev.attributes}"
            assert "Informe confidencial que no debe aparecer en logs." not in ev_str
            assert "Descripción altamente confidencial con PII" not in ev_str
