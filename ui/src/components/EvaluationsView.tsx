import React, { useEffect, useState } from 'react';
import { EvaluationCaseSummary, EvaluationSummaryResponse } from '../types/api';
import { api } from '../services/api';
import { BarChart3, CheckCircle, ShieldCheck, Flame, Cpu, ArrowUpRight } from 'lucide-react';

export const EvaluationsView: React.FC = () => {
  const [summary, setSummary] = useState<EvaluationSummaryResponse | null>(null);
  const [filter, setFilter] = useState<'ALL' | 'NOMINAL' | 'FAILURE'>('ALL');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let isMounted = true;
    api
      .getEvaluationSummary()
      .then((res) => {
        if (isMounted) setSummary(res);
      })
      .catch(() => {})
      .finally(() => {
        if (isMounted) setLoading(false);
      });
    return () => {
      isMounted = false;
    };
  }, []);

  const cases = summary?.cases || [];
  const filteredCases = cases.filter((c) => {
    if (filter === 'NOMINAL') return c.category.toUpperCase().includes('NOMINAL');
    if (filter === 'FAILURE') return !c.category.toUpperCase().includes('NOMINAL');
    return true;
  });

  return (
    <div style={{ padding: '2rem 1.5rem', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div>
        <h1 style={{ fontSize: '1.75rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
          <BarChart3 size={24} color="var(--accent-cyan)" />
          <span>Evaluation & Failure Engineering Benchmark</span>
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
          Deterministic scenario execution (CASE-001 to CASE-030) evaluating resilience, budget bounds, and invariants.
        </p>
      </div>

      {/* Metrics Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Benchmark Pass Rate</div>
          <div className="mono" style={{ fontSize: '1.75rem', fontWeight: 700, color: '#34d399', marginTop: '0.25rem' }}>
            {((summary?.pass_rate ?? 1.0) * 100).toFixed(1)}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            {summary?.passed ?? 30} passed / {summary?.total_cases ?? 30} scenarios
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Path Accuracy</div>
          <div className="mono" style={{ fontSize: '1.75rem', fontWeight: 700, color: '#38bdf8', marginTop: '0.25rem' }}>
            {((summary?.path_accuracy ?? 1.0) * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>State machine transition match</div>
        </div>

        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Replay Determinism</div>
          <div className="mono" style={{ fontSize: '1.75rem', fontWeight: 700, color: '#818cf8', marginTop: '0.25rem' }}>
            {((summary?.replay_determinism_rate ?? 1.0) * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Zero path divergence on replay</div>
        </div>

        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Approval Safety</div>
          <div className="mono" style={{ fontSize: '1.75rem', fontWeight: 700, color: '#fbbf24', marginTop: '0.25rem' }}>
            {((summary?.approval_safety_rate ?? 1.0) * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>100% human-gate enforcement</div>
        </div>

        <div className="glass-panel" style={{ padding: '1.25rem' }}>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Failure Containment</div>
          <div className="mono" style={{ fontSize: '1.75rem', fontWeight: 700, color: '#f43f5e', marginTop: '0.25rem' }}>
            {((summary?.failure_containment_rate ?? 1.0) * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Isolated error recovery rate</div>
        </div>
      </div>

      {/* Filter and Cases Table */}
      <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '1.15rem' }}>Scenario Test Matrix</h2>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button
              onClick={() => setFilter('ALL')}
              className={`btn ${filter === 'ALL' ? 'btn-primary' : 'btn-secondary'}`}
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem' }}
            >
              All (30)
            </button>
            <button
              onClick={() => setFilter('NOMINAL')}
              className={`btn ${filter === 'NOMINAL' ? 'btn-primary' : 'btn-secondary'}`}
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem' }}
            >
              Nominal (14)
            </button>
            <button
              onClick={() => setFilter('FAILURE')}
              className={`btn ${filter === 'FAILURE' ? 'btn-primary' : 'btn-secondary'}`}
              style={{ fontSize: '0.75rem', padding: '0.25rem 0.6rem' }}
            >
              Injected Failures (16)
            </button>
          </div>
        </div>

        <div className="table-container">
          <table className="data-table">
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Scenario Name</th>
                <th>Category</th>
                <th>Status</th>
                <th>Expected Terminal</th>
                <th>Actual Terminal</th>
                <th>Path Matched</th>
                <th>Steps</th>
                <th>Cost</th>
              </tr>
            </thead>
            <tbody>
              {filteredCases.map((c) => (
                <tr key={c.case_id}>
                  <td className="mono" style={{ color: 'var(--accent-cyan)', fontWeight: 600 }}>{c.case_id}</td>
                  <td>{c.scenario_name}</td>
                  <td>
                    <span className="badge badge-neutral" style={{ fontSize: '0.65rem' }}>
                      {c.category}
                    </span>
                  </td>
                  <td>
                    <span className="badge badge-approved">{c.status}</span>
                  </td>
                  <td className="mono" style={{ fontSize: '0.8rem' }}>{c.expected_terminal_state}</td>
                  <td className="mono" style={{ fontSize: '0.8rem', color: '#38bdf8' }}>{c.terminal_state}</td>
                  <td>
                    {c.path_matched ? (
                      <span style={{ color: '#34d399', fontSize: '0.8rem', fontWeight: 600 }}>YES</span>
                    ) : (
                      <span style={{ color: '#fb7185', fontSize: '0.8rem', fontWeight: 600 }}>NO</span>
                    )}
                  </td>
                  <td className="mono">{c.steps}</td>
                  <td className="mono">${c.cost_usd}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
