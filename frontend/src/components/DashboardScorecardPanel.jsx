import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Scale, HelpCircle } from 'lucide-react';
import { FreshnessBadge } from './Freshness';

function TooltipHelp({ text }) {
  const [visible, setVisible] = React.useState(false);
  return (
    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
          marginLeft: '4px',
          display: 'flex',
          alignItems: 'center',
          color: '#64748b'
        }}
        title="What does this mean?"
      >
        <HelpCircle size={12} />
      </button>
      {visible && (
        <div style={{
          position: 'absolute',
          bottom: '22px',
          left: '50%',
          transform: 'translateX(-50%)',
          width: '200px',
          padding: '8px 10px',
          borderRadius: '8px',
          background: 'rgba(15,23,42,0.96)',
          border: '1px solid rgba(255,255,255,0.15)',
          color: '#cbd5e1',
          fontSize: '0.72rem',
          lineHeight: '1.35',
          zIndex: 100,
          boxShadow: '0 8px 20px rgba(0,0,0,0.6)',
          textAlign: 'left',
          pointerEvents: 'none',
          whiteSpace: 'normal',
        }}>
          {text}
        </div>
      )}
    </div>
  );
}

const SIGNAL_COLORS_COMPARATIVE = {
  Exceptional: '#10b981',
  'Strong buy': '#10b981',
  Favorable: '#34d399',
  Balanced: '#94a3b8',
  Caution: '#f59e0b',
  Avoid: '#ef4444',
};

const PREVIEW_NEUTRAL = '#94a3b8';

function formatNum(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(2);
}

function formatCurrency(val) {
  if (val == null || Number.isNaN(Number(val))) return '—';
  const num = Number(val);
  if (num >= 1e9) {
    return `$${(num / 1e9).toFixed(2)}B`;
  }
  if (num >= 1e6) {
    return `$${(num / 1e6).toFixed(2)}M`;
  }
  if (num >= 1e3) {
    return `$${(num / 1e3).toFixed(2)}K`;
  }
  return `$${num.toLocaleString()}`;
}

function getTierStyle(tier) {
  const t = (tier || '').toLowerCase();
  if (t.includes('founder') || t.includes('top 10')) {
    return {
      color: '#10b981',
      background: 'rgba(16,185,129,0.12)',
      border: '1px solid rgba(16,185,129,0.25)',
    };
  }
  if (t.includes('above') || t.includes('most')) {
    return {
      color: '#f59e0b',
      background: 'rgba(245,158,11,0.12)',
      border: '1px solid rgba(245,158,11,0.25)',
    };
  }
  if (t.includes('below') || t.includes('bottom')) {
    return {
      color: '#ef4444',
      background: 'rgba(239,68,68,0.12)',
      border: '1px solid rgba(239,68,68,0.25)',
    };
  }
  return {
    color: '#94a3b8',
    background: 'rgba(148,163,184,0.12)',
    border: '1px solid rgba(148,163,184,0.25)',
  };
}

function MiniScatter({ row, neutralStyle }) {
  if (!row) return null;
  const width = 280;
  const height = 160;
  const padL = 36;
  const padB = 28;
  const padT = 16;
  const padR = 12;
  const innerW = width - padL - padR;
  const innerH = height - padT - padB;
  const scale = (v) => Math.max(0, Math.min(10, Number(v) || 0));
  const riskW = row.risk_score?.weighted ?? row.risk_score_weighted;
  const returnW = row.return_score?.weighted ?? row.return_score_weighted;
  const cx = padL + (scale(riskW) / 10) * innerW;
  const cy = padT + (1 - scale(returnW) / 10) * innerH;
  const color = neutralStyle
    ? PREVIEW_NEUTRAL
    : (SIGNAL_COLORS_COMPARATIVE[row.signal] || PREVIEW_NEUTRAL);

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="img"
      aria-label="Risk vs return position"
      style={{ background: 'rgba(0,0,0,0.2)', borderRadius: 8, maxWidth: 320 }}
    >
      <line x1={padL} y1={height - padB} x2={width - padR} y2={height - padB} stroke="rgba(148,163,184,0.35)" />
      <line x1={padL} y1={padT} x2={padL} y2={height - padB} stroke="rgba(148,163,184,0.35)" />
      <line
        x1={padL + innerW / 2}
        y1={padT}
        x2={padL + innerW / 2}
        y2={height - padB}
        stroke="rgba(148,163,184,0.15)"
        strokeDasharray="4 4"
      />
      <line
        x1={padL}
        y1={padT + innerH / 2}
        x2={width - padR}
        y2={padT + innerH / 2}
        stroke="rgba(148,163,184,0.15)"
        strokeDasharray="4 4"
      />
      <text x={width / 2} y={height - 6} textAnchor="middle" fontSize="9" fill="#64748b">
        Risk
      </text>
      <text x={8} y={height / 2} transform={`rotate(-90 8 ${height / 2})`} textAnchor="middle" fontSize="9" fill="#64748b">
        Return
      </text>
      <circle cx={cx} cy={cy} r={8} fill={color} stroke="#fff" strokeWidth={1.5} />
      <text x={cx} y={cy - 12} textAnchor="middle" fontSize="10" fill="#e2e8f0" fontWeight="700">
        {row.ticker}
      </text>
    </svg>
  );
}

function mergeScorecardData(apiRow, embeddedSummary) {
  if (apiRow) return { ...apiRow, is_comparative: apiRow.is_comparative ?? true };
  if (!embeddedSummary) return null;
  return {
    ticker: embeddedSummary.ticker,
    ratio: embeddedSummary.ratio,
    signal: embeddedSummary.signal,
    action: embeddedSummary.action,
    verdict: embeddedSummary.verdict,
    quadrant: embeddedSummary.quadrant,
    one_line_reason: embeddedSummary.one_line_reason,
    framing_note: embeddedSummary.framing_note,
    is_comparative: embeddedSummary.is_comparative === true,
    return_score: { weighted: embeddedSummary.return_score_weighted },
    risk_score: { weighted: embeddedSummary.risk_score_weighted },
    data_freshness: embeddedSummary.data_freshness,
  };
}

export default function DashboardScorecardPanel({ data, embeddedSummary, ticker, loading, error }) {
  const navigate = useNavigate();
  const row = mergeScorecardData(data, embeddedSummary);
  const isPreview = row && row.is_comparative === false;
  const signalColor = isPreview
    ? PREVIEW_NEUTRAL
    : (SIGNAL_COLORS_COMPARATIVE[row?.signal] || PREVIEW_NEUTRAL);
  const verdictColor = isPreview ? PREVIEW_NEUTRAL : signalColor;

  return (
    <section className="dt-panel" data-testid="dashboard-scorecard" style={{ gridColumn: '1 / -1' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Scale size={20} color="var(--accent-purple)" />
          <div>
            <h2 className="dt-panel-title" style={{ margin: 0 }}>Risk-Reward Scorecard</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>
              Balanced preset · risk-return profile (single-name preview — not a buy/sell rating)
            </p>
          </div>
        </div>
        {row?.data_freshness && <FreshnessBadge freshness={row.data_freshness} showEod />}
      </div>

      {isPreview && row?.framing_note && (
        <p style={{ color: 'var(--dt-muted)', fontSize: '0.8rem', marginTop: 12, marginBottom: 0, lineHeight: 1.45 }}>
          {row.framing_note}
        </p>
      )}

      {loading && (
        <p style={{ color: 'var(--text-muted)', marginTop: 16, fontSize: '0.9rem' }}>Scoring {ticker || 'ticker'}…</p>
      )}

      {!loading && error && (
        <p style={{ color: 'var(--accent-red)', marginTop: 16, fontSize: '0.9rem' }}>{error}</p>
      )}

      {!loading && !error && !row && (
        <p style={{ color: 'var(--text-muted)', marginTop: 16, fontSize: '0.9rem' }}>
          Run Analyze to load risk-return scores.
        </p>
      )}

      {!loading && row && (
        <div
          style={{
            marginTop: 16,
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))',
            gap: 20,
            alignItems: 'start',
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 12 }}>
            {[
              {
                label: 'Return',
                displayLabel: 'Reward Potential',
                value: formatNum(row.return_score?.weighted),
                tooltip: 'A score from 0 to 10 predicting expected upside based on historical growth and profitability metrics. Higher is better.'
              },
              {
                label: 'Risk',
                displayLabel: 'Risk Level',
                value: formatNum(row.risk_score?.weighted),
                tooltip: 'A score from 0 to 10 predicting potential downsides based on debt, stock volatility (Beta), and valuation multiples. Lower is safer.'
              },
              {
                label: 'Ratio',
                displayLabel: 'Reward-to-Risk Ratio',
                value: formatNum(row.ratio),
                bold: true,
                tooltip: 'Reward divided by Risk. A ratio above 1.0 indicates that the potential reward outweighs the risk.'
              },
              {
                label: 'SITG boost',
                displayLabel: 'Insider Alignment (SITG) Boost',
                value: row.sitg_boost > 0 ? `+${formatNum(row.sitg_boost)}` : formatNum(row.sitg_boost ?? 0),
                tooltip: "Skin In The Game (SITG) boost. An extra credit added to the score when corporate executives or founders own a significant amount of the company's stock, aligning their interests with yours."
              },
            ].map((m) => (
              <div
                key={m.label}
                style={{
                  padding: '12px 14px',
                  background: 'rgba(255,255,255,0.03)',
                  borderRadius: 10,
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4, display: 'flex', alignItems: 'center' }}>
                  <span>{m.displayLabel}</span>
                  <TooltipHelp text={m.tooltip} />
                </div>
                <div style={{ fontSize: '1.35rem', fontWeight: m.bold ? 800 : 700 }}>{m.value}</div>
              </div>
            ))}
            <div
              style={{
                padding: '12px 14px',
                background: 'rgba(255,255,255,0.03)',
                borderRadius: 10,
                border: '1px solid rgba(255,255,255,0.06)',
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4, display: 'flex', alignItems: 'center' }}>
                <span>Risk-Reward Profile</span>
                <TooltipHelp text="The broad category this stock's risk-reward configuration falls into (e.g. Balanced, Conservative, Caution, Avoid)." />
              </div>
              <div style={{ fontSize: '1rem', fontWeight: 700, color: signalColor }}>
                {row.signal || '—'}
              </div>
            </div>
            <div
              style={{
                padding: '12px 14px',
                background: 'rgba(255,255,255,0.03)',
                borderRadius: 10,
                border: '1px solid rgba(255,255,255,0.06)',
              }}
            >
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4, display: 'flex', alignItems: 'center' }}>
                <span>Overall Rating</span>
                <TooltipHelp text="The AI-synthesized rating label summarizing the general attractiveness of this risk-reward configuration." />
              </div>
              <div style={{ fontSize: '1rem', fontWeight: 700, color: verdictColor }}>
                {row.verdict || '—'}
              </div>
            </div>
          </div>

          <div>
            <MiniScatter row={row} neutralStyle={isPreview} />
            <div style={{ marginTop: 10, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              Quadrant: <strong style={{ color: 'var(--text-primary)' }}>{row.quadrant || '—'}</strong>
              {row.action ? (
                <>
                  {' '}
                  · Action: <strong style={{ color: 'var(--text-primary)' }}>{row.action}</strong>
                </>
              ) : null}
            </div>
            <p style={{ marginTop: 12, fontSize: '0.76rem', color: 'var(--text-muted)', lineHeight: 1.45 }}>
              💡 <strong>How to read:</strong> Look for assets in the <strong>top-left</strong> quadrant (High Return, Low Risk). 
              A <strong>Reward-to-Risk Ratio</strong> above <strong>1.00</strong> means the potential gains outweigh the risks.
            </p>
            <button
              type="button"
              onClick={() => navigate('/scorecard')}
              style={{
                marginTop: 10,
                background: 'none',
                border: 'none',
                color: 'var(--accent-purple)',
                fontSize: '0.78rem',
                cursor: 'pointer',
                padding: 0,
                textDecoration: 'underline',
              }}
            >
              Compare multiple tickers on Scorecard →
            </button>
          </div>

          {(row.one_line_reason || row.ceo_name || row.sitg_archetype) && (
            <div
              style={{
                gridColumn: '1 / -1',
                padding: '14px 16px',
                background: 'rgba(124,58,237,0.08)',
                border: '1px solid rgba(124,58,237,0.2)',
                borderRadius: 10,
              }}
            >
              {row.ceo_name && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 6 }}>
                    <span style={{ fontSize: '0.85rem', color: '#cbd5e1', fontWeight: 600 }}>
                      CEO: {row.ceo_name}
                    </span>
                    {row.sitg_percentile_tier ? (
                      <span
                        style={{
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: '12px',
                          ...getTierStyle(row.sitg_percentile_tier),
                        }}
                      >
                        {row.sitg_percentile_tier}
                      </span>
                    ) : row.sitg_archetype ? (
                      <span
                        style={{
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          padding: '2px 8px',
                          borderRadius: '12px',
                          ...getTierStyle(row.sitg_archetype),
                        }}
                      >
                        {row.sitg_archetype}
                      </span>
                    ) : null}
                  </div>
                  {(row.ceo_base_salary != null && row.sitg_value != null) && (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 4 }}>
                      <span>
                        SITG Multiple: <strong style={{ color: '#f59e0b' }}>{formatNum(row.sitg_multiple)}x</strong>
                      </span>
                      <span>·</span>
                      <span>SITG Value: <strong>{formatCurrency(row.sitg_value)}</strong></span>
                      <span>·</span>
                      <span>Base Salary: <strong>{formatCurrency(row.ceo_base_salary)}</strong></span>
                    </div>
                  )}
                  {!(row.ceo_base_salary != null && row.sitg_value != null) && row.sitg_archetype && (
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                      SITG Profile: {row.sitg_archetype}
                    </div>
                  )}
                </div>
              )}
              {row.one_line_reason && (
                <p style={{ margin: 0, fontSize: '0.9rem', lineHeight: 1.5, color: '#e2e8f0' }}>
                  {row.one_line_reason}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
