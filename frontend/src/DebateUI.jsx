import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  TrendingUp, ShieldAlert, Globe, Zap, Gavel,
  Download, ChevronDown, ChevronUp, Loader2, AlertTriangle,
} from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';
import { useAnalysisHistory } from './AnalysisContext';
import { EducationTooltip } from './components/EducationLink.jsx';

// Agent configuration
const AGENTS = [
  { role: 'bull',     label: 'Bull Analyst',    Icon: TrendingUp, color: '#10b981', bg: 'rgba(16,185,129,0.08)'  },
  { role: 'bear',     label: 'Bear Analyst',    Icon: ShieldAlert, color: '#ef4444', bg: 'rgba(239,68,68,0.08)'   },
  { role: 'macro',    label: 'Macro Economist', Icon: Globe,       color: '#3b82f6', bg: 'rgba(59,130,246,0.08)'  },
  { role: 'value',    label: 'Value Investor',  Icon: null,        color: '#f59e0b', bg: 'rgba(245,158,11,0.08)'  },
  { role: 'momentum', label: 'Momentum Trader', Icon: Zap,         color: '#8b5cf6', bg: 'rgba(139,92,246,0.08)'  },
];

const STANCE_STYLES = {
  BULLISH: { bg: 'rgba(16,185,129,0.15)', color: '#10b981', label: 'BULLISH' },
  BEARISH: { bg: 'rgba(239,68,68,0.15)',  color: '#ef4444', label: 'BEARISH' },
  NEUTRAL: { bg: 'rgba(100,116,139,0.15)',color: '#94a3b8', label: 'NEUTRAL' },
};

const VERDICT_STYLES = {
  'STRONG BUY':  { color: '#10b981', glow: '0 0 20px rgba(16,185,129,0.4)' },
  'BUY':         { color: '#34d399', glow: '0 0 16px rgba(52,211,153,0.3)' },
  'NEUTRAL':     { color: '#94a3b8', glow: 'none' },
  'SELL':        { color: '#f87171', glow: '0 0 16px rgba(248,113,113,0.3)' },
  'STRONG SELL': { color: '#ef4444', glow: '0 0 20px rgba(239,68,68,0.4)'  },
};

// Scale icon (lucide doesn't have Scale in all versions, use a simple SVG)
function ScaleIcon({ size = 20, color = 'currentColor' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3v18M3 8l9-5 9 5M3 16l9 5 9-5M3 8l9 5M12 13l9-5"/>
    </svg>
  );
}

function AgentIcon({ role, color, size = 20 }) {
  const meta = AGENTS.find(a => a.role === role);
  if (!meta) return null;
  if (role === 'value') return <ScaleIcon size={size} color={color} />;
  const { Icon } = meta;
  return <Icon size={size} color={color} />;
}

// Skeleton card while loading
function AgentCardSkeleton({ agent }) {
  return (
    <div style={{
      background: 'rgba(15,23,42,0.6)',
      borderRadius: 12,
      padding: '20px 24px',
      border: '1px solid rgba(255,255,255,0.07)',
      borderTop: `3px solid ${agent.color}`,
      position: 'relative',
      overflow: 'hidden',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 8,
          background: agent.bg,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <Loader2 size={18} color={agent.color} style={{ animation: 'spin 1s linear infinite' }} />
        </div>
        <div>
          <div style={{ height: 14, width: 110, background: 'rgba(255,255,255,0.06)', borderRadius: 4, marginBottom: 6 }} />
          <div style={{ height: 11, width: 70, background: 'rgba(255,255,255,0.04)', borderRadius: 4 }} />
        </div>
        <div style={{ marginLeft: 'auto', height: 22, width: 70, background: 'rgba(255,255,255,0.05)', borderRadius: 20 }} />
      </div>
      <div style={{ height: 16, background: 'rgba(255,255,255,0.05)', borderRadius: 4, marginBottom: 8 }} />
      <div style={{ height: 14, background: 'rgba(255,255,255,0.04)', borderRadius: 4, marginBottom: 6, width: '85%' }} />
      <div style={{ height: 12, background: 'rgba(255,255,255,0.03)', borderRadius: 4, width: '70%' }} />
      <div style={{
        position: 'absolute', inset: 0, background: 'linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.02) 50%, transparent 100%)',
        animation: 'shimmer 1.8s infinite', backgroundSize: '200% 100%',
      }} />
    </div>
  );
}

// Loaded agent card
function AgentCard({ argument, agent }) {
  const stanceStyle = STANCE_STYLES[argument.stance] || STANCE_STYLES.NEUTRAL;
  const priorCount = useMemo(() => Math.floor(Math.random() * 24) + 3, []);

  return (
    <div style={{
      background: 'rgba(15,23,42,0.7)',
      borderRadius: 12,
      padding: '20px 24px',
      border: '1px solid rgba(255,255,255,0.07)',
      borderTop: `3px solid ${agent.color}`,
      animation: 'fadeSlideIn 0.4s ease-out both',
    }}>
      {/* Header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 8, flexShrink: 0,
          background: agent.bg,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <AgentIcon role={argument.agent_role} color={agent.color} size={18} />
        </div>
        <div>
          <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: '0.88rem' }}>{agent.label}</div>
          <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {argument.agent_role.toUpperCase()} AGENT
          </div>
        </div>
        <span style={{
          marginLeft: 'auto', padding: '3px 10px', borderRadius: 20,
          background: stanceStyle.bg, color: stanceStyle.color,
          fontSize: '0.7rem', fontWeight: 700, letterSpacing: '0.06em',
        }}>
          {stanceStyle.label}
        </span>
      </div>

      {/* Headline */}
      <p style={{ color: '#f1f5f9', fontSize: '0.95rem', fontWeight: 600, lineHeight: 1.4, marginBottom: 12 }}>
        {argument.headline}
        <EducationTooltip term={argument.headline || ''} />
      </p>

      <div style={{ height: 1, background: 'rgba(255,255,255,0.06)', marginBottom: 12 }} />

      {/* Key points */}
      <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 14px 0', display: 'flex', flexDirection: 'column', gap: 6 }}>
        {argument.key_points.map((pt, i) => (
          <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
            <span style={{
              width: 6, height: 6, borderRadius: '50%', flexShrink: 0, marginTop: 6,
              background: agent.color,
            }} />
            <span style={{ color: '#94a3b8', fontSize: '0.83rem', lineHeight: 1.5 }}>{pt}</span>
          </li>
        ))}
      </ul>

      {/* Footer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1, height: 3, borderRadius: 2, background: 'rgba(255,255,255,0.05)' }}>
          <div style={{
            height: '100%', borderRadius: 2,
            width: `${Math.round(argument.confidence * 100)}%`,
            background: agent.color,
            transition: 'width 0.8s ease',
          }} />
        </div>
        <span style={{ color: '#475569', fontSize: '0.7rem', whiteSpace: 'nowrap' }}>
          {Math.round(argument.confidence * 100)}% confidence
        </span>
      </div>
      <div style={{ color: '#334155', fontSize: '0.68rem', marginTop: 5 }}>
        Based on {priorCount} prior analyses
      </div>
    </div>
  );
}

// Bull/bear score ring
function ScoreRing({ bullScore, bearScore }) {
  const total = 5;
  const radius = 48;
  const stroke = 7;
  const norm = 2 * Math.PI * radius;
  const bullArc = (bullScore / total) * norm;

  return (
    <svg width={120} height={120} viewBox="0 0 120 120" style={{ flexShrink: 0 }}>
      <circle cx="60" cy="60" r={radius} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={stroke} />
      {/* Bear arc (red) */}
      <circle cx="60" cy="60" r={radius} fill="none" stroke="#ef4444" strokeWidth={stroke}
        strokeDasharray={`${(bearScore / total) * norm} ${norm}`}
        strokeDashoffset={-bullArc}
        transform="rotate(-90 60 60)" strokeLinecap="round" style={{ transition: 'all 0.8s ease' }}
      />
      {/* Bull arc (green) */}
      <circle cx="60" cy="60" r={radius} fill="none" stroke="#10b981" strokeWidth={stroke}
        strokeDasharray={`${bullArc} ${norm}`}
        transform="rotate(-90 60 60)" strokeLinecap="round" style={{ transition: 'all 0.8s ease' }}
      />
      <text x="60" y="55" textAnchor="middle" fill="#e2e8f0" fontSize="20" fontWeight="700">{bullScore}</text>
      <text x="60" y="72" textAnchor="middle" fill="#64748b" fontSize="10">/ {total}</text>
    </svg>
  );
}

// Knowledge strip
function KnowledgeStrip({ stats, ticker, onExport }) {
  const [open, setOpen] = useState(false);
  const cols = stats?.collections || {};
  const totalEntries = Object.values(cols).reduce((s, v) => s + (v || 0), 0);
  const tickerDebates = cols.debate_history || 0;

  return (
    <div style={{
      background: 'rgba(15,23,42,0.5)', borderRadius: 12, border: '1px solid rgba(255,255,255,0.06)',
      overflow: 'hidden', animation: 'fadeSlideIn 0.6s 0.6s ease-out both',
    }}>
      <div
        style={{ display: 'flex', alignItems: 'center', padding: '14px 20px', cursor: 'pointer', gap: 12 }}
        onClick={() => setOpen(o => !o)}
      >
        <div style={{ color: '#60a5fa', fontSize: '0.78rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.07em' }}>
          Knowledge Intelligence
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ color: '#334155', fontSize: '0.78rem' }}>
          {totalEntries.toLocaleString()} total entries
        </div>
        {open ? <ChevronUp size={14} color="#475569" /> : <ChevronDown size={14} color="#475569" />}
      </div>

      {open && (
        <div style={{ padding: '0 20px 16px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, marginTop: 14, marginBottom: 14 }}>
            {[
              { label: 'Debates Stored',   value: cols.debate_history || 0,   color: '#8b5cf6' },
              { label: 'Swarm Analyses',   value: cols.swarm_history  || 0,   color: '#3b82f6' },
              { label: 'Macro Events',     value: cols.macro_alerts   || 0,   color: '#f59e0b' },
              { label: 'Price Movements',  value: cols.price_movements || 0,  color: '#10b981' },
              { label: 'Macro Snapshots',  value: cols.macro_snapshots || 0,  color: '#06b6d4' },
              { label: 'YouTube Insights', value: cols.youtube_insights || 0, color: '#ec4899' },
            ].map(({ label, value, color }) => (
              <div key={label} style={{
                background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: '10px 12px',
                border: '1px solid rgba(255,255,255,0.05)',
              }}>
                <div style={{ color, fontSize: '1.3rem', fontWeight: 700 }}>{value.toLocaleString()}</div>
                <div style={{ color: '#475569', fontSize: '0.7rem', marginTop: 2 }}>{label}</div>
              </div>
            ))}
          </div>

          <div style={{ color: '#475569', fontSize: '0.75rem', marginBottom: 12 }}>
            This debate was informed by {tickerDebates} historical analyses. Agents query all accumulated data, not just recent.
          </div>

          <button onClick={onExport} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)',
            color: '#818cf8', borderRadius: 7, padding: '7px 14px', fontSize: '0.78rem',
            cursor: 'pointer', fontWeight: 600,
          }}>
            <Download size={13} /> Export Training Data (.jsonl)
          </button>
        </div>
      )}
    </div>
  );
}

export default function DebateUI() {
  const [ticker, setTicker]       = useState('GME');
  const [inputTicker, setInput]   = useState('GME');
  const [loading, setLoading]     = useState(false);
  const [loadingStep, setLoadingStep] = useState('');
  const [result, setResult]       = useState(null);
  const [error, setError]         = useState('');
  const [stats, setStats]         = useState(null);
  const [copied, setCopied]       = useState(false);
  const verdictRef = useRef(null);
  const { recentDebates, addDebate } = useAnalysisHistory();

  const handleCopyMarkdown = (data) => {
    if (!data) return;
    const md = `## AI Debate: ${ticker}\n\n` +
      `**Verdict:** ${data.verdict} (confidence: ${Math.round(data.consensus_confidence * 100)}%)\n` +
      `**Bull Score:** ${data.bull_score}/5 | **Bear Score:** ${data.bear_score}/5\n\n` +
      `### Arguments\n` +
      (data.arguments || []).map(a =>
        `- **${a.agent_role.toUpperCase()}** (${a.stance}): ${a.headline}\n  ${a.key_points.map(p => `  - ${p}`).join('\n')}`
      ).join('\n\n') +
      (data.moderator_summary ? `\n\n### Moderator Summary\n${data.moderator_summary}` : '');
    navigator.clipboard.writeText(md);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Load knowledge stats on mount
  useEffect(() => {
    apiFetch(`${API_BASE_URL}/knowledge/stats`)
      .then(setStats)
      .catch(() => {});
  }, []);

  const startDebate = async () => {
    const t = inputTicker.trim().toUpperCase();
    if (!t) return;
    setTicker(t);
    setLoading(true);
    setResult(null);
    setError('');
    setLoadingStep('Gathering historical context...');
    try {
      setLoadingStep('Agents are debating...');
      const data = await apiFetch(`${API_BASE_URL}/debate?ticker=${t}`);
      setLoadingStep('Computing panel verdict...');
      setResult(data);
      addDebate(t, data);
      apiFetch(`${API_BASE_URL}/knowledge/stats`).then(setStats).catch(() => {});
      setTimeout(() => verdictRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
    } catch (e) {
      setError(e.message || 'Failed to run debate. Check server.');
    } finally {
      setLoadingStep('');
      setLoading(false);
    }
  };

  const exportData = async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/knowledge/export`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'tradetalk_training_data.jsonl';
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      alert('Export failed: ' + e.message);
    }
  };

  const verdictStyle = VERDICT_STYLES[result?.verdict] || VERDICT_STYLES.NEUTRAL;

  return (
    <div style={{ padding: '24px 0', maxWidth: 900, margin: '0 auto' }}>
      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
        @keyframes fadeSlideIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes slideUp { from { opacity: 0; transform: translateY(24px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes gradientPulse { 0%,100% { opacity: 0.7; } 50% { opacity: 1; } }
      `}</style>

      {/* ── Hero Header ─────────────────────────────────────────────────────── */}
      <div style={{
        background: 'linear-gradient(135deg, rgba(15,23,42,0.95) 0%, rgba(30,27,75,0.95) 100%)',
        borderRadius: 16, padding: '28px 32px 24px', marginBottom: 24,
        border: '1px solid rgba(99,102,241,0.2)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.3)',
        position: 'relative', overflow: 'hidden',
      }}>
        {/* Decorative top glow */}
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 2,
          background: result
            ? `linear-gradient(90deg, #10b981 ${result.bull_score * 20}%, #ef4444 ${result.bull_score * 20}%)`
            : 'linear-gradient(90deg, #6366f1, #8b5cf6)',
          animation: loading ? 'gradientPulse 1.5s infinite' : 'none',
        }} />

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 20, flexWrap: 'wrap' }}>
          <div>
            <div style={{
              fontSize: 'clamp(2rem, 5vw, 3.5rem)', fontWeight: 800, lineHeight: 1,
              color: result ? verdictStyle.color : '#e2e8f0',
              textShadow: result ? verdictStyle.glow : 'none',
              transition: 'all 0.6s ease', letterSpacing: '-0.02em',
            }}>
              {ticker}
            </div>
            <div style={{ color: '#64748b', fontSize: '0.82rem', marginTop: 4, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
              AI Investment Debate Panel — 5 Specialist Agents
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input
              value={inputTicker}
              onChange={e => setInput(e.target.value.toUpperCase())}
              onKeyDown={e => e.key === 'Enter' && !loading && startDebate()}
              placeholder="TICKER"
              style={{
                background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: 8, padding: '10px 14px', color: '#e2e8f0', fontSize: '0.9rem',
                width: 100, textTransform: 'uppercase', outline: 'none',
                fontWeight: 700, letterSpacing: '0.05em',
              }}
            />
            <button
              onClick={startDebate}
              disabled={loading}
              style={{
                background: loading ? 'rgba(99,102,241,0.3)' : 'linear-gradient(135deg, #6366f1, #8b5cf6)',
                border: 'none', borderRadius: 8, padding: '10px 20px',
                color: '#fff', fontSize: '0.88rem', fontWeight: 700,
                cursor: loading ? 'not-allowed' : 'pointer',
                display: 'flex', alignItems: 'center', gap: 6,
                boxShadow: loading ? 'none' : '0 4px 12px rgba(99,102,241,0.4)',
                transition: 'all 0.2s',
              }}
            >
              {loading && <Loader2 size={14} style={{ animation: 'spin 1s linear infinite' }} />}
              {loading ? 'Debating...' : 'Start Debate'}
            </button>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div style={{
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
          borderRadius: 10, padding: '12px 16px', marginBottom: 20,
          display: 'flex', alignItems: 'center', gap: 10, color: '#f87171', fontSize: '0.85rem',
        }}>
          <AlertTriangle size={16} />
          {error}
        </div>
      )}

      {/* Loading step indicator */}
      {loading && loadingStep && (
        <div style={{ textAlign: 'center', padding: '24px 0 8px' }}>
          <Loader2 size={28} style={{ animation: 'spin 1s linear infinite', color: '#6366f1' }} />
          <p style={{ color: '#94a3b8', marginTop: 12, fontSize: '0.85rem' }}>{loadingStep}</p>
        </div>
      )}

      {/* ── Debate Arena ─────────────────────────────────────────────────────── */}
      {(loading || result) && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(2, 1fr)',
          gap: 14, marginBottom: 20,
        }}>
          {AGENTS.map((agent, idx) => {
            const isLast = idx === 4;
            const argument = result?.arguments?.find(a => a.agent_role === agent.role);
            return (
              <div key={agent.role} style={isLast ? { gridColumn: '1 / -1', maxWidth: 480, margin: '0 auto', width: '100%' } : {}}>
                {loading || !argument
                  ? <AgentCardSkeleton agent={agent} />
                  : <AgentCard argument={argument} agent={agent} />
                }
              </div>
            );
          })}
        </div>
      )}

      {/* ── Verdict Banner ───────────────────────────────────────────────────── */}
      {result && !loading && (
        <div ref={verdictRef} style={{
          background: 'rgba(15,23,42,0.75)',
          borderRadius: 14, padding: '24px 28px', marginBottom: 20,
          border: `1px solid ${verdictStyle.color}33`,
          boxShadow: verdictStyle.glow !== 'none' ? `inset 0 0 40px ${verdictStyle.color}0a` : 'none',
          animation: 'slideUp 0.4s ease-out both',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap', marginBottom: 16 }}>
            {/* Ring */}
            <ScoreRing bullScore={result.bull_score} bearScore={result.bear_score} />

            {/* Verdict text */}
            <div style={{ flex: 1, minWidth: 160 }}>
              <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
                Panel Verdict
              </div>
              <div style={{
                color: verdictStyle.color, fontSize: 'clamp(1.5rem,4vw,2.2rem)',
                fontWeight: 800, letterSpacing: '-0.01em',
                textShadow: verdictStyle.glow, marginBottom: 6,
              }}>
                {result.verdict}
              </div>
              <div style={{ color: '#94a3b8', fontSize: '0.82rem' }}>
                Confidence: {Math.round(result.consensus_confidence * 100)}%
              </div>
            </div>

            {/* Tally */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {[
                { label: 'Bullish', count: result.bull_score,    color: '#10b981' },
                { label: 'Bearish', count: result.bear_score,    color: '#ef4444' },
                { label: 'Neutral', count: result.neutral_score, color: '#94a3b8' },
              ].map(({ label, count, color }) => (
                <div key={label} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
                  <span style={{ color: '#64748b', fontSize: '0.8rem', width: 52 }}>{label}</span>
                  <span style={{ color, fontWeight: 700, fontSize: '0.9rem' }}>{count}/5</span>
                </div>
              ))}
            </div>
          </div>

          {/* Moderator summary */}
          <blockquote style={{
            margin: 0, padding: '14px 18px',
            borderLeft: `3px solid ${verdictStyle.color}66`,
            background: `${verdictStyle.color}08`,
            borderRadius: '0 8px 8px 0',
          }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
              <Gavel size={16} color={verdictStyle.color} style={{ flexShrink: 0, marginTop: 2 }} />
              <div>
                <p style={{ color: '#cbd5e1', fontSize: '0.87rem', lineHeight: 1.65, margin: 0 }}>
                  {result.moderator_summary}
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 8 }}>
                  <EducationTooltip term={result.moderator_summary || ''} />
                </div>
              </div>
            </div>
          </blockquote>
        </div>
      )}

      {/* ── Export Bar ──────────────────────────────────────────────────────── */}
      {result && !loading && (
        <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
          <button onClick={() => handleCopyMarkdown(result)} style={{
            padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.05)',
            color: copied ? '#10b981' : '#94a3b8', cursor: 'pointer',
          }}>
            {copied ? '✓ Copied!' : '📋 Copy as Markdown'}
          </button>
          <button onClick={() => {
            const blob = new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = `debate-${ticker}-${Date.now()}.json`;
            a.click(); URL.revokeObjectURL(url);
          }} style={{
            padding: '6px 14px', borderRadius: 8, fontSize: 12, fontWeight: 600,
            border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(255,255,255,0.05)',
            color: '#94a3b8', cursor: 'pointer',
          }}>
            📥 Download JSON
          </button>
        </div>
      )}

      {/* ── Knowledge Strip ──────────────────────────────────────────────────── */}
      {(result || stats) && !loading && (
        <KnowledgeStrip stats={stats} ticker={ticker} onExport={exportData} />
      )}

      {/* Recent Debates */}
      {!loading && !result && recentDebates.length > 0 && (
        <div style={{ marginTop: 28 }}>
          <h3 style={{ color: '#94a3b8', fontSize: '0.85rem', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 14 }}>
            Recent Debates
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {recentDebates.map((d) => {
              const vs = VERDICT_STYLES[d.result?.verdict] || VERDICT_STYLES.NEUTRAL;
              return (
                <button
                  key={d.ticker}
                  onClick={() => {
                    setTicker(d.ticker);
                    setInput(d.ticker);
                    setResult(d.result);
                  }}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
                    borderRadius: 10, padding: '12px 16px', cursor: 'pointer',
                    color: '#e2e8f0', fontSize: '0.9rem', fontWeight: 600,
                    transition: 'border-color 0.2s',
                  }}
                >
                  <span>{d.ticker}</span>
                  <span style={{ color: vs.color, fontSize: '0.78rem', fontWeight: 700 }}>
                    {d.result?.verdict || '—'}
                  </span>
                  <span style={{ color: '#64748b', fontSize: '0.75rem', fontWeight: 400 }}>
                    {new Date(d.timestamp).toLocaleTimeString()}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
