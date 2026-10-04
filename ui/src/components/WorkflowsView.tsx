import React, { useState } from 'react';
import { WorkflowSummaryItem } from '../types/api';
import { Search, Filter, RefreshCw, GitBranch } from 'lucide-react';

interface WorkflowsViewProps {
  workflows: WorkflowSummaryItem[];
  onSelectWorkflow: (id: string) => void;
  onRefresh: () => void;
  loading: boolean;
}

export const WorkflowsView: React.FC<WorkflowsViewProps> = ({
  workflows,
  onSelectWorkflow,
  onRefresh,
  loading,
}) => {
  const [search, setSearch] = useState('');
  const [filterState, setFilterState] = useState<string>('ALL');

  const filtered = workflows.filter((wf) => {
    const matchesSearch =
      wf.workflow_run_id.toLowerCase().includes(search.toLowerCase()) ||
      wf.claim_id.toLowerCase().includes(search.toLowerCase());

    if (!matchesSearch) return false;
    if (filterState === 'ALL') return true;
    if (filterState === 'ACTIVE') return !wf.is_terminal && wf.current_state !== 'HUMAN_APPROVAL';
    if (filterState === 'WAITING') return wf.current_state === 'HUMAN_APPROVAL';
    if (filterState === 'APPROVED') return wf.current_state === 'APPROVED';
    if (filterState === 'REJECTED') return wf.current_state === 'REJECTED';
    if (filterState === 'FAILED') return wf.is_terminal && wf.current_state !== 'APPROVED' && wf.current_state !== 'REJECTED';
    return true;
  });

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
    <div style={{ padding: '2rem 1.5rem', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <GitBranch size={24} color="var(--accent-cyan)" />
            <span>Workflows & Claims Registry</span>
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
            Inspect state transitions, agent telemetry, checkpoints, and budget utilization.
          </p>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="btn btn-secondary"
          style={{ fontSize: '0.85rem' }}
        >
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Filter and Search Bar */}
      <div className="glass-panel" style={{ padding: '1rem', display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 260 }}>
          <Search size={16} color="var(--text-dim)" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)' }} />
          <input
            type="text"
            placeholder="Search by workflow ID or claim ID..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="input mono"
            style={{ paddingLeft: '2.25rem', fontSize: '0.85rem' }}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <Filter size={16} color="var(--text-dim)" />
          <select
            value={filterState}
            onChange={(e) => setFilterState(e.target.value)}
            className="select"
            style={{ width: 'auto', fontSize: '0.85rem' }}
          >
            <option value="ALL">All States ({workflows.length})</option>
            <option value="ACTIVE">In-Flight / Active</option>
            <option value="WAITING">Awaiting Review</option>
            <option value="APPROVED">Approved</option>
            <option value="REJECTED">Rejected</option>
            <option value="FAILED">Failed / Exhausted</option>
          </select>
        </div>
      </div>

      {/* Workflows Table */}
      <div className="glass-panel" style={{ overflow: 'hidden' }}>
        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Workflow ID</th>
                <th>Claim ID</th>
                <th>Current State</th>
                <th>Terminal</th>
                <th>Steps</th>
                <th>Rework</th>
                <th>Cost</th>
                <th>Created At</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={9} style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)' }}>
                    No matching workflows found.
                  </td>
                </tr>
              ) : (
                filtered.map((wf) => (
                  <tr key={wf.workflow_run_id}>
                    <td className="mono" style={{ color: 'var(--accent-cyan)', fontWeight: 600 }}>
                      {wf.workflow_run_id}
                    </td>
                    <td className="mono">{wf.claim_id}</td>
                    <td>{getStatusBadge(wf.current_state)}</td>
                    <td>
                      {wf.is_terminal ? (
                        <span style={{ color: '#34d399', fontSize: '0.8rem', fontWeight: 600 }}>YES</span>
                      ) : (
                        <span style={{ color: '#fbbf24', fontSize: '0.8rem', fontWeight: 600 }}>NO</span>
                      )}
                    </td>
                    <td className="mono">{wf.step_count}</td>
                    <td className="mono">{wf.rework_count}</td>
                    <td className="mono">${wf.cost_usd}</td>
                    <td style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      {new Date(wf.created_at).toLocaleString()}
                    </td>
                    <td>
                      <button
                        onClick={() => onSelectWorkflow(wf.workflow_run_id)}
                        className="btn btn-secondary"
                        style={{ padding: '0.3rem 0.7rem', fontSize: '0.75rem' }}
                      >
                        Inspect
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
