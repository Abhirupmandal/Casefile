# ADR-002: Pydantic for Typed Inter-Agent Contracts

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `agent-contracts.md`, `agent-architecture.md`, `versioning.md`

## Context

CASEFILE requires strict contracts between agents to ensure:
- **Type safety**: Catch schema violations at validation time, not runtime
- **No free-form data**: Eliminate untyped dictionaries and implicit contracts
- **Version evolution**: Support schema changes while maintaining compatibility
- **Serialization**: Persist contracts to database and reconstruct reliably
- **Documentation**: Self-documenting contracts with explicit field semantics
- **Validation**: Enforce business rules (e.g., required fields, value ranges)

## Decision

We will use **Pydantic v2** for all inter-agent contracts.

All agent inputs and outputs are defined as Pydantic models:
- `ClaimInput` - Initial claim submission
- `ExtractionRequest` / `ExtractionResult` - Extractor agent contract
- `InvestigationRequest` / `InvestigationResult` - Investigator agent contract
- `ReviewRequest` / `ReviewResult` - Reviewer agent contract
- `WorkflowState` - Shared state machine representation
- `BudgetState` - Budget tracking contract
- `Checkpoint` - Checkpoint serialization contract

**No free-form dictionaries or untyped data structures are permitted in inter-agent communication.**

## Consequences

### Positive
- **Compile-time safety**: Mypy and pyright catch type errors before runtime
- **Runtime validation**: Pydantic validates all agent outputs, rejecting malformed LLM responses
- **Self-documenting**: JSON Schema generation provides API documentation
- **IDE support**: Autocomplete and inline documentation for contract fields
- **Serialization**: JSON serialization/deserialization built-in
- **Versioning**: Field-level version tracking enables evolution
- **Testability**: Generate test fixtures from Pydantic models

### Negative
- **Validation overhead**: Every agent output validated (CPU cost)
- **Rigidity**: Schema changes require careful migration planning
- **LLM alignment**: Models must be prompted to produce exact schema (structured output)
- **Nested complexity**: Complex nested models can be verbose

### Mitigations
- **Structured output APIs**: Use provider structured output features (OpenAI JSON mode, Anthropic tool use)
- **Schema versioning**: Include `version` field in all contracts, document breaking changes
- **Validation caching**: Cache validated models within workflow execution
- **Schema simplification**: Keep contracts as flat as feasible, avoid deep nesting

## Alternatives Considered

### 1. TypedDict (Python 3.10+)
- **Pros**: Stdlib, zero dependencies, type checking support
- **Rejected**: No runtime validation, no serialization support, no evolution strategy

### 2. Dataclasses
- **Pros**: Stdlib, simple syntax, type hints
- **Rejected**: No validation, weak serialization, no schema generation

### 3. Marshmallow
- **Pros**: Mature validation library, good serialization
- **Rejected**: Not type-hint native, weaker IDE support than Pydantic

### 4. Protobuf
- **Pros**: Strong versioning, efficient serialization, cross-language
- **Rejected**: Overhead of .proto files, less Pythonic, poor LLM alignment (binary format)

### 5. JSON Schema (raw)
- **Pros**: Language-agnostic, standard format
- **Rejected**: No Python type integration, manual validation code, verbose definitions

## Implementation Notes

### Example Contract Definition
```python
from pydantic import BaseModel, Field
from typing import Literal

class InvestigationResult(BaseModel):
    """Output from Investigator agent."""
    version: Literal["1.0"] = "1.0"
    workflow_id: str
    findings: list[str] = Field(min_length=1)
    evidence: dict[str, str]
    confidence_score: float = Field(ge=0.0, le=1.0)
    tool_calls_made: list[str]
```

### Validation Strategy
- LLM outputs are **always** validated against Pydantic models
- Validation failure triggers retry (up to 3 attempts with refined prompts)
- After max retries, workflow transitions to FAILED state

### Version Evolution
- Additive changes (new optional fields): backward compatible
- Breaking changes (removing fields, changing types): require new version
- Checkpoints store contract version to detect incompatibilities

## References

- Pydantic v2 documentation: https://docs.pydantic.dev/
- CASEFILE contracts: `docs/agent-contracts.md`
- Versioning strategy: `docs/versioning.md`
