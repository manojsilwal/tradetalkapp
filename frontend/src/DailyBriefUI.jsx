import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Loader2, RefreshCw, TrendingDown, Shield, List, Plus, AlertTriangle } from 'lucide-react'
import { API_BASE_URL, apiFetch } from './api'
import { useAnalysisHistory } from './AnalysisContext'
import { DataTrustBanner } from './components/Freshness'
import ActionableCompaniesPanel, { ActionableCompaniesButton, useActionableCompanies } from './components/ActionableCompaniesPanel'
import { isBriefSessionTrustworthy, isSessionDateStale } from './freshness'

function formatTradeDateLabel(isoDateStr) {
  if (!isoDateStr) return ''
  try {
    const parts = isoDateStr.split('-')
    const d = new Date(parseInt(parts[0], 10), parseInt(parts[1], 10) - 1, parseInt(parts[2], 10))
    return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  } catch (e) {
    return isoDateStr
  }
}

// Reusable truthful-data indicator. When the underlying data is stale, it
// renders a prominent warning with the true age and never lets the caller
// label the data as "the last trading session". When fresh/live it renders a
// small, unobtrusive badge.
function DataFreshnessBadge({ freshness, variant = 'inline' }) {
  if (!freshness) return null
  const isStale = !!freshness.is_stale
  const lastDate = freshness.db_latest_date
  const days = freshness.staleness_days

  if (!isStale) {
    const isLive = freshness.source === 'realtime_overlay' || freshness.source === 'market_intel_live' || freshness.source === 'market_intel'
    if (!isLive) return null
    return (
      <span style={{
        fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4,
        color: '#34d399', background: 'rgba(52,211,153,0.12)', border: '1px solid rgba(52,211,153,0.35)',
        borderRadius: 6, padding: '2px 6px',
      }}>Live</span>
    )
  }

  const ageText = lastDate
    ? `Last updated ${formatTradeDateLabel(lastDate)}${typeof days === 'number' ? ` (${days} day${days === 1 ? '' : 's'} ago)` : ''}.`
    : 'No recent market data available.'

  if (variant === 'banner') {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        color: '#fbbf24', background: 'rgba(251,191,36,0.10)', border: '1px solid rgba(251,191,36,0.35)',
        borderRadius: 10, padding: '10px 14px', fontSize: '0.9rem',
      }}>
        <AlertTriangle size={18} style={{ flexShrink: 0 }} />
        <span><strong>Stale market data.</strong> {ageText} Live refresh is currently unavailable — these figures are not current.</span>
      </div>
    )
  }

  return (
    <span title={`${ageText} Live refresh unavailable.`} style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4,
      color: '#fbbf24', background: 'rgba(251,191,36,0.12)', border: '1px solid rgba(251,191,36,0.35)',
      borderRadius: 6, padding: '2px 6px',
    }}>
      <AlertTriangle size={12} /> Stale{lastDate ? ` · ${formatTradeDateLabel(lastDate)}` : ''}
    </span>
  )
}

function getCompanyLogoStyle(symbol) {
  const s = (symbol || '').toUpperCase()
  if (s === 'LRCX' || s === 'KLAC') {
    return { backgroundColor: '#ffffff', color: '#000000' }
  }
  if (s === 'ORCL') {
    return { backgroundColor: '#ef4444', color: '#ffffff' }
  }
  if (s === 'MRVL' || s === 'EWY') {
    return { backgroundColor: '#334155', color: '#ffffff' }
  }
  return { backgroundColor: '#1e293b', color: '#ffffff' }
}

// Truthful-data contract: no mock benchmarks, metadata, or rows anywhere on
// this page — missing live data renders as "N/A" / explicit empty states.

function formatMarketCap(val) {
  if (val == null) return null;
  const num = Number(val);
  if (isNaN(num)) return String(val);
  if (num >= 1e12) return (num / 1e12).toFixed(1) + 'T';
  if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
  if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
  return num.toLocaleString();
}

function formatPE(val) {
  if (val == null) return null;
  const num = Number(val);
  if (isNaN(num)) return String(val);
  return num.toFixed(1);
}

function getTickerMetadata(symbol, rowData) {
  return {
    marketCap: formatMarketCap(rowData?.market_cap || rowData?.marketCap) || 'N/A',
    pe: formatPE(rowData?.pe_ratio || rowData?.pe || rowData?.forward_pe) || 'N/A',
    industry: rowData?.industry || 'N/A',
  };
}

export default function DailyBriefUI() {
  const navigate = useNavigate()
  const { dailyBriefState, loadDailyBrief } = useAnalysisHistory()
  const { data, loading, error } = dailyBriefState
  const actionableState = useActionableCompanies()

  const [portfolioBrief, setPortfolioBrief] = useState(null)
  const [portfolioNews, setPortfolioNews] = useState([])
  const [extraLoading, setExtraLoading] = useState(false)
  const [showMoversTab, setShowMoversTab] = useState('losers')
  const [isMoversExpanded, setIsMoversExpanded] = useState(false)

  const sessionStatus = data?.market_session?.status || portfolioBrief?.market_session?.status;
  const isWeekendOrAfterHours = sessionStatus === 'weekend' || sessionStatus === 'after_hours';
  const isWeekend = sessionStatus === 'weekend';

  const briefFreshness = data?.data_freshness;
  const portfolioFreshness = portfolioBrief?.data_freshness;
  const freshness = briefFreshness || portfolioFreshness;
  const isBriefStale = !isBriefSessionTrustworthy(data);
  const isPortfolioStale = portfolioFreshness?.is_stale
    || isSessionDateStale(portfolioBrief?.trade_date, portfolioFreshness);
  const isStaleData = !!freshness?.is_stale || isBriefStale;
  const showMoversLoader = loading;

  const formatTradeDate = (isoDateStr) => {
    if (!isoDateStr) return '';
    try {
      const parts = isoDateStr.split('-');
      const year = parseInt(parts[0], 10);
      const month = parseInt(parts[1], 10) - 1;
      const day = parseInt(parts[2], 10);
      const d = new Date(year, month, day);
      return d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' });
    } catch (e) {
      return isoDateStr;
    }
  };

  const loadExtraData = useCallback(async () => {
    setExtraLoading(true)
    try {
      const briefData = await apiFetch(`${API_BASE_URL}/portfolio/morning-brief`).catch(() => null)
      if (briefData) {
        setPortfolioBrief(briefData)
        const tickers = briefData.impact_movers?.map(m => m.symbol).filter(Boolean) || []
        const uniqueTickers = [...new Set(tickers)].join(',')
        const url = uniqueTickers
          ? `${API_BASE_URL}/portfolio/news?tickers=${encodeURIComponent(uniqueTickers)}`
          : `${API_BASE_URL}/portfolio/news`;
        const newsData = await apiFetch(url).catch(() => null)
        if (newsData?.items) {
          setPortfolioNews(newsData.items)
        }
      }
    } catch (err) {
      console.warn('Failed to load portfolio news', err)
    } finally {
      setExtraLoading(false)
    }
  }, [])

  const initialLoadDone = useRef(false);

  useEffect(() => {
    if (initialLoadDone.current) return;
    initialLoadDone.current = true;
    loadDailyBrief(false);
    loadExtraData();
  }, [loadDailyBrief, loadExtraData]);

  const load = (refresh = false) => {
    loadDailyBrief(refresh)
    loadExtraData()
  }

  const goAnalyze = (sym) => {
    navigate(`/dashboard?ticker=${encodeURIComponent(sym)}`)
  }

  const hasPortfolioSetup = portfolioBrief?.has_portfolio === true
  const showHoldingsLoader = (loading || extraLoading) && hasPortfolioSetup;

  // Data mapping
  const portfolioVal = portfolioBrief?.summary?.total_value != null
    ? portfolioBrief.summary.total_value
    : null

  const portfolioChange = portfolioBrief?.summary?.daily_return_pct != null
    ? portfolioBrief.summary.daily_return_pct
    : null

  const spyChange = portfolioBrief?.summary?.benchmark_context?.spy_daily_return_pct != null
    ? portfolioBrief.summary.benchmark_context.spy_daily_return_pct
    : null

  const qqqChange = portfolioBrief?.summary?.benchmark_context?.qqq_daily_return_pct != null
    ? portfolioBrief.summary.benchmark_context.qqq_daily_return_pct
    : null

  const ijrChange = portfolioBrief?.summary?.benchmark_context?.ijr_daily_return_pct != null
    ? portfolioBrief.summary.benchmark_context.ijr_daily_return_pct
    : null

  const insightsText = portfolioBrief?.headline || (loading || extraLoading ? "Loading market insights..." : "Add a paper portfolio to unlock morning insights.")

  const mapRealLosers = () => {
    if (data?.losers && data.losers.length > 0) {
      const limit = isMoversExpanded ? 15 : 3
      return data.losers.slice(0, limit).map(r => {
        const meta = getTickerMetadata(r.symbol, r)
        return {
          symbol: r.symbol,
          move: r.daily_return_pct,
          insider: r.insider_sentiment || 'N/A',
          marketCap: meta.marketCap,
          pe: meta.pe,
          industry: meta.industry,
          rationale: r.one_line_reason || 'Movers analysis from session EOD.'
        }
      })
    }
    return []
  }

  const mapRealGainers = () => {
    if (data?.gainers && data.gainers.length > 0) {
      const limit = isMoversExpanded ? 15 : 3
      return data.gainers.slice(0, limit).map(r => {
        const meta = getTickerMetadata(r.symbol, r)
        return {
          symbol: r.symbol,
          move: r.daily_return_pct,
          insider: r.insider_sentiment || 'N/A',
          marketCap: meta.marketCap,
          pe: meta.pe,
          industry: meta.industry,
          rationale: r.one_line_reason || 'Movers analysis from session EOD.'
        }
      })
    }
    return []
  }

  const mapRealHoldings = () => {
    if (portfolioBrief?.impact_movers && portfolioBrief.impact_movers.length > 0) {
      return portfolioBrief.impact_movers.slice(0, 2).map(r => {
        const meta = getTickerMetadata(r.symbol, r)
        return {
          symbol: r.symbol,
          move: r.daily_return_pct,
          insider: r.insider_sentiment || 'N/A',
          marketCap: meta.marketCap,
          pe: meta.pe,
          industry: meta.industry,
          rationale: r.one_line_reason || `No rationale available for ${r.symbol}.`
        }
      })
    }
    return []
  }

  const mapRealNews = () => {
    if (portfolioNews && portfolioNews.length > 0) {
      return portfolioNews.slice(0, 5).map(item => {
        const timeVal = item.published_at
          ? (() => {
              const seconds = Math.floor(Date.now() / 1000 - item.published_at)
              const hours = Math.floor(seconds / 3600)
              if (hours <= 0) return `${Math.max(1, Math.floor(seconds / 60))}m ago`
              if (hours < 24) return `${hours}h ago`
              return `${Math.floor(hours / 24)}d ago`
            })()
          : '—'
        return {
          time: timeVal,
          sentiment: item.sentiment === 'positive' ? 'Positive' : item.sentiment === 'negative' ? 'Negative' : 'Neutral',
          symbol: item.ticker,
          text: item.title
        }
      })
    }
    return []
  }

  const tableLosers = mapRealLosers()
  const tableGainers = mapRealGainers()
  const tableHoldings = mapRealHoldings()
  const timelineNews = mapRealNews()
  const currentTableMovers = showMoversTab === 'losers' ? tableLosers : tableGainers

  // Page-level loading is now handled at the component level to render the shell immediately

  return (
    <div className="dt-wrap fade-in" style={{ maxWidth: 1400, margin: '0 auto', padding: '8px 4px 48px' }}>
      {/* Header section */}
      <header style={{ marginBottom: 24, display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', justifyContent: 'flex-end' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'flex-start' }}>
          <button
            type="button"
            onClick={() => load(true)}
            disabled={loading || extraLoading}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 8,
              padding: '10px 16px',
              borderRadius: 10,
              border: '1px solid rgba(148,163,184,0.25)',
              background: 'rgba(255,255,255,0.04)',
              color: '#e2e8f0',
              cursor: loading || extraLoading ? 'wait' : 'pointer',
            }}
          >
            {loading || extraLoading ? <Loader2 size={16} className="spinner" /> : <RefreshCw size={16} />}
            Refresh
          </button>
          <ActionableCompaniesButton busy={actionableState.busy} onClick={actionableState.startScan} />
        </div>
      </header>

      {error && (
        <div className="glass-panel" style={{ padding: 16, marginBottom: 20, borderColor: 'rgba(239,68,68,0.4)', color: '#fca5a5' }}>
          {error}
        </div>
      )}

      {freshness && <DataTrustBanner envelope={freshness} />}

      <ActionableCompaniesPanel state={actionableState} onSelectTicker={goAnalyze} />

        <>
          {/* Weekend Session Info Banner */}
          {((data?.market_session?.status === 'weekend') || (portfolioBrief?.market_session?.status === 'weekend')) && (
            <div className="glass-panel" style={{
              padding: '16px 20px',
              marginBottom: 20,
              borderColor: 'rgba(59, 130, 246, 0.4)',
              background: 'rgba(59, 130, 246, 0.05)',
              color: '#93c5fd',
              borderRadius: 12,
              display: 'flex',
              flexDirection: 'column',
              gap: 4
            }}>
              <div style={{ fontWeight: 700, fontSize: '1.05rem', color: '#60a5fa', display: 'flex', alignItems: 'center', gap: 8 }}>
                <Shield size={18} />
                Markets are closed today (Weekend)
              </div>
              {isStaleData ? (
                <DataFreshnessBadge freshness={freshness} variant="banner" />
              ) : (
                <div style={{ color: '#94a3b8', fontSize: '0.92rem' }}>
                  Stock markets are currently closed. Displaying data as of the last trading session {data?.trade_date ? `(${formatTradeDate(data.trade_date)})` : ''}. <strong>Crypto markets trade 24/7</strong> and may continue to move.
                </div>
              )}
            </div>
          )}

          {/* Top Row Cards */}
          <div className="brief-grid">
            {/* Portfolio Card */}
            {!hasPortfolioSetup ? (
              <div
                className="brief-card"
                onClick={() => navigate('/portfolio')}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  justifyContent: 'center',
                  border: '2px dashed #334155',
                  background: 'rgba(17, 22, 37, 0.4)',
                  cursor: 'pointer',
                  minHeight: '110px',
                  borderRadius: '16px',
                  transition: 'all 0.25s ease',
                  padding: '16px'
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = '#3b82f6';
                  e.currentTarget.style.boxShadow = '0 0 15px rgba(59, 130, 246, 0.15)';
                  e.currentTarget.style.background = 'rgba(17, 22, 37, 0.6)';
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = '#334155';
                  e.currentTarget.style.boxShadow = 'none';
                  e.currentTarget.style.background = 'rgba(17, 22, 37, 0.4)';
                }}
              >
                <Plus size={28} color="#64748b" style={{ marginBottom: 6 }} />
                <span style={{ color: '#94a3b8', fontSize: '0.9rem', fontWeight: 700 }}>New portfolio</span>
              </div>
            ) : (loading || extraLoading) && portfolioVal == null ? (
              <div className="brief-card" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '110px' }}>
                <Loader2 className="spinner" size={20} color="var(--accent-blue)" />
              </div>
            ) : (
              <div className="brief-card" style={{ display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <h3 className="brief-label">Portfolio</h3>
                  {portfolioChange != null && (
                    <span className={
                      portfolioChange === 0
                        ? 'brief-pill-neutral'
                        : portfolioChange > 0
                        ? 'brief-pill-green'
                        : 'brief-pill-red'
                    }>
                      {portfolioChange > 0 ? '+' : ''}{portfolioChange.toFixed(1)}% {isWeekendOrAfterHours ? 'Session' : 'Today'}
                    </span>
                  )}
                </div>
                <p className="brief-large-metric">
                  {portfolioVal != null ? `$${portfolioVal.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
                </p>
              </div>
            )}

            {/* Market Benchmarks Card */}
            <div className="brief-card">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <h3 className="brief-label">Market Benchmarks</h3>
                {isWeekendOrAfterHours && (
                  <span style={{ fontSize: '0.72rem', color: '#64748b', fontWeight: 600 }}>Last Session</span>
                )}
              </div>
              {(loading || extraLoading) && spyChange == null ? (
                <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '60px', marginTop: 12 }}>
                  <Loader2 className="spinner" size={20} color="var(--accent-blue)" />
                </div>
              ) : (
                <div style={{ marginTop: 12 }}>
                  <div className="brief-benchmark-row">
                    <div className="brief-benchmark-item">
                      <span className={spyChange == null ? 'brief-bullet-neutral' : (spyChange > 0 ? 'brief-bullet-green' : (spyChange < 0 ? 'brief-bullet-red' : 'brief-bullet-neutral'))} />
                      <span>SP500</span>
                    </div>
                    <span style={{ color: spyChange == null ? '#94a3b8' : (spyChange > 0 ? '#34d399' : (spyChange < 0 ? '#f87171' : '#94a3b8')), fontWeight: 700 }}>
                      {spyChange != null ? `${spyChange > 0 ? '+' : ''}${spyChange.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  <div className="brief-benchmark-row">
                    <div className="brief-benchmark-item">
                      <span className={qqqChange == null ? 'brief-bullet-neutral' : (qqqChange > 0 ? 'brief-bullet-green' : (qqqChange < 0 ? 'brief-bullet-red' : 'brief-bullet-neutral'))} />
                      <span>NASDAQ (QQQ)</span>
                    </div>
                    <span style={{ color: qqqChange == null ? '#94a3b8' : (qqqChange > 0 ? '#34d399' : (qqqChange < 0 ? '#f87171' : '#94a3b8')), fontWeight: 700 }}>
                      {qqqChange != null ? `${qqqChange > 0 ? '+' : ''}${qqqChange.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                  <div className="brief-benchmark-row">
                    <div className="brief-benchmark-item">
                      <span className={ijrChange == null ? 'brief-bullet-neutral' : (ijrChange > 0 ? 'brief-bullet-green' : (ijrChange < 0 ? 'brief-bullet-red' : 'brief-bullet-neutral'))} />
                      <span>iShares S&P Small-Cap ETF (IJR)</span>
                    </div>
                    <span style={{ color: ijrChange == null ? '#94a3b8' : (ijrChange > 0 ? '#34d399' : (ijrChange < 0 ? '#f87171' : '#94a3b8')), fontWeight: 700 }}>
                      {ijrChange != null ? `${ijrChange > 0 ? '+' : ''}${ijrChange.toFixed(1)}%` : '—'}
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Key Insights Card */}
            <div className="brief-card">
              <h3 className="brief-label">Key Insights</h3>
              <p className="brief-insight-text">{insightsText}</p>
            </div>
          </div>

          {/* Main Content: Two Columns */}
          <div className="brief-columns-grid">
            {/* Left Column: Losers and Holdings Tables */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
              {/* S&P 500 Movers Card */}
              <div className="brief-card" style={{ padding: '20px 24px' }}>
                <div className="brief-card-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <div style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
                    <div 
                      onClick={() => setShowMoversTab('losers')} 
                      style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: 8, 
                        cursor: 'pointer',
                        paddingBottom: 4,
                        borderBottom: showMoversTab === 'losers' ? '2px solid #f87171' : '2px solid transparent',
                        opacity: showMoversTab === 'losers' ? 1 : 0.6,
                        transition: 'all 0.2s ease'
                      }}
                    >
                      <TrendingDown size={18} color="#f87171" />
                      <h2 className="brief-card-title" style={{ margin: 0 }}>S&P 500 Losers</h2>
                    </div>
                    <div 
                      onClick={() => setShowMoversTab('gainers')} 
                      style={{ 
                        display: 'flex', 
                        alignItems: 'center', 
                        gap: 8, 
                        cursor: 'pointer',
                        paddingBottom: 4,
                        borderBottom: showMoversTab === 'gainers' ? '2px solid #34d399' : '2px solid transparent',
                        opacity: showMoversTab === 'gainers' ? 1 : 0.6,
                        transition: 'all 0.2s ease'
                      }}
                    >
                      <TrendingDown size={18} color="#34d399" style={{ transform: 'rotate(180deg)', alignSelf: 'center' }} />
                      <h2 className="brief-card-title" style={{ margin: 0 }}>S&P 500 Gainers</h2>
                    </div>
                    {isBriefStale || briefFreshness?.is_stale ? (
                      <DataFreshnessBadge freshness={briefFreshness || freshness} />
                    ) : data?.trade_date && isBriefSessionTrustworthy(data) && (
                      <span style={{ fontSize: '0.8rem', color: '#64748b', fontWeight: 600 }}>
                        ({isWeekendOrAfterHours ? 'As of ' : ''}{formatTradeDate(data.trade_date)})
                      </span>
                    )}
                  </div>
                  <span className="brief-card-link" onClick={() => setIsMoversExpanded(!isMoversExpanded)}>
                    {isMoversExpanded ? 'Collapse View' : 'Expand View'}
                  </span>
                </div>
                
                {showMoversLoader ? (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px 0', gap: 12 }}>
                    <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                    <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Loading top movers…</span>
                  </div>
                ) : currentTableMovers.length === 0 ? (
                  <p style={{ color: '#94a3b8', fontSize: '0.9rem', padding: '16px 4px', margin: 0 }}>
                    Live movers data is unavailable right now — nothing to show. Try Refresh later.
                  </p>
                ) : (
                <div className="brief-table-container">
                  <table className="brief-table">
                    <thead>
                      <tr>
                        <th>Stock</th>
                        <th>Industry</th>
                        <th>Market Cap</th>
                        <th>P/E</th>
                        <th>Move %</th>
                        <th>Insider / Smart Money</th>
                        <th>AI Rationale</th>
                      </tr>
                    </thead>
                    <tbody>
                      {currentTableMovers.map((row) => (
                        <tr key={row.symbol} style={{ cursor: 'pointer' }} onClick={() => goAnalyze(row.symbol)}>
                          <td>
                            <div className="brief-table-ticker">
                              <span className="stock-logo-circle" style={getCompanyLogoStyle(row.symbol)}>
                                {row.symbol}
                              </span>
                              <span className="brief-table-ticker-name">{row.symbol}</span>
                            </div>
                          </td>
                          <td>
                            <span style={{ color: '#cbd5e1' }}>{row.industry}</span>
                          </td>
                          <td>
                            <span style={{ color: '#cbd5e1' }}>{row.marketCap}</span>
                          </td>
                          <td>
                            <span style={{ color: '#cbd5e1' }}>{row.pe}</span>
                          </td>
                          <td style={{ color: row.move > 0 ? '#34d399' : (row.move < 0 ? '#f87171' : '#94a3b8'), fontWeight: 600 }}>
                            {row.move > 0 ? '+' : ''}{row.move.toFixed(1)}%
                          </td>
                          <td>
                            <span className="brief-pill-neutral">{row.insider}</span>
                          </td>
                          <td style={{ color: '#cbd5e1' }}>{row.rationale}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                )}
              </div>

              {/* Portfolio Exposure: Key Holdings Card */}
              <div className="brief-card" style={{ padding: '20px 24px' }}>
                <div className="brief-card-header">
                  <div className="brief-card-title-group" style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                    <Shield size={18} color="#60a5fa" style={{ alignSelf: 'center' }} />
                    <h2 className="brief-card-title">Portfolio Exposure: Key Holdings</h2>
                    {isPortfolioStale ? (
                      <DataFreshnessBadge freshness={portfolioFreshness || freshness} />
                    ) : portfolioBrief?.trade_date && !isPortfolioStale && (
                      <span style={{ fontSize: '0.8rem', color: '#64748b', fontWeight: 600 }}>
                        ({isWeekendOrAfterHours ? 'As of ' : ''}{formatTradeDate(portfolioBrief.trade_date)})
                      </span>
                    )}
                    {!isPortfolioStale && !portfolioBrief?.trade_date && data?.trade_date && isBriefSessionTrustworthy(data) && (
                      <span style={{ fontSize: '0.8rem', color: '#64748b', fontWeight: 600 }}>
                        ({isWeekendOrAfterHours ? 'As of ' : ''}{formatTradeDate(data.trade_date)})
                      </span>
                    )}
                  </div>
                  <span className="brief-pill-neutral" style={{ textTransform: 'uppercase', fontSize: 10, fontWeight: 700 }}>
                    Exposure: {hasPortfolioSetup ? 'Yes' : 'No'}
                  </span>
                </div>

                {showHoldingsLoader && tableHoldings.length === 0 ? (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '40px 0', gap: 12 }}>
                    <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                    <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Analyzing holdings exposure…</span>
                  </div>
                ) : !hasPortfolioSetup ? (
                  <div
                    onClick={() => navigate('/portfolio')}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      border: '2px dashed #334155',
                      borderRadius: '12px',
                      background: 'rgba(17, 22, 37, 0.2)',
                      padding: '36px 20px',
                      cursor: 'pointer',
                      textAlign: 'center',
                      marginTop: '16px',
                      transition: 'all 0.25s ease'
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = '#3b82f6';
                      e.currentTarget.style.boxShadow = '0 0 15px rgba(59, 130, 246, 0.15)';
                      e.currentTarget.style.background = 'rgba(17, 22, 37, 0.4)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = '#334155';
                      e.currentTarget.style.boxShadow = 'none';
                      e.currentTarget.style.background = 'rgba(17, 22, 37, 0.2)';
                    }}
                  >
                    <Plus size={28} color="#64748b" style={{ marginBottom: 8 }} />
                    <h4 style={{ margin: '0 0 4px 0', color: '#f8fafc', fontSize: '1rem', fontWeight: 700 }}>No active portfolio</h4>
                    <p style={{ margin: 0, color: '#94a3b8', fontSize: '0.85rem', maxWidth: '320px' }}>
                      Add or import your holdings in the Paper Portfolio view to enable real-time risk exposure analysis.
                    </p>
                  </div>
                ) : tableHoldings.length === 0 ? (
                  <p style={{ color: '#94a3b8', fontSize: '0.9rem', padding: '16px 4px', margin: 0 }}>
                    No live holdings impact data available yet — nothing to show. Try Refresh later.
                  </p>
                ) : (
                  <div className="brief-table-container">
                    <table className="brief-table">
                      <thead>
                        <tr>
                          <th>Stock</th>
                          <th>Industry</th>
                          <th>Market Cap</th>
                          <th>P/E</th>
                          <th>Move %</th>
                          <th>Insider / Smart Money</th>
                          <th>AI Rationale</th>
                        </tr>
                      </thead>
                      <tbody>
                        {tableHoldings.map((row) => (
                          <tr key={row.symbol} style={{ cursor: 'pointer' }} onClick={() => goAnalyze(row.symbol)}>
                            <td>
                              <div className="brief-table-ticker">
                                <span className="stock-logo-circle" style={getCompanyLogoStyle(row.symbol)}>
                                  {row.symbol}
                                </span>
                                <span className="brief-table-ticker-name">{row.symbol}</span>
                              </div>
                            </td>
                            <td>
                              <span style={{ color: '#cbd5e1' }}>{row.industry}</span>
                            </td>
                            <td>
                              <span style={{ color: '#cbd5e1' }}>{row.marketCap}</span>
                            </td>
                            <td>
                              <span style={{ color: '#cbd5e1' }}>{row.pe}</span>
                            </td>
                            <td style={{ color: row.move > 0 ? '#34d399' : (row.move < 0 ? '#f87171' : '#94a3b8'), fontWeight: 600 }}>
                              {row.move > 0 ? '+' : ''}{row.move.toFixed(1)}%
                            </td>
                            <td>
                              <span className="brief-pill-neutral">{row.insider}</span>
                            </td>
                            <td style={{ color: '#cbd5e1' }}>{row.rationale}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>

            {/* Right Column: Macro News Impact Timeline */}
            <div className="brief-card" style={{ padding: '20px 24px' }}>
              <div className="brief-card-header">
                <div className="brief-card-title-group">
                  <List size={18} color="#c084fc" />
                  <h2 className="brief-card-title">Macro News Impact</h2>
                </div>
              </div>

              {(loading || extraLoading) && timelineNews.length === 0 ? (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '60px 0', gap: 12 }}>
                  <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                  <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Fetching macro news timeline...</span>
                </div>
              ) : timelineNews.length === 0 ? (
                <p style={{ color: '#94a3b8', fontSize: '0.9rem', padding: '16px 4px', margin: 0 }}>
                  No live news could be fetched for your portfolio tickers — nothing to show. Try Refresh later.
                </p>
              ) : (
              <div className="timeline-list">
                <div className="timeline-line" />
                {timelineNews.map((item, index) => (
                  <div className="timeline-item" key={index}>
                    <span className={`timeline-dot ${item.sentiment === 'Positive' ? 'positive' : 'negative'}`} />
                    <div className="timeline-item-header">
                      <span className="timeline-item-time">{item.time}</span>
                      <span className={item.sentiment === 'Positive' ? 'brief-pill-green' : 'brief-pill-red'}>
                        {item.sentiment}
                      </span>
                    </div>
                    <div className="timeline-item-text">
                      <strong>{item.symbol}</strong>: {item.text}
                    </div>
                  </div>
                ))}
              </div>
              )}
            </div>
          </div>
        </>
    </div>
  )
}
