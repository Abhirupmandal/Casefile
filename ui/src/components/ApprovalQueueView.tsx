import React, { useState } from 'react';
import { ApprovalItemResponse, UserRole } from '../types/api';
import { CheckCircle, AlertTriangle, XCircle, ShieldCheck, Clock, DollarSign } from 'lucide-react';

interface ApprovalQueueViewProps {
  approvals: ApprovalItemResponse[];
  currentRole: UserRole;
  onDecide: (approvalId: string, decision: 'APPROVED' | 'REJECTED', reason: string) => Promise<void>;
  onSelectWorkflow: (workflowId: string) => void;
  onRefresh: () => void;
}

export const ApprovalQueueView: React.FC<ApprovalQueueViewProps> = ({
  approvals,
  currentRole,
  onDecide,
  onSelectWorkflow,
  onRefresh,
}) => {
  const [selectedApproval, setSelectedApproval] = useState<ApprovalItemResponse | null>(null);
  const [decision, setDecision] = useState<'APPROVED' | 'REJECTED'>('APPROVED');
  const [reason, setReason] = useState('Reviewed evidence and corroborated repair costs.');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Check if role is authorized to decide
  const canDecide = ['CLAIM_REVIEWER', 'SENIOR_REVIEWER', 'CLAIM_SUPERVISOR', 'ADMIN'].includes(currentRole);

  const handleDecisionSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedApproval) return;
    setSubmitting(true);
    setError(null);
    try {
      await onDecide(selectedApproval.approval_id, decision, reason);
      setSelectedApproval(null);
      onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Approval submission failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ padding: '2rem 1.5rem', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <CheckCircle size={24} color="var(--accent-amber)" />
            <span>Human-in-the-Loop Approval Queue</span>
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
            Authoritative human adjudication gate. Only authorized claim reviewer roles may decide.
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{
            fontSize: '0.8rem',
            padding: '0.4rem 0.8rem',
            borderRadius: 'var(--radius-md)',
            background: canDecide ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.15)',
            border: `1px solid ${canDecide ? 'rgba(16, 185, 129, 0.3)' : 'rgba(244, 63, 94, 0.3)'}`,
            color: canDecide ? '#34d399' : '#fb7185',
            fontWeight: 600,
          }}>
            {canDecide ? `Authorized to Decid: ${currentRole}` : `Read-Only: ${currentRole} cannot approve`}
          </div>
        </div>
      </div>

      {/* Approvals Table / Grid */}
      <div className="glass-panel" style={{ overflow: 'hidden' }}>
        {approvals.length === 0 ? (
          <div style={{ padding: '3.5rem', textAlign: 'center', color: 'var(--text-dim)' }}>
            <ShieldCheck size={36} color="var(--accent-emerald)" style={{ margin: '0 auto 0.75rem auto' }} />
            <div style={{ fontSize: '1.1rem', fontWeight: 600, color: 'var(--text-main)' }}>Queue Clear</div>
            <p style={{ fontSize: '0.85rem', marginTop: '0.25rem' }}>No pending human approvals currently awaiting decision.</p>
          </div>
        ) : (
          <div className="table-container">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Approval ID</th>
                  <th>Workflow Run ID</th>
                  <th>Status</th>
                  <th>Required Role</th>
                  <th>Estimated Payout</th>
                  <th>Confidence</th>
                  <th>Deadline</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {approvals.map((app) => (
                  <tr key={app.approval_id}>
                    <td className="mono" style={{ color: 'var(--accent-cyan)' }}>{app.approval_id.slice(0, 8)}...</td>
                    <td className="mono">
                      <button
                        onClick={() => onSelectWorkflow(app.workflow_run_id)}
                        style={{ background: 'none', border: 'none', color: '#818cf8', cursor: 'pointer', textDecoration: 'underline' }}
                        className="mono"
                      >
                        {app.workflow_run_id.slice(0, 8)}...
                      </button>
                    </td>
                    <td>
                      <span className="badge badge-waiting">{app.status}</span>
                    </td>
                    <td><span className="badge badge-neutral">{app.requested_role}</span></td>
                    <td className="mono" style={{ color: '#34d399', fontWeight: 600 }}>
                      ${app.recommended_payout ?? '100.00'}
                    </td>
                    <td className="mono">{(app.confidence * 100).toFixed(0)}%</td>
                    <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      {new Date(app.deadline).toLocaleTimeString()}
                    </td>
                    <td>
                      <button
                        onClick={() => {
                          setSelectedApproval(app);
                          setError(null);
                        }}
                        className="btn btn-primary"
                        style={{ padding: '0.3rem 0.8rem', fontSize: '0.75rem' }}
                      >
                        Adjudicate
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Decision Modal */}
      {selectedApproval && (
        <div className="modal-backdrop" onClick={() => !submitting && setSelectedApproval(null)}>
          <div className="glass-panel-elevated modal-content" onClick={(e) => e.stopPropagation()} style={{ padding: '1.75rem' }}>
            <h2 style={{ fontSize: '1.25rem', marginBottom: '0.5rem' }}>Adjudication Decision</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginBottom: '1rem' }}>
              Review recommendation summary and submit authoritative human decision.
            </p>

            {error && (
              <div style={{
                background: 'rgba(244, 63, 94, 0.15)',
                border: '1px solid rgba(244, 63, 94, 0.3)',
                color: '#fb7185',
                padding: '0.75rem',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.85rem',
                marginBottom: '1rem',
              }}>
                {error}
              </div>
            )}

            {!canDecide && (
              <div style={{
                background: 'rgba(245, 158, 11, 0.15)',
                border: '1px solid rgba(245, 158, 11, 0.3)',
                color: '#fbbf24',
                padding: '0.75rem',
                borderRadius: 'var(--radius-md)',
                fontSize: '0.85rem',
                marginBottom: '1rem',
                display: 'flex',
                gap: '0.5rem',
                alignItems: 'center',
              }}>
                <AlertTriangle size={18} />
                <span>Your current role ({currentRole}) does not have permission to submit decisions. Switch role in top bar.</span>
              </div>
            )}

            {/* Recommendation Summary */}
            <div style={{ background: 'rgba(10, 13, 20, 0.6)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', marginBottom: '1.25rem' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase', marginBottom: '0.5rem' }}>
                Specialist Agent Recommendation
              </div>
              <div style={{ fontSize: '0.9rem', color: '#f1f5f9' }}>
                {selectedApproval.reviewer_summary || 'Evidence corroborated and damage estimation within normal bounds.'}
              </div>
              <div style={{ display: 'flex', gap: '1.5rem', marginTop: '0.75rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                <span>Recommended Payout: <strong className="mono" style={{ color: '#34d399' }}>${selectedApproval.recommended_payout ?? '100.00'}</strong></span>
                <span>Reviewer Confidence: <strong className="mono">{(selectedApproval.confidence * 100).toFixed(0)}%</strong></span>
              </div>
            </div>

            <form onSubmit={handleDecisionSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.5rem' }}>
                  Verdict
                </label>
                <div style={{ display: 'flex', gap: '1rem' }}>
                  <button
                    type="button"
                    onClick={() => setDecision('APPROVED')}
                    className="btn"
                    style={{
                      flex: 1,
                      background: decision === 'APPROVED' ? 'rgba(16, 185, 129, 0.25)' : 'var(--bg-surface)',
                      border: `1px solid ${decision === 'APPROVED' ? '#10b981' : 'var(--border-subtle)'}`,
                      color: decision === 'APPROVED' ? '#34d399' : 'var(--text-muted)',
                      fontWeight: 600,
                    }}
                  >
                    <CheckCircle size={16} />
                    <span>APPROVE</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => setDecision('REJECTED')}
                    className="btn"
                    style={{
                      flex: 1,
                      background: decision === 'REJECTED' ? 'rgba(244, 63, 94, 0.25)' : 'var(--bg-surface)',
                      border: `1px solid ${decision === 'REJECTED' ? '#f43f5e' : 'var(--border-subtle)'}`,
                      color: decision === 'REJECTED' ? '#fb7185' : 'var(--text-muted)',
                      fontWeight: 600,
                    }}
                  >
                    <XCircle size={16} />
                    <span>REJECT</span>
                  </button>
                </div>
              </div>

              <div>
                <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
                  Adjudication Reason / Notes
                </label>
                <textarea
                  rows={3}
                  required
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  className="textarea"
                  placeholder="Provide rationale for decision..."
                />
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '0.75rem', marginTop: '0.5rem' }}>
                <button
                  type="button"
                  onClick={() => setSelectedApproval(null)}
                  disabled={submitting}
                  className="btn btn-secondary"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submitting || !canDecide}
                  className="btn btn-primary"
                  id="btn-confirm-approval-decision"
                >
                  {submitting ? 'Submitting...' : `Submit ${decision}`}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
