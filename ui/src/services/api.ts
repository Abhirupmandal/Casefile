import {
  ApprovalItemResponse,
  EvaluationSummaryResponse,
  HealthResponse,
  OperationalMetricsResponse,
  ReadinessResponse,
  ReplayResponse,
  UserRole,
  WorkflowHistoryResponse,
  WorkflowStatusResponse,
  WorkflowSummaryItem,
  AgentExecutionResponse,
  ToolInvocationResponse,
  CheckpointMetadataResponse,
} from '../types/api';

// Map mock operator roles to developer bearer tokens
export const ROLE_TOKENS: Record<UserRole, string> = {
  VIEWER: 'dev-viewer-token',
  OPERATOR: 'dev-operator-token',
  CLAIM_REVIEWER: 'dev-reviewer-token',
  SENIOR_REVIEWER: 'dev-senior-token',
  CLAIM_SUPERVISOR: 'dev-supervisor-token',
  ADMIN: 'dev-admin-token',
};

class ApiClient {
  private currentRole: UserRole = 'OPERATOR';
  private baseUrl: string = '';

  public setRole(role: UserRole) {
    this.currentRole = role;
  }

  public getRole(): UserRole {
    return this.currentRole;
  }

  private getHeaders(extraHeaders: Record<string, string> = {}): HeadersInit {
    const token = ROLE_TOKENS[this.currentRole];
    return {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      ...extraHeaders,
    };
  }

  private async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const url = `${this.baseUrl}${path}`;
    const headers = this.getHeaders((options.headers as Record<string, string>) || {});
    const response = await fetch(url, { ...options, headers });

    if (!response.ok) {
      let errorMessage = `HTTP ${response.status} ${response.statusText}`;
      try {
        const errorJson = await response.json();
        if (errorJson?.error?.message) {
          errorMessage = errorJson.error.message;
        } else if (errorJson?.detail) {
          errorMessage = typeof errorJson.detail === 'string' ? errorJson.detail : JSON.stringify(errorJson.detail);
        }
      } catch {
        // use default error message
      }
      throw new Error(errorMessage);
    }

    return response.json() as Promise<T>;
  }

  // Health
  public async getHealth(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health');
  }

  public async getReadiness(): Promise<ReadinessResponse> {
    return this.request<ReadinessResponse>('/ready');
  }

  // Metrics
  public async getMetrics(): Promise<OperationalMetricsResponse> {
    return this.request<OperationalMetricsResponse>('/api/v1/metrics');
  }

  // Workflows
  public async listWorkflows(): Promise<WorkflowSummaryItem[]> {
    const res = await this.request<{ workflows: WorkflowSummaryItem[]; total: number }>('/api/v1/workflows');
    return res.workflows;
  }

  public async getWorkflowStatus(workflowRunId: string): Promise<WorkflowStatusResponse> {
    return this.request<WorkflowStatusResponse>(`/api/v1/workflows/${workflowRunId}`);
  }

  public async getWorkflowHistory(workflowRunId: string): Promise<WorkflowHistoryResponse> {
    return this.request<WorkflowHistoryResponse>(`/api/v1/workflows/${workflowRunId}/history`);
  }

  public async getWorkflowAgents(workflowRunId: string): Promise<AgentExecutionResponse[]> {
    const res = await this.request<{ executions: AgentExecutionResponse[]; total: number }>(`/api/v1/workflows/${workflowRunId}/agents`);
    return res.executions;
  }

  public async getWorkflowTools(workflowRunId: string): Promise<ToolInvocationResponse[]> {
    const res = await this.request<{ invocations: ToolInvocationResponse[]; total: number }>(`/api/v1/workflows/${workflowRunId}/tools`);
    return res.invocations;
  }

  public async getWorkflowCheckpoints(workflowRunId: string): Promise<CheckpointMetadataResponse[]> {
    const res = await this.request<{ checkpoints: CheckpointMetadataResponse[]; total: number }>(`/api/v1/workflows/${workflowRunId}/checkpoints`);
    return res.checkpoints;
  }

  public async submitClaim(claim: {
    policy_id: string;
    claimant_name: string;
    incident_date: string;
    claim_amount: string;
    description: string;
    documents?: string[];
  }): Promise<{ workflow_run_id: string; claim_id: string; current_state: string; status_url: string }> {
    return this.request('/api/v1/claims', {
      method: 'POST',
      body: JSON.stringify(claim),
    });
  }

  public async replayWorkflow(workflowRunId: string): Promise<ReplayResponse> {
    return this.request<ReplayResponse>(`/api/v1/workflows/${workflowRunId}/replay`, {
      method: 'POST',
    });
  }

  // Approvals
  public async listApprovals(status?: string): Promise<ApprovalItemResponse[]> {
    const query = status ? `?status=${encodeURIComponent(status)}` : '';
    const res = await this.request<{ approvals: ApprovalItemResponse[]; total: number }>(`/api/v1/approvals${query}`);
    return res.approvals;
  }

  public async decideApproval(
    approvalId: string,
    decision: 'APPROVED' | 'REJECTED',
    reason: string
  ): Promise<{ status: string; outcome: string }> {
    return this.request(`/api/v1/approvals/${approvalId}/decision`, {
      method: 'POST',
      body: JSON.stringify({ decision, reason }),
    });
  }

  // Evaluations
  public async getEvaluationSummary(): Promise<EvaluationSummaryResponse> {
    return this.request<EvaluationSummaryResponse>('/api/v1/evaluations');
  }
}

export const api = new ApiClient();
