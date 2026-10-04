import React, { useState } from 'react';
import { OperationalMetricsResponse, WorkflowSummaryItem } from '../types/api';
import {
  Activity,
  CheckCircle2,
  AlertTriangle,
  Clock,
  DollarSign,
  PlusCircle,
  FileText,
  ArrowRight,
  TrendingUp,
} from 'lucide-react';

interface DashboardViewProps {
  metrics: OperationalMetricsResponse | null;
  recentWorkflows: WorkflowSummaryItem[];
  onSelectWorkflow: (id: string) => void;
  onSubmitClaim: (claim: {
    policy_id: string;
    claimant_name: string;
    incident_date: string;
    claim_amount: string;
    description: string;
    documents?: string[];
  }) => Promise<void>;
  onNavigateTab: (tab: string) => void;
}

export const DashboardView: React.FC<DashboardViewProps> = ({
  metrics,
  recentWorkflows,
  onSelectWorkflow,
  onSubmitClaim,
  onNavigateTab,
}) => {
  const [showSubmitModal, setShowSubmitModal] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Form state
  const [policyId, setPolicyId] = useState('POL-8840');
  const [claimantName, setClaimantName] = useState('Alice Morgan');
  const [incidentDate, setIncidentDate] = useState('2026-04-14');
  const [claimAmount, setClaimAmount] = useState('2450.00');
  const [description, setDescription] = useState('Intersection fender collision with damage to right bumper.');
  const [documents, setDocuments] = useState('estimate_8840.pdf');

  const handleFormSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const docList = documents.split(',').map((d) => d.trim()).filter(Boolean);
      await onSubmitClaim({
        policy_id: policyId,
        claimant_name: claimantName,
        incident_date: incidentDate,
        claim_amount: claimAmount,
        description,
        documents: docList.length > 0 ? docList : undefined,
      });
      setShowSubmitModal(false);
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : 'Claim submission failed');
    } finally {
      setSubmitting(false);
    }
  };

  const getStatusBadge = (state: string) => {
    if (state === 'APPROVED') return <span className="badge badge-approved">APPROVED</span>;
    if (state === 'REJECTED') return <span className="badge badge-rejected">REJECTED</span>;
    if (state === 'HUMAN_APPROVAL') return <span className="badge badge-waiting">WAITING REVIEW</span>;
    if (state.includes('FAILED') || state.includes('EXCEEDED') || state.includes('EXHAUSTED')) {
      return <span className="badge badge-failed">{state}</span>;
    }
    return <span className="badge badge-active">{state}</span>;
  };

  return (
    <div style={{ padding: '2rem 1.5rem', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '2rem' }}>
      {/* Title & Quick Actions */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700 }}>Operations Control Surface</h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
            Real-time telemetry, state machine progression, and human-in-the-loop decisioning.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.75rem' }}>
          <button
            onClick={() => setShowSubmitModal(true)}
            className="btn btn-primary"
            id="btn-submit-new-claim"
          >
            <PlusCircle size={16} />
            <span>Submit Claim</span>
          </button>
        </div>
      </div>

      {/* KPI Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '1rem' }}>
        {/* Active */}
        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-dim)' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Active Workflows</span>
            <Activity size={18} color="var(--accent-cyan)" />
          </div>
          <div style={{ fontSize: '1.85rem', fontWeight: 700, marginTop: '0.5rem', color: '#38bdf8' }}>
            {metrics?.active_workflows ?? 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            In-flight state transitions
          </div>
        </div>

        {/* Pending Approvals */}
        <div
          className="glass-panel"
          style={{ padding: '1.25rem', cursor: 'pointer', border: metrics?.pending_approvals ? '1px solid rgba(245, 158, 11, 0.4)' : undefined }}
          onClick={() => onNavigateTab('approvals')}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-dim)' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Pending Approvals</span>
            <AlertTriangle size={18} color="var(--accent-amber)" />
          </div>
          <div style={{ fontSize: '1.85rem', fontWeight: 700, marginTop: '0.5rem', color: '#fbbf24' }}>
            {metrics?.pending_approvals ?? 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            Awaiting human decision
          </div>
        </div>

        {/* Completed */}
        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-dim)' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Completed Claims</span>
            <CheckCircle2 size={18} color="var(--accent-emerald)" />
          </div>
          <div style={{ fontSize: '1.85rem', fontWeight: 700, marginTop: '0.5rem', color: '#34d399' }}>
            {metrics?.completed_workflows ?? 0}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            Adjudicated terminal claims
          </div>
        </div>

        {/* Average Cost */}
        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-dim)' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Avg Cost / Run</span>
            <DollarSign size={18} color="var(--accent-primary)" />
          </div>
          <div className="mono" style={{ fontSize: '1.85rem', fontWeight: 700, marginTop: '0.5rem', color: '#a5b4fc' }}>
            ${metrics?.average_cost_usd ?? '0.0006'}
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            Budget consumption rate
          </div>
        </div>

        {/* Eval Pass Rate */}
        <div
          className="glass-panel"
          style={{ padding: '1.25rem', cursor: 'pointer' }}
          onClick={() => onNavigateTab('evaluations')}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', color: 'var(--text-dim)' }}>
            <span style={{ fontSize: '0.8rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Eval Pass Rate</span>
            <TrendingUp size={18} color="#10b981" />
          </div>
          <div className="mono" style={{ fontSize: '1.85rem', fontWeight: 700, marginTop: '0.5rem', color: '#34d399' }}>
            {((metrics?.evaluation_pass_rate ?? 1.0) * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem' }}>
            30 benchmark scenarios verified
          </div>
        </div>
      </div>

      {/* Recent Workflows Section */}
      <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <FileText size={18} color="var(--accent-cyan)" />
            <h2 style={{ fontSize: '1.1rem' }}>Recent Workflows</h2>
          </div>
          <button
            onClick={() => onNavigateTab('workflows')}
            className="btn btn-secondary"
            style={{ fontSize: '0.8rem', padding: '0.35rem 0.75rem' }}
          >
            <span>View All</span>
            <ArrowRight size={14} />
          </button>
        </div>

        {recentWorkflows.length === 0 ? (
          <div style={{ padding: '2.5rem', textAlign: 'center', color: 'var(--text-dim)' }}>
            No workflows executed yet. Click &quot;Submit Claim&quot; to initiate multi-agent adjudication.
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Workflow Run ID</th>
                  <th>Claim ID</th>
                  <th>State</th>
                  <th>Steps</th>
                  <th>Cost</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {recentWorkflows.slice(0, 6).map((wf) => (
                  <tr key={wf.workflow_run_id}>
                    <td className="mono" style={{ fontSize: '0.8rem', color: 'var(--accent-cyan)' }}>
                      {wf.workflow_run_id.slice(0, 8)}...
                    </td>
                    <td className="mono" style={{ fontSize: '0.8rem' }}>
                      {wf.claim_id.slice(0, 8)}...
                    </td>
                    <td>{getStatusBadge(wf.current_state)}</td>
                    <td className="mono">{wf.step_count}</td>
                    <td className="mono">${wf.cost_usd}</td>
                    <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      {new Date(wf.created_at).toLocaleTimeString()}
                    </td>
                    <td>
                      <button
                        onClick={() => onSelectWorkflow(wf.workflow_run_id)}
                        className="btn btn-secondary"
                        style={{ padding: '0.25rem 0.6rem', fontSize: '0.75rem' }}
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Claim Submission Modal */}
      {showSubmitModal && (
        <div className="modal-backdrop" onClick={() => !submitting && setShowSubmitModal(false)}>
          <div className="glass-panel-elevated modal-content" onClick={(e) => e.stopPropagation()} style={{ padding: '1.75rem' }}>
            <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Submit New Claim</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: '1.25rem' }}>
              Initiates supervisor orchestration through Extractor, Investigator, and Reviewer agents.
            </p>

            {submitError && (
              <div style={{
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                color: '#fb7185',
                padding: '0.75rem',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.85rem',
                marginBottom: '1rem',
              }}>
                {submitError}
              </div>
            )}

            <form onSubmit={handleFormSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                    Policy ID
                  </label>
                  <input
                    type="text"
                    required
                    value={policyId}
                    onChange={(e) => setPolicyId(e.target.value)}
                    className="input mono"
                    placeholder="POL-1234"
                  />
                </div>
                <div>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                    Claimant Name
                  </label>
                  <input
                    type="text"
                    required
                    value={claimantName}
                    onChange={(e) => setClaimantName(e.target.value)}
                    className="input"
                    placeholder="Jane Doe"
                  />
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                <div>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                    Incident Date
                  </label>
                  <input
                    type="date"
                    required
                    value={incidentDate}
                    onChange={(e) => setIncidentDate(e.target.value)}
                    className="input mono"
                  />
                </div>
                <div>
                  <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                    Claim Amount (USD)
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={claimAmount}
                    onChange={(e) => setClaimAmount(e.target.value)}
                    className="input mono"
                    placeholder="2500.00"
                  />
                </div>
              </div>

              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                  Incident Description
                </label>
                <textarea
                  rows={3}
                  required
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="textarea"
                  placeholder="Describe the incident..."
                />
              </div>

              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                  Documents (Comma separated)
                </label>
                <input
                  type="text"
                  value={documents}
                  onChange={(e) => setDocuments(e.target.value)}
                  className="input mono"
                  placeholder="estimate.pdf, police_report.pdf"
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem', marginTop: '1rem' }}>
                <button
                  type="button"
                  onClick={() => setShowSubmitModal(false)}
                  disabled={submitting}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="btn btn-primary"
                  id="btn-submit-claim-modal"
                >
                  {submitting ? 'Orchestrating...' : 'Submit & Execute'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
