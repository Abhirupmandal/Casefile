# ADR-001: LangGraph for Workflow Orchestration

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `architecture.md`, `state-machine.md`, `agent-architecture.md`

## Context

CASEFILE requires a workflow orchestration framework that can:
- Model complex state machines with 14 states and conditional transitions
- Coordinate 4 specialized agents (Supervisor, Extractor, Investigator, Reviewer)
- Support checkpoint/resume for long-running workflows
- Provide deterministic execution for replay and testing
- Integrate with LLM providers while maintaining workflow control
- Enforce budget limits and termination guarantees

## Decision

We will use **LangGraph** as the workflow orchestration framework.

LangGraph provides:
- **Graph-based workflow definition**: States and edges model our state machine naturally
- **Built-in checkpointing**: Native support for saving/restoring workflow state
- **Agent coordination**: First-class support for multi-agent systems
- **Typed state management**: Compatible with Pydantic contracts
- **Deterministic execution**: Replay capability for testing and debugging
- **LLM integration**: Provider-agnostic abstractions for model calls

## Consequences

### Positive
- **Declarative workflow definition**: State machine expressed as graph structure
- **Built-in persistence**: Checkpoint mechanism reduces custom code
- **Testing support**: Deterministic replay simplifies evaluation
- **Community patterns**: LangGraph patterns for multi-agent coordination
- **Observability hooks**: Integration points for OpenTelemetry instrumentation

### Negative
- **Framework dependency**: Tied to LangGraph lifecycle and breaking changes
- **Learning curve**: Team must learn LangGraph-specific patterns
- **Abstraction leakage**: Some LangGraph implementation details may surface
- **Version lock-in**: Checkpoints tied to specific LangGraph versions

### Mitigations
- **Version pinning**: Lock LangGraph version, test upgrades thoroughly
- **Adapter pattern**: Wrap LangGraph APIs to isolate framework dependencies
- **Documentation**: Maintain team knowledge base for LangGraph patterns
- **Checkpoint versioning**: Include workflow_definition_version to detect incompatibilities

## Alternatives Considered

### 1. Temporal.io
- **Pros**: Battle-tested orchestration, strong durability guarantees, workflow versioning
- **Rejected**: Heavyweight infrastructure (requires separate service), less LLM-native

### 2. Prefect
- **Pros**: Python-native, good observability, dynamic workflows
- **Rejected**: Less focused on LLM/agent use cases, weaker checkpoint semantics

### 3. Custom state machine
- **Pros**: Full control, minimal dependencies, tailored to exact needs
- **Rejected**: Significant engineering effort, reinventing solved problems (checkpointing, replay)

### 4. Airflow
- **Pros**: Mature ecosystem, strong scheduling
- **Rejected**: Batch-oriented (not real-time), heavy infrastructure, DAG model doesn't fit conditional state machine

## References

- LangGraph documentation: https://langchain-ai.github.io/langgraph/
- CASEFILE state machine: `docs/state-machine.md`
- Checkpoint strategy: `docs/checkpointing.md`
