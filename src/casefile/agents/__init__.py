"""
CASEFILE specialized agents (Phase 4, AGENTS.md).

Provider-agnostic specialists behind typed boundaries: supervisor,
extractor, investigator, reviewer. Real behavior, no fake intelligence;
deterministic test provider included, real adapters optional and key-gated.
"""

from casefile.agents.adapters import LangChainProviderAdapter
from casefile.agents.base import (
    AgentFailedError,
    BaseAgent,
    agent_hook_event_for,
    build_tool_context,
    lookup_tool,
)
from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider, scripted_provider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import ALLOWED_INVESTIGATOR_TOOLS, InvestigatorAgent
from casefile.agents.prompts import (
    EXTRACTOR_PROMPT,
    INVESTIGATOR_PROMPT,
    PROMPT_REGISTRY,
    REVIEWER_PROMPT,
    SUPERVISOR_PROMPT,
    PromptTemplate,
    RenderedPrompt,
    get_prompt,
    render,
)
from casefile.agents.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderTimeoutError,
)
from casefile.agents.results import AgentError, AgentErrorCategory, AgentExecutionRecord
from casefile.agents.reviewer import ALLOWED_REVIEWER_TOOLS, ReviewerAgent
from casefile.agents.supervisor_agent import SupervisorAgent

__all__ = [
    "LangChainProviderAdapter",
    "AgentFailedError",
    "BaseAgent",
    "agent_hook_event_for",
    "build_tool_context",
    "lookup_tool",
    "AgentContext",
    "DeterministicProvider",
    "scripted_provider",
    "ExtractorAgent",
    "ALLOWED_INVESTIGATOR_TOOLS",
    "InvestigatorAgent",
    "EXTRACTOR_PROMPT",
    "INVESTIGATOR_PROMPT",
    "PROMPT_REGISTRY",
    "REVIEWER_PROMPT",
    "SUPERVISOR_PROMPT",
    "PromptTemplate",
    "RenderedPrompt",
    "get_prompt",
    "render",
    "LLMMessage",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "ProviderError",
    "ProviderTimeoutError",
    "AgentError",
    "AgentErrorCategory",
    "AgentExecutionRecord",
    "ALLOWED_REVIEWER_TOOLS",
    "ReviewerAgent",
    "SupervisorAgent",
]
