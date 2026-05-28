import React from 'react';
import { Gavel } from 'lucide-react';
import { VERDICT_STYLES } from './debateConfig';

function ScoreRing({ bullScore, bearScore }) {
  const total = 5;
  const radius = 48;
  const stroke = 7;
  const norm = 2 * Math.PI * radius;
  const bullArc = (bullScore / total) * norm;

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
      <text x="60" y="55" textAnchor="middle" fill="#e2e8f0" fontSize="20" fontWeight="700">{bullScore}</text>
      <text x="60" y="72" textAnchor="middle" fill="#64748b" fontSize="10">/ {total}</text>
    </svg>
  );
}

export default function DebateVerdictSummary({ result }) {
  if (!result) return null;
  const verdictStyle = VERDICT_STYLES[result?.verdict] || VERDICT_STYLES.NEUTRAL;
  return (
    <div style={{
      background: 'rgba(15,23,42,0.75)',
      borderRadius: 14, padding: '24px 28px',
      border: `1px solid ${verdictStyle.color}33`,
      boxShadow: verdictStyle.glow !== 'none' ? `inset 0 0 40px ${verdictStyle.color}0a` : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap', marginBottom: 16 }}>
        <ScoreRing bullScore={result.bull_score} bearScore={result.bear_score} />
        <div style={{ flex: 1, minWidth: 160 }}>
          <div style={{ color: '#64748b', fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
            Panel Verdict
          </div>
          <div style={{ color: verdictStyle.color, fontSize: 'clamp(1.4rem,3vw,2rem)', fontWeight: 800, letterSpacing: '-0.01em', textShadow: verdictStyle.glow, marginBottom: 6 }}>
            {result.verdict}
          </div>
          <div style={{ color: '#94a3b8', fontSize: '0.82rem' }}>
            Confidence: {Math.round((result.consensus_confidence || 0) * 100)}%
          </div>
        </div>
      </div>
      <blockquote style={{ margin: 0, padding: '14px 18px', borderLeft: `3px solid ${verdictStyle.color}66`, background: `${verdictStyle.color}08`, borderRadius: '0 8px 8px 0' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <Gavel size={16} color={verdictStyle.color} style={{ flexShrink: 0, marginTop: 2 }} />
          <p style={{ color: '#cbd5e1', fontSize: '0.87rem', lineHeight: 1.65, margin: 0 }}>{result.moderator_summary}</p>
        </div>
      </blockquote>
    </div>
  );
}

