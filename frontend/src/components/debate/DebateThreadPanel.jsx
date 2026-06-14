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
    <div style={{ background: 'rgba(15,23,42,0.6)', borderRadius: 12, padding: '20px 24px', border: '1px solid rgba(255,255,255,0.07)', borderTop: `3px solid ${agent.color}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <div style={{ width: 36, height: 36, borderRadius: 8, background: agent.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Loader2 size={18} color={agent.color} style={{ animation: 'spin 1s linear infinite' }} />
        </div>
        <div style={{ height: 14, width: 110, background: 'rgba(255,255,255,0.06)', borderRadius: 4 }} />
      </div>
      <div style={{ height: 16, background: 'rgba(255,255,255,0.05)', borderRadius: 4, marginBottom: 8 }} />
      <div style={{ height: 14, background: 'rgba(255,255,255,0.04)', borderRadius: 4, marginBottom: 6, width: '85%' }} />
      <div style={{ height: 12, background: 'rgba(255,255,255,0.03)', borderRadius: 4, width: '70%' }} />
    </div>
  );
}

function AgentCard({ argument, agent }) {
  const stanceStyle = STANCE_STYLES[argument.stance] || STANCE_STYLES.NEUTRAL;
  const isDegraded = argument.degraded;
  return (
    <div style={{
      background: isDegraded ? 'rgba(15,23,42,0.4)' : 'rgba(15,23,42,0.7)',
      borderRadius: 12,
      padding: '20px 24px',
      border: isDegraded ? '1px solid rgba(245,158,11,0.25)' : '1px solid rgba(255,255,255,0.07)',
      borderTop: `3px solid ${isDegraded ? '#f59e0b' : agent.color}`,
      opacity: isDegraded ? 0.7 : 1,
      position: 'relative',
    }}>
      {isDegraded && (
        <div style={{
          position: 'absolute', top: 8, right: 10,
          padding: '2px 8px', borderRadius: 10,
          background: 'rgba(245,158,11,0.15)', color: '#f59e0b',
          fontSize: '0.62rem', fontWeight: 700, letterSpacing: '0.04em',
          textTransform: 'uppercase',
        }}>
          Heuristic only
        </div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <div style={{ width: 36, height: 36, borderRadius: 8, flexShrink: 0, background: agent.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <AgentIcon role={argument.agent_role} color={isDegraded ? '#f59e0b' : agent.color} size={18} />
        </div>
        <div>
          <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '0.88rem' }}>{agent.label}</div>
          <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{argument.agent_role.toUpperCase()} AGENT</div>
        </div>
        <span style={{ marginLeft: 'auto', padding: '3px 10px', borderRadius: 20, background: stanceStyle.bg, color: stanceStyle.color, fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.06em' }}>
          {stanceStyle.label}
        </span>
      </div>
      <p style={{ color: '#f1f5f9', fontSize: '0.95rem', fontWeight: 600, lineHeight: 1.4, marginBottom: 12 }}>{argument.headline}</p>
      <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', marginBottom: 12 }} />
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {(argument.key_points || []).map((pt, i) => (
          <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <span style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, marginTop: 6, background: isDegraded ? '#f59e0b' : agent.color }} />
            <span style={{ color: '#94a3b8', fontSize: '0.83rem', lineHeight: 1.5 }}>{pt}</span>
          </li>
        ))}
      </ul>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
        <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.05)' }}>
          <div style={{ height: '100%', borderRadius: 2, width: `${Math.round((argument.confidence || 0) * 100)}%`, background: isDegraded ? '#f59e0b' : agent.color, transition: 'width 0.8s ease' }} />
        </div>
        <span style={{ color: '#475569', fontSize: '0.7rem', whiteSpace: 'nowrap' }}>{Math.round((argument.confidence || 0) * 100)}% confidence</span>
      </div>
    </div>
  );
}

export default function DebateThreadPanel({ result, loading = false }) {
  if (!loading && !result) return null;
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 14 }}>
      {DEBATE_AGENTS.map((agent, idx) => {
        const isLast = idx === 4;
        const argument = result?.arguments?.find((a) => a.agent_role === agent.role);
        return (
          <div key={agent.role} style={isLast ? { gridColumn: '1 / -1', maxWidth: 480, margin: '0 auto', width: '100%' } : {}}>
            {loading || !argument ? <AgentCardSkeleton agent={agent} /> : <AgentCard argument={argument} agent={agent} />}
          </div>
        );
      })}
    </div>
  );
}

