import React, { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, RefreshCw, TrendingDown, TrendingUp, Sparkles } from 'lucide-react'
import { API_BASE_URL, apiFetch } from './api'
import { verdictBadgeStyle, verdictRowStyle } from './utils/verdictStyles'

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}%`
}

function fmtNum(v, d = 2) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(d)
}

function BriefTable({ title, icon: Icon, rows, onRowClick }) {
  if (!rows?.length) {
    return (
      <p style={{ color: '#94a3b8', fontSize: 13, padding: 16 }}>
        No {title.toLowerCase()} for this session.
      </p>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 960 }}>
        <thead>
          <tr style={{ background: 'rgba(15,23,42,0.55)', color: '#e2e8f0' }}>
            <th style={th}>#</th>
            <th style={th}>Symbol</th>
            <th style={th}>Move</th>
            <th style={th}>Close</th>
            <th style={th}>Rel Vol</th>
            <th style={th}>Z (60d)</th>
            <th style={th}>Catalyst</th>
            <th style={th}>Primary cause</th>
            <th style={th}>Verdict</th>
            <th style={th}>Rationale</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.bucket}-${row.symbol}`}
              style={{
                borderBottom: '1px solid rgba(148,163,184,0.08)',
                cursor: 'pointer',
                ...verdictRowStyle(row.verdict),
              }}
              onClick={() => onRowClick(row.symbol)}
              title={`Analyze ${row.symbol}`}
            >
              <td style={td}>{row.rank}</td>
              <td style={{ ...td, fontWeight: 800, color: '#f8fafc' }}>{row.symbol}</td>
              <td style={{ ...td, color: row.daily_return_pct >= 0 ? '#10b981' : '#f87171' }}>
                {fmtPct(row.daily_return_pct)}
              </td>
              <td style={td}>${fmtNum(row.close)}</td>
              <td style={td}>{fmtNum(row.relative_volume)}</td>
              <td style={td}>{fmtNum(row.return_zscore_60d)}</td>
              <td style={td}>{row.catalyst_status || '—'}</td>
              <td style={{ ...td, maxWidth: 220 }}>
                <div style={{ fontSize: 11, color: '#cbd5e1' }}>
                  {row.primary_cause_category || '—'}
                </div>
                <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 2 }}>
                  {(row.primary_cause_headline || '').slice(0, 80)}
                  {(row.primary_cause_headline || '').length > 80 ? '…' : ''}
                </div>
              </td>
              <td style={td}>
                <span style={verdictBadgeStyle(row.verdict)}>{row.verdict}</span>
                {row.is_compelling && (
                  <Sparkles size={12} style={{ marginLeft: 6, verticalAlign: 'middle', color: '#a78bfa' }} />
                )}
              </td>
              <td style={{ ...td, maxWidth: 280, color: '#94a3b8', fontSize: 11 }}>
                {row.one_line_reason || '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const th = { textAlign: 'left', padding: '10px 12px', fontWeight: 600, whiteSpace: 'nowrap' }
const td = { padding: '10px 12px', verticalAlign: 'top' }

export default function DailyBriefUI() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const json = await apiFetch(`${API_BASE_URL}/daily-brief`)
      setData(json)
    } catch (e) {
      setError(e.message || 'Failed to load daily brief')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const goAnalyze = (sym) => {
    navigate(`/?ticker=${encodeURIComponent(sym)}`)
  }

  return (
    <div className="dt-wrap fade-in" style={{ maxWidth: 1400, margin: '0 auto', padding: '8px 4px 48px' }}>
      <header style={{ marginBottom: 24, display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 28, fontWeight: 800, color: '#f8fafc' }}>Daily Brief</h1>
          <p style={{ margin: '8px 0 0', color: '#94a3b8', fontSize: 14, maxWidth: 640 }}>
            Top movers with movement context and heuristic verdicts. Color-coded rows: dark green = Strong Buy,
            green = Buy, amber = Hold, red = Sell (event-driven spikes downgrade to Hold).
          </p>
          {data && (
            <p style={{ margin: '6px 0 0', fontSize: 12, color: '#64748b' }}>
              Session {data.trade_date} · source {data.source} · updated {new Date(data.updated_at).toLocaleString()}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderRadius: 10,
            border: '1px solid rgba(148,163,184,0.25)',
            background: 'rgba(255,255,255,0.04)',
            color: '#e2e8f0',
            cursor: loading ? 'wait' : 'pointer',
          }}
        >
          {loading ? <Loader2 size={16} className="spinner" /> : <RefreshCw size={16} />}
          Refresh
        </button>
      </header>

      {error && (
        <div className="glass-panel" style={{ padding: 16, marginBottom: 20, borderColor: 'rgba(239,68,68,0.4)', color: '#fca5a5' }}>
          {error}
        </div>
      )}

      {loading && !data && (
        <div style={{ padding: 48, textAlign: 'center', color: '#94a3b8' }}>
          <Loader2 size={32} className="spinner" style={{ margin: '0 auto 12px' }} />
          Loading market brief…
        </div>
      )}

      {data && (
        <>
          {data.compelling?.length > 0 && (
            <section className="glass-panel" style={{ padding: 16, marginBottom: 20 }}>
              <h2 style={{ margin: '0 0 12px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                <Sparkles size={18} color="#a78bfa" />
                Compelling movers
              </h2>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {data.compelling.map((r) => (
                  <button
                    key={r.symbol}
                    type="button"
                    onClick={() => goAnalyze(r.symbol)}
                    style={{
                      padding: '6px 12px',
                      borderRadius: 8,
                      border: '1px solid rgba(167,139,250,0.35)',
                      background: 'rgba(124,58,237,0.12)',
                      color: '#e9d5ff',
                      cursor: 'pointer',
                      fontSize: 12,
                      fontWeight: 600,
                    }}
                  >
                    {r.symbol} {fmtPct(r.daily_return_pct)}
                  </button>
                ))}
              </div>
            </section>
          )}

          <section className="glass-panel" style={{ padding: 16, marginBottom: 20 }}>
            <h2 style={{ margin: '0 0 12px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <TrendingDown size={18} color="#f87171" />
              Top 20 losers
            </h2>
            <BriefTable title="Losers" rows={data.losers} onRowClick={goAnalyze} />
          </section>

          <section className="glass-panel" style={{ padding: 16 }}>
            <h2 style={{ margin: '0 0 12px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
              <TrendingUp size={18} color="#10b981" />
              Top 10 gainers
            </h2>
            <BriefTable title="Gainers" rows={data.gainers} onRowClick={goAnalyze} />
          </section>
        </>
      )}
    </div>
  )
}
