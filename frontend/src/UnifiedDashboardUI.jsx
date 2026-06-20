import React, { useState, useMemo, useId, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TrendingUp, Shield, CircleDollarSign, Wallet, PieChart, Scale, CheckCircle2, ArrowUpRight, HelpCircle, Loader2, Search, Zap, XCircle, ShieldAlert } from 'lucide-react';
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RechartsTooltip, LineChart as ReLineChart, Line, Legend } from 'recharts';
import { API_BASE_URL } from './api';
import { useAnalysisHistory } from './AnalysisContext';
import { useSession } from './SessionContext';
import * as sessionStore from './store/sessionStore';
import { SP500_TICKERS } from './sp500';
import { StaleValue, FreshnessBadge, LastUpdated } from './components/Freshness';
import { formatFreshnessDateTime, cleanSource } from './freshness';
import DashboardScorecardPanel from './components/DashboardScorecardPanel';
import ConsensusValuationPanel from './components/ConsensusValuationPanel';
import FundamentalHealthBanner, { MetricHealthChip } from './components/FundamentalHealthBanner';
import VerdictToneLegend from './components/VerdictToneLegend';
import DebateThreadPanel from './components/debate/DebateThreadPanel';
import DebateVerdictSummary from './components/debate/DebateVerdictSummary';
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

const VERDICT_STEP_LABEL = 'Synthesizing valuation terminal & roadmap';
const FAST_PROGRESS_WEIGHT = 30;
const VERDICT_PROGRESS_WEIGHT = 70;

function formatElapsed(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  if (m <= 0) return `${s}s`;
  return `${m}m ${String(s).padStart(2, '0')}s`;
}

function DtSkeletonTile({ label }) {
  return (
    <div className="dt-q-tile dt-skeleton-tile" aria-hidden="true">
      <div className="dt-q-tile-icon">
        <div className="dt-skeleton-block dt-skeleton-icon" />
      </div>
      <div className="dt-q-tile-body">
        <div className="dt-skeleton-line dt-skeleton-label" />
        {label ? <span className="sr-only">{label}</span> : null}
        <div className="dt-skeleton-line dt-skeleton-value" />
        <div className="dt-skeleton-line dt-skeleton-status" />
      </div>
    </div>
  );
}

function assessmentToneClass(tone) {
  if (tone === 'positive') return 'ok';
  if (tone === 'caution') return 'warn';
  if (tone === 'negative') return 'negative';
  return 'muted';
}

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

function polymarketArcRatio(pct) {
  if (pct == null || Number.isNaN(pct)) return 0.5;
  return Math.max(0.08, Math.min(0.95, pct / 100));
}

function ProvenanceTip({ provenance, label }) {
  if (!provenance) return label;
  const cleanedSrc = cleanSource(provenance.source);
  const parts = [
    cleanedSrc && `Source: ${cleanedSrc}`,
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

/** Pick ticker: URL → running session → loading → recent success → recentAnalyses. */
function resolveDashboardTicker({ urlTicker, sessionActions, analyses, recentAnalyses = [] }) {
  const fromUrl = urlTicker?.trim().toUpperCase();
  if (fromUrl) return fromUrl;

  const runningSession = sessionActions.find(
    (a) => a.type === 'analysis' && a.status === 'running' && a.meta?.ticker,
  );
  if (runningSession?.meta?.ticker) {
    return runningSession.meta.ticker.trim().toUpperCase();
  }

  const loadingTicker = Object.keys(analyses).find(
    (sym) => analyses[sym]?.status === 'loading',
  );
  if (loadingTicker) return loadingTicker;

  const successTicker = Object.keys(analyses).find(
    (sym) => analyses[sym]?.status === 'success'
      && (analyses[sym]?.decisionData || analyses[sym]?.fundamentalsData || analyses[sym]?.traceData),
  );
  if (successTicker) return successTicker;

  const recent = recentAnalyses[0]?.ticker?.trim().toUpperCase();
  if (recent) return recent;

  return '';
}

export default function UnifiedDashboardUI() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { analyses, analyzeTicker: contextAnalyzeTicker, recentAnalyses } = useAnalysisHistory();
  const { actions: sessionActions, hydrated } = useSession();
  const lastAutoTicker = useRef('');
  const [analysisElapsedSec, setAnalysisElapsedSec] = useState(0);
  const analysisStartedAtRef = useRef(null);
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
    metricsFreshness,
    liveSpotData,
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

  useEffect(() => {
    if (!isAnalyzing) {
      analysisStartedAtRef.current = null;
      setAnalysisElapsedSec(0);
      return undefined;
    }
    if (!analysisStartedAtRef.current) {
      analysisStartedAtRef.current = Date.now();
    }
    const tick = () => {
      const started = analysisStartedAtRef.current;
      if (!started) return;
      setAnalysisElapsedSec(Math.floor((Date.now() - started) / 1000));
    };
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [isAnalyzing]);
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

  // Re-bind local ticker when returning from another route without ?ticker=
  useEffect(() => {
    if (!hydrated) return;
    let cancelled = false;
    const fromUrl = searchParams.get('ticker')?.trim().toUpperCase() || '';
    const resolved = resolveDashboardTicker({ urlTicker: fromUrl, sessionActions, analyses, recentAnalyses });
    if (!resolved || cancelled) return;

    if (resolved !== ticker.trim().toUpperCase()) {
      setTicker(resolved);
    }
    if (fromUrl !== resolved) {
      setSearchParams({ ticker: resolved }, { replace: true });
    }
    return () => { cancelled = true; };
  }, [hydrated, sessionActions, analyses, searchParams, setSearchParams, ticker]);

  // Deep-link: /dashboard?ticker=NVDA from Daily Brief or bookmarks
  useEffect(() => {
    if (!hydrated) return;
    const fromUrl = searchParams.get('ticker')?.trim().toUpperCase();
    if (!fromUrl || fromUrl === lastAutoTicker.current) return;

    const row = analyses[fromUrl];
    const alreadyLoading = row?.status === 'loading';
    const alreadySuccess = (
      (row?.status === 'success' && row?.decisionData)
      || recentAnalyses.some(
        (a) => a.ticker?.trim().toUpperCase() === fromUrl && (a.result?.dt || a.result?.trace),
      )
    );
    const runningSession = sessionStore.findAction('analysis', 'ticker', fromUrl);
    const sessionRunning = runningSession?.status === 'running';

    lastAutoTicker.current = fromUrl;

    if (ticker.trim().toUpperCase() !== fromUrl) {
      setTicker(fromUrl);
    }

    if (alreadyLoading || sessionRunning) {
      return;
    }

    if (alreadySuccess) {
      if (!analyses[fromUrl]) {
        contextAnalyzeTicker(fromUrl, false);
      }
      return;
    }

    analyzeTicker(fromUrl);
  }, [searchParams, analyzeTicker, hydrated, analyses, recentAnalyses, ticker, contextAnalyzeTicker]);

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

  const spot = liveSpotData?.price
    ?? decisionData?.spot?.price_usd
    ?? v?.current_price_usd
    ?? fundamentalsData?.company_info?.current_price;
  const spotSource = liveSpotData?.source ?? decisionData?.spot?.source;
  const spotFreshness = liveSpotData?.data_freshness
    ?? decisionData?.data_freshness
    ?? fundamentalsData?.spot_freshness
    ?? fundamentalsData?.data_freshness;
  const spotCapturedLabel = formatFreshnessDateTime(
    spotFreshness?.captured_at ?? decisionData?.spot?.captured_at_utc ?? liveSpotData?.captured_at,
  );
  const reconciliation = decisionData?.reconciliation;
  const embeddedScorecard = decisionData?.scorecard_summary;
  const fundamentalHealth = q?.fundamental_health ?? fundamentalsData?.health?.fundamental_health;
  const financialMetricHealth = fundamentalsData?.health?.metrics ?? {};
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
    { label: VERDICT_STEP_LABEL, done: !decisionLoading },
    { label: 'Scanning prediction market contracts', done: !predMarketsLoading }
  ], [metricsLoading, scorecardLoading, debateLoading, traceLoading, decisionLoading, predMarketsLoading]);

  const progressPct = useMemo(() => {
    const fastSteps = steps.filter((s) => s.label !== VERDICT_STEP_LABEL);
    const fastDone = fastSteps.filter((s) => s.done).length;
    const fastPct = fastSteps.length ? (fastDone / fastSteps.length) * FAST_PROGRESS_WEIGHT : 0;
    const verdictStep = steps.find((s) => s.label === VERDICT_STEP_LABEL);
    let verdictPct = 0;
    if (verdictStep?.done) {
      verdictPct = VERDICT_PROGRESS_WEIGHT;
    } else if (decisionLoading && isAnalyzing) {
      const creep = Math.min(0.92, analysisElapsedSec / 180);
      verdictPct = creep * VERDICT_PROGRESS_WEIGHT;
    }
    const total = fastPct + verdictPct;
    if (verdictStep?.done && fastDone === fastSteps.length) {
      return 100;
    }
    return Math.min(99, Math.round(total));
  }, [steps, decisionLoading, isAnalyzing, analysisElapsedSec]);

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
          Enter a ticker and click Analyze. First load can take up to 3 minutes (swarm, debate, and decision terminal).
        </div>
      )}

      {!isAnalyzing && !error && analysisStatus === 'success' && !hasDecisionData && fundamentalsData?.metrics && (
        <div className="glass-panel" style={{ borderColor: '#f59e0b', padding: '14px 16px', borderRadius: '8px', background: 'rgba(245, 158, 11, 0.08)' }}>
          <p style={{ color: '#f59e0b', margin: 0, display: 'flex', alignItems: 'flex-start', gap: 8, fontSize: '0.85rem', lineHeight: 1.5 }}>
            <ShieldAlert size={18} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              Fundamentals loaded, but the verdict/debate pipeline did not finish
              {debateError ? ` (${debateError})` : ''}.
              Click <strong>Refresh</strong> and wait up to 3 minutes — or retry in a moment if the API was cold.
            </span>
          </p>
        </div>
      )}

      {isAnalyzing && (
        <section className="dt-panel dt-analysis-progress" data-testid="dashboard-analysis-progress">
          <div className="dt-analysis-progress-head">
            <span className="dt-analysis-progress-title">
              {loadingStep || 'Running analysis…'}
              {decisionLoading && (
                <span
                  className="dt-analysis-elapsed"
                  data-testid="dashboard-verdict-elapsed"
                >
                  {' '}
                  · Verdict pipeline running… {formatElapsed(analysisElapsedSec)}
                </span>
              )}
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
          {hasDecisionData && q?.fundamental_health && (
            <FundamentalHealthBanner health={q.fundamental_health} />
          )}
          {decisionLoading ? (
            <div className="dt-quality-3x2" style={{ marginTop: '16px' }} aria-busy="true">
              {['ROIC', 'Moat', 'FCF', 'Debt', 'Margin', 'Current ratio'].map((label) => (
                <DtSkeletonTile key={label} label={label} />
              ))}
            </div>
          ) : (
            <div className="dt-quality-3x2" style={{ marginTop: '16px' }}>
              {(q?.rows || []).map((row) => {
                const IconComp = QUALITY_ICONS[row.id] || TrendingUp;
                const tone = row.assessment_tone
                  ? assessmentToneClass(row.assessment_tone)
                  : 'muted';
                const statusText = row.assessment_label || row.status_label || '—';
                return (
                  <div key={row.id} className="dt-q-tile">
                    <div className="dt-q-tile-icon"><IconComp size={20} strokeWidth={1.6} /></div>
                    <div className="dt-q-tile-body">
                      <div className="dt-q-tile-label"><ProvenanceTip provenance={row.provenance} label={row.label} /></div>
                      <div className="dt-q-tile-value">{row.value_label || '—'}</div>
                      <div
                        className={`dt-q-tile-status dt-q-tile-assessment dt-tone-${tone}`}
                        title={row.assessment_detail || ''}
                      >
                        {statusText}
                      </div>
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
          <div className="dt-chart-breadcrumb">
            STOCKS &gt; US &gt; <span>{searchUpper || '—'}</span>
          </div>
          <div className="dt-company-header">
            <h1 className="dt-company-name">
              {!searchUpper
                ? 'Select a ticker'
                : fundamentalsLoading
                  ? 'Loading...'
                  : (fundamentalsData?.company_info?.company_name || searchUpper)}
            </h1>
            {!fundamentalsLoading && (fundamentalsData?.company_info || spot != null) && (
              <div className="dt-price-display">
                <StaleValue freshness={spotFreshness} priceSensitive>
                  <span className="dt-price-value" data-testid="dashboard-current-price" data-symbol={searchUpper}>${spot != null ? Number(spot).toFixed(2) : '—'}</span>
                  {fundamentalsData?.company_info?.price_change_pct != null && (
                    <span className={`dt-price-badge ${isPricePositive ? 'positive' : 'negative'}`}>
                      {isPricePositive ? '▲' : '▼'} {Math.abs(fundamentalsData.company_info.price_change_pct).toFixed(2)}%
                    </span>
                  )}
                  {fundamentalsData?.company_info?.price_change != null && (
                    <span className="dt-price-change-abs">
                      {isPricePositive ? '+' : ''}{fundamentalsData.company_info.price_change.toFixed(2)}
                      {spotCapturedLabel ? ` · ${spotCapturedLabel}` : ''}
                    </span>
                  )}
                </StaleValue>
                {spotFreshness && (
                  <FreshnessBadge freshness={spotFreshness} />
                )}
                <LastUpdated freshness={spotFreshness} label="Spot" />
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



        {/* 4. CONSOLIDATED METRICS & FINANCIAL PERFORMANCE */}
        <section className="dt-panel dt-area-metrics-perf">
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
            <h2 className="dt-panel-title" style={{ margin: 0 }}>Financial Health &amp; Performance</h2>
            <LastUpdated freshness={fundamentalsData?.data_freshness} label="Fundamentals" />
          </div>
          {fundamentalHealth && (
            <FundamentalHealthBanner
              health={fundamentalHealth}
              testId="financial-health-banner"
            />
          )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 20 }}>
              {/* Row 1: Valuation + Financial Performance */}
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'stretch' }}>
                {/* Valuation Card Column */}
                <div style={{ flex: '0 0 340px', width: 340, boxSizing: 'border-box' }}>
                  {showFundamentalsMetricsLoader ? (
                    <div className="dt-metrics-loading" style={{ height: 290 }}>
                      <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                      <span>Loading metrics...</span>
                    </div>
                  ) : (
                    /* VALUATION CARD */
                    <div className="dt-clean-tile" style={{ height: '100%', boxSizing: 'border-box' }}>
                      <div className="dt-tile-header-clean">
                        <span className="dt-tile-pill-clean pill-blue" />
                        <h3 className="dt-tile-title-clean">Valuation</h3>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Market Cap</span>
                          <span className="dt-metric-value-clean">{fmtUsdCompact(fundamentalsData?.metrics?.valuation?.market_cap)}</span>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">PE Ratio (TTM)</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fundamentalsData?.metrics?.valuation?.trailing_pe?.toFixed(1) || <span className="dt-metric-dash">—</span>}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.trailing_pe} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">PE Ratio (Forward)</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fundamentalsData?.metrics?.valuation?.forward_pe?.toFixed(1) || <span className="dt-metric-dash">—</span>}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.forward_pe} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Price to Sales</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fundamentalsData?.metrics?.valuation?.price_to_sales?.toFixed(2) || <span className="dt-metric-dash">—</span>}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.price_to_sales} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">EV / EBITDA</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fundamentalsData?.metrics?.valuation?.ev_to_ebitda?.toFixed(1) || <span className="dt-metric-dash">—</span>}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.ev_to_ebitda} />
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Financial Performance Chart Column */}
                <div className="dt-metrics-col-chart" style={{ flex: 1, minWidth: 320, boxSizing: 'border-box', marginBottom: 0 }}>
                  <div className="dt-metrics-chart-head">
                    <h3 className="dt-metrics-block-title" style={{ margin: 0 }}>Financial Performance</h3>
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

                  <div className="dt-perf-chart-box" style={{ height: '100%', display: 'flex', flexDirection: 'column', boxSizing: 'border-box', margin: 0 }}>
                    <div className="dt-perf-chart-inner dt-perf-chart-inner-lg" style={{ flex: 1, minHeight: 220 }}>
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

              {/* Row 2: Margins & Growth + Dividends & Returns (Left) + Risk-Reward Scorecard (Right) */}
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'stretch' }}>
                {/* Left Column: Margins & Growth + Dividends & Returns */}
                <div style={{ flex: '0 0 340px', width: 340, display: 'flex', flexDirection: 'column', gap: 20, boxSizing: 'border-box' }}>
                  {/* Margins & Growth Card */}
                  {showFundamentalsMetricsLoader ? (
                    <div className="dt-metrics-loading" style={{ height: 200 }}>
                      <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                      <span>Loading metrics...</span>
                    </div>
                  ) : (
                    <div className="dt-clean-tile" style={{ flex: 1, boxSizing: 'border-box' }}>
                      <div className="dt-tile-header-clean">
                        <span className="dt-tile-pill-clean pill-green" />
                        <h3 className="dt-tile-title-clean">Margins &amp; Growth</h3>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Profit Margin</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.margins_and_growth?.profit_margins != null ? fundamentalsData.metrics.margins_and_growth.profit_margins * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.profit_margin} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Operating Margin</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.margins_and_growth?.operating_margins != null ? fundamentalsData.metrics.margins_and_growth.operating_margins * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.operating_margin} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Earnings Growth YoY</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.margins_and_growth?.earnings_growth_yoy != null ? fundamentalsData.metrics.margins_and_growth.earnings_growth_yoy * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.earnings_growth_yoy} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Revenue Growth YoY</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.margins_and_growth?.revenue_growth_yoy != null ? fundamentalsData.metrics.margins_and_growth.revenue_growth_yoy * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.revenue_growth_yoy} />
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Dividends & Returns Card */}
                  {showFundamentalsMetricsLoader ? (
                    <div className="dt-metrics-loading" style={{ height: 160 }}>
                      <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                      <span>Loading metrics...</span>
                    </div>
                  ) : (
                    <div className="dt-clean-tile" style={{ flex: 1, boxSizing: 'border-box' }}>
                      <div className="dt-tile-header-clean">
                        <span className="dt-tile-pill-clean pill-purple" />
                        <h3 className="dt-tile-title-clean">Dividends &amp; Returns</h3>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Dividend Yield</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.dividend?.dividend_yield != null ? fundamentalsData.metrics.dividend.dividend_yield * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.dividend_yield} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Payout Ratio</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.dividend?.payout_ratio != null ? fundamentalsData.metrics.dividend.payout_ratio * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.payout_ratio} />
                          </div>
                        </div>
                        {metricsData?.owner_earnings?.current && metricsData.owner_earnings.current !== 'N/A' && (
                          <div className="dt-metric-row-clean">
                            <span className="dt-metric-label-clean">Owner Earnings</span>
                            <span className="dt-metric-value-clean">{metricsData.owner_earnings.current}</span>
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </div>

                {/* Right Column: Risk-Reward Scorecard */}
                <div style={{ flex: 1, minWidth: 320, boxSizing: 'border-box' }}>
                  <DashboardScorecardPanel
                    data={scorecardData}
                    embeddedSummary={embeddedScorecard}
                    ticker={searchUpper}
                    loading={scorecardLoading || (isAnalyzing && !scorecardData && !embeddedScorecard)}
                    error={scorecardError}
                  />
                </div>
              </div>

              {/* Row 3: Cash Flow & Balance Hub (Left) + Future Price Roadmap (Right) */}
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'stretch' }}>
                {/* Left Column: Cash Flow & Balance Hub */}
                <div style={{ flex: '0 0 340px', width: 340, boxSizing: 'border-box' }}>
                  {showFundamentalsMetricsLoader ? (
                    <div className="dt-metrics-loading" style={{ height: '100%', minHeight: 250 }}>
                      <Loader2 className="spinner" size={24} color="var(--accent-blue)" />
                      <span>Loading metrics...</span>
                    </div>
                  ) : (
                    <div className="dt-clean-tile" style={{ height: '100%', boxSizing: 'border-box' }}>
                      <div className="dt-tile-header-clean">
                        <span className="dt-tile-pill-clean pill-orange" />
                        <h3 className="dt-tile-title-clean">Cash Flow &amp; Balance</h3>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Free Cash Flow</span>
                          <span className="dt-metric-value-clean">{fmtUsdCompact(fundamentalsData?.metrics?.cash_flow?.free_cash_flow)}</span>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">FCF Yield</span>
                          <div className="dt-metric-value-container-clean">
                            <span className="dt-metric-value-clean">
                              {fmtPct(fundamentalsData?.metrics?.cash_flow?.fcf_yield != null ? fundamentalsData.metrics.cash_flow.fcf_yield * 100 : null)}
                            </span>
                            <MetricHealthChip assessment={financialMetricHealth.fcf_yield} />
                          </div>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">FCF Per Share</span>
                          <span className="dt-metric-value-clean">{fundamentalsData?.metrics?.cash_flow?.fcf_per_share != null ? `$${fundamentalsData.metrics.cash_flow.fcf_per_share.toFixed(2)}` : <span className="dt-metric-dash">—</span>}</span>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Total Cash</span>
                          <span className="dt-metric-value-clean">{fmtUsdCompact(fundamentalsData?.metrics?.balance?.total_cash)}</span>
                        </div>
                        <div className="dt-metric-row-clean">
                          <span className="dt-metric-label-clean">Total Debt</span>
                          <span className="dt-metric-value-clean">{fmtUsdCompact(fundamentalsData?.metrics?.balance?.total_debt)}</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Right Column: Future Price Roadmap */}
                <div style={{ flex: 1, minWidth: 320, boxSizing: 'border-box' }}>
                  <div className="dt-clean-tile" style={{ height: '100%', boxSizing: 'border-box' }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 12 }}>
                      <div className="dt-tile-header-clean" style={{ marginBottom: 0 }}>
                        <span className="dt-tile-pill-clean pill-blue" />
                        <h3 className="dt-tile-title-clean">Future Price Roadmap</h3>
                      </div>
                      {predictedCagrPct != null && (
                        <span style={{ fontSize: '0.78rem', color: '#00ff88', fontWeight: 600 }}>
                          CAGR: {predictedCagrPct > 0 ? '+' : ''}{predictedCagrPct.toFixed(1)}%
                        </span>
                      )}
                    </div>

                    <div className="dt-roadmap-legend-row" style={{ marginTop: 0, marginBottom: 12, display: 'flex', gap: 12, justifyContent: 'flex-start' }}>
                      <span className="dt-roadmap-legend-item" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.72rem', color: '#94a3b8' }}>
                        <span className="dt-roadmap-dot bull" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#00ff88' }} />
                        Bull {scenarioPrices?.bull != null && `($${Number(scenarioPrices.bull).toFixed(0)})`}
                      </span>
                      <span className="dt-roadmap-legend-item" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.72rem', color: '#94a3b8' }}>
                        <span className="dt-roadmap-dot base" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#8b5cf6' }} />
                        Base {scenarioPrices?.base != null && `($${Number(scenarioPrices.base).toFixed(0)})`}
                      </span>
                      <span className="dt-roadmap-legend-item" style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: '0.72rem', color: '#94a3b8' }}>
                        <span className="dt-roadmap-dot bear" style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: '#f87171' }} />
                        Bear {scenarioPrices?.bear != null && `($${Number(scenarioPrices.bear).toFixed(0)})`}
                      </span>
                    </div>

                    <div className="dt-roadmap-chart-sm" style={{ flex: 1, minHeight: 180, width: '100%' }}>
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
              </div>

              {/* Row 4: Verdict & Sentiment Hub (Left) + Consensus Valuation Details (Right) */}
              <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'stretch' }}>
                {/* Left Column: Verdict & Sentiment Hub */}
                <div style={{ flex: '0 0 340px', width: 340, boxSizing: 'border-box' }}>
                  <div className="dt-clean-tile" style={{ height: '100%', boxSizing: 'border-box' }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 12 }}>
                      <div className="dt-tile-header-clean" style={{ marginBottom: 0 }}>
                        <span className="dt-tile-pill-clean pill-green" />
                        <h3 className="dt-tile-title-clean">Verdict &amp; Sentiment Hub</h3>
                      </div>
                      <LastUpdated
                        freshness={
                          decisionData?.verdict_captured_at_utc
                            ? { captured_at: decisionData.verdict_captured_at_utc, policy_max_age_s: 86400 }
                            : decisionData?.data_freshness
                        }
                        label={decisionData?.verdict_from_cache ? 'Verdict (cached)' : 'Verdict'}
                      />
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      <div className="dt-verdict-row">
                        <span className="dt-verdict-row-label">Prediction Markets</span>
                        {decisionLoading || predMarketsLoading ? (
                          <Loader2 className="spinner" size={16} />
                        ) : (
                          <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#ffffff', textAlign: 'right', maxWidth: '58%' }}>
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
                              <span style={{ fontSize: '0.72rem', color: 'var(--dt-muted)', fontWeight: 600 }}>
                                Confidence: {socialConfPct}%
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
                      
                      <div className="dt-aggregate-card" style={{ marginTop: 4 }}>
                        <div className="dt-aggregate-card-title">Aggregate Verdict</div>
                        {decisionLoading ? (
                          <div
                            className="dt-verdict-pipeline-wait"
                            data-testid="dashboard-verdict-pipeline-wait"
                          >
                            <Loader2 className="spinner" size={14} />
                            <span>Verdict pipeline running… {formatElapsed(analysisElapsedSec)}</span>
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
                            padding: '10px 12px',
                            background: 'rgba(245, 158, 11, 0.08)',
                            border: '1px solid rgba(245, 158, 11, 0.25)',
                            borderRadius: 8,
                            fontSize: '0.75rem',
                            lineHeight: 1.4,
                            color: '#e2e8f0',
                          }}
                          data-testid="reconciliation-banner"
                        >
                          {reconciliation.reconciliation_note}
                        </div>
                      )}

                      <VerdictToneLegend />
                    </div>
                  </div>
                </div>

                {/* Right Column: Consensus Valuation Details */}
                <div style={{ flex: 1, minWidth: 320, boxSizing: 'border-box' }}>
                  <section className="dt-clean-tile dt-area-valuation" style={{ margin: 0 }}>
                    <div className="dt-tile-header-clean">
                      <span className="dt-tile-pill-clean pill-purple" />
                      <h3 className="dt-tile-title-clean">Consensus Valuation Details</h3>
                    </div>
                    <ConsensusValuationPanel
                      valuation={v}
                      hasData={hasDecisionData}
                      loading={decisionLoading}
                      ticker={searchUpper}
                      loadingFallback={
                        <div className="dt-metrics-loading" style={{ minHeight: 120 }}>
                          <Loader2 className="spinner" size={22} />
                        </div>
                      }
                    />
                  </section>
                </div>
              </div>
            </div>
        </section>

      </div>

      {(debateLoading || debateData || isAnalyzing) && (
        <section className="dt-panel dt-area-debate" data-testid="dashboard-debate-panel">
          <h2 className="dt-panel-title" style={{ fontSize: '0.78rem' }}>Investment Committee Debate</h2>
          {debateError && (
            <p style={{ color: 'var(--accent-red)', fontSize: '0.85rem', marginTop: 12 }}>{debateError}</p>
          )}
          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', marginTop: 14 }}>
            <div style={{ flex: '0 0 350px', width: 350, boxSizing: 'border-box' }}>
              <DebateVerdictSummary
                result={debateData}
                loading={debateLoading || (isAnalyzing && !debateData)}
              />
            </div>
            <div style={{ flex: 1, minWidth: 320, boxSizing: 'border-box' }}>
              <DebateThreadPanel result={debateData} loading={debateLoading || (isAnalyzing && !debateData)} />
            </div>
          </div>
        </section>
      )}

    </div>
  );
}
