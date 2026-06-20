import React from 'react';
import { Loader2 } from 'lucide-react';
import { DEBATE_AGENTS, STANCE_STYLES } from './debateConfig';

function ScaleIcon({ size = 20, color = 'currentColor' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v18M3 8l9-5 9 5M3 16l9 5 9-5M3 8l9 5M12 13l9-5" />
    </svg>
  );
}

function AgentIcon({ role, color, size = 20 }) {
  const meta = DEBATE_AGENTS.find((a) => a.role === role);
  if (!meta) return null;
  if (role === 'value') return <ScaleIcon size={size} color={color} />;
  const { Icon } = meta;
  return <Icon size={size} color={color} />;
}

function AgentCardSkeleton({ agent }) {
  return (
    <div style={{
      background: 'rgba(18,24,31,0.6)',
      borderRadius: 12,
      padding: '20px 24px',
      border: '1px solid rgba(255,255,255,0.05)',
      borderTop: `3px solid ${agent.color}`,
      height: '360px',
      display: 'flex',
      flexDirection: 'column',
      boxSizing: 'border-box',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, background: agent.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Loader2 size={18} color={agent.color} style={{ animation: 'spin 1s linear infinite' }} />
          </div>
          <div>
            <div style={{ height: 14, width: 95, background: 'rgba(255,255,255,0.06)', borderRadius: 4, marginBottom: 4 }} />
            <div style={{ height: 10, width: 70, background: 'rgba(255,255,255,0.04)', borderRadius: 4 }} />
          </div>
        </div>
      </div>
      <div style={{ height: '1px', background: 'rgba(255,255,255,0.06)', width: '100%', marginBottom: 14 }} />
      <div style={{ height: 16, background: 'rgba(255,255,255,0.05)', borderRadius: 4, marginBottom: 8, flexShrink: 0 }} />
      <div style={{ flex: 1, background: 'rgba(255,255,255,0.02)', borderRadius: 4, width: '100%', marginBottom: 12 }} />
      <div style={{ height: '1px', background: 'rgba(255,255,255,0.06)', width: '100%', marginBottom: 12 }} />
      <div style={{ height: 10, background: 'rgba(255,255,255,0.03)', borderRadius: 4, width: '70%', flexShrink: 0 }} />
    </div>
  );
}

function AgentCard({ argument, agent }) {
  const stanceStyle = STANCE_STYLES[argument.stance] || STANCE_STYLES.NEUTRAL;
  const isDegraded = argument.degraded;
  return (
    <div style={{
      background: isDegraded ? 'rgba(18,24,31,0.5)' : 'var(--dt-card)',
      borderRadius: 12,
      padding: '20px 24px',
      border: isDegraded ? '1px solid rgba(245,158,11,0.25)' : '1px solid rgba(255,255,255,0.05)',
      borderTop: `3px solid ${isDegraded ? '#f59e0b' : stanceStyle.color}`,
      opacity: isDegraded ? 0.7 : 1,
      position: 'relative',
      height: '360px',
      display: 'flex',
      flexDirection: 'column',
      boxSizing: 'border-box',
    }}>
      {isDegraded && (
        <div style={{
          position: 'absolute', top: 44, right: 10,
          padding: '2px 8px', borderRadius: 10,
          background: 'rgba(245,158,11,0.15)', color: '#f59e0b',
          fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}>
          Heuristic only
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 36, height: 36, borderRadius: 8, flexShrink: 0, background: agent.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <AgentIcon role={argument.agent_role} color={isDegraded ? '#f59e0b' : agent.color} size={18} />
          </div>
          <div>
            <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '0.88rem', lineHeight: 1.2 }}>{agent.label}</div>
            <div style={{ color: '#64748b', fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 700 }}>{argument.agent_role.toUpperCase()} AGENT</div>
          </div>
        </div>
        {/* Stance Badge on the far right */}
        <span
          style={{
            padding: '3px 8px',
            borderRadius: 4,
            background: stanceStyle.label === 'NEUTRAL' ? '#1e293b' : `${stanceStyle.color}15`,
            color: stanceStyle.label === 'NEUTRAL' ? '#e2e8f0' : stanceStyle.color,
            fontSize: '0.68rem',
            fontWeight: 700,
            letterSpacing: '0.04em',
            textTransform: 'uppercase',
          }}
        >
          {stanceStyle.label}
        </span>
      </div>

      <div style={{ height: '1px', background: 'rgba(255,255,255,0.06)', width: '100%', marginBottom: 14 }} />

      <p style={{
        color: '#cbd5e1',
        fontSize: '0.82rem',
        fontWeight: 600,
        lineHeight: 1.45,
        marginBottom: 12,
        flexShrink: 0,
        display: '-webkit-box',
        WebkitLineClamp: 3,
        WebkitBoxOrient: 'vertical',
        overflow: 'hidden'
      }}>
        {argument.headline}
      </p>
      
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 6, flex: 1, overflowY: 'auto', paddingRight: 4 }} className="dt-debate-card-list">
        {(argument.key_points || []).map((pt, i) => (
          <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 4 }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, marginTop: 6, background: isDegraded ? '#f59e0b' : agent.color }} />
            <span style={{ color: '#94a3b8', fontSize: '0.82rem', lineHeight: 1.5 }}>{pt}</span>
          </li>
        ))}
      </ul>

      <div style={{ height: '1px', background: 'rgba(255,255,255,0.06)', width: '100%', marginTop: 'auto', marginBottom: 12 }} />

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        <div style={{ flex: 1, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.08)' }}>
          <div style={{
            height: '100%',
            borderRadius: 2,
            width: `${Math.round((argument.confidence || 0) * 100)}%`,
            background: isDegraded ? '#f59e0b' : stanceStyle.color,
            transition: 'width 0.8s ease'
          }} />
        </div>
        <div style={{ textAlign: 'right', minWidth: 35, lineHeight: 1.1 }}>
          <div style={{ color: '#e2e8f0', fontSize: '0.8rem', fontWeight: 700 }}>
            {Math.round((argument.confidence || 0) * 100)}%
          </div>
          <div style={{ color: '#64748b', fontSize: '0.58rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.02em' }}>
            conf
          </div>
        </div>
      </div>
    </div>
  );
}

export default function DebateThreadPanel({ result, loading = false }) {
  if (!loading && !result) return null;
  return (
    <div className="dt-debate-grid">
      {DEBATE_AGENTS.map((agent) => {
        const argument = result?.arguments?.find((a) => a.agent_role === agent.role);
        return (
          <div key={agent.role} style={{ minWidth: 0, boxSizing: 'border-box' }}>
            {loading || !argument ? <AgentCardSkeleton agent={agent} /> : <AgentCard argument={argument} agent={agent} />}
          </div>
        );
      })}
    </div>
  );
}

