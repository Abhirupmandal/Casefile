import React from 'react';
import { UserRole } from '../types/api';
import {
  ShieldAlert,
  Activity,
  GitBranch,
  CheckCircle,
  RotateCcw,
  BarChart3,
  HeartPulse,
  UserCheck,
} from 'lucide-react';

interface NavbarProps {
  currentTab: string;
  onTabChange: (tab: string) => void;
  currentRole: UserRole;
  onRoleChange: (role: UserRole) => void;
  pendingApprovalsCount: number;
  isHealthy: boolean;
}

export const Navbar: React.FC<NavbarProps> = ({
  currentTab,
  onTabChange,
  currentRole,
  onRoleChange,
  pendingApprovalsCount,
  isHealthy,
}) => {
  const tabs = [
    { id: 'dashboard', label: 'Overview', icon: Activity },
    { id: 'workflows', label: 'Workflows', icon: GitBranch },
    {
      id: 'approvals',
      label: 'Approvals',
      icon: CheckCircle,
      badge: pendingApprovalsCount > 0 ? pendingApprovalsCount : undefined,
    },
    { id: 'replay', label: 'Replay Simulator', icon: RotateCcw },
    { id: 'evaluations', label: 'Evaluations', icon: BarChart3 },
    { id: 'health', label: 'System Health', icon: HeartPulse },
  ];

  return (
    <header style={{
      borderBottom: '1px solid var(--border-subtle)',
      background: 'rgba(16, 21, 34, 0.85)',
      backdropFilter: 'blur(12px)',
      position: 'sticky',
      top: 0,
      zIndex: 100,
    }}>
      <div style={{
        maxWidth: 1400,
        margin: '0 auto',
        padding: '0.75rem 1.5rem',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '1rem',
      }}>
        {/* Brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{
            background: 'linear-gradient(135deg, #6366f1, #06b6d4)',
            padding: '0.45rem',
            borderRadius: 'var(--radius-md)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 0 15px rgba(99, 102, 241, 0.4)',
          }}>
            <ShieldAlert size={20} color="#fff" />
          </div>
          <div>
            <div style={{ fontWeight: 700, fontSize: '1.1rem', letterSpacing: '-0.02em', display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span>CASEFILE</span>
              <span style={{ fontSize: '0.65rem', padding: '0.1rem 0.4rem', borderRadius: 4, background: 'rgba(99, 102, 241, 0.2)', color: '#818cf8', fontWeight: 600 }}>
                OPERATIONS CONSOLE
              </span>
            </div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
              Multi-Agent Insurance Adjudication
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const active = currentTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => onTabChange(tab.id)}
                className="btn"
                style={{
                  background: active ? 'var(--bg-surface-hover)' : 'transparent',
                  color: active ? '#fff' : 'var(--text-muted)',
                  border: active ? '1px solid var(--border-strong)' : '1px solid transparent',
                  padding: '0.45rem 0.85rem',
                  fontSize: '0.85rem',
                }}
              >
                <Icon size={16} color={active ? 'var(--accent-cyan)' : 'currentColor'} />
                <span>{tab.label}</span>
                {tab.badge !== undefined && (
                  <span style={{
                    background: 'var(--accent-amber)',
                    color: '#000',
                    fontSize: '0.7rem',
                    fontWeight: 700,
                    padding: '0.05rem 0.4rem',
                    borderRadius: 'var(--radius-full)',
                    marginLeft: '0.25rem',
                  }}>
                    {tab.badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        {/* Right Tools: Role switcher & Health badge */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          {/* Health indicator */}
          <div
            onClick={() => onTabChange('health')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.35rem',
              fontSize: '0.75rem',
              color: isHealthy ? '#34d399' : '#fb7185',
              background: isHealthy ? 'rgba(16, 185, 129, 0.1)' : 'rgba(244, 63, 94, 0.1)',
              border: `1px solid ${isHealthy ? 'rgba(16, 185, 129, 0.25)' : 'rgba(244, 63, 94, 0.25)'}`,
              padding: '0.25rem 0.6rem',
              borderRadius: 'var(--radius-full)',
              cursor: 'pointer',
            }}
          >
            <span style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: isHealthy ? '#10b981' : '#f43f5e',
              boxShadow: isHealthy ? '0 0 6px #10b981' : '0 0 6px #f43f5e',
            }} />
            <span className="mono">{isHealthy ? 'ONLINE' : 'DEGRADED'}</span>
          </div>

          {/* Role selector */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <UserCheck size={16} color="var(--text-muted)" />
            <select
              value={currentRole}
              onChange={(e) => onRoleChange(e.target.value as UserRole)}
              className="select mono"
              style={{
                padding: '0.25rem 0.5rem',
                fontSize: '0.75rem',
                width: 'auto',
                fontWeight: 600,
                color: '#e2e8f0',
              }}
              title="Switch simulated operator role to test server-side authorization"
            >
              <option value="VIEWER">Role: VIEWER</option>
              <option value="OPERATOR">Role: OPERATOR</option>
              <option value="CLAIM_REVIEWER">Role: CLAIM_REVIEWER</option>
              <option value="SENIOR_REVIEWER">Role: SENIOR_REVIEWER</option>
              <option value="CLAIM_SUPERVISOR">Role: CLAIM_SUPERVISOR</option>
              <option value="ADMIN">Role: ADMIN</option>
            </select>
          </div>
        </div>
      </div>
    </header>
  );
};
