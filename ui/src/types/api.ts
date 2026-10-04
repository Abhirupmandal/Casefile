export type WorkflowState =
  | 'RECEIVED'
  | 'EXTRACTION'
  | 'INVESTIGATION'
  | 'REVIEW'
  | 'REWORK_LOOP'
  | 'HUMAN_APPROVAL'
  | 'APPROVED'
  | 'REJECTED'
  | 'FAILED'
  | 'ESCALATION'
  | 'BUDGET_EXHAUSTED'
  | 'TIMEOUT'
  | 'MAX_STEPS_EXCEEDED'
  | 'MAX_REWORK_EXCEEDED';

export type UserRole =
  | 'VIEWER'
  | 'OPERATOR'
  | 'CLAIM_REVIEWER'
  | 'SENIOR_REVIEWER'
  | 'CLAIM_SUPERVISOR'
  | 'ADMIN';

export interface WorkflowSummaryItem {
  workflow_run_id: string;
  claim_id: string;
  current_state: WorkflowState;
  is_terminal: boolean;
  step_count: number;
  rework_count: number;
  created_at: string;
  updated_at: string;
  budget_exhausted: boolean;
  cost_usd: string;
  pending_approval: boolean;
}

export interface WorkflowStatusResponse {
  workflow_run_id: string;
  claim_id: string;
  current_state: WorkflowState;
  is_terminal: boolean;
  current_step: number;
  step_count: number;
  rework_count: number;
  retry_count: number;
  created_at: string;
  updated_at: string;
  budget_usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cost_usd: string;
    steps_completed: number;
    rework_count: number;
    is_exhausted: boolean;
    exhaustion_reason?: string | null;
  };
  estimated_cost: string;
  pending_approval: boolean;
  checkpoint_count: number;
  correlation_id: string;
}

export interface StateTransitionItem {
  sequence_no: number;
  from_state: string;
  to_state: string;
  trigger: string;
  actor: string;
  reason: string;
  timestamp: string;
  correlation_id: string;
}

export interface WorkflowHistoryResponse {
  workflow_run_id: string;
  transitions: StateTransitionItem[];
  total_transitions: number;
}

export interface AgentExecutionResponse {
  execution_id: string;
  workflow_run_id: string;
  agent_type: string;
  status: string;
  started_at: string;
  completed_at?: string | null;
  duration_ms: number;
  retry_count: number;
  failure_category?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: string;
  output_summary?: Record<string, unknown> | null;
}

export interface ToolInvocationResponse {
  invocation_id: string;
  workflow_run_id: string;
  tool_name: string;
  status: string;
  duration_ms: number;
  executed_at: string;
  provenance_agent: string;
  parameters_summary: Record<string, unknown>;
  output_summary: Record<string, unknown>;
}

export interface CheckpointMetadataResponse {
  checkpoint_id: string;
  workflow_run_id: string;
  sequence_no: number;
  state: string;
  kind: string;
  created_at: string;
  integrity_valid: boolean;
  resumable: boolean;
}

export interface ApprovalItemResponse {
  approval_id: string;
  workflow_run_id: string;
  claim_id: string;
  status: 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED' | 'CANCELLED';
  requested_role: string;
  requested_at: string;
  deadline: string;
  reviewer_summary: string;
  recommended_payout?: string | null;
  confidence: number;
}

export interface OperationalMetricsResponse {
  active_workflows: number;
  completed_workflows: number;
  failed_workflows: number;
  pending_approvals: number;
  budget_exhausted_workflows: number;
  average_duration_seconds: number;
  average_cost_usd: string;
  evaluation_pass_rate: number;
  system_health: string;
}

export interface EvaluationCaseSummary {
  case_id: string;
  scenario_name: string;
  category: string;
  status: string;
  expected_terminal_state: string;
  terminal_state: string;
  path_matched: boolean;
  terminal_matched: boolean;
  cost_usd: string;
  steps: number;
}

export interface EvaluationSummaryResponse {
  total_cases: number;
  passed: number;
  failed: number;
  pass_rate: number;
  path_accuracy: number;
  terminal_state_accuracy: number;
  replay_determinism_rate: number;
  budget_enforcement_rate: number;
  approval_safety_rate: number;
  failure_containment_rate: number;
  average_cost: string;
  average_steps: number;
  cases: EvaluationCaseSummary[];
}

export interface ReplayResponse {
  replay_id: string;
  original_workflow_run_id: string;
  status: string;
  terminal_state: string;
  path_matched: boolean;
  terminal_matched: boolean;
  deterministic_match: boolean;
  is_simulation: boolean;
  simulated_at: string;
}

export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  timestamp: string;
}

export interface ReadinessResponse {
  ready: boolean;
  service: string;
  timestamp: string;
  checks: Record<string, string>;
}
