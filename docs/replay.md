# CASEFILE Replay Architecture

## Overview

Replay is a critical capability that allows stored workflow runs to be re-executed from checkpoints. This enables:
- Debugging of production issues
- Audit verification
- Regression testing
- Determinism verification
- Post-hoc analysis

This document defines the replay mechanism, handling of nondeterminism, and verification strategies.

## Design Principles

### 1. Deterministic Where Possible

Replay should produce the same terminal state as the original run, accounting for LLM nondeterminism.

### 2. Recorded Tool Outputs

All tool outputs from the original run are recorded and replayed exactly, ensuring determinism for external calls.

### 3. Replay Modes

Multiple replay modes support different use cases:
- **Exact Replay**: Use recorded tool outputs, allow LLM variance
- **Deterministic Replay**: Use recorded tool outputs AND recorded LLM responses
- **Fresh Replay**: Re-execute everything fresh (for testing)

### 4. Comparison and Verification

Replay produces a detailed comparison between original and replayed runs for verification.

## Replay Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           REPLAY ARCHITECTURE                                │
│                                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                  │
│  │   Original   │    │  Checkpoint  │    │   Replay     │                  │
│  │     Run      │───►│   Storage    │───►│    Engine    │                  │
│  └──────────────┘    └──────────────┘    └──────────────┘                  │
│         │                                        │                          │
│         │                                        │                          │
│         ▼                                        ▼                          │
│  ┌──────────────┐                       ┌──────────────┐                  │
│  │   Recorded   │                       │   Replay     │                  │
│  │   Outputs    │──────────────────────►│   Executor   │                  │
│  └──────────────┘                       └──────────────┘                  │
│         │                                        │                          │
│         │                                        │                          │
│         ▼                                        ▼                          │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         COMPARISON ENGINE                             │  │
│  │                                                                       │  │
│  │   • State path comparison                                            │  │
│  │   • Result comparison                                                 │  │
│  │   • Token usage comparison                                            │  │
│  │   • Cost comparison                                                   │  │
│  │   • Error comparison                                                  │  │
│  │                                                                       │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                     │                                       │
│                                     ▼                                       │
│                          ┌──────────────────┐                               │
│                          │ Verification     │                               │
│                          │    Report        │                               │
│                          └──────────────────┘                               │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Replay Data Model

### Replay Request

```python
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from uuid import UUID
from pydantic import BaseModel, Field


class ReplayRequest(BaseModel):
    """
    Request to replay a workflow from a checkpoint.
    """

    # Identification
    replay_request_id: UUID = Field(default_factory=uuid4)

    # Source
    original_workflow_run_id: UUID
    checkpoint_id: Optional[UUID] = None  # If None, use latest

    # Mode
    replay_mode: "ReplayMode"

    # Configuration
    stop_at_state: Optional[WorkflowState] = None  # Stop at specific state
    record_llm_responses: bool = True  # Record LLM responses for comparison
    enable_comparison: bool = True  # Generate comparison report

    # Context
    requested_by: str
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    reason: Optional[str] = None


class ReplayMode(str, Enum):
    """
    Replay mode determines how non-deterministic elements are handled.
    """

    # Use recorded tool outputs, allow fresh LLM calls
    EXACT = "EXACT"

    # Use recorded tool outputs AND recorded LLM responses
    DETERMINISTIC = "DETERMINISTIC"

    # Re-execute everything fresh (for testing)
    FRESH = "FRESH"

    # Step-through mode for debugging
    DEBUG = "DEBUG"
```

### Replay Run

```python
class ReplayRun(BaseModel):
    """
    Represents a replay execution.
    """

    # Identification
    replay_run_id: UUID = Field(default_factory=uuid4)
    replay_request_id: UUID

    # Source reference
    original_workflow_run_id: UUID
    source_checkpoint_id: UUID

    # Execution
    mode: ReplayMode
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: "ReplayStatus" = ReplayStatus.RUNNING

    # Results
    terminal_state: Optional[WorkflowState] = None
    comparison_report: Optional["ComparisonReport"] = None

    # Metadata
    step_count: int = 0
    deviation_points: List["DeviationPoint"] = []
    errors: List[str] = []


class ReplayStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEVIATION_DETECTED = "DEVIATION_DETECTED"
```

### Comparison Report

```python
class ComparisonReport(BaseModel):
    """
    Detailed comparison between original and replayed runs.
    """

    # Summary
    is_identical: bool
    match_percentage: float

    # State path comparison
    state_path_match: bool
    original_path: List[WorkflowState]
    replayed_path: List[WorkflowState]
    path_divergence_point: Optional[int] = None

    # Result comparison
    result_match: bool
    result_differences: List["ResultDifference"]

    # Token comparison
    token_usage_match: bool
    original_tokens: TokenUsage
    replayed_tokens: TokenUsage
    token_variance_percentage: float

    # Cost comparison
    cost_match: bool
    original_cost: Decimal
    replayed_cost: Decimal
    cost_variance_percentage: float

    # Timing comparison
    timing_match: bool
    original_duration_ms: int
    replayed_duration_ms: int
    timing_variance_percentage: float

    # Errors
    errors_match: bool
    original_errors: List[str]
    replayed_errors: List[str]

    # Details
    detailed_comparison: Dict[str, Any]


class ResultDifference(BaseModel):
    """
    A difference between original and replayed results.
    """

    field_path: str
    original_value: Any
    replayed_value: Any
    difference_type: str  # "value_mismatch", "missing", "extra"
    significance: str  # "critical", "major", "minor"


class DeviationPoint(BaseModel):
    """
    A point where replay deviated from original.
    """

    step_number: int
    state: WorkflowState
    deviation_type: str
    description: str
    original_value: Any
    replayed_value: Any
```

## Replay Engine

### Core Interface

```python
from abc import ABC, abstractmethod


class ReplayEngine(ABC):
    """
    Engine for replaying workflow executions.
    """

    @abstractmethod
    async def replay(
        self,
        request: ReplayRequest
    ) -> ReplayRun:
        """
        Execute a replay request.
        """
        ...

    @abstractmethod
    async def get_replay_run(
        self,
        replay_run_id: UUID
    ) -> ReplayRun:
        """
        Get a replay run by ID.
        """
        ...

    @abstractmethod
    async def get_comparison_report(
        self,
        replay_run_id: UUID
    ) -> ComparisonReport:
        """
        Get the comparison report for a replay run.
        """
        ...
```

### Implementation

```python
import json
from datetime import datetime


class DefaultReplayEngine(ReplayEngine):
    """
    Default implementation of the replay engine.
    """

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        workflow_executor: WorkflowExecutor,
        comparison_engine: ComparisonEngine,
        llm_response_cache: LLMResponseCache,
        tool_output_cache: ToolOutputCache
    ):
        self.checkpoint_manager = checkpoint_manager
        self.executor = workflow_executor
        self.comparison_engine = comparison_engine
        self.llm_cache = llm_response_cache
        self.tool_cache = tool_output_cache

    async def replay(
        self,
        request: ReplayRequest
    ) -> ReplayRun:
        """
        Execute a replay request.
        """

        # Create replay run record
        replay_run = ReplayRun(
            replay_request_id=request.replay_request_id,
            original_workflow_run_id=request.original_workflow_run_id,
            source_checkpoint_id=request.checkpoint_id or UUID(int=0),
            mode=request.replay_mode
        )

        try:
            # 1. Load original workflow run
            original_run = await self._load_original_run(request.original_workflow_run_id)

            # 2. Load source checkpoint
            checkpoint = await self._load_source_checkpoint(
                request.checkpoint_id,
                request.original_workflow_run_id
            )
            replay_run.source_checkpoint_id = checkpoint.checkpoint_id

            # 3. Load recorded outputs based on mode
            recorded_outputs = await self._load_recorded_outputs(
                checkpoint,
                request.replay_mode
            )

            # 4. Create replay executor
            replay_executor = self._create_replay_executor(
                checkpoint,
                request.replay_mode,
                recorded_outputs
            )

            # 5. Execute replay
            replay_result = await replay_executor.execute()

            # 6. Generate comparison report
            if request.enable_comparison:
                comparison = await self.comparison_engine.compare(
                    original_run,
                    replay_result,
                    checkpoint
                )
                replay_run.comparison_report = comparison

                if not comparison.is_identical:
                    replay_run.status = ReplayStatus.DEVIATION_DETECTED
                else:
                    replay_run.status = ReplayStatus.COMPLETED
            else:
                replay_run.status = ReplayStatus.COMPLETED

            # 7. Record completion
            replay_run.completed_at = datetime.utcnow()
            replay_run.terminal_state = replay_result.terminal_state
            replay_run.step_count = replay_result.step_count

            return replay_run

        except Exception as e:
            replay_run.status = ReplayStatus.FAILED
            replay_run.errors.append(str(e))
            replay_run.completed_at = datetime.utcnow()
            return replay_run

    async def _load_original_run(
        self,
        workflow_run_id: UUID
    ) -> WorkflowRun:
        """Load the original workflow run."""

        run = await self.workflow_repository.get_by_id(workflow_run_id)
        if not run:
            raise ReplayError(f"Original run {workflow_run_id} not found")

        return run

    async def _load_source_checkpoint(
        self,
        checkpoint_id: Optional[UUID],
        workflow_run_id: UUID
    ) -> Checkpoint:
        """Load the source checkpoint for replay."""

        if checkpoint_id:
            return await self.checkpoint_manager.load_checkpoint(checkpoint_id)
        else:
            # Use latest checkpoint
            checkpoint = await self.checkpoint_manager.get_latest_checkpoint(
                workflow_run_id
            )
            if not checkpoint:
                raise ReplayError(f"No checkpoint found for run {workflow_run_id}")
            return checkpoint

    async def _load_recorded_outputs(
        self,
        checkpoint: Checkpoint,
        mode: ReplayMode
    ) -> RecordedOutputs:
        """Load recorded outputs based on replay mode."""

        outputs = RecordedOutputs()

        # Always load tool outputs from checkpoint
        outputs.tool_outputs = checkpoint.recorded_tool_outputs

        # Load LLM responses for deterministic mode
        if mode == ReplayMode.DETERMINISTIC:
            outputs.llm_responses = await self._load_llm_responses(
                checkpoint.workflow_run_id
            )

        return outputs

    def _create_replay_executor(
        self,
        checkpoint: Checkpoint,
        mode: ReplayMode,
        recorded_outputs: RecordedOutputs
    ) -> "ReplayExecutor":
        """Create a replay executor based on mode."""

        if mode == ReplayMode.DETERMINISTIC:
            return DeterministicReplayExecutor(
                checkpoint=checkpoint,
                recorded_outputs=recorded_outputs
            )
        elif mode == ReplayMode.EXACT:
            return ExactReplayExecutor(
                checkpoint=checkpoint,
                recorded_outputs=recorded_outputs
            )
        elif mode == ReplayMode.FRESH:
            return FreshReplayExecutor(
                checkpoint=checkpoint
            )
        elif mode == ReplayMode.DEBUG:
            return DebugReplayExecutor(
                checkpoint=checkpoint,
                recorded_outputs=recorded_outputs
            )
        else:
            raise ReplayError(f"Unknown replay mode: {mode}")
```

## Replay Executors

### Deterministic Replay Executor

```python
class DeterministicReplayExecutor:
    """
    Executor for deterministic replay using all recorded outputs.
    """

    def __init__(
        self,
        checkpoint: Checkpoint,
        recorded_outputs: RecordedOutputs
    ):
        self.checkpoint = checkpoint
        self.recorded_outputs = recorded_outputs

    async def execute(self) -> ReplayResult:
        """
        Execute replay deterministically.
        """

        # Reconstruct context
        context = ExecutionContext(
            workflow_run_id=self.checkpoint.workflow_run_id,
            claim_id=self.checkpoint.claim_id,
            current_state=self.checkpoint.state,
            state_data=self.checkpoint.state_data,
            step_count=self.checkpoint.step_count,
            budget_state=self.checkpoint.budget_state
        )

        # Create mock LLM provider that returns recorded responses
        mock_llm = RecordedLLMProvider(
            recorded_responses=self.recorded_outputs.llm_responses
        )

        # Create mock tool executor that returns recorded outputs
        mock_tools = RecordedToolExecutor(
            recorded_outputs=self.recorded_outputs.tool_outputs
        )

        # Execute workflow with mocks
        result = await self._execute_workflow(
            context,
            llm_provider=mock_llm,
            tool_executor=mock_tools
        )

        return result

    async def _execute_workflow(
        self,
        context: ExecutionContext,
        llm_provider: LLMProvider,
        tool_executor: ToolExecutor
    ) -> ReplayResult:
        """
        Execute the workflow from checkpoint state.
        """

        # Execute based on current state
        while context.current_state not in TERMINAL_STATES:

            # Get next action
            action = await self._get_next_action(context)

            # Execute action with mocks
            if action.type == "node_execution":
                result = await self._execute_node(
                    action.agent,
                    context,
                    llm_provider,
                    tool_executor
                )
            elif action.type == "state_transition":
                result = await self._execute_transition(
                    action.source,
                    action.destination,
                    context
                )

            # Update context
            context = result.context
            context.step_count += 1

        return ReplayResult(
            terminal_state=context.current_state,
            step_count=context.step_count,
            budget_state=context.budget_state
        )
```

### Exact Replay Executor

```python
class ExactReplayExecutor:
    """
    Executor for exact replay using recorded tool outputs but fresh LLM calls.
    """

    def __init__(
        self,
        checkpoint: Checkpoint,
        recorded_outputs: RecordedOutputs
    ):
        self.checkpoint = checkpoint
        self.recorded_outputs = recorded_outputs

    async def execute(self) -> ReplayResult:
        """
        Execute replay with exact tool outputs but fresh LLM calls.
        """

        # Reconstruct context
        context = ExecutionContext(
            workflow_run_id=self.checkpoint.workflow_run_id,
            claim_id=self.checkpoint.claim_id,
            current_state=self.checkpoint.state,
            state_data=self.checkpoint.state_data,
            step_count=self.checkpoint.step_count,
            budget_state=self.checkpoint.budget_state
        )

        # Use real LLM provider
        llm_provider = get_real_llm_provider()

        # Use recorded tool executor
        mock_tools = RecordedToolExecutor(
            recorded_outputs=self.recorded_outputs.tool_outputs
        )

        # Execute workflow
        result = await self._execute_workflow(
            context,
            llm_provider=llm_provider,
            tool_executor=mock_tools
        )

        return result
```

### Recorded LLM Provider

```python
class RecordedLLMProvider(LLMProvider):
    """
    LLM provider that returns recorded responses for deterministic replay.
    """

    def __init__(
        self,
        recorded_responses: Dict[str, LLMResponse]
    ):
        self.recorded = recorded_responses
        self.call_index = 0

    async def complete(
        self,
        prompt: str,
        response_model: type,
        **kwargs
    ) -> Any:
        """
        Return the next recorded response.
        """

        # Get response by index
        key = f"llm_call_{self.call_index}"

        if key not in self.recorded:
            raise ReplayError(
                f"No recorded LLM response at index {self.call_index}"
            )

        recorded = self.recorded[key]
        self.call_index += 1

        # Validate response matches expected model
        return response_model.model_validate(recorded.output)
```

### Recorded Tool Executor

```python
class RecordedToolExecutor(ToolExecutor):
    """
    Tool executor that returns recorded outputs for deterministic replay.
    """

    def __init__(
        self,
        recorded_outputs: Dict[str, Dict[str, Any]]
    ):
        self.recorded = recorded_outputs

    async def execute_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        call_id: UUID
    ) -> Dict[str, Any]:
        """
        Return the recorded tool output.
        """

        key = f"{tool_name}:{call_id}"

        if key not in self.recorded:
            raise ReplayError(
                f"No recorded output for tool call {key}"
            )

        return self.recorded[key]["output"]
```

## Comparison Engine

### Interface

```python
class ComparisonEngine(ABC):
    """
    Engine for comparing original and replayed runs.
    """

    @abstractmethod
    async def compare(
        self,
        original_run: WorkflowRun,
        replay_result: ReplayResult,
        checkpoint: Checkpoint
    ) -> ComparisonReport:
        """
        Generate a detailed comparison report.
        """
        ...
```

### Implementation

```python
class DefaultComparisonEngine(ComparisonEngine):
    """
    Default comparison engine implementation.
    """

    async def compare(
        self,
        original_run: WorkflowRun,
        replay_result: ReplayResult,
        checkpoint: Checkpoint
    ) -> ComparisonReport:
        """
        Generate a detailed comparison report.
        """

        # 1. Compare state paths
        state_path_comparison = await self._compare_state_paths(
            original_run,
            replay_result
        )

        # 2. Compare results
        result_comparison = await self._compare_results(
            original_run,
            replay_result
        )

        # 3. Compare token usage
        token_comparison = self._compare_tokens(
            original_run,
            replay_result
        )

        # 4. Compare costs
        cost_comparison = self._compare_costs(
            original_run,
            replay_result
        )

        # 5. Compare timing
        timing_comparison = self._compare_timing(
            original_run,
            replay_result
        )

        # 6. Compare errors
        error_comparison = self._compare_errors(
            original_run,
            replay_result
        )

        # 7. Determine overall match
        is_identical = (
            state_path_comparison["match"] and
            result_comparison["match"] and
            len(error_comparison["differences"]) == 0
        )

        # 8. Calculate match percentage
        match_percentage = self._calculate_match_percentage(
            state_path_comparison,
            result_comparison,
            token_comparison,
            cost_comparison
        )

        return ComparisonReport(
            is_identical=is_identical,
            match_percentage=match_percentage,
            state_path_match=state_path_comparison["match"],
            original_path=state_path_comparison["original_path"],
            replayed_path=state_path_comparison["replayed_path"],
            path_divergence_point=state_path_comparison.get("divergence_point"),
            result_match=result_comparison["match"],
            result_differences=result_comparison["differences"],
            token_usage_match=token_comparison["match"],
            original_tokens=token_comparison["original"],
            replayed_tokens=token_comparison["replayed"],
            token_variance_percentage=token_comparison["variance_percentage"],
            cost_match=cost_comparison["match"],
            original_cost=cost_comparison["original"],
            replayed_cost=cost_comparison["replayed"],
            cost_variance_percentage=cost_comparison["variance_percentage"],
            timing_match=timing_comparison["match"],
            original_duration_ms=timing_comparison["original"],
            replayed_duration_ms=timing_comparison["replayed"],
            timing_variance_percentage=timing_comparison["variance_percentage"],
            errors_match=error_comparison["match"],
            original_errors=error_comparison["original"],
            replayed_errors=error_comparison["replayed"],
            detailed_comparison={
                "state_path": state_path_comparison,
                "results": result_comparison,
                "tokens": token_comparison,
                "cost": cost_comparison,
                "timing": timing_comparison,
                "errors": error_comparison
            }
        )

    async def _compare_state_paths(
        self,
        original: WorkflowRun,
        replayed: ReplayResult
    ) -> Dict[str, Any]:
        """Compare state transition paths."""

        # Get original path
        original_transitions = await self.transition_repository.get_by_workflow(
            original.workflow_run_id
        )
        original_path = [
            t.to_state for t in sorted(original_transitions, key=lambda t: t.step_number)
        ]

        # Get replayed path
        replayed_path = replayed.state_path

        # Compare
        match = original_path == replayed_path

        # Find divergence point
        divergence_point = None
        if not match:
            for i, (o, r) in enumerate(zip(original_path, replayed_path)):
                if o != r:
                    divergence_point = i
                    break

        return {
            "match": match,
            "original_path": original_path,
            "replayed_path": replayed_path,
            "divergence_point": divergence_point
        }

    async def _compare_results(
        self,
        original: WorkflowRun,
        replayed: ReplayResult
    ) -> Dict[str, Any]:
        """Compare final results."""

        differences = []

        # Compare extraction results
        if hasattr(replayed, 'extraction_result'):
            diff = self._compare_objects(
                original.extraction_result,
                replayed.extraction_result,
                "extraction_result"
            )
            differences.extend(diff)

        # Compare investigation results
        if hasattr(replayed, 'investigation_result'):
            diff = self._compare_objects(
                original.investigation_result,
                replayed.investigation_result,
                "investigation_result"
            )
            differences.extend(diff)

        # Compare review results
        if hasattr(replayed, 'review_result'):
            diff = self._compare_objects(
                original.review_result,
                replayed.review_result,
                "review_result"
            )
            differences.extend(diff)

        # Compare recommendations
        if hasattr(replayed, 'recommendation'):
            diff = self._compare_objects(
                original.recommendation,
                replayed.recommendation,
                "recommendation"
            )
            differences.extend(diff)

        return {
            "match": len(differences) == 0,
            "differences": differences
        }

    def _compare_objects(
        self,
        original: Any,
        replayed: Any,
        path: str
    ) -> List[ResultDifference]:
        """Deep compare two objects."""

        differences = []

        if type(original) != type(replayed):
            differences.append(ResultDifference(
                field_path=path,
                original_value=original,
                replayed_value=replayed,
                difference_type="type_mismatch",
                significance="critical"
            ))
            return differences

        if isinstance(original, BaseModel):
            for field in original.model_fields:
                field_path = f"{path}.{field}"
                orig_val = getattr(original, field)
                replay_val = getattr(replayed, field)

                diff = self._compare_objects(orig_val, replay_val, field_path)
                differences.extend(diff)

        elif isinstance(original, dict):
            all_keys = set(original.keys()) | set(replayed.keys())
            for key in all_keys:
                field_path = f"{path}.{key}"

                if key not in original:
                    differences.append(ResultDifference(
                        field_path=field_path,
                        original_value=None,
                        replayed_value=replayed[key],
                        difference_type="extra",
                        significance="minor"
                    ))
                elif key not in replayed:
                    differences.append(ResultDifference(
                        field_path=field_path,
                        original_value=original[key],
                        replayed_value=None,
                        difference_type="missing",
                        significance="minor"
                    ))
                else:
                    diff = self._compare_objects(
                        original[key],
                        replayed[key],
                        field_path
                    )
                    differences.extend(diff)

        elif isinstance(original, list):
            if len(original) != len(replayed):
                differences.append(ResultDifference(
                    field_path=path,
                    original_value=original,
                    replayed_value=replayed,
                    difference_type="length_mismatch",
                    significance="minor"
                ))
            else:
                for i, (o, r) in enumerate(zip(original, replayed)):
                    diff = self._compare_objects(o, r, f"{path}[{i}]")
                    differences.extend(diff)

        elif original != replayed:
            # Determine significance
            significance = "major"
            if isinstance(original, (int, float)):
                variance = abs(original - replayed) / max(abs(original), abs(replayed), 1)
                if variance < 0.01:
                    significance = "minor"

            differences.append(ResultDifference(
                field_path=path,
                original_value=original,
                replayed_value=replayed,
                difference_type="value_mismatch",
                significance=significance
            ))

        return differences

    def _compare_tokens(
        self,
        original: WorkflowRun,
        replayed: ReplayResult
    ) -> Dict[str, Any]:
        """Compare token usage."""

        original_tokens = original.budget_state.total_tokens_used
        replayed_tokens = replayed.budget_state.total_tokens_used

        variance = abs(original_tokens - replayed_tokens) / max(original_tokens, 1)

        return {
            "match": variance < 0.1,  # Within 10%
            "original": TokenUsage(
                input_tokens=original.budget_state.input_tokens_used,
                output_tokens=original.budget_state.output_tokens_used,
                total_tokens=original_tokens
            ),
            "replayed": TokenUsage(
                input_tokens=replayed.budget_state.input_tokens_used,
                output_tokens=replayed.budget_state.output_tokens_used,
                total_tokens=replayed_tokens
            ),
            "variance_percentage": variance * 100
        }

    def _calculate_match_percentage(
        self,
        state_path: Dict,
        results: Dict,
        tokens: Dict,
        costs: Dict
    ) -> float:
        """Calculate overall match percentage."""

        weights = {
            "state_path": 0.3,
            "results": 0.5,
            "tokens": 0.1,
            "costs": 0.1
        }

        scores = {
            "state_path": 1.0 if state_path["match"] else 0.0,
            "results": 1.0 if results["match"] else max(0, 1 - len(results["differences"]) * 0.1),
            "tokens": max(0, 1 - tokens["variance_percentage"] / 100),
            "costs": max(0, 1 - costs["variance_percentage"] / 100)
        }

        total = sum(scores[k] * weights[k] for k in weights)
        return total * 100
```

## Handling Nondeterminism

### LLM Nondeterminism

LLM calls are inherently nondeterministic. We handle this in three ways:

```python
class NondeterminismStrategy(str, Enum):
    """
    Strategy for handling LLM nondeterminism in replay.
    """

    # Accept variance, compare structural elements only
    ACCEPT_VARIANCE = "ACCEPT_VARIANCE"

    # Use recorded LLM responses for exact determinism
    USE_RECORDED = "USE_RECORDED"

    # Set temperature to 0 for maximum determinism
    ZERO_TEMPERATURE = "ZERO_TEMPERATURE"
```

### Comparison with Tolerance

```python
def compare_with_tolerance(
    original: Any,
    replayed: Any,
    tolerance_config: ToleranceConfig
) -> ComparisonResult:
    """
    Compare values with configurable tolerance for nondeterminism.
    """

    if isinstance(original, str) and isinstance(replayed, str):
        # For text, compare semantic similarity
        similarity = compute_semantic_similarity(original, replayed)

        if similarity >= tolerance_config.text_similarity_threshold:
            return ComparisonResult(match=True, confidence=similarity)
        else:
            return ComparisonResult(match=False, confidence=similarity)

    elif isinstance(original, (int, float)) and isinstance(replayed, (int, float)):
        # For numbers, compare with relative tolerance
        if original == 0:
            match = replayed == 0
        else:
            relative_diff = abs(original - replayed) / abs(original)
            match = relative_diff <= tolerance_config.numeric_tolerance

        return ComparisonResult(match=match)

    elif isinstance(original, BaseModel):
        # For Pydantic models, compare field by field
        all_match = True
        for field in original.model_fields:
            field_result = compare_with_tolerance(
                getattr(original, field),
                getattr(replayed, field),
                tolerance_config
            )
            if not field_result.match:
                all_match = False
                break

        return ComparisonResult(match=all_match)

    else:
        # Exact comparison for other types
        return ComparisonResult(match=original == replayed)


@dataclass
class ToleranceConfig:
    """
    Tolerance settings for nondeterminism handling.
    """

    # Text similarity threshold (0.0 to 1.0)
    text_similarity_threshold: float = 0.95

    # Numeric relative tolerance
    numeric_tolerance: float = 0.05

    # Ignore certain fields entirely
    ignored_fields: List[str] = field(default_factory=lambda: [
        "timestamp",
        "execution_time_ms",
        "trace_id"
    ])
```

## Replay Use Cases

### 1. Debug Production Issue

```python
async def debug_production_issue(
    workflow_run_id: UUID
) -> DebugReport:
    """
    Debug a production workflow by replaying with detailed logging.
    """

    # Create replay request in debug mode
    request = ReplayRequest(
        original_workflow_run_id=workflow_run_id,
        replay_mode=ReplayMode.DEBUG,
        record_llm_responses=True
    )

    # Execute replay
    replay_run = await replay_engine.replay(request)

    # Generate debug report
    return DebugReport(
        workflow_run_id=workflow_run_id,
        replay_run=replay_run,
        comparison=replay_run.comparison_report,
        deviations=replay_run.deviation_points
    )
```

### 2. Verify Audit Trail

```python
async def verify_audit_trail(
    workflow_run_id: UUID
) -> AuditVerificationResult:
    """
    Verify that a stored workflow can be replayed to the same result.
    """

    # Create replay request in deterministic mode
    request = ReplayRequest(
        original_workflow_run_id=workflow_run_id,
        replay_mode=ReplayMode.DETERMINISTIC,
        enable_comparison=True
    )

    # Execute replay
    replay_run = await replay_engine.replay(request)

    # Check if replay matches
    return AuditVerificationResult(
        workflow_run_id=workflow_run_id,
        is_verified=replay_run.comparison_report.is_identical,
        match_percentage=replay_run.comparison_report.match_percentage,
        verification_timestamp=datetime.utcnow()
    )
```

### 3. Regression Testing

```python
async def run_regression_tests(
    test_case_ids: List[UUID]
) -> RegressionTestResults:
    """
    Run regression tests using recorded test cases.
    """

    results = []

    for test_case_id in test_case_ids:
        # Load test case
        test_case = await test_case_repository.get_by_id(test_case_id)

        # Create replay request in fresh mode
        request = ReplayRequest(
            original_workflow_run_id=test_case.original_workflow_run_id,
            replay_mode=ReplayMode.FRESH,
            enable_comparison=True
        )

        # Execute replay
        replay_run = await replay_engine.replay(request)

        # Compare with expected result
        passed = (
            replay_run.comparison_report.state_path_match and
            replay_run.terminal_state == test_case.expected_terminal_state
        )

        results.append(RegressionTestResult(
            test_case_id=test_case_id,
            passed=passed,
            comparison=replay_run.comparison_report
        ))

    return RegressionTestResults(
        total=len(results),
        passed=sum(1 for r in results if r.passed),
        failed=sum(1 for r in results if not r.passed),
        results=results
    )
```

## Summary

The replay architecture provides:
- **Determinism**: Use recorded outputs for predictable execution
- **Flexibility**: Multiple replay modes for different use cases
- **Verification**: Detailed comparison between original and replayed runs
- **Debugging**: Step-through capability for production issues
- **Testing**: Regression test support using recorded runs
- **Audit**: Verification of stored workflow integrity
