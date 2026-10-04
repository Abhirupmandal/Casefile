import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { App } from '../App';
import { DashboardView } from '../components/DashboardView';
import { ApprovalQueueView } from '../components/ApprovalQueueView';
import { ReplayView } from '../components/ReplayView';
import { HealthView } from '../components/HealthView';
import { EvaluationsView } from '../components/EvaluationsView';
import { OperationalMetricsResponse, ApprovalItemResponse } from '../types/api';

describe('Operations Console UI Components', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it('renders DashboardView with real operational KPI metrics', () => {
    const mockMetrics: OperationalMetricsResponse = {
      active_workflows: 3,
      completed_workflows: 14,
      failed_workflows: 1,
      pending_approvals: 2,
      budget_exhausted_workflows: 0,
      average_duration_seconds: 4,
      average_cost_usd: '0.0006',
      evaluation_pass_rate: 1.0,
      system_health: 'healthy',
    };

    render(
      <DashboardView
        metrics={mockMetrics}
        recentWorkflows={[]}
        onSelectWorkflow={vi.fn()}
        onSubmitClaim={vi.fn()}
        onNavigateTab={vi.fn()}
      />
    );

    expect(screen.getByText('Operations Control Surface')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument(); // active
    expect(screen.getByText('14')).toBeInTheDocument(); // completed
    expect(screen.getByText('100%')).toBeInTheDocument(); // eval pass rate
  });

  it('enforces RBAC in ApprovalQueueView: viewer/operator cannot submit decisions', async () => {
    const mockApprovals: ApprovalItemResponse[] = [
      {
        approval_id: 'app-12345',
        workflow_run_id: 'wf-67890',
        claim_id: 'claim-abc',
        status: 'PENDING',
        requested_role: 'CLAIM_REVIEWER',
        requested_at: new Date().toISOString(),
        deadline: new Date(Date.now() + 86400000).toISOString(),
        reviewer_summary: 'Damaged front bumper estimate verified.',
        recommended_payout: '1500.00',
        confidence: 0.95,
      },
    ];

    // Render with OPERATOR role (should not be allowed to decide)
    const { rerender } = render(
      <ApprovalQueueView
        approvals={mockApprovals}
        currentRole="OPERATOR"
        onDecide={vi.fn()}
        onSelectWorkflow={vi.fn()}
        onRefresh={vi.fn()}
      />
    );

    expect(screen.getByText(/Read-Only: OPERATOR cannot approve/)).toBeInTheDocument();

    // Click Adjudicate button to open modal
    fireEvent.click(screen.getByText('Adjudicate'));

    // Verify submit button is disabled for OPERATOR
    const submitBtn = screen.getByRole('button', { name: /Submit APPROVED/ });
    expect(submitBtn).toBeDisabled();

    // Re-render with CLAIM_REVIEWER role
    rerender(
      <ApprovalQueueView
        approvals={mockApprovals}
        currentRole="CLAIM_REVIEWER"
        onDecide={vi.fn()}
        onSelectWorkflow={vi.fn()}
        onRefresh={vi.fn()}
      />
    );

    // Verify submit button is enabled for CLAIM_REVIEWER
    expect(submitBtn).not.toBeDisabled();
  });

  it('renders ReplayView with explicit simulation warning and isolated results', () => {
    render(
      <ReplayView
        workflows={[
          {
            workflow_run_id: 'wf-11111',
            claim_id: 'claim-11111',
            current_state: 'APPROVED',
            is_terminal: true,
            step_count: 5,
            rework_count: 0,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            budget_exhausted: false,
            cost_usd: '0.0006',
            pending_approval: false,
          },
        ]}
      />
    );

    expect(screen.getByText('Deterministic Replay Simulator')).toBeInTheDocument();
    expect(screen.getByText(/REPLAY \/ SIMULATION MODE/)).toBeInTheDocument();
  });

  it('renders EvaluationsView and health diagnostic views', async () => {
    render(<EvaluationsView />);
    expect(screen.getByText(/Evaluation & Failure Engineering Benchmark/)).toBeInTheDocument();

    render(<HealthView />);
    expect(screen.getByText(/Operational Health & Readiness Probes/)).toBeInTheDocument();
  });
});
