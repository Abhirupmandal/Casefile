"""
Document retrieval tool: typed, claim-scoped, read-only.

Security: reference allowlist pattern, claim-ownership check, bounded
content size, no filesystem paths, no URLs, no path traversal, no
cross-claim leakage. Content returns as UNTRUSTED text for the Phase 4
prompt quarantine.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel

from casefile.models.domain import ClaimDocumentReference
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext, ToolError, ToolErrorCode, ToolFailureError
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool

DOCUMENT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_CONTENT_CHARS = 32768


class DocumentRetrievalInput(BaseModel):
    """Typed document request. References only — never paths or URLs."""

    model_config = {"frozen": True}

    document_ref: str
    claim_ref: str


class DocumentRetrievalOutput(BaseModel):
    """Structured metadata plus content marked UNTRUSTED."""

    model_config = {"frozen": True}

    found: bool
    document: ClaimDocumentReference | None = None
    content_untrusted: str = ""
    truncated: bool = False
    schema_version: SchemaVersion = "1.0.0"


class DocumentRetrievalTool(Tool[DocumentRetrievalInput, DocumentRetrievalOutput]):
    """Fetch one claim document by reference within claim scope."""

    name = "document_retrieval"
    version = "1.0.0"
    description = "Retrieve a claim document's metadata and content by reference"
    timeout_seconds = 5.0

    input_model = DocumentRetrievalInput
    output_model = DocumentRetrievalOutput

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE

    def run(self, tool_input: DocumentRetrievalInput, ctx: ToolContext) -> DocumentRetrievalOutput:
        """Validate reference, enforce claim scope, return bounded content."""
        ref = tool_input.document_ref.strip()
        if not DOCUMENT_REF_PATTERN.match(ref):
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.INVALID_INPUT,
                    message=f"Malformed document reference {tool_input.document_ref!r}",
                )
            )
        stored = self._store.get_document(ref)
        if stored is None:
            return DocumentRetrievalOutput(found=False)
        if stored.claim_ref != tool_input.claim_ref.strip():
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.SCOPE_VIOLATION,
                    message=f"Document {ref!r} is not in claim scope {tool_input.claim_ref!r}",
                )
            )
        content = stored.content
        truncated = len(content) > MAX_CONTENT_CHARS
        if truncated:
            content = content[:MAX_CONTENT_CHARS]
        return DocumentRetrievalOutput(
            found=True,
            document=ClaimDocumentReference(
                document_id=_stable_uuid(ref),
                document_type=stored.document_type,
                filename=stored.filename,
                content_type=stored.content_type,
                content_hash=stored.content_hash,
                source=stored.source,
                trusted_source=False,
            ),
            content_untrusted=content,
            truncated=truncated,
        )


def _stable_uuid(ref: str) -> UUID:
    import hashlib

    try:
        return UUID(ref)
    except ValueError:
        pass
    digest = hashlib.sha256(f"casefile-doc:{ref}".encode()).hexdigest()[:32]
    return UUID(hex=digest)
