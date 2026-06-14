import React from 'react';
import { useNavigate } from 'react-router-dom';
import { Scale, ExternalLink } from 'lucide-react';
import { FreshnessBadge } from './Freshness';

const SIGNAL_COLORS = {
  Exceptional: '#10b981',
  'Strong buy': '#10b981',
  Favorable: '#34d399',
  Balanced: '#94a3b8',
  Caution: '#f59e0b',
  Avoid: '#ef4444',
};

const VERDICT_COLORS = {
  Strong: '#10b981',
  Favorable: '#34d399',
  Balanced: '#94a3b8',
  Stretched: '#f59e0b',
  Avoid: '#ef4444',
};

function formatNum(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(2);
}

function MiniScatter({ row }) {
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
  const cx = padL + (scale(row.risk_score?.weighted) / 10) * innerW;
  const cy = padT + (1 - scale(row.return_score?.weighted) / 10) * innerH;
  const color = SIGNAL_COLORS[row.signal] || '#94a3b8';

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

export default function DashboardScorecardPanel({ data, ticker, loading, error }) {
  const navigate = useNavigate();

  return (
    <section className="dt-panel" data-testid="dashboard-scorecard" style={{ gridColumn: '1 / -1' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <Scale size={20} color="var(--accent-purple)" />
          <div>
            <h2 className="dt-panel-title" style={{ margin: 0 }}>Risk-Reward Scorecard</h2>
            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>
              Balanced preset · runs automatically on each analyze
            </p>
          </div>
        </div>
        {data?.data_freshness && <FreshnessBadge freshness={data.data_freshness} showEod />}
      </div>

      {loading && (
        <p style={{ color: 'var(--text-muted)', marginTop: 16, fontSize: '0.9rem' }}>Scoring {ticker || 'ticker'}…</p>
      )}

      {!loading && error && (
        <p style={{ color: 'var(--accent-red)', marginTop: 16, fontSize: '0.9rem' }}>{error}</p>
      )}

      {!loading && !error && !data && (
        <p style={{ color: 'var(--text-muted)', marginTop: 16, fontSize: '0.9rem' }}>
          Run Analyze to load risk-return scores.
        </p>
      )}

      {!loading && data && (
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
              { label: 'Return', value: formatNum(data.return_score?.weighted) },
              { label: 'Risk', value: formatNum(data.risk_score?.weighted) },
              { label: 'Ratio', value: formatNum(data.ratio), bold: true },
              { label: 'SITG boost', value: data.sitg_boost > 0 ? `+${formatNum(data.sitg_boost)}` : formatNum(data.sitg_boost) },
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
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>{m.label}</div>
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
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Signal</div>
              <div style={{ fontSize: '1rem', fontWeight: 700, color: SIGNAL_COLORS[data.signal] || '#94a3b8' }}>
                {data.signal || '—'}
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
              <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>Verdict</div>
              <div style={{ fontSize: '1rem', fontWeight: 700, color: VERDICT_COLORS[data.verdict] || '#94a3b8' }}>
                {data.verdict || '—'}
              </div>
            </div>
          </div>

          <div>
            <MiniScatter row={data} />
            <div style={{ marginTop: 10, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              Quadrant: <strong style={{ color: 'var(--text-primary)' }}>{data.quadrant || '—'}</strong>
              {data.action ? (
                <>
                  {' '}
                  · Action: <strong style={{ color: 'var(--text-primary)' }}>{data.action}</strong>
                </>
              ) : null}
            </div>
          </div>

          {(data.one_line_reason || data.ceo_name || data.sitg_archetype) && (
            <div
              style={{
                gridColumn: '1 / -1',
                padding: '14px 16px',
                background: 'rgba(124,58,237,0.08)',
                border: '1px solid rgba(124,58,237,0.2)',
                borderRadius: 10,
              }}
            >
              {data.ceo_name && (
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 6 }}>
                  CEO: {data.ceo_name}
                  {data.sitg_archetype ? ` · SITG: ${data.sitg_archetype}` : ''}
                </div>
              )}
              {data.one_line_reason && (
                <p style={{ margin: 0, fontSize: '0.9rem', lineHeight: 1.5, color: '#e2e8f0' }}>
                  {data.one_line_reason}
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
