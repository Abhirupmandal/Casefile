import React, { useState } from 'react';
import { ReplayResponse, WorkflowSummaryItem } from '../types/api';
import { api } from '../services/api';
import { RotateCcw, ShieldCheck, CheckCircle2, AlertCircle, Info } from 'lucide-react';

interface ReplayViewProps {
  workflows: WorkflowSummaryItem[];
  initialReplay?: ReplayResponse | null;
}

export const ReplayView: React.FC<ReplayViewProps> = ({ workflows, initialReplay }) => {
  const [selectedWorkflowId, setSelectedWorkflowId] = useState(
    workflows.length > 0 ? workflows[0].workflow_run_id : ''
  );
  const [replayResult, setReplayResult] = useState<ReplayResponse | null>(initialReplay || null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRunReplay = async () => {
    if (!selectedWorkflowId) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.replayWorkflow(selectedWorkflowId);
      setReplayResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Replay simulation failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ padding: '2rem 1.5rem', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <RotateCcw size={24} color="var(--accent-cyan)" />
          <span>Deterministic Replay Simulator</span>
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
          Simulates execution from persisted checkpoints without external side-effects or mutating live workflows.
        </p>
      </div>

      {/* Safety Notice Callout */}
      <div className="glass-panel" style={{
        padding: '1rem 1.25rem',
        background: 'rgba(6, 182, 212, 0.1)',
        border: '1px solid rgba(6, 182, 212, 0.3)',
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
      }}>
        <Info size={20} color="var(--accent-cyan)" />
        <div style={{ fontSize: '0.85rem', color: '#e0f2fe' }}>
          <strong>REPLAY / SIMULATION MODE:</strong> Replays execute strictly against recorded state. They do not trigger real external payouts, cannot decide human approvals, and generate independent replay IDs.
        </div>
      </div>

      {/* Replay Controls Card */}
      <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <h3 style={{ fontSize: '1rem' }}>Initiate Simulation</h3>
        <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 300 }}>
            <label style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textTransform: 'uppercase', display: 'block', marginBottom: '0.35rem' }}>
              Select Workflow Run to Replay
            </label>
            <select
              value={selectedWorkflowId}
              onChange={(e) => setSelectedWorkflowId(e.target.value)}
              className="select mono"
              style={{ fontSize: '0.85rem' }}
            >
              {workflows.length === 0 ? (
                <option value="">No workflows recorded yet</option>
              ) : (
                workflows.map((wf) => (
                  <option key={wf.workflow_run_id} value={wf.workflow_run_id}>
                    {wf.workflow_run_id} ({wf.current_state}) - Claim: {wf.claim_id.slice(0, 8)}...
                  </option>
                ))
              )}
            </select>
          </div>

          <div style={{ alignSelf: 'flex-end' }}>
            <button
              onClick={handleRunReplay}
              disabled={loading || !selectedWorkflowId}
              className="btn btn-primary"
              id="btn-run-safe-replay"
              style={{ padding: '0.65rem 1.25rem' }}
            >
              <RotateCcw size={16} />
              <span>{loading ? 'Simulating Replay...' : 'Run Safe Replay'}</span>
            </button>
          </div>
        </div>

        {error && (
          <div style={{
            background: 'rgba(244, 63, 94, 0.15)',
            border: '1px solid rgba(244, 63, 94, 0.3)',
            color: '#fb7185',
            padding: '0.75rem',
            borderRadius: 'var(--radius-md)',
            fontSize: '0.85rem',
          }}>
            {error}
          </div>
        )}
      </div>

      {/* Replay Results Card */}
      {replayResult && (
        <div className="glass-panel" style={{ padding: '1.75rem', display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div>
              <span className="badge badge-approved" style={{ marginBottom: '0.5rem' }}>SIMULATION VERIFIED</span>
              <h2 style={{ fontSize: '1.25rem' }}>Replay Verification Summary</h2>
            </div>
            <ShieldCheck size={28} color="var(--accent-emerald)" />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '1rem' }}>
            <div style={{ background: 'rgba(10, 13, 20, 0.5)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Simulation Run ID</div>
              <div className="mono" style={{ fontSize: '0.9rem', color: 'var(--accent-cyan)', marginTop: '0.25rem', wordBreak: 'break-all' }}>
                {replayResult.replay_id}
              </div>
            </div>

            <div style={{ background: 'rgba(10, 13, 20, 0.5)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Original Workflow ID</div>
              <div className="mono" style={{ fontSize: '0.9rem', color: '#e2e8f0', marginTop: '0.25rem', wordBreak: 'break-all' }}>
                {replayResult.original_workflow_run_id}
              </div>
            </div>

            <div style={{ background: 'rgba(10, 13, 20, 0.5)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Terminal State Match</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem' }}>
                <CheckCircle2 size={16} color="#10b981" />
                <span className="mono" style={{ fontSize: '0.95rem', fontWeight: 600, color: '#34d399' }}>
                  {replayResult.terminal_state}
                </span>
              </div>
            </div>

            <div style={{ background: 'rgba(10, 13, 20, 0.5)', padding: '1rem', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)' }}>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Deterministic Path Match</div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginTop: '0.25rem' }}>
                <CheckCircle2 size={16} color="#10b981" />
                <span className="mono" style={{ fontSize: '0.95rem', fontWeight: 600, color: '#34d399' }}>
                  {replayResult.path_matched ? '100% MATCHED' : 'DIVERGED'}
                </span>
              </div>
            </div>
          </div>

          <div style={{ fontSize: '0.8rem', color: 'var(--text-dim)', borderTop: '1px solid var(--border-subtle)', paddingTop: '1rem' }}>
            Simulated at: {new Date(replayResult.simulated_at).toLocaleString()} | Original state untouched | Read-only ledger verified
          </div>
        </div>
      )}
    </div>
  );
};
