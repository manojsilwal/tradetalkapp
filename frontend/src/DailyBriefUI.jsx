import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Brain, Loader2, RefreshCw, TrendingDown, TrendingUp, Sparkles } from 'lucide-react'
import { API_BASE_URL, apiFetch } from './api'
import { verdictBadgeStyle, verdictRowStyle } from './utils/verdictStyles'
import { useAnalysisHistory } from './AnalysisContext'

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

function tierLabel(tier) {
  if (tier === 'deep') return 'Deep (batched LLM)'
  return 'Heuristic'
}

const CATALYST_LABELS = {
  symbol_specific: 'Company event',
  macro_only: 'Macro-driven',
  no_catalyst: '—',
}
function friendlyCatalyst(status, category) {
  if (status === 'symbol_specific') {
    if (category === 'sec_filing') return 'SEC Filing'
    if (category === 'earnings') return 'Earnings'
    if (category === 'corporate_action') return 'Corp. Action'
    if (category === 'news') return 'Company News'
    if (category === 'insider_trade') return 'Insider'
    return 'Company event'
  }
  return CATALYST_LABELS[status] || status || '—'
}

const CAUSE_LABELS = {
  sec_filing: 'SEC filing',
  earnings: 'Earnings',
  corporate_action: 'Corp. action',
  news: 'News event',
  macro_data: 'Macro data',
  geopolitical: 'Geopolitical',
  tariff_policy: 'Trade policy',
  insider_trade: 'Insider activity',
}
const STUB_RE = /^\s*[A-Z]{1,5}\s+SEC\s+/i
function friendlyCause(category, headline) {
  const hl = (headline || '').trim()
  if (hl) {
    // If it's a standard SEC filing headline with a form type, keep the form type and ticker
    const m = hl.match(/^([A-Z]+)\s+SEC\s+([A-Z0-9-]+)/i)
    if (m) {
      return `${m[1]} SEC ${m[2]}` // e.g. "WDC SEC 8-K"
    }
    // If it's a generic stub, fall back to label
    if (STUB_RE.test(hl) && hl.toLowerCase().includes('filing') && !hl.toLowerCase().includes('8-k') && !hl.toLowerCase().includes('10-k') && !hl.toLowerCase().includes('10-q')) {
      return CAUSE_LABELS[category] || 'SEC filing'
    }
    const words = hl.split(/\s+/).slice(0, 6).join(' ')
    return words + (hl.split(/\s+/).length > 6 ? '…' : '')
  }
  return CAUSE_LABELS[category] || (category ? category.replace(/_/g, ' ') : '—')
}

function BriefTable({ title, rows, onRowClick }) {
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
              <td style={td}>{friendlyCatalyst(row.catalyst_status, row.primary_cause_category)}</td>
              <td style={{ ...td, maxWidth: 220, fontSize: 11, color: '#cbd5e1' }}>
                {friendlyCause(row.primary_cause_category, row.primary_cause_headline)}
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

function ScreenerTable({ title, rows, preset, onRowClick }) {
  if (!rows?.length) {
    return (
      <p style={{ color: '#94a3b8', fontSize: 13, padding: 24, textAlign: 'center' }}>
        No actionable {title.toLowerCase()} found for this session.
      </p>
    )
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 960 }}>
        <thead>
          <tr style={{ background: 'rgba(15,23,42,0.55)', color: '#e2e8f0' }}>
            <th style={th}>Symbol</th>
            <th style={th}>Verdict</th>
            <th style={th}>Close</th>
            <th style={th}>Move</th>
            
            {preset === 'growth' && (
              <>
                <th style={th}>Revenue Growth</th>
                <th style={th}>EPS Growth</th>
              </>
            )}
            {preset === 'income' && (
              <>
                <th style={th}>Dividend Yield</th>
                <th style={th}>Debt / Equity</th>
              </>
            )}
            {preset === 'value' && (
              <>
                <th style={th}>PE vs 5y Avg</th>
                <th style={th}>Scorecard Ratio</th>
              </>
            )}
            
            <th style={th}>Beta</th>
            <th style={th}>Catalyst Category</th>
            <th style={th}>Rationale</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.symbol}
              style={{
                borderBottom: '1px solid rgba(148,163,184,0.08)',
                cursor: 'pointer',
                ...verdictRowStyle(row.verdict),
              }}
              onClick={() => onRowClick(row.symbol)}
              title={`Analyze ${row.symbol}`}
            >
              <td style={{ ...td, fontWeight: 800, color: '#f8fafc' }}>{row.symbol}</td>
              <td style={td}>
                <span style={verdictBadgeStyle(row.verdict)}>{row.verdict}</span>
              </td>
              <td style={td}>${fmtNum(row.close)}</td>
              <td style={{ ...td, color: row.daily_return_pct >= 0 ? '#10b981' : '#f87171' }}>
                {fmtPct(row.daily_return_pct)}
              </td>
              
              {preset === 'growth' && (
                <>
                  <td style={{ ...td, fontWeight: 600, color: '#a78bfa' }}>
                    {fmtPct(row.revenue_growth_pct)}
                  </td>
                  <td style={td}>{fmtPct(row.eps_growth_pct)}</td>
                </>
              )}
              {preset === 'income' && (
                <>
                  <td style={{ ...td, fontWeight: 600, color: '#60a5fa' }}>
                    {fmtPct(row.dividend_yield_pct)}
                  </td>
                  <td style={td}>{fmtNum(row.debt_to_equity)}x</td>
                </>
              )}
              {preset === 'value' && (
                <>
                  <td style={{ ...td, fontWeight: 600, color: '#fbbf24' }}>
                    {row.valuation_pct_vs_fair != null ? `${row.valuation_pct_vs_fair > 0 ? '+' : ''}${fmtNum(row.valuation_pct_vs_fair)}%` : '—'}
                  </td>
                  <td style={td}>{fmtNum(row.scorecard_ratio, 3)}</td>
                </>
              )}
              
              <td style={td}>{fmtNum(row.beta, 2)}</td>
              <td style={td}>{friendlyCause(row.primary_cause_category, row.primary_cause_headline)}</td>
              <td style={{ ...td, maxWidth: 300, color: '#cbd5e1', fontSize: 11 }}>
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

const btnStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 8,
  padding: '10px 16px',
  borderRadius: 10,
  border: '1px solid rgba(148,163,184,0.25)',
  background: 'rgba(255,255,255,0.04)',
  color: '#e2e8f0',
  cursor: 'pointer',
}

export default function DailyBriefUI() {
  const navigate = useNavigate()
  const { dailyBriefState, loadDailyBrief, startDailyBriefDeepRefresh, setDailyBriefActiveTab } = useAnalysisHistory()
  const { data, screenerData, activeTab, loading, error, deepStatus, deepBusy } = dailyBriefState

  useEffect(() => {
    loadDailyBrief(false)
  }, [loadDailyBrief])

  const load = (refresh = false) => {
    loadDailyBrief(refresh)
  }

  const startDeepRefresh = () => {
    startDailyBriefDeepRefresh()
  }

  const setActiveTab = setDailyBriefActiveTab

  const goAnalyze = (sym) => {
    navigate(`/?ticker=${encodeURIComponent(sym)}`)
  }

  const tier = data?.verdict_tier || 'heuristic'
  const deepRunning = deepStatus?.status === 'running' || deepBusy

  return (
    <div className="dt-wrap fade-in" style={{ maxWidth: 1400, margin: '0 auto', padding: '8px 4px 48px' }}>
      <header style={{ marginBottom: 24, display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 28, fontWeight: 800, color: '#f8fafc' }}>Daily Brief</h1>
          <p style={{ margin: '8px 0 0', color: '#94a3b8', fontSize: 14, maxWidth: 640 }}>
            Top movers with movement context and verdicts. Heuristic loads instantly from the data lake;
            deep refresh runs one batched LLM pass plus scorecard enrichment.
          </p>
          {data && (
            <p style={{ margin: '6px 0 0', fontSize: 12, color: '#64748b', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              Session {data.trade_date} · source {data.source} · {tierLabel(tier)}
              {data.from_snapshot ? ' · snapshot' : ''} · updated{' '}
              {new Date(data.updated_at).toLocaleString()}
              {data.realtime_overlay && (
                <span style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  padding: '2px 8px', borderRadius: 12,
                  background: 'rgba(16,185,129,0.15)',
                  border: '1px solid rgba(16,185,129,0.35)',
                  color: '#10b981', fontSize: 11, fontWeight: 700,
                  animation: 'pulse 2s ease-in-out infinite',
                }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#10b981', display: 'inline-block' }} />
                  Live · {data.rt_overlay_count} quotes
                </span>
              )}
            </p>
          )}
          {deepRunning && deepStatus && (
            <p style={{ margin: '6px 0 0', fontSize: 12, color: '#a78bfa' }}>
              Deep refresh: {deepStatus.message || deepStatus.status} ({deepStatus.progress || 0}%)
            </p>
          )}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <button
            type="button"
            onClick={() => load(false)}
            disabled={loading}
            style={{ ...btnStyle, cursor: loading ? 'wait' : 'pointer' }}
          >
            {loading ? <Loader2 size={16} className="spinner" /> : <RefreshCw size={16} />}
            Refresh
          </button>
          <button
            type="button"
            onClick={startDeepRefresh}
            disabled={deepRunning || loading}
            title="Batched LLM + scorecard enrichment (background)"
            style={{
              ...btnStyle,
              borderColor: 'rgba(167,139,250,0.45)',
              background: 'rgba(124,58,237,0.15)',
              cursor: deepRunning ? 'wait' : 'pointer',
            }}
          >
            {deepRunning ? <Loader2 size={16} className="spinner" /> : <Brain size={16} />}
            Actionable Companies
          </button>
        </div>
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

          {/* Screener Tabs */}
          {screenerData && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 20, borderBottom: '1px solid rgba(148,163,184,0.1)', paddingBottom: 12 }}>
              {[
                { id: 'growth', label: 'Growth Buy/Sells' },
                { id: 'value', label: 'Value Buy/Sells' },
                { id: 'income', label: 'Income Buy/Sells' },
                { id: 'movers', label: 'Movers (Daily)' },
              ].map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setActiveTab(t.id)}
                  style={{
                    padding: '8px 16px',
                    borderRadius: 8,
                    border: '1px solid',
                    borderColor: activeTab === t.id ? 'rgba(167,139,250,0.45)' : 'transparent',
                    background: activeTab === t.id ? 'rgba(124,58,237,0.15)' : 'transparent',
                    color: activeTab === t.id ? '#e9d5ff' : '#94a3b8',
                    cursor: 'pointer',
                    fontSize: 13,
                    fontWeight: activeTab === t.id ? 700 : 500,
                    transition: 'all 0.2s',
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          )}

          {(activeTab === 'movers' || !screenerData) ? (
            <>
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
          ) : (
            <>
              {activeTab === 'growth' && (
                <section className="glass-panel" style={{ padding: 16 }}>
                  <h2 style={{ margin: '0 0 12px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Sparkles size={18} color="#a78bfa" />
                    Growth Actionable Signals (Revenue Growth ≥ 15%)
                  </h2>
                  <ScreenerTable
                    title="Growth Signals"
                    rows={screenerData.rows.filter((r) => r.preset === 'growth')}
                    preset="growth"
                    onRowClick={goAnalyze}
                  />
                </section>
              )}

              {activeTab === 'value' && (
                <section className="glass-panel" style={{ padding: 16 }}>
                  <h2 style={{ margin: '0 0 12px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <Brain size={18} color="#fbbf24" />
                    Value Actionable Signals (Standard/Value Leaders)
                  </h2>
                  <ScreenerTable
                    title="Value Signals"
                    rows={screenerData.rows.filter((r) => r.preset === 'value')}
                    preset="value"
                    onRowClick={goAnalyze}
                  />
                </section>
              )}

              {activeTab === 'income' && (
                <section className="glass-panel" style={{ padding: 16 }}>
                  <h2 style={{ margin: '0 0 12px', fontSize: 16, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <TrendingUp size={18} color="#60a5fa" />
                    Income Actionable Signals (Dividend Yield ≥ 3%)
                  </h2>
                  <ScreenerTable
                    title="Income Signals"
                    rows={screenerData.rows.filter((r) => r.preset === 'income')}
                    preset="income"
                    onRowClick={goAnalyze}
                  />
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  )
}
