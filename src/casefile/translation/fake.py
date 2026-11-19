"""
Phase 12.2: Deterministic Fake Translation Service Client.

Provides an offline, predictable, zero-network implementation of
TranslationServiceClientProtocol with full support for Spanish, French,
German, and English normalization, dual-hash calculation, and controllable
failure injection (timeouts, 503 unavailable, malformed responses, ambiguous detection).
"""

from __future__ import annotations

import re
from decimal import Decimal

from casefile.models.contracts import SUPPORTED_LANGUAGE_CODES
from casefile.translation.contracts import (
    LanguageDetectionResult,
    TranslationFailureReason,
    TranslationRequest,
    TranslationResponse,
    TranslationResultMetadata,
    TranslationStatus,
    compute_sha256,
)

# Common insurance terminology dictionaries for deterministic normalization
SPANISH_TO_ENGLISH = {
    "informe de accidente": "accident report",
    "informe de policía": "police report",
    "parte de accidente": "accident report",
    "declaración del conductor": "driver statement",
    "estimación de reparación": "repair estimate",
    "factura de reparación": "repair invoice",
    "reclamación": "claim",
    "reclamante": "claimant",
    "póliza": "policy",
    "vehículo": "vehicle",
    "daño": "damage",
    "daños": "damages",
    "colisión": "collision",
    "siniestro": "incident",
    "conductor": "driver",
    "asegurado": "insured",
    "cobertura": "coverage",
    "choque": "crash",
    "delantero": "front",
    "trasero": "rear",
    "lateral": "side",
    "parachoques": "bumper",
    "capó": "hood",
    "puerta": "door",
    "faro": "headlight",
    "coste estimado": "estimated cost",
    "total estimado": "estimated total",
    "descripción del incidente": "incident description",
    "fecha del incidente": "incident date",
    "lugar del incidente": "incident location",
}

FRENCH_TO_ENGLISH = {
    "rapport de police": "police report",
    "constat d'accident": "accident report",
    "rapport d'accident": "accident report",
    "déclaration du conducteur": "driver statement",
    "devis de réparation": "repair estimate",
    "facture de réparation": "repair invoice",
    "réclamation": "claim",
    "demandeur": "claimant",
    "police d'assurance": "insurance policy",
    "police": "policy",
    "véhicule": "vehicle",
    "dommage": "damage",
    "dommages": "damages",
    "collision": "collision",
    "sinistre": "incident",
    "conducteur": "driver",
    "assuré": "insured",
    "couverture": "coverage",
    "avant": "front",
    "arrière": "rear",
    "latéral": "side",
    "pare-chocs": "bumper",
    "capot": "hood",
    "portière": "door",
    "phare": "headlight",
    "coût estimé": "estimated cost",
    "total estimé": "estimated total",
    "description de l'incident": "incident description",
    "date de l'incident": "incident date",
    "lieu de l'incident": "incident location",
}

GERMAN_TO_ENGLISH = {
    "polizeibericht": "police report",
    "unfallbericht": "accident report",
    "fahrer-aussage": "driver statement",
    "fahreraussage": "driver statement",
    "kostenvoranschlag": "repair estimate",
    "reparaturrechnung": "repair invoice",
    "schadensmeldung": "claim",
    "anspruch": "claim",
    "anspruchsteller": "claimant",
    "versicherungspolice": "insurance policy",
    "police": "policy",
    "fahrzeug": "vehicle",
    "schaden": "damage",
    "schäden": "damages",
    "kollision": "collision",
    "unfall": "incident",
    "unfallereignis": "incident",
    "fahrer": "driver",
    "versicherter": "insured",
    "deckung": "coverage",
    "vorne": "front",
    "hinten": "rear",
    "seite": "side",
    "stoßstange": "bumper",
    "motorhaube": "hood",
    "tür": "door",
    "scheinwerfer": "headlight",
    "geschätzte kosten": "estimated cost",
    "gesamtsumme": "estimated total",
    "vorfallbeschreibung": "incident description",
    "unfalldatum": "incident date",
    "unfallort": "incident location",
}

ENGLISH_MARKERS = {
    "the",
    "is",
    "was",
    "were",
    "and",
    "in",
    "at",
    "of",
    "to",
    "for",
    "with",
    "this",
    "that",
    "my",
    "your",
    "his",
    "her",
    "their",
    "from",
    "by",
    "bumper",
    "headlight",
}
SPANISH_MARKERS = {
    "daño",
    "daños",
    "reclamación",
    "siniestro",
    "vehículo",
    "póliza",
    "asegurado",
    "conductor",
    "informe",
    "choque",
    "calle",
    "avenida",
    "el",
    "los",
    "las",
}
FRENCH_MARKERS = {
    "dommage",
    "dommages",
    "réclamation",
    "sinistre",
    "véhicule",
    "assuré",
    "conducteur",
    "rapport",
    "constat",
    "rue",
    "avenue",
    "le",
    "la",
    "les",
    "du",
    "des",
}
GERMAN_MARKERS = {
    "schaden",
    "schäden",
    "anspruch",
    "unfall",
    "fahrzeug",
    "versicherung",
    "versicherter",
    "fahrer",
    "bericht",
    "kostenvoranschlag",
    "straße",
    "der",
    "die",
    "das",
}


class FakeTranslationServiceClient:
    """
    In-memory deterministic test double for translation and language detection.
    """

    def __init__(
        self,
        *,
        provider_name: str = "fake_translation_service",
        model_name: str = "deterministic-translator-v1",
    ) -> None:
        self.provider_name = provider_name
        self.model_name = model_name
        self.call_count: int = 0
        self.detection_call_count: int = 0
        self.last_request: TranslationRequest | None = None

        # Fault injection toggles
        self.fail_with_timeout: bool = False
        self.fail_with_unavailable: bool = False
        self.fail_with_malformed: bool = False
        self.fail_with_unsupported: bool = False

        # Configurable overrides
        self.forced_detection_language: str | None = None
        self.forced_detection_confidence: float | None = None
        self.ambiguous_texts: set[str] = set()
        self.unsupported_texts: set[str] = set()

    def detect_language(self, text: str) -> LanguageDetectionResult:
        """Deterministically detect language of source text."""
        self.detection_call_count += 1
        cleaned = text.strip().lower()

        if not cleaned:
            return LanguageDetectionResult(
                detected_language="en",
                confidence=1.0,
                is_supported=True,
                is_ambiguous=False,
                model_version=self.model_name,
            )

        if self.forced_detection_language:
            lang = self.forced_detection_language
            conf = self.forced_detection_confidence or 1.0
            is_sup = lang in SUPPORTED_LANGUAGE_CODES
            return LanguageDetectionResult(
                detected_language=lang,
                confidence=conf,
                is_supported=is_sup,
                is_ambiguous=conf < 0.7,
                model_version=self.model_name,
            )

        if cleaned in self.ambiguous_texts or "ambiguous_lang_test" in cleaned:
            return LanguageDetectionResult(
                detected_language="unknown",
                confidence=0.45,
                is_supported=False,
                is_ambiguous=True,
                model_version=self.model_name,
            )

        if cleaned in self.unsupported_texts or any(
            marker in cleaned
            for marker in ("japanese_doc", "russian_doc", "chinese_doc", "italian_doc")
        ):
            return LanguageDetectionResult(
                detected_language="it",
                confidence=0.95,
                is_supported=False,
                is_ambiguous=False,
                model_version=self.model_name,
            )

        # Word token matching
        tokens = set(re.findall(r"\b[\w'-]+\b", cleaned))

        score_en = len(tokens.intersection(ENGLISH_MARKERS))
        score_es = len(tokens.intersection(SPANISH_MARKERS))
        score_fr = len(tokens.intersection(FRENCH_MARKERS))
        score_de = len(tokens.intersection(GERMAN_MARKERS))

        if score_en > 0 and score_en >= max(score_es, score_fr, score_de):
            return LanguageDetectionResult(
                detected_language="en",
                confidence=0.99,
                is_supported=True,
                is_ambiguous=False,
                model_version=self.model_name,
            )

        max_score = max(score_es, score_fr, score_de)
        if max_score == 0:
            return LanguageDetectionResult(
                detected_language="en",
                confidence=1.0,
                is_supported=True,
                is_ambiguous=False,
                model_version=self.model_name,
            )

        if score_es == max_score and score_es > score_fr and score_es > score_de:
            return LanguageDetectionResult(
                detected_language="es",
                confidence=0.98,
                is_supported=True,
                is_ambiguous=False,
                model_version=self.model_name,
            )
        if score_fr == max_score and score_fr > score_es and score_fr > score_de:
            return LanguageDetectionResult(
                detected_language="fr",
                confidence=0.98,
                is_supported=True,
                is_ambiguous=False,
                model_version=self.model_name,
            )
        if score_de == max_score and score_de > score_es and score_de > score_fr:
            return LanguageDetectionResult(
                detected_language="de",
                confidence=0.98,
                is_supported=True,
                is_ambiguous=False,
                model_version=self.model_name,
            )

        # Tie or collision between distinct languages
        return LanguageDetectionResult(
            detected_language="unknown",
            confidence=0.50,
            is_supported=False,
            is_ambiguous=True,
            model_version=self.model_name,
        )

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        """Translate source text deterministically to target language."""
        self.call_count += 1
        self.last_request = request

        # Fault injection checks
        if self.fail_with_timeout:
            return TranslationResponse(
                status=TranslationStatus.FAILED,
                original_text=request.text,
                failure_reason=TranslationFailureReason.TIMEOUT,
                error_message="Fake translation service timeout after 5000ms",
            )

        if self.fail_with_unavailable:
            return TranslationResponse(
                status=TranslationStatus.FAILED,
                original_text=request.text,
                failure_reason=TranslationFailureReason.PROVIDER_UNAVAILABLE,
                error_message="Fake translation service 503 unavailable",
            )

        if self.fail_with_malformed:
            return TranslationResponse(
                status=TranslationStatus.FAILED,
                original_text=request.text,
                failure_reason=TranslationFailureReason.MALFORMED_RESPONSE,
                error_message="Received corrupted non-JSON payload from provider",
            )

        if not request.text or not request.text.strip():
            return TranslationResponse(
                status=TranslationStatus.FAILED,
                original_text=request.text,
                failure_reason=TranslationFailureReason.EMPTY_CONTENT,
                error_message="Cannot translate empty or whitespace content",
            )

        orig_hash = compute_sha256(request.text)

        # Determine source language
        src_lang = request.source_language
        det_result: LanguageDetectionResult | None = None
        if not src_lang or src_lang.lower() == "auto":
            det_result = self.detect_language(request.text)
            src_lang = det_result.detected_language
            if det_result.is_ambiguous:
                return TranslationResponse(
                    status=TranslationStatus.AMBIGUOUS,
                    original_text=request.text,
                    failure_reason=TranslationFailureReason.AMBIGUOUS_LANGUAGE,
                    error_message=f"Ambiguous language detected with confidence {det_result.confidence}",
                )
            if not det_result.is_supported:
                return TranslationResponse(
                    status=TranslationStatus.FAILED,
                    original_text=request.text,
                    failure_reason=TranslationFailureReason.UNSUPPORTED_LANGUAGE,
                    error_message=f"Unsupported language detected: {src_lang!r}",
                )
        else:
            src_lang = src_lang.strip().lower()

        if self.fail_with_unsupported or src_lang not in SUPPORTED_LANGUAGE_CODES:
            return TranslationResponse(
                status=TranslationStatus.FAILED,
                original_text=request.text,
                failure_reason=TranslationFailureReason.UNSUPPORTED_LANGUAGE,
                error_message=f"Requested language {src_lang!r} is not supported",
            )

        target_lang = (request.target_language or "en").strip().lower()

        # If already in target language, skip translation
        if src_lang == target_lang:
            meta = TranslationResultMetadata(
                source_language=src_lang,
                target_language=target_lang,
                provider=self.provider_name,
                model=self.model_name,
                confidence=1.0,
                original_content_hash=orig_hash,
                translated_content_hash=orig_hash,
                character_count=len(request.text),
                token_count=max(1, len(request.text) // 4),
                latency_ms=1.5,
                cost_usd=Decimal("0.00"),
                detected_language=det_result.detected_language if det_result else src_lang,
            )
            return TranslationResponse(
                status=TranslationStatus.SKIPPED,
                translated_text=request.text,
                original_text=request.text,
                metadata=meta,
            )

        # Deterministic phrase translation
        translated = self._deterministic_translate(request.text, src_lang, target_lang)
        trans_hash = compute_sha256(translated)

        char_count = len(request.text)
        token_count = max(1, char_count // 4)
        cost = Decimal(str(round(token_count * 0.00002, 6)))

        meta = TranslationResultMetadata(
            source_language=src_lang,
            target_language=target_lang,
            provider=self.provider_name,
            model=self.model_name,
            confidence=0.99,
            original_content_hash=orig_hash,
            translated_content_hash=trans_hash,
            character_count=char_count,
            token_count=token_count,
            latency_ms=12.5,
            cost_usd=cost,
            detected_language=det_result.detected_language if det_result else src_lang,
        )

        return TranslationResponse(
            status=TranslationStatus.SUCCESS,
            translated_text=translated,
            original_text=request.text,
            metadata=meta,
        )

    def _deterministic_translate(self, text: str, src: str, target: str) -> str:
        """Map text through single-pass phrase substitutions while preserving numbers, IDs, and structure."""
        dictionary: dict[str, str] = {}
        if src == "es":
            dictionary = SPANISH_TO_ENGLISH
        elif src == "fr":
            dictionary = FRENCH_TO_ENGLISH
        elif src == "de":
            dictionary = GERMAN_TO_ENGLISH

        if not dictionary:
            return f"[TRANSLATED from {src.upper()} to {target.upper()}]:\n{text}"

        # Sort keys by length descending to match longest phrases first
        sorted_keys = sorted(dictionary.keys(), key=len, reverse=True)
        # Create single-pass regex matching whole phrases
        escaped_phrases = [re.escape(k) for k in sorted_keys]
        pattern = re.compile(r"(" + "|".join(escaped_phrases) + r")", re.IGNORECASE)

        def _replace(match: re.Match[str]) -> str:
            token = match.group(0).lower()
            return dictionary.get(token, match.group(0))

        result = pattern.sub(_replace, text)

        # Add translation provenance header in normalized text
        return f"[TRANSLATED from {src.upper()} to {target.upper()}]:\n{result}"
