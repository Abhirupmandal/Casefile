# CASEFILE Agents

**Agent Specifications and Responsibilities**

This document provides detailed specifications for the four AI agents in the CASEFILE system.

---

## Agent Architecture

CASEFILE implements a **supervisor-worker** multi-agent architecture:

```
                    ┌──────────────┐
                    │  Supervisor  │
                    │ (Coordinator)│
                    └──────┬───────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Extractor   │  │ Investigator │  │   Reviewer   │
│ (Specialist) │  │ (Specialist) │  │ (Specialist) │
└──────────────┘  └──────────────┘  └──────────────┘
```

- **Supervisor**: Orchestrates workflow, enforces budgets, no domain logic
- **Specialists**: Domain-specific agents (Extractor, Investigator, Reviewer)

---

## 1. Supervisor Agent

### Responsibility

The Supervisor is the **orchestration agent** responsible for:
- Workflow entry point (receives ClaimInput)
- Routing to specialist agents (Extractor, Investigator, Reviewer)
- State transition logic (manages WorkflowState transitions)
- Budget enforcement (pre-flight checks before agent invocations)
- Termination handling (transitions to terminal states)

**The Supervisor does NOT perform domain logic** (extraction, investigation, review).

### Contract

**Input**: `ClaimInput`

```python
class ClaimInput(BaseModel):
    claim_id: str
    policy_id: str
    claimant_name: str
    incident_date: str
    claim_amount: Decimal
    description: str
    documents: list[str]
```

**Output**: `WorkflowResult`

```python
class WorkflowResult(BaseModel):
    workflow_id: UUID
    terminal_state: WorkflowState
    terminal_reason: str
    budget_state: BudgetState
    duration_seconds: int
```

### Behavior

1. **Entry**: Receive ClaimInput
2. **Initialize**: Create workflow_id, initialize BudgetState
3. **Route to Extractor**: Transition RECEIVED → EXTRACTION, invoke Extractor
4. **Route to Investigator**: Transition EXTRACTION → INVESTIGATION, invoke Investigator
5. **Route to Reviewer**: Transition INVESTIGATION → REVIEW, invoke Reviewer
6. **Handle Reviewer Decision**:
   - APPROVE → Transition to HUMAN_APPROVAL
   - REJECT → Transition to REJECTED terminal state
   - REWORK → Transition to REWORK_LOOP, route back to Investigator
7. **Budget Enforcement**:
   - Pre-flight check before every agent invocation
   - If budget exceeded, transition to appropriate terminal state
8. **Termination**: Ensure workflow reaches terminal state

### Tools

**None** - Supervisor does not call tools directly.

### LLM Provider

- **Provider**: Anthropic
- **Model**: `claude-3-5-sonnet-20241022`
- **Temperature**: 0.0
- **Rationale**: Coordination requires minimal creativity, Claude is cost-effective

### Budget Allocation

- **Input tokens**: ~500 per invocation (workflow state + routing logic)
- **Output tokens**: ~100 per invocation (next state decision)
- **Cost**: ~$0.002 per invocation

---

## 2. Extractor Agent

### Responsibility

The Extractor **parses claim documents and extracts structured data**:
- Extract claimant information (name, contact, policy ID)
- Extract incident details (date, location, description)
- Extract claim amount and requested coverage
- Validate completeness (all required fields present)
- Output structured ExtractionResult

**The Extractor does NOT investigate or make decisions** (that's Investigator and Reviewer).

### Contract

**Input**: `ExtractionRequest`

```python
class ExtractionRequest(BaseModel):
    workflow_id: UUID
    claim_input: ClaimInput
```

**Output**: `ExtractionResult`

```python
class ExtractionResult(BaseModel):
    workflow_id: UUID

    # Extracted data
    claimant_name: str
    policy_id: str
    incident_date: str
    incident_location: str
    incident_description: str
    claim_amount: Decimal
    requested_coverage_type: str

    # Metadata
    documents_processed: list[str]
    extraction_confidence: float  # 0.0 to 1.0
    missing_fields: list[str]
    is_complete: bool
```

### Behavior

1. **Receive**: ExtractionRequest with ClaimInput
2. **Parse**: Parse claim documents (PDFs, forms)
3. **Extract**: Extract structured fields using LLM
4. **Validate**: Check for missing required fields
5. **Confidence**: Assess extraction confidence
6. **Output**: Structured ExtractionResult (Pydantic model)

### Prompt Template

```
You are an insurance claim extraction specialist. Extract structured data from the claim documents.

Claim Documents:
{documents}

Extract the following fields:
- Claimant name
- Policy ID
- Incident date
- Incident location
- Incident description
- Claim amount
- Requested coverage type

Output format: JSON matching ExtractionResult schema
Confidence: Estimate confidence (0.0 to 1.0) based on document clarity
Missing fields: List any required fields not found in documents
```

### Tools

**None** - Extractor does not call external tools. All information comes from claim documents.

### LLM Provider

- **Provider**: Anthropic
- **Model**: `claude-3-5-sonnet-20241022`
- **Temperature**: 0.0
- **Rationale**: Extraction requires structured output, Claude excels at tool use / structured output

### Budget Allocation

- **Input tokens**: ~2,000 per claim (claim documents + prompt)
- **Output tokens**: ~300 per claim (structured extraction)
- **Cost**: ~$0.010 per claim

---

## 3. Investigator Agent

### Responsibility

The Investigator **gathers evidence using external tools**:
- Look up policy details (coverage, exclusions, conditions)
- Retrieve claim history (prior claims, patterns)
- Validate repair costs (market rates, reasonableness)
- Check fraud signals (fraud database, anomaly detection)
- Retrieve supporting documents (police reports, estimates)
- Synthesize findings into InvestigationResult

**The Investigator does NOT make decisions** (that's Reviewer). It only gathers evidence.

### Contract

**Input**: `InvestigationRequest`

```python
class InvestigationRequest(BaseModel):
    workflow_id: UUID
    extraction_result: ExtractionResult
    rework_feedback: str | None = None  # If Reviewer triggered rework
```

**Output**: `InvestigationResult`

```python
class InvestigationResult(BaseModel):
    workflow_id: UUID

    # Investigation findings
    policy_details: dict[str, Any]
    claim_history: dict[str, Any]
    repair_cost_validation: dict[str, Any]
    fraud_signals: dict[str, Any]
    supporting_documents: list[str]

    # Synthesis
    findings_summary: str
    evidence_strength: float  # 0.0 to 1.0
    tool_calls_made: list[str]

    # Metadata
    investigation_duration_seconds: int
    tools_succeeded: int
    tools_failed: int
```

### Behavior

1. **Receive**: InvestigationRequest (includes ExtractionResult)
2. **Plan**: Determine which tools to call based on claim type
3. **Execute Tools**: Call tools iteratively
   - `policy_lookup`: Retrieve policy details
   - `claim_history_lookup`: Check prior claims
   - `repair_cost_lookup`: Validate claimed costs
   - `fraud_signal_lookup`: Check fraud indicators
   - `document_retrieval`: Retrieve supporting docs
4. **Synthesize**: Aggregate evidence, summarize findings
5. **Output**: Structured InvestigationResult (Pydantic model)

### Prompt Template

```
You are an insurance claim investigator. Gather evidence to support claim adjudication.

Claim Details:
{extraction_result}

Available Tools:
- policy_lookup: Retrieve policy details (coverage, exclusions, conditions)
- claim_history_lookup: Check claimant's prior claims
- repair_cost_lookup: Validate repair cost estimates against market rates
- fraud_signal_lookup: Check for fraud indicators
- document_retrieval: Retrieve supporting documents

{rework_feedback}

Use tools iteratively to gather evidence. After each tool call, decide:
1. Is more evidence needed? → Call another tool
2. Is evidence sufficient? → Synthesize findings

Output format: JSON matching InvestigationResult schema
```

### Tools

| Tool | Description | Authorization | Cache TTL |
|------|-------------|---------------|-----------|
| `policy_lookup` | Retrieve policy details | ✅ Authorized | 1 hour |
| `claim_history_lookup` | Retrieve claim history | ✅ Authorized | 1 hour |
| `repair_cost_lookup` | Validate repair costs | ✅ Authorized | 1 hour |
| `fraud_signal_lookup` | Check fraud signals | ✅ Authorized | 30 min |
| `document_retrieval` | Retrieve documents | ✅ Authorized | 2 hours |

**Tool Execution**: Investigator uses LLM tool calling (function calling) to invoke tools.

### LLM Provider

- **Provider**: OpenAI
- **Model**: `gpt-4o`
- **Temperature**: 0.3
- **Rationale**: Investigation benefits from reasoning, GPT-4o has strong tool use

### Budget Allocation

- **Input tokens**: ~5,000 per investigation (claim + tool responses + multi-turn conversation)
- **Output tokens**: ~1,000 per investigation (tool calls + synthesis)
- **Cost**: ~$0.025 per investigation

---

## 4. Reviewer Agent

### Responsibility

The Reviewer **evaluates investigation completeness and recommends a decision**:
- Assess evidence quality (is investigation thorough?)
- Identify gaps (missing evidence, unclear findings)
- Recommend decision: APPROVE, REJECT, or REWORK
- Provide reasoning for recommendation
- Assign confidence score

**The Reviewer does NOT execute the final decision** (that's human approval gate).

### Contract

**Input**: `ReviewRequest`

```python
class ReviewRequest(BaseModel):
    workflow_id: UUID
    extraction_result: ExtractionResult
    investigation_result: InvestigationResult
    rework_count: int  # How many rework cycles so far
```

**Output**: `ReviewResult`

```python
class ReviewResult(BaseModel):
    workflow_id: UUID

    # Review decision
    decision: Literal["APPROVE", "REJECT", "REWORK"]
    reasoning: str
    confidence_score: float  # 0.0 to 1.0

    # Evidence assessment
    evidence_completeness: float  # 0.0 to 1.0
    identified_gaps: list[str]

    # Rework guidance (if decision == REWORK)
    rework_feedback: str | None = None

    # Fraud assessment
    fraud_risk_level: Literal["LOW", "MEDIUM", "HIGH"]
    fraud_signals_detected: bool
```

### Behavior

1. **Receive**: ReviewRequest (includes ExtractionResult + InvestigationResult)
2. **Evaluate Evidence**:
   - Is investigation thorough?
   - Are all required evidence sources consulted?
   - Is evidence quality sufficient?
3. **Identify Gaps**: Missing evidence, unclear findings, contradictions
4. **Recommend Decision**:
   - **APPROVE**: Evidence supports claim, all conditions met
   - **REJECT**: Evidence refutes claim, or policy exclusion applies
   - **REWORK**: Gaps in evidence, need more investigation
5. **Confidence**: Assess decision confidence
6. **Output**: Structured ReviewResult (Pydantic model)

### Prompt Template

```
You are an insurance claim reviewer. Evaluate investigation completeness and recommend a decision.

Extraction:
{extraction_result}

Investigation:
{investigation_result}

Rework Count: {rework_count} / 3

Evaluate:
1. Evidence Completeness: Is investigation thorough? Are all required sources consulted?
2. Evidence Quality: Is evidence sufficient to make a decision?
3. Gaps: What evidence is missing or unclear?

Recommend Decision:
- APPROVE: Evidence supports claim, policy conditions met, no exclusions apply
- REJECT: Evidence refutes claim, OR policy exclusion applies, OR fraud detected
- REWORK: Evidence gaps exist, more investigation needed (max 3 rework cycles)

If REWORK: Provide specific feedback for Investigator (what to investigate next)

Output format: JSON matching ReviewResult schema
```

### Tools

| Tool | Description | Authorization | Cache TTL |
|------|-------------|---------------|-----------|
| `document_retrieval` | Retrieve documents | ✅ Authorized | 2 hours |

**Rationale**: Reviewer can retrieve documents to verify investigation findings, but cannot call other tools (policy lookup, fraud detection, etc.) — that's Investigator's job.

### LLM Provider

- **Provider**: Anthropic
- **Model**: `claude-3-5-sonnet-20241022`
- **Temperature**: 0.0
- **Rationale**: Review requires consistent judgment, Claude is more conservative

### Budget Allocation

- **Input tokens**: ~6,000 per review (extraction + investigation + prompt)
- **Output tokens**: ~500 per review (decision + reasoning)
- **Cost**: ~$0.025 per review

---

## Agent Comparison

| Agent | Responsibility | Tools | Decision-Making | Temperature |
|-------|----------------|-------|-----------------|-------------|
| Supervisor | Orchestration, routing | None | Yes (routing) | 0.0 |
| Extractor | Parse documents | None | No | 0.0 |
| Investigator | Gather evidence | 5 tools | No | 0.3 |
| Reviewer | Evaluate evidence | 1 tool | Yes (recommend) | 0.0 |

---

## Agent Handoffs

### 1. Supervisor → Extractor

```python
extraction_request = ExtractionRequest(
    workflow_id=workflow_id,
    claim_input=claim_input,
)
extraction_result = await extractor.invoke(extraction_request)
```

### 2. Extractor → Supervisor → Investigator

```python
investigation_request = InvestigationRequest(
    workflow_id=workflow_id,
    extraction_result=extraction_result,
    rework_feedback=None,  # First investigation
)
investigation_result = await investigator.invoke(investigation_request)
```

### 3. Investigator → Supervisor → Reviewer

```python
review_request = ReviewRequest(
    workflow_id=workflow_id,
    extraction_result=extraction_result,
    investigation_result=investigation_result,
    rework_count=0,
)
review_result = await reviewer.invoke(review_request)
```

### 4. Reviewer (REWORK) → Supervisor → Investigator

```python
# Reviewer decision: REWORK
investigation_request = InvestigationRequest(
    workflow_id=workflow_id,
    extraction_result=extraction_result,
    rework_feedback=review_result.rework_feedback,  # Specific guidance
)
investigation_result = await investigator.invoke(investigation_request)
```

---

## Agent Authorization Matrix

| Agent | policy_lookup | claim_history_lookup | repair_cost_lookup | fraud_signal_lookup | document_retrieval |
|-------|---------------|----------------------|--------------------|---------------------|--------------------|
| Supervisor | ❌ | ❌ | ❌ | ❌ | ❌ |
| Extractor | ❌ | ❌ | ❌ | ❌ | ❌ |
| Investigator | ✅ | ✅ | ✅ | ✅ | ✅ |
| Reviewer | ❌ | ❌ | ❌ | ❌ | ✅ |

**Rationale**:
- **Supervisor & Extractor**: No tool access (work only with claim documents)
- **Investigator**: Full tool access (gather evidence)
- **Reviewer**: Read-only document retrieval (verify findings)

---

## Agent Testing

### Unit Tests

Each agent has unit tests covering:
- ✅ Valid input → Valid output (Pydantic validation)
- ✅ Malformed LLM output → Validation error → Retry
- ✅ Missing required fields → Error handling
- ✅ Budget exhaustion → Reject invocation

### Integration Tests

- ✅ Extractor → Investigator handoff
- ✅ Investigator → Reviewer handoff
- ✅ Reviewer REWORK → Investigator loop
- ✅ End-to-end: ClaimInput → Terminal state

### Evaluation Tests

- ✅ 30+ evaluation scenarios (see `docs/evaluation.md`)
- ✅ Replay from checkpoints
- ✅ Deterministic execution verification

---

## Agent Observability

Every agent invocation is instrumented with OpenTelemetry spans:

```python
with tracer.start_as_current_span("agent_invocation") as span:
    span.set_attribute("agent_name", "investigator")
    span.set_attribute("workflow_id", str(workflow_id))
    span.set_attribute("model", "gpt-4o")
    span.set_attribute("input_tokens", 5234)
    span.set_attribute("output_tokens", 892)
    span.set_attribute("cost_usd", 0.0245)
    span.set_attribute("latency_ms", 3420)
```

**Trace hierarchy**:

```
workflow_run
├── agent_invocation (Extractor)
├── agent_invocation (Investigator)
│   ├── tool_call (policy_lookup)
│   ├── tool_call (claim_history_lookup)
│   └── tool_call (fraud_signal_lookup)
└── agent_invocation (Reviewer)
```

See [`docs/observability.md`](docs/observability.md) for complete instrumentation details.

---

## Agent Prompt Evolution

Agent prompts are versioned and tracked:

```python
class AgentPrompt(BaseModel):
    agent_name: str
    prompt_template_version: str  # e.g., "extractor-v1.2"
    model_version: str            # e.g., "gpt-4o-2024-08-06"
    prompt_text: str
```

**Version changes trigger evaluation re-run** to detect regressions.

See [`docs/versioning.md`](docs/versioning.md) for versioning strategy.

---

## References

- Architecture: [`docs/agent-architecture.md`](docs/agent-architecture.md)
- Contracts: [`docs/agent-contracts.md`](docs/agent-contracts.md)
- State Machine: [`docs/state-machine.md`](docs/state-machine.md)
- Tool Architecture: [`docs/tool-architecture.md`](docs/tool-architecture.md)
- Evaluation: [`docs/evaluation.md`](docs/evaluation.md)

---

**AGENTS.md** - CASEFILE Agent Specifications
