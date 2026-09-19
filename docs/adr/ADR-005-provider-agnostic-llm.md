# ADR-005: Provider-Agnostic LLM Abstraction

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `agent-architecture.md`, `versioning.md`, `budget-control.md`

## Context

CASEFILE must support multiple LLM providers to:
- **Avoid vendor lock-in**: Switch providers without rewriting agent logic
- **Cost optimization**: Use different providers for different agents based on cost/performance
- **Resilience**: Failover to backup provider if primary unavailable
- **Model evolution**: Test new models without changing orchestration code
- **Compliance**: Use specific providers for regulatory requirements

Requirements:
- **Unified interface**: Single API for all agent invocations
- **Provider-specific features**: Support structured output, tool calling, streaming
- **Token tracking**: Accurate token counts across providers (input/output)
- **Cost attribution**: Map token counts to USD costs per provider/model
- **Version tracking**: Log provider, model, prompt version for reproducibility

## Decision

We will implement a **provider-agnostic LLM abstraction layer** that wraps provider-specific SDKs.

### Abstraction Interface

```python
from abc import ABC, abstractmethod
from typing import TypeVar, Type
from pydantic import BaseModel

T = TypeVar('T', bound=BaseModel)

class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def invoke_structured(
        self,
        prompt: str,
        response_model: Type[T],
        model: str,
        temperature: float = 0.0,
    ) -> tuple[T, InvocationMetadata]:
        """Invoke LLM with structured output validation."""
        pass

    @abstractmethod
    def count_tokens(self, text: str, model: str) -> int:
        """Count tokens for budget tracking."""
        pass

class InvocationMetadata(BaseModel):
    """Metadata from LLM invocation."""
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    prompt_version: str
```

### Supported Providers

1. **OpenAI** (GPT-4, GPT-4o)
   - Structured output via JSON mode
   - Native token counting via tiktoken
   - Cost: $0.03/1K input, $0.06/1K output (GPT-4o)

2. **Anthropic** (Claude 3.5 Sonnet, Opus)
   - Structured output via tool use pattern
   - Token counting via Anthropic API
   - Cost: $0.003/1K input, $0.015/1K output (Sonnet)

3. **Azure OpenAI** (Enterprise deployments)
   - Same API as OpenAI, different endpoint
   - Required for regulated industries

### Agent-Provider Mapping

```yaml
agents:
  extractor:
    provider: anthropic
    model: claude-3-5-sonnet-20241022
    temperature: 0.0
    rationale: "Extraction requires structured output, Claude excels at tool use"

  investigator:
    provider: openai
    model: gpt-4o
    temperature: 0.3
    rationale: "Investigation benefits from reasoning, GPT-4o has strong tool use"

  reviewer:
    provider: anthropic
    model: claude-3-5-sonnet-20241022
    temperature: 0.0
    rationale: "Review requires consistent judgment, Claude is more conservative"

  supervisor:
    provider: anthropic
    model: claude-3-5-sonnet-20241022
    temperature: 0.0
    rationale: "Coordination requires minimal creativity, Claude is cost-effective"
```

## Consequences

### Positive
- **Vendor independence**: Switch providers by changing configuration, not code
- **Cost optimization**: Use cheapest model adequate for each agent
- **A/B testing**: Compare providers on same evaluation corpus
- **Graceful degradation**: Failover to backup provider on errors
- **Model evolution**: Adopt new models without orchestration changes
- **Budget accuracy**: Unified token tracking across providers

### Negative
- **Abstraction overhead**: Wrapper code adds complexity
- **Feature parity**: Not all providers support same features (streaming, tools)
- **Token counting variance**: Different tokenizers yield different counts
- **Testing complexity**: Must test against multiple provider implementations
- **Cost model updates**: Provider pricing changes require config updates

### Mitigations
- **Provider feature flags**: Detect capabilities at runtime (e.g., native structured output)
- **Token count buffering**: Add 5% safety margin for token count variance
- **Mock provider**: Test implementation for deterministic unit tests
- **Cost update monitoring**: Alert when provider pricing changes detected
- **Structured output fallback**: Parse JSON from text if provider lacks native support

## Alternatives Considered

### 1. Direct provider SDK usage
- **Pros**: Full feature access, no abstraction overhead
- **Rejected**: Vendor lock-in, difficult to switch providers, hard to A/B test

### 2. LangChain ChatModel abstraction
- **Pros**: Existing abstraction, community support
- **Rejected**: Heavy dependency, more features than needed, less control over token counting

### 3. LiteLLM
- **Pros**: Unified API for 100+ providers, active development
- **Rejected**: External dependency, potential breaking changes, less control over cost attribution

### 4. OpenAI-compatible proxy (vLLM, LiteLLM proxy)
- **Pros**: Single interface, load balancing
- **Rejected**: Additional infrastructure, latency overhead, less visibility into provider-specific behavior

## Implementation Strategy

### Phase 1: Core Providers
- Implement OpenAI and Anthropic providers
- Structured output using provider-specific best practices
- Token counting and cost attribution

### Phase 2: Enterprise Features
- Azure OpenAI support (required for regulated deployments)
- Failover and retry logic across providers
- Provider health checks and circuit breakers

### Phase 3: Optimization
- Prompt caching (where supported)
- Streaming for low-latency applications
- Batch API support for evaluation runs

## Token Counting Strategy

```python
# OpenAI: Use tiktoken
import tiktoken
encoder = tiktoken.encoding_for_model("gpt-4o")
token_count = len(encoder.encode(text))

# Anthropic: Use Anthropic API
from anthropic import Anthropic
client = Anthropic()
token_count = client.count_tokens(text)

# Budget pre-flight check
estimated_tokens = count_input_tokens(prompt) + estimated_output_tokens
if budget_state.total_tokens + estimated_tokens > max_tokens:
    raise BudgetExceeded()
```

## Cost Attribution

```python
# Cost table (updated quarterly)
COST_PER_1K_TOKENS = {
    "gpt-4o": {"input": 0.0025, "output": 0.010},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "claude-3-5-sonnet-20241022": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku-20241022": {"input": 0.0008, "output": 0.004},
}

def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = COST_PER_1K_TOKENS[model]
    input_cost = (input_tokens / 1000) * pricing["input"]
    output_cost = (output_tokens / 1000) * pricing["output"]
    return input_cost + output_cost
```

## References

- OpenAI API documentation: https://platform.openai.com/docs/
- Anthropic API documentation: https://docs.anthropic.com/
- CASEFILE agent architecture: `docs/agent-architecture.md`
- Budget control: `docs/budget-control.md`
