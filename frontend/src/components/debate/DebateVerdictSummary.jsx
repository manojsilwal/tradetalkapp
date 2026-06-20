import React from 'react';
import { Gavel, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { VERDICT_STYLES, normalizeDebateResult } from './debateConfig';

function highlightText(text) {
  if (!text) return '';
  
  // Regex to capture key terms: percentages, multiples, macro regimes, stances, tickers, specific metrics, and standalone numbers.
  const pattern = /(\b[A-Z]{3,5}(?:'s)?\b|\b-?\d+(?:\.\d+)?%|\b\d+(?:\.\d+)?x\b|\b(?:STRONG_BUY|STRONG_SELL|BEAR_STRESS|BULL_STRESS|GOLDILOCKS|STAGNATION)\b|\b(?:STRONG BUY|STRONG SELL|BUY|SELL|NEUTRAL)\b|\b\d+(?:\.\d+)?\b|\bFCF\b|\bROE\b|\bP\/E\b|\bVIX\b)/g;

  const parts = text.split(pattern);
  return parts.map((part, index) => {
    const isTicker = /^[A-Z]{3,5}(?:'s)?$/.test(part) && !['FCF', 'ROE', 'VIX', 'BUY', 'SELL'].includes(part);
    const isPercentage = /-?\d+(?:\.\d+)?%/.test(part);
    const isMultiple = /\d+(?:\.\d+)?x/.test(part);
    const isStanceOrRegime = /^(?:STRONG_BUY|STRONG_SELL|BEAR_STRESS|BULL_STRESS|GOLDILOCKS|STAGNATION|STRONG BUY|STRONG SELL|BUY|SELL|NEUTRAL)$/.test(part);
    const isNumber = /^\d+(?:\.\d+)?$/.test(part);
    const isMetric = /^(?:FCF|ROE|P\/E|VIX)$/.test(part);
    
    if (isTicker || isPercentage || isMultiple || isStanceOrRegime || isNumber || isMetric) {
      let color = '#38bdf8'; // Blue highlight for tickers/misc
      if (isPercentage || isMultiple || isNumber) {
        color = '#34d399'; // Greenish for numbers/metrics
        if (part.startsWith('-')) {
          color = '#f87171'; // Red for negative percentages
        }
      } else if (isStanceOrRegime) {
        if (part.includes('BUY') || part === 'BULLISH' || part === 'GOLDILOCKS') {
          color = '#10b981';
        } else if (part.includes('SELL') || part === 'BEARISH' || part.includes('STRESS') || part === 'STAGNATION') {
          color = '#ef4444';
        } else {
          color = '#94a3b8';
        }
      } else if (isMetric) {
        color = '#fbbf24'; // Yellow for metrics (FCF, ROE, P/E, VIX)
      }
      
      return (
        <strong key={index} style={{ color, fontWeight: 700 }}>
          {part}
        </strong>
      );
    }
    
    return part;
  });
}

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
      <text x="60" y="58" textAnchor="middle" fill="#ffffff" fontSize="24" fontWeight="800">{displayScore}</text>
      <text x="60" y="78" textAnchor="middle" fill="#64748b" fontSize="11" fontWeight="600">/ {total}</text>
    </svg>
  );
}

function VoteChip({ label, count, color }) {
  if (!count) return null;
  const bg = label === 'Neutral' ? '#1e293b' : `${color}18`;
  const textCol = label === 'Neutral' ? '#e2e8f0' : color;
  return (
    <span
      style={{
        padding: '4px 10px',
        borderRadius: 4,
        background: bg,
        color: textCol,
        fontSize: '0.72rem',
        fontWeight: 700,
        letterSpacing: '0.02em',
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
          background: 'var(--dt-card)',
          borderRadius: 12,
          padding: '24px 28px',
          border: '1px solid rgba(255,255,255,0.05)',
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

  // Determine trend icon next to verdict
  let TrendIcon = Minus;
  if (normalized.verdict.includes('BUY')) {
    TrendIcon = TrendingUp;
  } else if (normalized.verdict.includes('SELL')) {
    TrendIcon = TrendingDown;
  }

  return (
    <div
      data-testid="debate-panel-verdict"
      style={{
        background: 'var(--dt-card)',
        borderRadius: 12,
        padding: '24px 20px',
        border: '1px solid rgba(255,255,255,0.05)',
        borderTop: `3px solid ${verdictStyle.color}`,
        boxShadow: '0 4px 20px rgba(0, 0, 0, 0.2)',
        display: 'flex',
        flexDirection: 'column',
        boxSizing: 'border-box',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
        <ScoreRing bullScore={bull_score} bearScore={bear_score} />
        <div style={{ flex: 1, minWidth: 160 }}>
          <div style={{ color: '#64748b', fontSize: '0.66rem', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 700, marginBottom: 4 }}>
            Investment Committee Verdict
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <div
              data-testid="debate-panel-verdict-label"
              style={{
                color: verdictStyle.color,
                fontSize: '1.9rem',
                fontWeight: 800,
                letterSpacing: '-0.01em',
                textShadow: verdictStyle.glow,
                lineHeight: 1.1,
              }}
            >
              {normalized.verdict}
            </div>
            <TrendIcon size={26} color={verdictStyle.color} style={{ opacity: 0.9, display: 'inline-block' }} />
          </div>
          <div style={{ color: '#94a3b8', fontSize: '0.78rem', marginBottom: 8, lineHeight: 1.3 }}>
            Confidence: {Math.round((normalized.consensus_confidence || 0) * 100)}% · from 5 specialist agents
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            <VoteChip label="Bullish" count={bull_score} color="#10b981" />
            <VoteChip label="Neutral" count={neutral_score} color="#94a3b8" />
            <VoteChip label="Bearish" count={bear_score} color="#ef4444" />
          </div>
        </div>
      </div>

      {/* Dark background box for the Moderator Summary */}
      <div
        style={{
          background: '#090e15',
          borderRadius: 8,
          padding: '16px',
          border: '1px solid rgba(255,255,255,0.03)',
        }}
      >
        <p style={{ color: '#cbd5e1', fontSize: '0.84rem', lineHeight: 1.55, margin: 0, fontWeight: 500 }}>
          {highlightText(normalized.moderator_summary)}
        </p>
      </div>
    </div>
  );
}
