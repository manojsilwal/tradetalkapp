import React from 'react';
import { Gavel } from 'lucide-react';
import { VERDICT_STYLES, normalizeDebateResult } from './debateConfig';

function ScoreRing({ bullScore, bearScore }) {
  const total = 5;
  const radius = 48;
  const stroke = 7;
  const norm = 2 * Math.PI * radius;
  const bullArc = (bullScore / total) * norm;
  const netScore = 2.5 + (bullScore - bearScore) * 0.5;
  const displayScore = netScore % 1 === 0 ? netScore.toFixed(0) : netScore.toFixed(1);

  return (
    <svg width={120} height={120} viewBox="0 0 120 120" style={{ flexShrink: 0 }}>
      <circle cx="60" cy="60" r={radius} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth={stroke} />
      <circle
        cx="60" cy="60" r={radius} fill="none" stroke="#ef4444" strokeWidth={stroke}
        strokeDasharray={`${(bearScore / total) * norm} ${norm}`}
        strokeDashoffset={-bullArc}
        transform="rotate(-90 60 60)" strokeLinecap="round"
      />
      <circle
        cx="60" cy="60" r={radius} fill="none" stroke="#10b981" strokeWidth={stroke}
        strokeDasharray={`${bullArc} ${norm}`}
        transform="rotate(-90 60 60)" strokeLinecap="round"
      />
      <text x="60" y="55" textAnchor="middle" fill="#e2e8f0" fontSize="20" fontWeight="700">{displayScore}</text>
      <text x="60" y="72" textAnchor="middle" fill="#64748b" fontSize="10">/ {total}</text>
    </svg>
  );
}

function VoteChip({ label, count, color }) {
  if (!count) return null;
  return (
    <span
      style={{
        padding: '4px 10px',
        borderRadius: 20,
        background: `${color}18`,
        color,
        fontSize: '0.72rem',
        fontWeight: 700,
        letterSpacing: '0.04em',
      }}
    >
      {count} {label}
    </span>
  );
}

export default function DebateVerdictSummary({ result, loading = false }) {
  if (loading) {
    return (
      <div
        data-testid="debate-panel-verdict"
        style={{
          background: 'rgba(15,23,42,0.75)',
          borderRadius: 14,
          padding: '24px 28px',
          border: '1px solid rgba(255,255,255,0.08)',
          marginBottom: 16,
        }}
      >
        <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
          Synthesizing committee verdict…
        </div>
      </div>
    );
  }

  const normalized = normalizeDebateResult(result);
  if (!normalized) return null;

  const verdictStyle = VERDICT_STYLES[normalized.verdict] || VERDICT_STYLES.NEUTRAL;
  const { bull_score, bear_score, neutral_score } = normalized;

  return (
    <div
      data-testid="debate-panel-verdict"
      style={{
        background: 'rgba(15,23,42,0.75)',
        borderRadius: 14,
        padding: '24px 28px',
        border: `1px solid ${verdictStyle.color}33`,
        boxShadow: verdictStyle.glow !== 'none' ? `inset 0 0 40px ${verdictStyle.color}0a` : 'none',
        marginBottom: 16,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap', marginBottom: 16 }}>
        <ScoreRing bullScore={bull_score} bearScore={bear_score} />
        <div style={{ flex: 1, minWidth: 160 }}>
          <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
            Investment Committee Verdict
          </div>
          <div
            data-testid="debate-panel-verdict-label"
            style={{
              color: verdictStyle.color,
              fontSize: 'clamp(1.4rem,3vw,2rem)',
              fontWeight: 800,
              letterSpacing: '-0.01em',
              textShadow: verdictStyle.glow,
              marginBottom: 6,
            }}
          >
            {normalized.verdict}
          </div>
          <div style={{ color: '#94a3b8', fontSize: '0.82rem', marginBottom: 8 }}>
            Confidence: {Math.round((normalized.consensus_confidence || 0) * 100)}%
            <span style={{ color: '#64748b' }}> · from 5 specialist agents</span>
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            <VoteChip label="Bullish" count={bull_score} color="#10b981" />
            <VoteChip label="Bearish" count={bear_score} color="#ef4444" />
            <VoteChip label="Neutral" count={neutral_score} color="#94a3b8" />
          </div>
        </div>
      </div>
      <blockquote style={{ margin: 0, padding: '14px 18px', borderLeft: `3px solid ${verdictStyle.color}66`, background: `${verdictStyle.color}08`, borderRadius: '0 8px 8px 0' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <Gavel size={16} color={verdictStyle.color} style={{ flexShrink: 0, marginTop: 2 }} />
          <p style={{ color: '#cbd5e1', fontSize: '0.87rem', lineHeight: 1.65, margin: 0 }}>{normalized.moderator_summary}</p>
        </div>
      </blockquote>
    </div>
  );
}
