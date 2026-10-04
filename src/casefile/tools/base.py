"""
Abstract base class and contract for typed CASEFILE tools (Phase 3).

Every tool declares:
- stable tool name
- tool version
- input model (Pydantic v2)
- output model (Pydantic v2)
- authorized agents
- trust classification
- execution mode
- description
- schema version
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel

from casefile.models.domain import AgentType
from casefile.models.versioning import SchemaVersion
from casefile.tools.context import ToolContext

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class Tool(ABC, Generic[InputT, OutputT]):
    """Base class for strongly-typed, read-only CASEFILE tools."""

    name: str = ""
    version: str = "1.0.0"
    description: str = ""
    timeout_seconds: float = 10.0
    max_results: int = 50
    cost_units: int = 1
    trust_classification: str = "UNTRUSTED_EXTERNAL"
    execution_mode: str = "READ_ONLY"
    schema_version: SchemaVersion = "1.0.0"
    authorized_agents: frozenset[AgentType] = frozenset()

    input_model: type[InputT]
    output_model: type[OutputT]

    @abstractmethod
    def run(self, tool_input: InputT, ctx: ToolContext) -> OutputT:
        """Execute tool logic against repository boundaries; return typed output."""
        ...
