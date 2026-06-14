import React, { useState, useMemo, useId, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TrendingUp, Shield, CircleDollarSign, Wallet, PieChart, Scale, CheckCircle2, ArrowUpRight, HelpCircle, Loader2, Search, Zap, XCircle, ShieldAlert } from 'lucide-react';
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RechartsTooltip, LineChart as ReLineChart, Line, Legend } from 'recharts';
import { API_BASE_URL } from './api';
import { useAnalysisHistory } from './AnalysisContext';
import { SP500_TICKERS } from './sp500';
import ActionableCompaniesPanel, { ActionableCompaniesButton, useActionableCompanies } from './components/ActionableCompaniesPanel';
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
    loading,
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

  // Async S&P 500 batch screener
  const actionableState = useActionableCompanies();

  // Deep-link: /?ticker=NVDA from Daily Brief or bookmarks
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
    if (!predMarketsData || !predMarketsData.has_relevant_data) return 'No Active Markets';
    
    const directEvts = (predMarketsData.events || []).filter(e => e.relevance_type !== 'sector');
    const directWithProb = directEvts.filter(e => e.probability != null);
    
    let prob = null;
    
    if (directWithProb.length > 0) {
      const sum = directWithProb.reduce((acc, e) => acc + e.probability, 0);
      prob = sum / directWithProb.length;
    } else if (z?.prediction_market_bullish_pct != null && !z?.polymarket_gated_out) {
      prob = z.prediction_market_bullish_pct / 100;
    } else {
      const sectorEvts = (predMarketsData.events || []).filter(e => e.relevance_type === 'sector');
      const sectorWithProb = sectorEvts.filter(e => e.probability != null);
      if (sectorWithProb.length > 0) {
        const sum = sectorWithProb.reduce((acc, e) => acc + e.probability, 0);
        prob = sum / sectorWithProb.length;
      }
    }
    
    if (prob !== null) {
      const pct = Math.round(prob * 100);
      if (pct >= 55) {
        return `Positive Prediction (${pct}% Yes avg)`;
      } else if (pct <= 45) {
        return `Negative Prediction (${pct}% Yes avg)`;
      } else {
        return `Neutral Prediction (${pct}% Yes avg)`;
      }
    }
    
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

  const expertPct = z?.expert_bullish_pct;
  const expertBullish = expertPct != null && expertPct >= 55;

  const spot = v?.current_price_usd || fundamentalsData?.company_info?.current_price;
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
    <div className="dt-wrap fade-in" style={{ maxWidth: '1400px', margin: '0 auto', padding: '0 20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Search Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
         <div className="title-group">
            <h2 style={{ fontSize: '1.8rem', fontWeight: 700, margin: '0 0 5px 0' }}>Stock Analysis</h2>
            <p style={{ color: 'var(--text-muted)', margin: 0 }}>Real-time Swarm Analysis &amp; Valuation Hub</p>
         </div>

         <div style={{ position: 'relative' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <input
              type="text"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              placeholder="e.g. AAPL"
              className="dt-search-input"
              style={{ width: '160px', padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(0,0,0,0.2)', color: 'white' }}
              onKeyDown={(e) => { if (e.key === 'Enter') analyzeTicker(ticker); }}
            />
            <button
              type="button"
              onClick={() => analyzeTicker(ticker)}
              disabled={isAnalyzing}
              style={{ padding: '10px 20px', borderRadius: '8px', border: 'none', background: 'var(--accent-blue)', color: 'white', fontWeight: 600, cursor: isAnalyzing ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, opacity: isAnalyzing ? 0.55 : 1 }}
            >
              {isAnalyzing ? <Loader2 className="spinner" size={16} /> : <Search size={16} />}
              Analyze
            </button>
            {hasDecisionData && (
              <button
                type="button"
                onClick={() => analyzeTicker(ticker, true)}
                disabled={isAnalyzing}
                style={{
                  padding: '10px 14px',
                  borderRadius: '8px',
                  border: '1px solid rgba(255, 255, 255, 0.12)',
                  background: 'rgba(255, 255, 255, 0.05)',
                  color: 'white',
                  fontWeight: 600,
                  cursor: isAnalyzing ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 6,
                  opacity: isAnalyzing ? 0.55 : 1,
                  transition: 'background 0.2s',
                }}
                title="Force refresh data"
              >
                {isAnalyzing ? <Loader2 className="spinner" size={16} /> : <Zap size={16} />}
                Refresh
              </button>
            )}
            <ActionableCompaniesButton busy={actionableState.busy} onClick={actionableState.startScan} />
          </div>
          {!isInSp500 && searchUpper && suggestions.length > 0 && (
            <div className="dt-suggestions" style={{ position: 'absolute', top: '100%', left: 0, width: '160px', background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', marginTop: '4px', zIndex: 10 }}>
              <div style={{ padding: '8px', fontSize: '11px', color: '#94a3b8' }}>Suggestions (S&P 500)</div>
              {suggestions.map((s) => (
                <div key={s} onClick={() => analyzeTicker(s)} style={{ padding: '8px', cursor: 'pointer', fontSize: '14px' }}>
                  {s}
                </div>
              ))}
            </div>
          )}
         </div>
      </div>

      {error && (
        <div className="glass-panel" style={{ borderColor: 'var(--accent-red)', padding: '16px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.1)' }}>
          <p style={{ color: 'var(--accent-red)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}><ShieldAlert size={18} /> {error}</p>
        </div>
      )}

      {!isAnalyzing && !error && !hasDecisionData && !traceData && (
        <div className="dt-prompt-banner glass-panel" style={{ padding: '16px', marginBottom: 4, color: '#94a3b8', fontSize: '0.9rem' }}>
          Enter a ticker and click Analyze. First load can take up to a minute (swarm, debate, and decision terminal).
        </div>
      )}

      {/* Actionable Companies batch screener results */}
      <ActionableCompaniesPanel state={actionableState} onSelectTicker={(sym) => analyzeTicker(sym)} />

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
            {!fundamentalsLoading && fundamentalsData?.company_info && (
              <div className="dt-price-display">
                <span className="dt-price-value" data-testid="dashboard-current-price" data-symbol={searchUpper}>${fundamentalsData.company_info.current_price?.toFixed(2) || '—'}</span>
                {fundamentalsData.company_info.price_change_pct != null && (
                  <span className={`dt-price-badge ${isPricePositive ? 'positive' : 'negative'}`}>
                    {isPricePositive ? '▲' : '▼'} {Math.abs(fundamentalsData.company_info.price_change_pct).toFixed(2)}%
                  </span>
                )}
                {fundamentalsData.company_info.price_change != null && (
                  <span className="dt-price-change-abs">
                    {isPricePositive ? '+' : ''}{fundamentalsData.company_info.price_change.toFixed(2)} Today
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
            {fundamentalsLoading || (loading && !fundamentalsData?.price_history?.[period]) ? (
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
              <span className="dt-verdict-row-label">Social Sentiment</span>
              {traceLoading ? (
                <Loader2 className="spinner" size={16} />
              ) : (
                <div className="dt-verdict-mini-gauge">
                  <SemiGauge fillRatio={socialFill} size="small" />
                </div>
              )}
            </div>
            <div className="dt-verdict-row">
              <span className="dt-verdict-row-label">Expert Consensus</span>
              {decisionLoading ? (
                <Loader2 className="spinner" size={16} />
              ) : (
                <span style={{ fontSize: '0.82rem', fontWeight: 700, color: expertBullish ? '#00ff88' : '#94a3b8' }}>
                  {hasDecisionData && expertPct != null ? `${expertBullish ? 'Bullish' : 'Mixed'} (${expertPct.toFixed(0)}%)` : '—'}
                </span>
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
            <div style={{ display: 'flex', gap: '40px', flexWrap: 'wrap', marginTop: '20px', alignItems: 'flex-start' }}>
              {/* Left Column: Consolidated Metrics (approx 38% width) */}
              <div style={{ flex: '1.2 1 360px', minWidth: '320px' }}>
                <h3 className="dt-metrics-section-title" style={{ fontSize: '0.85rem', textTransform: 'uppercase', color: '#64748b', letterSpacing: '0.05em', marginBottom: 16 }}>
                  Consolidated Metrics
                </h3>
                {fundamentalsLoading || (loading && !fundamentalsData?.metrics) ? (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '100px 0', gap: 12 }}>
                    <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                    <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Loading metrics...</span>
                  </div>
                ) : (
                  <div className="dt-consolidated" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px 28px' }}>
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

              {/* Right Column: Financial Performance Graph (approx 62% width) */}
              <div style={{ flex: '2 1 500px', minWidth: '400px', display: 'flex', flexDirection: 'column' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
                  <h3 className="dt-metrics-section-title" style={{ fontSize: '0.85rem', textTransform: 'uppercase', color: '#64748b', letterSpacing: '0.05em', margin: 0 }}>
                    Financial Performance
                  </h3>
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

                <div className="dt-perf-chart-box" style={{ padding: '20px 24px', margin: 0 }}>
                  <div className="dt-perf-chart-inner" style={{ height: '310px' }}>
                    {fundamentalsLoading || (loading && !fundamentalsData?.financials?.[perfPeriod]) ? (
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
          )}
        </section>

      </div>
    </div>
  );
}
