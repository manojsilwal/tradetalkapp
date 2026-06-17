import React, { useState, useMemo, useId, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TrendingUp, Shield, CircleDollarSign, Wallet, PieChart, Scale, CheckCircle2, ArrowUpRight, HelpCircle, Loader2, Search, Zap, XCircle, ShieldAlert } from 'lucide-react';
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RechartsTooltip, LineChart as ReLineChart, Line, Legend } from 'recharts';
import { API_BASE_URL } from './api';
import { useAnalysisHistory } from './AnalysisContext';
import { SP500_TICKERS } from './sp500';
import { StaleValue, FreshnessBadge } from './components/Freshness';
import DashboardScorecardPanel from './components/DashboardScorecardPanel';
import VerdictToneLegend from './components/VerdictToneLegend';
import DebateThreadPanel from './components/debate/DebateThreadPanel';
import './DecisionTerminalUI.css';
import { buildRoadmapChartData, roadmapScenarioPrices } from './roadmapChartData';

const QUALITY_ICONS = {
  roic: TrendingUp,
  moat: Shield,
  fcf: CircleDollarSign,
  debt: Wallet,
  margin: PieChart,
  current_ratio: Scale,
};

function SemiGauge({ fillRatio, size = 'large', className = '' }) {
  const gid = useId().replace(/:/g, '');
  const r = size === 'large' ? 88 : 56;
  const sw = size === 'large' ? 10 : 7;
  const w = r * 2 + sw;
  const h = r + sw / 2 + 8;
  const cx = w / 2;
  const cy = r + sw / 2;
  const arcLen = Math.PI * r;
  const dash = Math.max(0, Math.min(1, fillRatio)) * arcLen;
  const theta = Math.PI * (1 - Math.max(0, Math.min(1, fillRatio)));

  return (
    <svg
      className={`dt-semi-gauge ${size} ${className}`}
      viewBox={`0 0 ${w} ${h}`}
      width={size === 'large' ? 200 : 130}
      height={size === 'large' ? 115 : 78}
      aria-hidden
    >
      <defs>
        <linearGradient id={`dtgg-${gid}`} x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#00c978" />
          <stop offset="100%" stopColor="#00ff88" />
        </linearGradient>
      </defs>
      <path
        className="dt-gauge-track"
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
        fill="none"
        stroke="rgba(148,163,184,0.18)"
        strokeWidth={sw}
        strokeLinecap="round"
      />
      <path
        className="dt-gauge-fill"
        d={`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`}
        fill="none"
        stroke={`url(#dtgg-${gid})`}
        strokeWidth={sw}
        strokeLinecap="round"
        strokeDasharray={`${dash} ${arcLen}`}
      />
      <line
        className="dt-gauge-needle"
        x1={cx}
        y1={cy}
        x2={cx + (r - sw) * 0.78 * Math.cos(theta)}
        y2={cy - (r - sw) * 0.78 * Math.sin(theta)}
        stroke="rgba(255,255,255,0.85)"
        strokeWidth={size === 'large' ? 2 : 1.5}
        strokeLinecap="round"
      />
    </svg>
  );
}

function valuationArcRatio(pctVsAverage) {
  if (pctVsAverage == null || Number.isNaN(pctVsAverage)) return 0.42;
  const c = Math.max(-35, Math.min(35, pctVsAverage));
  return (c + 35) / 70;
}

function polymarketArcRatio(pct) {
  if (pct == null || Number.isNaN(pct)) return 0.5;
  return Math.max(0.08, Math.min(0.95, pct / 100));
}

function ProvenanceTip({ provenance, label }) {
  if (!provenance) return label;
  const parts = [
    provenance.source && `Source: ${provenance.source}`,
    provenance.formula_or_note,
    provenance.missing_reason,
    provenance.confidence != null && `Confidence: ${Math.round(provenance.confidence * 100)}%`,
  ].filter(Boolean);
  return (
    <span className="dt-tip" title={parts.join(' — ')}>
      {label}
      <HelpCircle size={11} className="dt-tip-icon" />
    </span>
  );
}

function verdictTone(v) {
  const u = (v || '').toUpperCase();
  if (u.includes('STRONG BUY') || u === 'BUY') return 'buy';
  if (u.includes('STRONG SELL') || u === 'SELL') return 'sell';
  return 'neutral';
}

function fmtUsdCompact(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000_000) return `$${(n / 1_000_000_000_000).toFixed(2)}T`;
  if (abs >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(1)}%`;
}

export default function UnifiedDashboardUI() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { analyses, analyzeTicker: contextAnalyzeTicker, recentAnalyses } = useAnalysisHistory();
  const lastAutoTicker = useRef('');
  const [ticker, setTicker] = useState(() => {
    const param = searchParams.get('ticker')?.trim().toUpperCase();
    if (param) return param;
    return '';
  });

  const [localError, setLocalError] = useState(null);
  const [period, setPeriod] = useState('1mo');
  const [perfPeriod, setPerfPeriod] = useState('quarterly');

  // Sync page context so the app-level assistant knows what ticker is on screen
  useEffect(() => {
    window.__tt_page_context__ = {
      ...(window.__tt_page_context__ || {}),
      page: 'dashboard',
      ticker: ticker || null,
    };
  }, [ticker]);

  const searchUpper = ticker.trim().toUpperCase();
  const isInSp500 = !!searchUpper && SP500_TICKERS.includes(searchUpper);
  const suggestions = useMemo(() => {
    if (!searchUpper || isInSp500) return [];
    return SP500_TICKERS.filter(t => t.startsWith(searchUpper) || t.includes(searchUpper)).slice(0, 4);
  }, [searchUpper, isInSp500]);

  const currentAnalysis = useMemo(() => {
    const normalized = searchUpper;
    if (analyses[normalized]) return analyses[normalized];
    
    // Fallback to recentAnalyses if it's cached there
    const cached = recentAnalyses.find(a => a.ticker === normalized)?.result;
    if (cached) {
      return {
        status: 'success',
        loadingStep: '',
        error: null,
        loading: false,
        traceData: cached.trace,
        traceLoading: false,
        metricsData: cached.metrics,
        metricsLoading: false,
        capBucket: cached.capBucket,
        smallCapData: cached.smallCap,
        smallCapLoading: false,
        debateData: cached.debate,
        debateLoading: false,
        debateError: null,
        decisionData: cached.dt,
        decisionLoading: false,
        scorecardData: cached.scorecard,
        scorecardLoading: false,
        scorecardError: null,
        predMarketsData: cached.predMarkets,
        predMarketsLoading: false,
        fundamentalsData: cached.fundamentals,
        fundamentalsLoading: false,
      };
    }
    
    return {
      status: 'idle',
      loadingStep: '',
      error: null,
      loading: false,
      traceData: null,
      traceLoading: false,
      metricsData: null,
      metricsLoading: false,
      capBucket: null,
      smallCapData: null,
      smallCapLoading: false,
      debateData: null,
      debateLoading: false,
      debateError: null,
      decisionData: null,
      decisionLoading: false,
      scorecardData: null,
      scorecardLoading: false,
      scorecardError: null,
      predMarketsData: null,
      predMarketsLoading: false,
      fundamentalsData: null,
      fundamentalsLoading: false,
    };
  }, [analyses, recentAnalyses, searchUpper]);

  const {
    status: analysisStatus,
    loadingStep,
    error: analysisError,
    traceData,
    traceLoading,
    metricsData,
    metricsLoading,
    capBucket,
    smallCapData,
    smallCapLoading,
    debateData,
    debateLoading,
    debateError,
    decisionData,
    decisionLoading,
    scorecardData,
    scorecardLoading,
    scorecardError,
    predMarketsData,
    predMarketsLoading,
    fundamentalsData,
    fundamentalsLoading,
  } = currentAnalysis;

  const error = localError || analysisError;
  const isAnalyzing = analysisStatus === 'loading';
  const showFundamentalsMetricsLoader = fundamentalsLoading || (isAnalyzing && !fundamentalsData?.metrics);
  const showFundamentalsPriceLoader = fundamentalsLoading || (isAnalyzing && !fundamentalsData?.price_history?.[period]);
  const showFundamentalsChartLoader = fundamentalsLoading || (isAnalyzing && !fundamentalsData?.financials?.[perfPeriod]);

  const analyzeTicker = useCallback((overrideTicker = ticker, forceRefresh = false) => {
    const sym = (overrideTicker ?? ticker).trim().toUpperCase();
    if (!sym) {
      setLocalError('Enter a ticker symbol to analyze.');
      return;
    }
    setLocalError(null);
    setTicker(sym);
    setSearchParams({ ticker: sym }, { replace: true });
    contextAnalyzeTicker(sym, forceRefresh);
  }, [ticker, setSearchParams, contextAnalyzeTicker]);

  // Deep-link: /dashboard?ticker=NVDA from Daily Brief or bookmarks
  useEffect(() => {
    const fromUrl = searchParams.get('ticker')?.trim().toUpperCase();
    if (!fromUrl || fromUrl === lastAutoTicker.current) return;
    lastAutoTicker.current = fromUrl;
    analyzeTicker(fromUrl);
  }, [searchParams, analyzeTicker]);

  // Decision Terminal Extracted Variables
  const hasDecisionData = decisionData != null;
  const v = decisionData?.valuation;
  const q = decisionData?.quality;
  const z = decisionData?.verdict;
  const r = decisionData?.roadmap;

  const getBriefText = () => {
    if (predMarketsLoading) return 'Loading...';
    if (z?.prediction_market_bullish_pct != null && !z?.polymarket_gated_out) {
      const pct = Math.round(z.prediction_market_bullish_pct);
      if (pct >= 55) return `Positive Prediction (${pct}% Yes)`;
      if (pct <= 45) return `Negative Prediction (${pct}% Yes)`;
      return `Neutral Prediction (${pct}% Yes)`;
    }
    if (predMarketsData?.gated_out === false && predMarketsData?.gated_probability != null) {
      const pct = Math.round(predMarketsData.gated_probability);
      if (pct >= 55) return `Positive Prediction (${pct}% Yes)`;
      if (pct <= 45) return `Negative Prediction (${pct}% Yes)`;
      return `Neutral Prediction (${pct}% Yes)`;
    }
    if (!predMarketsData || !predMarketsData.has_relevant_data) return 'No Active Markets';
    return 'Active Markets Scan';
  };

  const valFill = valuationArcRatio(v?.pct_vs_average);
  const pmFill = z?.polymarket_gated_out ? 0.35 : polymarketArcRatio(z?.prediction_market_bullish_pct);
  const socialFactor = traceData?.factors?.social_sentiment;
  const socialConfPct = socialFactor?.confidence != null
    ? Number((socialFactor.confidence * 100).toFixed(0))
    : null;
  const socialBullish = Number(socialFactor?.trading_signal ?? 0) > 0;
  const socialFill = socialConfPct != null
    ? polymarketArcRatio(socialBullish ? socialConfPct : 100 - socialConfPct)
    : 0.5;

  const stancePct = z?.debate_stance_bull_pct;
  const moderatorConfPct = z?.debate_confidence_pct;
  const expertBullish = stancePct != null && stancePct >= 55;

  const spot = decisionData?.spot?.price_usd ?? v?.current_price_usd ?? fundamentalsData?.company_info?.current_price;
  const spotSource = decisionData?.spot?.source;
  const reconciliation = decisionData?.reconciliation;
  const embeddedScorecard = decisionData?.scorecard_summary;
  const scenarioPrices = useMemo(() => roadmapScenarioPrices(r, spot), [r, spot]);

  const roadmapChartData = useMemo(
    () => buildRoadmapChartData(r, spot),
    [r, spot],
  );

  const predictedCagrPct = useMemo(() => {
    if (r?.predicted_cagr_base_pct != null) return r.predicted_cagr_base_pct;
    if (!scenarioPrices || !spot || spot <= 0) return null;
    return Number((((scenarioPrices.base / spot) ** (1 / 3) - 1) * 100).toFixed(2));
  }, [r?.predicted_cagr_base_pct, scenarioPrices, spot]);

  // Derived from trace / metrics
  const isBullish = traceData?.global_signal >= 2;
  const verdict = traceData?.global_verdict || "AWAITING ANALYSIS";

  const chartTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;
    return (
      <div style={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', padding: 8, borderRadius: 6, fontSize: 12 }}>
        <div style={{ color: '#94a3b8', marginBottom: 4 }}>{label}</div>
        {payload.map((entry, index) => (
          <div key={index} style={{ color: entry.color, fontWeight: 500 }}>
            {entry.name}: ${Number(entry.value).toFixed(2)}
          </div>
        ))}
      </div>
    );
  };

  const priceChartTooltip = ({ active, payload, label }) => {
    if (!active || !payload || !payload.length) return null;
    const priceVal = payload[0].value;
    return (
      <div style={{ background: '#0f172a', border: '1px solid rgba(255,255,255,0.1)', padding: '8px 12px', borderRadius: '6px', fontSize: '12px' }}>
        <div style={{ color: 'var(--dt-muted)', marginBottom: 4 }}>
          {label ? new Date(label).toLocaleString() : ''}
        </div>
        <div style={{ color: '#fff', fontWeight: 600 }}>
          Price: ${Number(priceVal).toFixed(2)}
        </div>
      </div>
    );
  };

  const steps = useMemo(() => [
    { label: 'Validating ticker & format', done: true },
    { label: 'Retrieving RAG knowledge base & metrics', done: !metricsLoading && !scorecardLoading },
    { label: 'Assembling multi-agent debate chamber', done: !debateLoading },
    { label: 'Executing swarm consensus trace', done: !traceLoading },
    { label: 'Synthesizing valuation terminal & roadmap', done: !decisionLoading },
    { label: 'Scanning prediction market contracts', done: !predMarketsLoading }
  ], [metricsLoading, scorecardLoading, debateLoading, traceLoading, decisionLoading, predMarketsLoading]);

  const doneCount = useMemo(() => steps.filter(s => s.done).length, [steps]);
  const progressPct = useMemo(() => Math.round((doneCount / steps.length) * 100), [doneCount, steps.length]);

  const isPricePositive = (fundamentalsData?.company_info?.price_change ?? 0) >= 0;

  const PERIOD_TABS = [
    { label: '1D', value: '1d' },
    { label: '5D', value: '5d' },
    { label: '1M', value: '1mo' },
    { label: '6M', value: '6mo' },
    { label: 'YTD', value: 'ytd' },
    { label: '1Y', value: '1y' },
    { label: '5Y', value: '5y' },
    { label: 'MAX', value: 'max' },
  ];

  return (
    <div className="dt-wrap fade-in">

      <header className="dt-search-header">
        <div className="dt-search-title">
          <h2>Stock Analysis</h2>
          <p>Real-time Swarm Analysis &amp; Valuation Hub</p>
        </div>

        <div className="dt-search-actions">
          <div className="dt-search-input-wrap">
            <input
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="e.g. AAPL"
              className="dt-search-input"
              onKeyDown={(e) => { if (e.key === 'Enter') analyzeTicker(ticker); }}
            />
            {!isInSp500 && searchUpper && suggestions.length > 0 && (
              <div className="dt-suggestions">
                <div className="dt-suggestions-label">Suggestions (S&amp;P 500)</div>
                {suggestions.map((s) => (
                  <button key={s} type="button" className="dt-suggestion-item" onClick={() => analyzeTicker(s)}>
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="dt-search-buttons">
            <button
              type="button"
              className="dt-analyze-btn"
              onClick={() => analyzeTicker(ticker)}
              disabled={isAnalyzing}
            >
              {isAnalyzing ? <Loader2 className="spinner" size={16} /> : <Search size={16} />}
              Analyze
            </button>
            {hasDecisionData && (
              <button
                type="button"
                className="dt-refresh-btn"
                onClick={() => analyzeTicker(ticker, true)}
                disabled={isAnalyzing}
                title="Force refresh data"
              >
                {isAnalyzing ? <Loader2 className="spinner" size={16} /> : <Zap size={16} />}
                Refresh
              </button>
            )}
          </div>
        </div>
      </header>

      {error && (
        <div className="glass-panel" style={{ borderColor: 'var(--accent-red)', padding: '16px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.1)' }}>
          <p style={{ color: 'var(--accent-red)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}><ShieldAlert size={18} /> {error}</p>
        </div>
      )}

      {/* Partial-degradation amber warning: debate succeeded but some agents fell back to heuristic */}
      {!error && debateData?.quality_warning && debateData?.degraded_roles?.length > 0 && (
        <div className="glass-panel" style={{ borderColor: '#f59e0b', padding: '14px 16px', borderRadius: '8px', background: 'rgba(245, 158, 11, 0.08)' }}>
          <p style={{ color: '#f59e0b', margin: 0, display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: '0.85rem', lineHeight: 1.5 }}>
            <ShieldAlert size={18} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>{debateData.quality_warning}</span>
          </p>
        </div>
      )}

      {!isAnalyzing && !error && !hasDecisionData && !traceData && (
        <div className="dt-prompt-banner glass-panel">
          Enter a ticker and click Analyze. First load can take up to a minute (swarm, debate, and decision terminal).
        </div>
      )}

      {isAnalyzing && (
        <section className="dt-panel dt-analysis-progress" data-testid="dashboard-analysis-progress">
          <div className="dt-analysis-progress-head">
            <span className="dt-analysis-progress-title">
              {loadingStep || 'Running analysis…'}
            </span>
            <span className="dt-analysis-progress-pct">{progressPct}%</span>
          </div>
          <div className="dt-analysis-progress-bar">
            <div className="dt-analysis-progress-fill" style={{ width: `${progressPct}%` }} />
          </div>
          <ul className="dt-analysis-steps">
            {steps.map((step) => (
              <li key={step.label} className={step.done ? 'done' : 'pending'}>
                {step.done ? <CheckCircle2 size={14} /> : <Loader2 size={14} className="spinner" />}
                <span>{step.label}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Main Redesigned Layout Grid */}
      <div className="dt-dashboard-grid">

        {/* 1. BUSINESS QUALITY SCORECARD */}
        <section className="dt-panel dt-area-scorecard">
          <h2 className="dt-panel-title">Business Quality Scorecard</h2>
          {decisionLoading ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', marginTop: 14, fontSize: '0.9rem' }}>
              <Loader2 className="spinner" size={18} /> Loading scorecard…
            </div>
          ) : (
            <div className="dt-quality-3x2" style={{ marginTop: '16px' }}>
              {(q?.rows || []).map((row) => {
                const IconComp = QUALITY_ICONS[row.id] || TrendingUp;
                const st = (row.status_label || '').toLowerCase();
                const tone = st.includes('good') && !st.includes('strong') ? 'warn' : ['excellent', 'strong', 'robust', 'low', 'high'].some((k) => st.includes(k)) ? 'ok' : 'muted';
                return (
                  <div key={row.id} className="dt-q-tile">
                    <div className="dt-q-tile-icon"><IconComp size={20} strokeWidth={1.6} /></div>
                    <div className="dt-q-tile-body">
                      <div className="dt-q-tile-label"><ProvenanceTip provenance={row.provenance} label={row.label} /></div>
                      <div className="dt-q-tile-value">{row.value_label || '—'}</div>
                      <div className={`dt-q-tile-status dt-tone-${tone}`}>{row.status_label || '—'}</div>
                    </div>
                  </div>
                );
              })}
              {(!hasDecisionData || !q?.rows?.length) &&
                ['ROIC', 'Moat', 'FCF', 'Debt', 'Margin', 'Current ratio'].map((label) => (
                  <div key={label} className="dt-q-tile dt-q-tile-empty">
                    <div className="dt-q-tile-icon muted"><TrendingUp size={20} /></div>
                    <div className="dt-q-tile-body">
                      <div className="dt-q-tile-label">{label}</div>
                      <div className="dt-q-tile-value">—</div>
                      <div className="dt-q-tile-status dt-tone-muted">—</div>
                    </div>
                  </div>
                ))}
            </div>
          )}
        </section>

        {/* 2. STOCK CHART CENTER PANEL */}
        <section className="dt-panel dt-area-chart">
          <div className="dt-chart-breadcrumb">STOCKS &gt; US &gt; <span>{searchUpper || 'AAPL'}</span></div>
          <div className="dt-company-header">
            <h1 className="dt-company-name">
              {fundamentalsLoading ? 'Loading...' : (fundamentalsData?.company_info?.company_name || searchUpper || 'AAPL')}
            </h1>
            {!fundamentalsLoading && (fundamentalsData?.company_info || spot != null) && (
              <div className="dt-price-display">
                <StaleValue freshness={decisionData?.data_freshness || fundamentalsData?.data_freshness} priceSensitive>
                  <span className="dt-price-value" data-testid="dashboard-current-price" data-symbol={searchUpper}>${spot != null ? Number(spot).toFixed(2) : '—'}</span>
                  {fundamentalsData?.company_info?.price_change_pct != null && (
                    <span className={`dt-price-badge ${isPricePositive ? 'positive' : 'negative'}`}>
                      {isPricePositive ? '▲' : '▼'} {Math.abs(fundamentalsData.company_info.price_change_pct).toFixed(2)}%
                    </span>
                  )}
                  {fundamentalsData?.company_info?.price_change != null && (
                    <span className="dt-price-change-abs">
                      {isPricePositive ? '+' : ''}{fundamentalsData.company_info.price_change.toFixed(2)} Today
                    </span>
                  )}
                </StaleValue>
                {(decisionData?.data_freshness || fundamentalsData?.data_freshness) && (
                  <FreshnessBadge freshness={decisionData?.data_freshness || fundamentalsData?.data_freshness} />
                )}
                {spotSource && (
                  <span style={{ fontSize: '0.7rem', color: 'var(--dt-muted)', marginLeft: 8 }}>
                    {decisionData?.spot?.degraded ? `Delayed (${spotSource})` : `Live (${spotSource})`}
                  </span>
                )}
              </div>
            )}
          </div>
          
          <div className="dt-period-tabs">
            {PERIOD_TABS.map((tab) => (
              <button
                key={tab.value}
                type="button"
                className={`dt-period-tab ${period === tab.value ? 'active' : ''}`}
                onClick={() => setPeriod(tab.value)}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="dt-stock-chart-container">
            {showFundamentalsPriceLoader ? (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--dt-muted)' }}>
                <Loader2 className="spinner" size={18} /> Loading price history…
              </div>
            ) : fundamentalsData?.price_history?.[period]?.length > 0 ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={fundamentalsData.price_history[period]} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={isPricePositive ? '#00ff88' : '#f87171'} stopOpacity={0.25}/>
                      <stop offset="95%" stopColor={isPricePositive ? '#00ff88' : '#f87171'} stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                  <XAxis 
                    dataKey="timestamp" 
                    tick={{ fill: '#94a3b8', fontSize: 10 }}
                    axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                    tickLine={false}
                    tickFormatter={(tick) => {
                      if (!tick) return '';
                      try {
                        const d = new Date(tick);
                        if (period === '1d' || period === '5d') {
                          return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
                        }
                        return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
                      } catch {
                        return tick;
                      }
                    }}
                  />
                  <YAxis 
                    tick={{ fill: '#94a3b8', fontSize: 10 }}
                    axisLine={false}
                    tickLine={false}
                    domain={['auto', 'auto']}
                    tickFormatter={(val) => `$${val.toFixed(2)}`}
                  />
                  <RechartsTooltip content={priceChartTooltip} />
                  <Area 
                    type="monotone" 
                    dataKey="close" 
                    stroke={isPricePositive ? '#00ff88' : '#f87171'} 
                    strokeWidth={2}
                    fillOpacity={1} 
                    fill="url(#colorPrice)" 
                  />
                </AreaChart>
              </ResponsiveContainer>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--dt-muted)', fontSize: '0.85rem' }}>
                No price history available.
              </div>
            )}
          </div>
        </section>

        {/* 3. VERDICT & SENTIMENT + FUTURE PRICE ROADMAP */}
        <section className="dt-panel dt-area-verdict">
          <h2 className="dt-panel-title">Verdict &amp; Sentiment Hub</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 12 }}>
            <div className="dt-verdict-row">
              <span className="dt-verdict-row-label">Prediction Markets</span>
              {decisionLoading || predMarketsLoading ? (
                <Loader2 className="spinner" size={16} />
              ) : (
                <span style={{ fontSize: '0.78rem', fontWeight: 600, color: '#94a3b8', textAlign: 'right', maxWidth: '58%' }}>
                  {getBriefText()}
                </span>
              )}
            </div>
            <div className="dt-verdict-row">
              <span className="dt-verdict-row-label">Social Sentiment</span>
              {traceLoading ? (
                <Loader2 className="spinner" size={16} />
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                  <div className="dt-verdict-mini-gauge">
                    <SemiGauge fillRatio={socialFill} size="small" />
                  </div>
                  {socialConfPct != null && (
                    <span style={{ fontSize: '0.72rem', color: 'var(--dt-muted)' }}>
                      Social factor confidence: {socialConfPct}%
                    </span>
                  )}
                </div>
              )}
            </div>
            <div className="dt-verdict-row">
              <span className="dt-verdict-row-label">Expert Consensus</span>
              {decisionLoading ? (
                <Loader2 className="spinner" size={16} />
              ) : (
                <div style={{ fontSize: '0.78rem', textAlign: 'right', lineHeight: 1.45 }}>
                  {stancePct != null ? (
                    <div style={{ fontWeight: 700, color: expertBullish ? '#00ff88' : '#94a3b8' }}>
                      Stance: {stancePct.toFixed(0)}% bull · {(100 - stancePct).toFixed(0)}% bear/neutral
                    </div>
                  ) : (
                    <div>—</div>
                  )}
                  {moderatorConfPct != null && (
                    <div style={{ color: 'var(--dt-muted)', marginTop: 2 }}>
                      Moderator confidence: {moderatorConfPct.toFixed(0)}%
                    </div>
                  )}
                </div>
              )}
            </div>
            
            <div className="dt-aggregate-card">
              <div className="dt-aggregate-card-title">Aggregate Verdict</div>
              {decisionLoading ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: 'var(--dt-muted)', fontSize: '0.82rem' }}>
                  <Loader2 className="spinner" size={14} /> Synthesizing...
                </div>
              ) : (
                <div className={`dt-aggregate-badge ${verdictTone(z?.headline_verdict || verdict)}`}>
                  <CheckCircle2 size={16} />
                  <span>{hasDecisionData ? (z?.headline_verdict || verdict).toUpperCase() : 'AWAITING ANALYSIS'}</span>
                </div>
              )}
            </div>

            {reconciliation?.conflicting_signals?.length > 0 && reconciliation.reconciliation_note && (
              <div
                style={{
                  padding: '12px 14px',
                  background: 'rgba(245, 158, 11, 0.08)',
                  border: '1px solid rgba(245, 158, 11, 0.25)',
                  borderRadius: 8,
                  fontSize: '0.8rem',
                  lineHeight: 1.5,
                  color: '#e2e8f0',
                }}
                data-testid="reconciliation-banner"
              >
                {reconciliation.reconciliation_note}
              </div>
            )}

            <VerdictToneLegend />

            <div className="dt-roadmap-compact">
              <h2 className="dt-panel-title">Future Price Roadmap</h2>
              <div className="dt-roadmap-legend-row" style={{ marginTop: 6 }}>
                <span className="dt-roadmap-legend-item">
                  <span className="dt-roadmap-dot bull" />
                  Bull {scenarioPrices?.bull != null && `($${Number(scenarioPrices.bull).toFixed(0)})`}
                </span>
                <span className="dt-roadmap-legend-item">
                  <span className="dt-roadmap-dot base" />
                  Base {scenarioPrices?.base != null && `($${Number(scenarioPrices.base).toFixed(0)})`}
                </span>
                <span className="dt-roadmap-legend-item">
                  <span className="dt-roadmap-dot bear" />
                  Bear {scenarioPrices?.bear != null && `($${Number(scenarioPrices.bear).toFixed(0)})`}
                </span>
              </div>
              <div className="dt-roadmap-chart-sm" style={{ marginTop: 6 }}>
                {decisionLoading ? (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--dt-muted)' }}>
                    <Loader2 className="spinner" size={18} /> Generating paths…
                  </div>
                ) : roadmapChartData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <ReLineChart data={roadmapChartData} margin={{ top: 5, right: 5, left: -25, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                      <XAxis dataKey="t" tick={{ fill: '#94a3b8', fontSize: 9 }} tickLine={false} axisLine={false} />
                      <YAxis tick={{ fill: '#94a3b8', fontSize: 8 }} axisLine={false} tickLine={false} domain={['auto', 'auto']} />
                      <RechartsTooltip content={chartTooltip} />
                      <Line type="monotone" dataKey="bull" name="Bull case" stroke="#00ff88" strokeWidth={1.5} dot={false} strokeDasharray="3 3" />
                      <Line type="monotone" dataKey="base" name="Base case" stroke="#8b5cf6" strokeWidth={1.5} dot={false} strokeDasharray="3 3" />
                      <Line type="monotone" dataKey="bear" name="Bear case" stroke="#f87171" strokeWidth={1.5} dot={false} strokeDasharray="3 3" />
                    </ReLineChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#64748b', fontSize: '11px' }}>
                    Run analysis to load paths
                  </div>
                )}
              </div>
            </div>
          </div>
        </section>

        {/* 4. CONSOLIDATED METRICS & FINANCIAL PERFORMANCE */}
        <section className="dt-panel dt-area-metrics-perf">
          <h2 className="dt-panel-title">Financial Health &amp; Performance</h2>
            <div className="dt-metrics-split">
              <div className="dt-metrics-col">
                <h3 className="dt-metrics-block-title">Consolidated Metrics</h3>
                {showFundamentalsMetricsLoader ? (
                  <div className="dt-metrics-loading">
                    <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                    <span>Loading metrics...</span>
                  </div>
                ) : (
                  <div className="dt-consolidated">
                    {/* Left Column: Valuation & Margins */}
                    <div>
                      <h4 className="dt-metrics-section-title">Valuation</h4>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Market Cap</span>
                        <span className="dt-metric-value">{fmtUsdCompact(fundamentalsData?.metrics?.valuation?.market_cap)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">PE Ratio (TTM)</span>
                        <span className="dt-metric-value">{fundamentalsData?.metrics?.valuation?.trailing_pe?.toFixed(1) || <span className="dt-metric-dash">—</span>}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">PE Ratio (Forward)</span>
                        <span className="dt-metric-value">{fundamentalsData?.metrics?.valuation?.forward_pe?.toFixed(1) || <span className="dt-metric-dash">—</span>}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Price to Sales</span>
                        <span className="dt-metric-value">{fundamentalsData?.metrics?.valuation?.price_to_sales?.toFixed(2) || <span className="dt-metric-dash">—</span>}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">EV / EBITDA</span>
                        <span className="dt-metric-value">{fundamentalsData?.metrics?.valuation?.ev_to_ebitda?.toFixed(1) || <span className="dt-metric-dash">—</span>}</span>
                      </div>

                      <h4 className="dt-metrics-section-title" style={{ marginTop: 20 }}>Margins &amp; Growth</h4>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Profit Margin</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.margins_and_growth?.profit_margins != null ? fundamentalsData.metrics.margins_and_growth.profit_margins * 100 : null)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Operating Margin</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.margins_and_growth?.operating_margins != null ? fundamentalsData.metrics.margins_and_growth.operating_margins * 100 : null)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Earnings Growth YoY</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.margins_and_growth?.earnings_growth_yoy != null ? fundamentalsData.metrics.margins_and_growth.earnings_growth_yoy * 100 : null)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Revenue Growth YoY</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.margins_and_growth?.revenue_growth_yoy != null ? fundamentalsData.metrics.margins_and_growth.revenue_growth_yoy * 100 : null)}</span>
                      </div>
                    </div>

                    {/* Right Column: Cash Flow, Balance Sheet, Dividends */}
                    <div>
                      <h4 className="dt-metrics-section-title">Cash Flow</h4>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Free Cash Flow</span>
                        <span className="dt-metric-value">{fmtUsdCompact(fundamentalsData?.metrics?.cash_flow?.free_cash_flow)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">FCF Yield</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.cash_flow?.fcf_yield != null ? fundamentalsData.metrics.cash_flow.fcf_yield * 100 : null)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">FCF Per Share</span>
                        <span className="dt-metric-value">{fundamentalsData?.metrics?.cash_flow?.fcf_per_share != null ? `$${fundamentalsData.metrics.cash_flow.fcf_per_share.toFixed(2)}` : <span className="dt-metric-dash">—</span>}</span>
                      </div>

                      <h4 className="dt-metrics-section-title" style={{ marginTop: 20 }}>Balance Sheet</h4>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Total Cash</span>
                        <span className="dt-metric-value">{fmtUsdCompact(fundamentalsData?.metrics?.balance?.total_cash)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Total Debt</span>
                        <span className="dt-metric-value">{fmtUsdCompact(fundamentalsData?.metrics?.balance?.total_debt)}</span>
                      </div>

                      <h4 className="dt-metrics-section-title" style={{ marginTop: 20 }}>Dividends</h4>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Dividend Yield</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.dividend?.dividend_yield != null ? fundamentalsData.metrics.dividend.dividend_yield * 100 : null)}</span>
                      </div>
                      <div className="dt-metric-row">
                        <span className="dt-metric-label">Payout Ratio</span>
                        <span className="dt-metric-value">{fmtPct(fundamentalsData?.metrics?.dividend?.payout_ratio != null ? fundamentalsData.metrics.dividend.payout_ratio * 100 : null)}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              <div className="dt-metrics-col dt-metrics-col-chart">
                <div className="dt-metrics-chart-head">
                  <h3 className="dt-metrics-block-title">Financial Performance</h3>
                  <div className="dt-perf-toggle">
                    <button
                      type="button"
                      className={`dt-perf-toggle-btn ${perfPeriod === 'quarterly' ? 'active' : ''}`}
                      onClick={() => setPerfPeriod('quarterly')}
                    >
                      Quarterly
                    </button>
                    <button
                      type="button"
                      className={`dt-perf-toggle-btn ${perfPeriod === 'annual' ? 'active' : ''}`}
                      onClick={() => setPerfPeriod('annual')}
                    >
                      Annually
                    </button>
                  </div>
                </div>

                <div className="dt-perf-chart-box">
                  <div className="dt-perf-chart-inner dt-perf-chart-inner-lg">
                    {showFundamentalsChartLoader ? (
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', gap: 12 }}>
                        <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Loading chart...</span>
                      </div>
                    ) : fundamentalsData?.financials?.[perfPeriod]?.length > 0 ? (
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={fundamentalsData.financials[perfPeriod]} margin={{ top: 10, right: 10, left: -15, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" vertical={false} />
                          <XAxis dataKey="period" tick={{ fill: '#94a3b8', fontSize: 9 }} tickLine={false} axisLine={false} />
                          <YAxis tick={{ fill: '#94a3b8', fontSize: 8 }} axisLine={false} tickLine={false} tickFormatter={(val) => fmtUsdCompact(val)} />
                          <RechartsTooltip
                            contentStyle={{ background: '#0f172a', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px' }}
                            labelStyle={{ color: '#94a3b8', fontSize: '10px' }}
                            itemStyle={{ color: '#fff', fontSize: '11px' }}
                            formatter={(value) => [fmtUsdCompact(value)]}
                            cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }}
                          />
                          <Legend wrapperStyle={{ fontSize: 10, color: '#94a3b8', paddingTop: 10 }} />
                          <Bar dataKey="revenue" fill="#3b82f6" name="Revenue" radius={[4, 4, 0, 0]} />
                          <Bar dataKey="net_income" fill="#10b981" name="Net Income" radius={[4, 4, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    ) : (
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                        No financial performance data
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
        </section>

      </div>

      <section className="dt-panel dt-area-valuation">
        <h2 className="dt-panel-title">Consensus Valuation Signal</h2>
        <div className="dt-valuation-split">
          <div className="dt-valuation-gauge">
            {decisionLoading ? (
              <div className="dt-metrics-loading" style={{ minHeight: 120 }}>
                <Loader2 className="spinner" size={22} />
              </div>
            ) : (
              <>
                <SemiGauge fillRatio={hasDecisionData ? valFill : 0.38} size="large" />
                <div className="dt-gauge-caption">{hasDecisionData ? v?.gauge_label || '—' : '—'}</div>
                {hasDecisionData && spot != null && (
                  <div className="dt-gauge-sub">
                    Spot ${Number(spot).toFixed(2)}
                    {v?.average_fair_value_usd != null && (
                      <> · Avg fair ${Number(v.average_fair_value_usd).toFixed(0)}</>
                    )}
                  </div>
                )}
              </>
            )}
          </div>
          <div className="dt-valuation-models">
            <div className="dt-models-heading">Valuation models</div>
            <ul className="dt-models-list">
              {(v?.models || []).map((m) => (
                <li key={m.name} className="dt-models-li">
                  <span className="dt-models-name">
                    <ProvenanceTip provenance={m.provenance} label={`${m.name}:`} />
                  </span>
                  <span className={m.available && m.fair_value_usd != null ? 'dt-models-val' : 'dt-models-na'}>
                    {m.available && m.fair_value_usd != null
                      ? `$${Number(m.fair_value_usd).toFixed(0)}`
                      : '—'}
                  </span>
                </li>
              ))}
              {hasDecisionData && (
                <li className="dt-models-li dt-models-average">
                  <span className="dt-models-name">Average:</span>
                  <span className="dt-models-val">
                    {v?.average_fair_value_usd != null
                      ? `$${Number(v.average_fair_value_usd).toFixed(0)}`
                      : '—'}
                  </span>
                </li>
              )}
              {!hasDecisionData && !decisionLoading && (
                <li className="dt-models-li dt-models-placeholder"><span>Average:</span><span>—</span></li>
              )}
            </ul>
          </div>
        </div>
      </section>

      <DashboardScorecardPanel
        data={scorecardData}
        embeddedSummary={embeddedScorecard}
        ticker={searchUpper}
        loading={scorecardLoading || (isAnalyzing && !scorecardData && !embeddedScorecard)}
        error={scorecardError}
      />

      {(debateLoading || debateData || isAnalyzing) && (
        <section className="dt-panel dt-area-debate" data-testid="dashboard-debate-panel">
          <h2 className="dt-panel-title">Investment Committee Debate</h2>
          {debateError && (
            <p style={{ color: 'var(--accent-red)', fontSize: '0.85rem', marginTop: 12 }}>{debateError}</p>
          )}
          <div style={{ marginTop: 14 }}>
            <DebateThreadPanel result={debateData} loading={debateLoading || (isAnalyzing && !debateData)} />
          </div>
        </section>
      )}

    </div>
  );
}
