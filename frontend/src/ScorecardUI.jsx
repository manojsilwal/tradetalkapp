import React, { useEffect, useMemo, useState } from 'react'
import { API_BASE_URL, fetchJsonWithMeta } from './api'

/**
 * Risk-Return Ratio Scorecard UI.
 *
 * Preset selector + ticker basket input → POST /scorecard/compare. Renders
 * the results table (with the SITG boost column that the methodology's Step 5
 * applied example highlights) plus a scatter quadrant chart (Step 6 layout).
 *
 * Situational-adjustment checkboxes map to Step 7 flag names understood by the
 * router. The scatter is rendered as a plain SVG so we don't pull in recharts
 * here; each ticker is plotted at (risk_score.weighted, return_score.weighted)
 * on a 0-10 axis with a midpoint at 5 matching classify_quadrant().
 */

const PRESETS = [
  { id: 'balanced', label: 'Balanced' },
  { id: 'growth', label: 'Growth' },
  { id: 'value', label: 'Value' },
  { id: 'income', label: 'Income / Defensive' },
]

const STEP7_FLAGS = [
  { id: 'utilities_vs_industrials', label: 'Basket mixes utilities and industrials (lower D/E weight)' },
  { id: 'bear_or_rate_hike', label: 'Bear market or rising rates (double beta weight)' },
  { id: 'ma_integration', label: 'Company is in M&A integration year (+50% execution risk)' },
  { id: 'missed_2_earnings', label: 'Company missed last two earnings (+50% execution risk)' },
  { id: 'ceo_sold_20pct_plus', label: 'CEO recently sold 20%+ of holdings (halve SITG weight)' },
  { id: 'recent_ipo_lt_2y', label: 'Company IPO\u2019d less than 2 years ago (halve SITG weight)' },
  { id: 'ceo_comp_mostly_cash', label: 'CEO compensation is 90%+ cash (dampen SITG weight)' },
]

const SIGNAL_COLORS = {
  Exceptional: '#10b981',
  'Strong buy': '#10b981',
  Favorable: '#34d399',
  Balanced: '#94a3b8',
  Caution: '#f59e0b',
  Avoid: '#ef4444',
}

const VERDICT_COLORS = {
  Strong: '#10b981',
  Favorable: '#34d399',
  Balanced: '#94a3b8',
  Stretched: '#f59e0b',
  Avoid: '#ef4444',
}

function Section({ title, children }) {
  return (
    <section style={{ marginBottom: 24 }}>
      <h2 style={{ fontSize: '1rem', fontWeight: 700, color: '#e2e8f0', marginBottom: 10 }}>{title}</h2>
      {children}
    </section>
  )
}

function ScatterPlot({ rows }) {
  const width = 560
  const height = 320
  const padL = 48
  const padB = 44
  const padT = 24
  const padR = 20
  const innerW = width - padL - padR
  const innerH = height - padT - padB

  const scale = (v) => Math.max(0, Math.min(10, Number(v) || 0))
  const xOf = (riskScore) => padL + (scale(riskScore) / 10) * innerW
  const yOf = (returnScore) => padT + (1 - scale(returnScore) / 10) * innerH

  const midX = xOf(5)
  const midY = yOf(5)

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="img"
      aria-label="Risk vs Return scatter quadrant"
      style={{ background: 'rgba(15,23,42,0.5)', borderRadius: 10, border: '1px solid rgba(148,163,184,0.2)' }}
    >
      {/* Axes */}
      <line x1={padL} y1={height - padB} x2={width - padR} y2={height - padB} stroke="rgba(148,163,184,0.4)" />
      <line x1={padL} y1={padT} x2={padL} y2={height - padB} stroke="rgba(148,163,184,0.4)" />

      {/* Quadrant midlines */}
      <line x1={midX} y1={padT} x2={midX} y2={height - padB} stroke="rgba(148,163,184,0.18)" strokeDasharray="4 4" />
      <line x1={padL} y1={midY} x2={width - padR} y2={midY} stroke="rgba(148,163,184,0.18)" strokeDasharray="4 4" />

      {/* Axis labels */}
      <text x={width / 2} y={height - 8} textAnchor="middle" fontSize="11" fill="#94a3b8">
        {'Risk score (0 low \u2192 10 high)'}
      </text>
      <text
        x={-height / 2}
        y={14}
        transform="rotate(-90)"
        textAnchor="middle"
        fontSize="11"
        fill="#94a3b8"
      >
        {'Return score (0 low \u2192 10 high)'}
      </text>

      {/* Quadrant labels */}
      <text x={padL + 8} y={padT + 14} fontSize="10" fill="#10b981">Sweet spot</text>
      <text x={width - padR - 8} y={padT + 14} textAnchor="end" fontSize="10" fill="#f59e0b">High conviction</text>
      <text x={padL + 8} y={height - padB - 6} fontSize="10" fill="#64748b">Safe but slow</text>
      <text x={width - padR - 8} y={height - padB - 6} textAnchor="end" fontSize="10" fill="#ef4444">Avoid</text>

      {/* Plotted points */}
      {rows.map((r) => {
        const cx = xOf(r.risk_score.weighted)
        const cy = yOf(r.return_score.weighted)
        const color = SIGNAL_COLORS[r.signal] || '#94a3b8'
        return (
          <g key={r.ticker}>
            <circle cx={cx} cy={cy} r={8} fill={color} fillOpacity={0.35} stroke={color} strokeWidth={1.5} />
            <text x={cx + 11} y={cy + 4} fontSize="11" fontWeight="700" fill="#e2e8f0">
              {r.ticker}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

function formatNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '\u2014'
  return Number(v).toFixed(digits)
}

export default function ScorecardUI() {
  const [tickerInput, setTickerInput] = useState('HUBB, PWR, ETN, GEV, NEE, MTZ')
  const [preset, setPreset] = useState('balanced')
  const [flags, setFlags] = useState({})
  const [skipLlm, setSkipLlm] = useState(false)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const tickers = useMemo(
    () =>
      tickerInput
        .split(/[,\s]+/)
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean)
        .slice(0, 10),
    [tickerInput],
  )

  async function runScorecard(e) {
    if (e) e.preventDefault()
    if (tickers.length === 0) {
      setError('Enter at least one ticker symbol.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const { data } = await fetchJsonWithMeta(
        `${API_BASE_URL}/scorecard/compare`,
        {
          method: 'POST',
          body: JSON.stringify({
            tickers,
            preset,
            situational_flags: flags,
            skip_llm_scores: skipLlm,
          }),
        },
        180000,
      )
      setResult(data)
    } catch (err) {
      setError(String(err.message || err))
      setResult(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    // No auto-run; user must submit to avoid surprising LLM spend on mount.
  }, [])

  return (
    <div style={{ maxWidth: 1080, margin: '0 auto', padding: '24px 16px', color: '#e2e8f0' }}>
      <h1 style={{ fontSize: '1.5rem', fontWeight: 800, marginBottom: 8 }}>Risk-Return Scorecard</h1>
      <p style={{ fontSize: 13, color: '#94a3b8', marginBottom: 20, maxWidth: 760 }}>
        {'Compare a basket of 1\u201310 tickers on a dimensionless risk-to-return ratio. The math is ' +
          'deterministic; the Skin-In-The-Game (SITG) and execution-risk scores are produced by LLM ' +
          'personas with public-filing signals. Choose an investor-type preset to re-weight the factors.'}
      </p>

      <form onSubmit={runScorecard}>
        <Section title="Basket">
          <textarea
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
            rows={2}
            placeholder="Comma or space separated, e.g. HUBB, PWR, ETN, GEV, NEE, MTZ"
            style={{
              width: '100%',
              padding: '10px 12px',
              borderRadius: 8,
              background: 'rgba(15,23,42,0.6)',
              border: '1px solid rgba(148,163,184,0.25)',
              color: '#e2e8f0',
              fontSize: 14,
              fontFamily: 'inherit',
              resize: 'vertical',
            }}
          />
          <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>
            {tickers.length === 0 ? 'Parsed: (none)' : `Parsed: ${tickers.join(', ')}`}
          </div>
        </Section>

        <Section title="Investor preset">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
            {PRESETS.map((p) => (
              <label
                key={p.id}
                style={{
                  fontSize: 13,
                  padding: '8px 14px',
                  borderRadius: 8,
                  cursor: 'pointer',
                  border: `1px solid ${preset === p.id ? 'rgba(129,140,248,0.7)' : 'rgba(148,163,184,0.25)'}`,
                  background: preset === p.id ? 'rgba(79,70,229,0.25)' : 'rgba(30,41,59,0.5)',
                  color: preset === p.id ? '#e0e7ff' : '#cbd5e1',
                }}
              >
                <input
                  type="radio"
                  name="preset"
                  checked={preset === p.id}
                  onChange={() => setPreset(p.id)}
                  style={{ marginRight: 6 }}
                />
                {p.label}
              </label>
            ))}
          </div>
        </Section>

        <Section title="Situational adjustments (Step 7)">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 6 }}>
            {STEP7_FLAGS.map((f) => (
              <label key={f.id} style={{ fontSize: 12, color: '#cbd5e1', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={!!flags[f.id]}
                  onChange={(e) => setFlags((prev) => ({ ...prev, [f.id]: e.target.checked }))}
                  style={{ marginRight: 8 }}
                />
                {f.label}
              </label>
            ))}
            <label style={{ fontSize: 12, color: '#94a3b8', cursor: 'pointer', marginTop: 6 }}>
              <input
                type="checkbox"
                checked={skipLlm}
                onChange={(e) => setSkipLlm(e.target.checked)}
                style={{ marginRight: 8 }}
              />
              Skip LLM scoring (fast preview, uses fallback SITG=3 and execution-risk=5)
            </label>
          </div>
        </Section>

        <button
          type="submit"
          disabled={loading || tickers.length === 0}
          style={{
            padding: '10px 20px',
            borderRadius: 8,
            border: 'none',
            background: loading ? 'rgba(71,85,105,0.6)' : 'linear-gradient(135deg,#6366f1,#8b5cf6)',
            color: '#fff',
            fontWeight: 700,
            fontSize: 14,
            cursor: loading ? 'wait' : 'pointer',
            marginTop: 4,
          }}
        >
          {loading ? 'Scoring…' : 'Run scorecard'}
        </button>
      </form>

      {error && (
        <div
          role="alert"
          style={{
            marginTop: 16,
            padding: '10px 12px',
            borderRadius: 8,
            background: 'rgba(127,29,29,0.25)',
            border: '1px solid rgba(248,113,113,0.4)',
            color: '#fecaca',
            fontSize: 13,
            whiteSpace: 'pre-wrap',
          }}
        >
          {error}
        </div>
      )}

      {result && result.rows && result.rows.length > 0 && (
        <>
          <Section title="Results">
            <div style={{ overflowX: 'auto' }}>
              <table
                style={{
                  width: '100%',
                  borderCollapse: 'collapse',
                  fontSize: 13,
                  minWidth: 720,
                }}
              >
                <thead>
                  <tr style={{ background: 'rgba(30,41,59,0.6)', color: '#e2e8f0' }}>
                    {['Ticker', 'Return', 'Risk', 'Ratio', 'SITG boost', 'Signal', 'Verdict', 'Quadrant'].map((h) => (
                      <th
                        key={h}
                        style={{
                          padding: '8px 10px',
                          textAlign: h === 'Ticker' ? 'left' : 'right',
                          borderBottom: '1px solid rgba(148,163,184,0.2)',
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((r) => {
                    const sigColor = SIGNAL_COLORS[r.signal] || '#94a3b8'
                    const verdictColor = VERDICT_COLORS[r.verdict] || '#94a3b8'
                    return (
                      <tr
                        key={r.ticker}
                        data-testid={`scorecard-row-${r.ticker}`}
                        data-current-price={r.inputs?.current_price ?? ''}
                        data-beta={r.inputs?.beta ?? ''}
                        data-forward-pe={r.inputs?.forward_pe ?? ''}
                        data-revenue-growth-pct={r.inputs?.revenue_growth_pct ?? ''}
                        data-eps-growth-pct={r.inputs?.eps_growth_pct ?? ''}
                        style={{ borderBottom: '1px solid rgba(148,163,184,0.08)' }}
                      >
                        <td style={{ padding: '8px 10px', fontWeight: 700 }}>
                          {r.ticker}
                          {r.ceo_name && (
                            <div style={{ fontSize: 10, color: '#64748b', fontWeight: 400 }}>CEO: {r.ceo_name}</div>
                          )}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>{formatNum(r.return_score.weighted)}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right' }}>{formatNum(r.risk_score.weighted)}</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700 }}>
                          {formatNum(r.ratio)}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: r.sitg_boost > 0 ? '#34d399' : '#94a3b8' }}>
                          {r.sitg_boost > 0 ? '+' : ''}
                          {formatNum(r.sitg_boost)}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: sigColor, fontWeight: 600 }}>
                          {r.signal}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: verdictColor, fontWeight: 600 }}>
                          {r.verdict}
                          {r.one_line_reason && (
                            <div style={{ fontSize: 11, color: '#94a3b8', fontWeight: 400, maxWidth: 260, whiteSpace: 'normal' }}>
                              {r.one_line_reason}
                            </div>
                          )}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: '#64748b', fontSize: 11 }}>
                          {r.quadrant}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Scatter quadrant (Step 6)">
            <ScatterPlot rows={result.rows} />
          </Section>

          <Section title="Applied weights">
            <div style={{ fontSize: 12, color: '#94a3b8', background: 'rgba(15,23,42,0.5)', padding: 10, borderRadius: 8, border: '1px solid rgba(148,163,184,0.15)' }}>
              Preset: <span style={{ color: '#e2e8f0', fontWeight: 700 }}>{result.preset}</span>
              <pre style={{ margin: '6px 0 0', color: '#cbd5e1', fontSize: 12, fontFamily: 'ui-monospace, Menlo, monospace' }}>
                {JSON.stringify(result.weights, null, 2)}
              </pre>
              {result.notes && result.notes.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <strong style={{ color: '#e2e8f0' }}>Data notes:</strong>
                  <ul style={{ margin: '4px 0 0 18px', padding: 0 }}>
                    {result.notes.map((n, i) => (
                      <li key={i} style={{ fontSize: 12 }}>
                        {n}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </Section>
        </>
      )}
    </div>
  )
}
