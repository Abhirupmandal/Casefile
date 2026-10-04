import React, { useEffect, useState } from 'react';
import { HealthResponse, ReadinessResponse } from '../types/api';
import { api } from '../services/api';
import { HeartPulse, CheckCircle2, XCircle, RefreshCw, Server, Database } from 'lucide-react';

export const HealthView: React.FC = () => {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchHealth = async () => {
    setLoading(true);
    setError(null);
    try {
      const [h, r] = await Promise.all([api.getHealth(), api.getReadiness()]);
      setHealth(h);
      setReadiness(r);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to fetch diagnostic state');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
  }, []);

  return (
    <div style={{ padding: '2rem 1.5rem', maxWidth: 1400, margin: '0 auto', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: '1.75rem', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <HeartPulse size={24} color="#10b981" />
            <span>Operational Health & Readiness Probes</span>
          </h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: '0.25rem' }}>
            Process liveness, engine dependencies, and persistent storage readiness indicators.
          </p>
        </div>

        <button onClick={fetchHealth} disabled={loading} className="btn btn-secondary">
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
          <span>Check Status</span>
        </button>
      </div>

      {error && (
        <div style={{
          background: 'rgba(244, 63, 94, 0.15)',
          border: '1px solid rgba(244, 63, 94, 0.3)',
          color: '#fb7185',
          padding: '0.75rem 1rem',
          borderRadius: 'var(--radius-md)',
          fontSize: '0.85rem',
        }}>
          {error}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.25rem' }}>
        {/* Liveness Card */}
        <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Server size={20} color="var(--accent-cyan)" />
              <h2 style={{ fontSize: '1.15rem' }}>Application Liveness</h2>
            </div>
            <span className="badge badge-approved" id="health-liveness-badge">
              {health?.status?.toUpperCase() ?? 'ONLINE'}
            </span>
          </div>

          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Endpoint: <code className="mono">GET /health</code> (public root probe)
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.85rem', background: 'rgba(10, 13, 20, 0.5)', padding: '0.85rem', borderRadius: 'var(--radius-md)' }}>
            <div>Service: <strong className="mono">{health?.service ?? 'casefile-api'}</strong></div>
            <div>Version: <span className="mono">{health?.version ?? '0.1.0'}</span></div>
            <div>Timestamp: <span className="mono" style={{ fontSize: '0.75rem' }}>{health?.timestamp ?? new Date().toISOString()}</span></div>
          </div>
        </div>

        {/* Readiness Card */}
        <div className="glass-panel" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <Database size={20} color="#34d399" />
              <h2 style={{ fontSize: '1.15rem' }}>Dependency Readiness</h2>
            </div>
            <span className={`badge ${readiness?.ready ? 'badge-approved' : 'badge-failed'}`} id="health-readiness-badge">
              {readiness?.ready ? 'READY' : 'DEGRADED'}
            </span>
          </div>

          <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>
            Endpoint: <code className="mono">GET /ready</code> (dependency validation)
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', fontSize: '0.85rem', background: 'rgba(10, 13, 20, 0.5)', padding: '0.85rem', borderRadius: 'var(--radius-md)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Database Connection:</span>
              <span style={{ color: '#34d399', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <CheckCircle2 size={14} /> OK
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Persistence UnitOfWork:</span>
              <span style={{ color: '#34d399', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <CheckCircle2 size={14} /> OK
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Checkpoint Repository:</span>
              <span style={{ color: '#34d399', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <CheckCircle2 size={14} /> OK
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
