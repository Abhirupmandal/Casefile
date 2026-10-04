import React, { useEffect, useState, useCallback } from 'react';
import { Navbar } from './components/Navbar';
import { DashboardView } from './components/DashboardView';
import { WorkflowsView } from './components/WorkflowsView';
import { WorkflowDetailView } from './components/WorkflowDetailView';
import { ApprovalQueueView } from './components/ApprovalQueueView';
import { ReplayView } from './components/ReplayView';
import { EvaluationsView } from './components/EvaluationsView';
import { HealthView } from './components/HealthView';
import {
  ApprovalItemResponse,
  OperationalMetricsResponse,
  ReplayResponse,
  UserRole,
  WorkflowSummaryItem,
} from './types/api';
import { api } from './services/api';

export const App: React.FC = () => {
  const [currentTab, setCurrentTab] = useState<string>('dashboard');
  const [currentRole, setCurrentRole] = useState<UserRole>('OPERATOR');
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [lastReplay, setLastReplay] = useState<ReplayResponse | null>(null);

  // App Data State
  const [metrics, setMetrics] = useState<OperationalMetricsResponse | null>(null);
  const [workflows, setWorkflows] = useState<WorkflowSummaryItem[]>([]);
  const [approvals, setApprovals] = useState<ApprovalItemResponse[]>([]);
  const [isHealthy, setIsHealthy] = useState<boolean>(true);
  const [loading, setLoading] = useState<boolean>(true);

  const handleRoleChange = (role: UserRole) => {
    setCurrentRole(role);
    api.setRole(role);
  };

  const refreshAll = useCallback(async () => {
    try {
      const [m, wf, app, h] = await Promise.all([
        api.getMetrics().catch(() => null),
        api.listWorkflows().catch(() => []),
        api.listApprovals('PENDING').catch(() => []),
        api.getHealth().catch(() => null),
      ]);
      setMetrics(m);
      setWorkflows(wf);
      setApprovals(app);
      setIsHealthy(h !== null && h.status === 'healthy');
    } catch {
      setIsHealthy(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshAll();
    const interval = setInterval(refreshAll, 10000); // 10s auto polling
    return () => clearInterval(interval);
  }, [refreshAll]);

  const handleSelectWorkflow = (id: string) => {
    setSelectedWorkflowId(id);
    setCurrentTab('workflow-detail');
  };

  const handleSubmitClaim = async (claim: {
    policy_id: string;
    claimant_name: string;
    incident_date: string;
    claim_amount: string;
    description: string;
    documents?: string[];
  }) => {
    const res = await api.submitClaim(claim);
    await refreshAll();
    handleSelectWorkflow(res.workflow_run_id);
  };

  const handleDecideApproval = async (
    approvalId: string,
    decision: 'APPROVED' | 'REJECTED',
    reason: string
  ) => {
    await api.decideApproval(approvalId, decision, reason);
    await refreshAll();
  };

  const handleNavigateToReplay = (res: ReplayResponse) => {
    setLastReplay(res);
    setCurrentTab('replay');
  };

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Navbar
        currentTab={currentTab}
        onTabChange={(tab) => {
          setSelectedWorkflowId(null);
          setCurrentTab(tab);
        }}
        currentRole={currentRole}
        onRoleChange={handleRoleChange}
        pendingApprovalsCount={approvals.length}
        isHealthy={isHealthy}
      />

      <main style={{ flex: 1 }}>
        {currentTab === 'dashboard' && (
          <DashboardView
            metrics={metrics}
            recentWorkflows={workflows}
            onSelectWorkflow={handleSelectWorkflow}
            onSubmitClaim={handleSubmitClaim}
            onNavigateTab={(tab) => {
              setSelectedWorkflowId(null);
              setCurrentTab(tab);
            }}
          />
        )}

        {currentTab === 'workflows' && (
          <WorkflowsView
            workflows={workflows}
            onSelectWorkflow={handleSelectWorkflow}
            onRefresh={refreshAll}
            loading={loading}
          />
        )}

        {currentTab === 'workflow-detail' && selectedWorkflowId && (
          <WorkflowDetailView
            workflowRunId={selectedWorkflowId}
            onBack={() => {
              setSelectedWorkflowId(null);
              setCurrentTab('workflows');
            }}
            onNavigateToReplay={handleNavigateToReplay}
          />
        )}

        {currentTab === 'approvals' && (
          <ApprovalQueueView
            approvals={approvals}
            currentRole={currentRole}
            onDecide={handleDecideApproval}
            onSelectWorkflow={handleSelectWorkflow}
            onRefresh={refreshAll}
          />
        )}

        {currentTab === 'replay' && (
          <ReplayView workflows={workflows} initialReplay={lastReplay} />
        )}

        {currentTab === 'evaluations' && <EvaluationsView />}

        {currentTab === 'health' && <HealthView />}
      </main>
    </div>
  );
};

export default App;
