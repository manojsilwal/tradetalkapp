import React, { useState, useMemo, useId, useEffect, useCallback, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TrendingUp, Shield, CircleDollarSign, Wallet, PieChart, Scale, CheckCircle2, ArrowUpRight, HelpCircle, Loader2, Search, Zap, CheckCircle, BarChart3, TrendingDown, Target, Activity, ShieldAlert, XCircle } from 'lucide-react';
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RechartsTooltip, LineChart as ReLineChart, Line, Legend } from 'recharts';
import { API_BASE_URL, apiFetch } from './api';
import { useAnalysisHistory } from './AnalysisContext';
import { SP500_TICKERS } from './sp500';
import DashboardScorecardPanel from './components/DashboardScorecardPanel';
import DebateThreadPanel from './components/debate/DebateThreadPanel';
import DebateVerdictSummary from './components/debate/DebateVerdictSummary';
import './DecisionTerminalUI.css';
import { buildRoadmapChartData, roadmapScenarioPrices } from './roadmapChartData';

// From Decision Terminal
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

const SMALL_CAP_BUCKETS = new Set(['Small Cap', 'Micro Cap']);

function smallCapScoreColor(score) {
  if (score === 'green') return '#00ff88';
  if (score === 'red') return '#f87171';
  return '#eab308';
}

function fmtUsdCompact(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function fmtPct(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  return `${Number(value).toFixed(1)}%`;
}

function RevenueStreamsSection({ streams }) {
  if (!streams?.length) return null;
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
        Revenue Streams (5-Year History)
      </div>
      {streams.map(stream => (
        <div key={stream.name} style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
            <span style={{ fontWeight: 700, color: '#f8fafc' }}>{stream.name}</span>
            {stream.latest_share_pct != null && (
              <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                ~{fmtPct(stream.latest_share_pct)} of revenue
              </span>
            )}
            {stream.source && (
              <span style={{ fontSize: '0.68rem', color: '#64748b', fontStyle: 'italic' }}>
                Source: {stream.source}
              </span>
            )}
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem', minWidth: 520 }}>
              <thead>
                <tr style={{ color: '#94a3b8', background: 'rgba(15,23,42,0.45)' }}>
                  {['Year', 'Revenue', 'Gross Margin', 'Operating Margin'].map(h => (
                    <th key={h} style={{ padding: '7px 10px', textAlign: h === 'Year' ? 'left' : 'right', borderBottom: '1px solid rgba(148,163,184,0.15)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(stream.years || []).map(row => (
                  <tr key={`${stream.name}-${row.year}`} style={{ borderBottom: '1px solid rgba(148,163,184,0.08)' }}>
                    <td style={{ padding: '7px 10px', fontWeight: 600 }}>{row.year}</td>
                    <td style={{ padding: '7px 10px', textAlign: 'right' }}>{fmtUsdCompact(row.revenue_usd)}</td>
                    <td style={{ padding: '7px 10px', textAlign: 'right' }}>{fmtPct(row.gross_margin_pct)}</td>
                    <td style={{ padding: '7px 10px', textAlign: 'right' }}>{fmtPct(row.operating_margin_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </div>
  );
}

function MajorDealsSection({ deals }) {
  if (!deals?.length) return null;
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
        Major Enterprise Deals
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
        {deals.map((deal, idx) => (
          <div
            key={`${deal.partner}-${idx}`}
            style={{
              padding: '12px 14px',
              borderRadius: 10,
              background: 'rgba(255,255,255,0.03)',
              border: '1px solid rgba(255,255,255,0.08)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
              <span style={{ fontWeight: 700, color: '#e2e8f0' }}>{deal.partner}</span>
              <span style={{ fontSize: '0.72rem', color: '#a78bfa', whiteSpace: 'nowrap' }}>{deal.deal_type}</span>
            </div>
            <div style={{ fontSize: '0.85rem', fontWeight: 700, color: '#facc15', marginBottom: 6 }}>
              {deal.amount_label || fmtUsdCompact(deal.amount_usd)}
              {deal.year ? ` · ${deal.year}` : ''}
            </div>
            {deal.summary && (
              <div style={{ fontSize: '0.8rem', color: '#cbd5e1', lineHeight: 1.45, marginBottom: 6 }}>{deal.summary}</div>
            )}
            {deal.predictability_note && (
              <div style={{ fontSize: '0.75rem', color: '#94a3b8', lineHeight: 1.45 }}>
                <span style={{ color: '#64748b' }}>Visibility: </span>
                {deal.predictability_note}
              </div>
            )}
            {deal.source && (
              <div style={{ fontSize: '0.68rem', color: '#64748b', marginTop: 6, fontStyle: 'italic' }}>
                Source: {deal.source}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SmallCapSignalCard({ signal }) {
  const [expanded, setExpanded] = useState(false);
  const color = smallCapScoreColor(signal?.score);

  return (
    <div
      style={{
        padding: '14px 16px',
        borderRadius: 12,
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.08)',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        minHeight: 120,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            background: color,
            boxShadow: `0 0 8px ${color}55`,
            flexShrink: 0,
          }}
          aria-hidden
        />
        <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#e2e8f0', letterSpacing: '0.02em' }}>
          {signal?.label}
        </div>
      </div>
      <div style={{ fontSize: '0.88rem', fontWeight: 600, lineHeight: 1.45, color: '#f8fafc' }}>
        {signal?.headline}
      </div>
      {expanded && (
        <div style={{ fontSize: '0.8rem', lineHeight: 1.55, color: 'var(--text-muted)' }}>
          {signal?.detail}
        </div>
      )}
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        style={{
          alignSelf: 'flex-start',
          marginTop: 'auto',
          background: 'transparent',
          border: 'none',
          color: '#94a3b8',
          fontSize: '0.72rem',
          cursor: 'pointer',
          padding: 0,
        }}
      >
        {expanded ? 'Hide detail' : 'Show detail'}
      </button>
    </div>
  );
}

function SmallCapPanel({ data, loading, capBucket }) {
  if (loading) {
    return (
      <section className="dt-panel" style={{ gridColumn: '1 / -1' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: '0.9rem' }}>
          <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} />
          Running growth-stage assessment…
        </div>
      </section>
    );
  }
  if (!data?.signals?.length) return null;

  const verdictColor = data.overall_verdict === 'Compelling'
    ? '#00ff88'
    : data.overall_verdict === 'Avoid'
      ? '#f87171'
      : '#eab308';

  return (
    <section className="dt-panel" style={{ gridColumn: '1 / -1' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
        <h2 className="dt-panel-title" style={{ margin: 0 }}>Growth-Stage Assessment</h2>
        <span style={{ fontSize: '0.72rem', padding: '2px 8px', borderRadius: 6, background: 'rgba(234,179,8,0.12)', color: '#facc15', fontWeight: 600 }}>
          {capBucket || data.cap_bucket}
        </span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          Standard valuation metrics not applicable — growth-stage framework applied
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))',
          gap: 14,
          marginTop: 14,
        }}
      >
        {data.signals.map(signal => (
          <SmallCapSignalCard key={signal.label} signal={signal} />
        ))}
      </div>

      <RevenueStreamsSection streams={data.revenue_streams} />
      <MajorDealsSection deals={data.major_deals} />

      <div
        style={{
          marginTop: 18,
          padding: '14px 16px',
          borderRadius: 12,
          background: 'rgba(255,255,255,0.04)',
          border: `1px solid ${verdictColor}33`,
        }}
      >
        <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Overall Verdict
        </div>
        <div style={{ fontSize: '1.15rem', fontWeight: 800, color: verdictColor, marginTop: 6 }}>
          {data.overall_verdict}
        </div>
        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: 8, lineHeight: 1.55 }}>
          {data.overall_rationale}
        </div>
      </div>
    </section>
  );
}

export default function UnifiedDashboardUI() {
  const [searchParams, setSearchParams] = useSearchParams();
  const lastAutoTicker = useRef('');
  const [ticker, setTicker] = useState(() => searchParams.get('ticker')?.trim().toUpperCase() || 'AAPL');

  // From Consumer UI
  // Per-section state — each section loads independently
  const [traceData, setTraceData] = useState(null);
  const [traceLoading, setTraceLoading] = useState(false);

  const [metricsData, setMetricsData] = useState(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [capBucket, setCapBucket] = useState(null);

  const [smallCapData, setSmallCapData] = useState(null);
  const [smallCapLoading, setSmallCapLoading] = useState(false);

  const [debateData, setDebateData] = useState(null);
  const [debateLoading, setDebateLoading] = useState(false);
  const [debateError, setDebateError] = useState(null);

  const [decisionData, setDecisionData] = useState(null);
  const [decisionLoading, setDecisionLoading] = useState(false);

  const [scorecardData, setScorecardData] = useState(null);
  const [scorecardError, setScorecardError] = useState(null);
  const [scorecardLoading, setScorecardLoading] = useState(false);

  const [predMarketsData, setPredMarketsData] = useState(null);
  const [predMarketsLoading, setPredMarketsLoading] = useState(false);

  // Global loading: true only until at least ONE section resolves (optimistic)
  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState('');
  const [error, setError] = useState(null);

  const { addAnalysis } = useAnalysisHistory();

  const searchUpper = ticker.trim().toUpperCase();
  const isInSp500 = !!searchUpper && SP500_TICKERS.includes(searchUpper);
  const suggestions = useMemo(() => {
    if (!searchUpper || isInSp500) return [];
    return SP500_TICKERS.filter(t => t.startsWith(searchUpper) || t.includes(searchUpper)).slice(0, 4);
  }, [searchUpper, isInSp500]);

  const analyzeTicker = useCallback(async (overrideTicker = ticker) => {
    const sym = (overrideTicker ?? ticker).trim().toUpperCase();
    if (!sym) {
      setError('Enter a ticker symbol to analyze.');
      return;
    }
    setTicker(sym);
    setSearchParams({ ticker: sym }, { replace: true });

    // Reset all per-section state
    setLoading(true);
    setError(null);
    setLoadingStep('Validating symbol…');
    setTraceData(null); setTraceLoading(true);
    setMetricsData(null); setMetricsLoading(true); setCapBucket(null);
    setSmallCapData(null); setSmallCapLoading(false);
    setDebateData(null); setDebateLoading(true); setDebateError(null);
    setDecisionData(null); setDecisionLoading(true);
    setScorecardData(null); setScorecardLoading(true); setScorecardError(null);
    setPredMarketsData(null); setPredMarketsLoading(true);

    let validationFailed = false;

    // Symbol validation first (fast)
    try {
      const probe = await apiFetch(`${API_BASE_URL}/metrics/validate/${encodeURIComponent(sym)}`).catch(() => null);
      const probeSoftFail = probe?.reason === 'probe_timeout' || probe?.reason === 'probe_failed';
      if (probe && probe.exists === false && !probeSoftFail) {
        const msg = probe.reason === 'invalid_format'
          ? `Ticker "${sym}" looks invalid. Check the symbol format and try again.`
          : `Could not find a market quote for "${sym}". Check the symbol and try again.`;
        setError(msg);
        validationFailed = true;
      }
    } catch (_) { /* continue */ }

    if (validationFailed) {
      setLoading(false);
      setLoadingStep('');
      setTraceLoading(false); setMetricsLoading(false); setDebateLoading(false);
      setDecisionLoading(false); setScorecardLoading(false); setPredMarketsLoading(false);
      return;
    }

    setLoadingStep('Loading data…');

    let firstResolved = false;
    let successCount = 0;
    let lastErr = null;
    const onFirstResolved = () => {
      if (!firstResolved) {
        firstResolved = true;
        setLoading(false);
        setLoadingStep('');
      }
    };
    const onSuccess = () => {
      successCount += 1;
      onFirstResolved();
    };
    const onFail = (err) => {
      if (err) lastErr = err;
    };
    const whenAllSettled = () => {
      setLoading(false);
      setLoadingStep('');
      if (successCount === 0) {
        const msg = lastErr?.message || String(lastErr || '');
        if (/failed to fetch|network|load failed/i.test(msg)) {
          setError(
            `Cannot reach the API at ${API_BASE_URL}. Check VITE_API_BASE_URL (Vercel) and that the backend allows your origin (CORS).`,
          );
        } else {
          setError(msg || 'Analysis failed — all API requests returned errors.');
        }
      }
    };

    const jobs = [
      apiFetch(`${API_BASE_URL}/metrics/${sym}`)
        .then((res) => {
          setMetricsData(res?.metrics ?? null);
          setCapBucket(res?.cap_bucket ?? null);
          onSuccess();
        })
        .catch((err) => { onFail(err); setMetricsData(null); setCapBucket(null); })
        .finally(() => setMetricsLoading(false)),
      apiFetch(`${API_BASE_URL}/prediction-markets?ticker=${sym}`)
        .then((res) => { setPredMarketsData(res); onSuccess(); })
        .catch((err) => { onFail(err); setPredMarketsData(null); })
        .finally(() => setPredMarketsLoading(false)),
      apiFetch(`${API_BASE_URL}/trace?ticker=${sym}`)
        .then((res) => { setTraceData(res); onSuccess(); })
        .catch((err) => { onFail(err); setTraceData(null); })
        .finally(() => setTraceLoading(false)),
      apiFetch(`${API_BASE_URL}/debate?ticker=${sym}`)
        .then((res) => { setDebateData(res); setDebateError(null); onSuccess(); })
        .catch((err) => {
          onFail(err);
          setDebateError('Debate temporarily unavailable.');
          setDebateData(null);
        })
        .finally(() => setDebateLoading(false)),
      apiFetch(`${API_BASE_URL}/decision-terminal?ticker=${sym}`)
        .then((res) => { setDecisionData(res); onSuccess(); })
        .catch((err) => { onFail(err); setDecisionData(null); })
        .finally(() => setDecisionLoading(false)),
      apiFetch(`${API_BASE_URL}/scorecard/${encodeURIComponent(sym)}?preset=balanced`)
        .then((res) => { setScorecardData(res); setScorecardError(null); onSuccess(); })
        .catch((err) => {
          onFail(err);
          setScorecardError(err?.message || 'Scorecard unavailable');
          setScorecardData(null);
        })
        .finally(() => setScorecardLoading(false)),
    ];

    Promise.allSettled(jobs).then(whenAllSettled);
  }, [ticker, setSearchParams]);

  // Deep-link: /?ticker=NVDA from Daily Brief or bookmarks
  useEffect(() => {
    const fromUrl = searchParams.get('ticker')?.trim().toUpperCase();
    if (!fromUrl || fromUrl === lastAutoTicker.current) return;
    lastAutoTicker.current = fromUrl;
    analyzeTicker(fromUrl);
  }, [searchParams, analyzeTicker]);

  // Add to history once decision data arrives
  useEffect(() => {
    if (decisionData && searchUpper) {
      addAnalysis(searchUpper, {
        trace: traceData,
        debate: debateData,
        metrics: metricsData,
        dt: decisionData,
        scorecard: scorecardData,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decisionData]);

  useEffect(() => {
    if (!searchUpper || !capBucket || !SMALL_CAP_BUCKETS.has(capBucket)) {
      setSmallCapData(null);
      setSmallCapLoading(false);
      return undefined;
    }

    let cancelled = false;
    setSmallCapLoading(true);
    setSmallCapData(null);

    apiFetch(`${API_BASE_URL}/small-cap-assessment/${encodeURIComponent(searchUpper)}`)
      .then(res => {
        if (!cancelled) setSmallCapData(res);
      })
      .catch(() => {
        if (!cancelled) setSmallCapData(null);
      })
      .finally(() => {
        if (!cancelled) setSmallCapLoading(false);
      });

    return () => { cancelled = true; };
  }, [searchUpper, capBucket]);

  // Decision Terminal Extracted Variables
  const hasDecisionData = decisionData != null;
  const v = decisionData?.valuation;
  const q = decisionData?.quality;
  const z = decisionData?.verdict;
  const r = decisionData?.roadmap;

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

  const spot = v?.current_price_usd;
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

  const getRationale = (factorKey) => {
    if (!traceData?.factors?.[factorKey]) return 'Awaiting Scan...';
    const factorData = traceData.factors[factorKey];
    const history = Array.isArray(factorData.history) ? factorData.history : [];
    if (history.length < 2) return factorData.rationale || 'No trace found.';
    const snippet = history[history.length - 2]?.content;
    if (!snippet) return factorData.rationale || 'No trace found.';
    return snippet.length > 110 ? `${snippet.substring(0, 110)}...` : snippet;
  };

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

  return (
    <div className="dt-wrap fade-in" style={{ maxWidth: '1400px', margin: '0 auto', padding: '0 20px', display: 'flex', flexDirection: 'column', gap: '20px' }}>

      {/* Search Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
         <div className="title-group">
            <h2 style={{ fontSize: '1.8rem', fontWeight: 700, margin: '0 0 5px 0' }}>Unified Dashboard</h2>
            <p style={{ color: 'var(--text-muted)', margin: 0 }}>Real-time Swarm Analysis & Valuation Hub</p>
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
              disabled={loading || !searchUpper}
              style={{ padding: '10px 20px', borderRadius: '8px', border: 'none', background: 'var(--accent-blue)', color: 'white', fontWeight: 600, cursor: loading || !searchUpper ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', gap: 6, opacity: loading || !searchUpper ? 0.55 : 1 }}
            >
              {loading ? <Loader2 className="spinner" size={16} /> : <Search size={16} />}
              Analyze
            </button>
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

      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '12px 0', color: '#94a3b8', fontSize: 13 }}>
          <Loader2 size={18} style={{ animation: 'spin 1s linear infinite', color: '#3b82f6', flexShrink: 0 }} />
          {loadingStep || 'Loading…'}
        </div>
      )}

      {error && (
        <div className="glass-panel" style={{ borderColor: 'var(--accent-red)', padding: '16px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.1)' }}>
          <p style={{ color: 'var(--accent-red)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}><ShieldAlert size={18} /> {error}</p>
        </div>
      )}

      {!loading && !error && !hasDecisionData && !traceData && (
        <div className="dt-prompt-banner glass-panel" style={{ padding: '16px', marginBottom: 4, color: '#94a3b8', fontSize: '0.9rem' }}>
          Enter a ticker and click Analyze. First load can take up to a minute (swarm, debate, and decision terminal).
        </div>
      )}

      {/* Main Content Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '20px' }}>

        {/* Quality Scorecard */}
        <section className="dt-panel" style={{ gridColumn: 'span 1' }}>
          <h2 className="dt-panel-title">Business Quality Scorecard</h2>
          {decisionLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', marginTop: 14, fontSize: '0.9rem' }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} /> Loading quality data…
            </div>
          )}
          <div className="dt-quality-3x2" style={{ marginTop: '16px' }}>
            {(q?.rows || []).map((row) => {
              const IconComp = QUALITY_ICONS[row.id] || TrendingUp;
              const st = (row.status_label || '').toLowerCase();
              const tone = st.includes('good') && !st.includes('strong') ? 'warn' : ['excellent', 'strong', 'robust', 'low', 'high'].some((k) => st.includes(k)) ? 'ok' : 'muted';
              return (
                <div key={row.id} className="dt-q-tile">
                  <div className="dt-q-tile-icon"><IconComp size={22} strokeWidth={1.6} /></div>
                  <div className="dt-q-tile-body">
                    <div className="dt-q-tile-label"><ProvenanceTip provenance={row.provenance} label={row.label} /></div>
                    <div className="dt-q-tile-value">{row.value_label || '—'}</div>
                    <div className={`dt-q-tile-status dt-tone-${tone}`}>{row.status_label || '—'}</div>
                  </div>
                </div>
              );
            })}
            {!hasDecisionData &&
              ['ROIC', 'Moat', 'FCF', 'Debt', 'Margin', 'Current ratio'].map((label) => (
                <div key={label} className="dt-q-tile dt-q-tile-empty">
                  <div className="dt-q-tile-icon muted"><TrendingUp size={22} /></div>
                  <div className="dt-q-tile-body">
                    <div className="dt-q-tile-label">{label}</div>
                    <div className="dt-q-tile-value">—</div>
                    <div className="dt-q-tile-status dt-tone-muted">—</div>
                  </div>
                </div>
              ))}
          </div>
        </section>

        {/* Verdict & Sentiment Hub */}
        <section className="dt-panel" style={{ gridColumn: 'span 1' }}>
          <h2 className="dt-panel-title">Verdict & Sentiment Hub</h2>
          <div style={{ display: 'flex', gap: '24px', marginTop: '16px', alignItems: 'flex-start', flexWrap: 'wrap' }}>

            {/* Social gauge */}
            <div style={{ textAlign: 'center', minWidth: 120 }}>
              <div className="dt-subblock-title" style={{ marginBottom: 10 }}>Social Sentiment</div>
              {traceLoading
                ? <Loader2 size={28} style={{ animation: 'spin 1s linear infinite', color: '#3b82f6', margin: '12px auto', display: 'block' }} />
                : (
                  <div className="dt-pm-gauge-wrap" style={{ position: 'relative', display: 'inline-block' }}>
                    <SemiGauge fillRatio={socialFill} size="small" />
                    <div className="dt-pm-label" style={{ position: 'absolute', bottom: '8px', left: '50%', transform: 'translateX(-50%)', fontWeight: 600, whiteSpace: 'nowrap' }}>
                      {socialConfPct != null
                        ? socialBullish ? `${socialConfPct}% Bullish` : `${100 - socialConfPct}% Mixed`
                        : '—'}
                    </div>
                  </div>
                )
              }
            </div>

            {/* Verdict col */}
            <div style={{ flex: 1, minWidth: 160 }}>
              <div className="dt-subblock-title">Expert Consensus</div>
              {decisionLoading
                ? <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: 8 }}><Loader2 size={16} style={{ animation: 'spin 1s linear infinite', marginRight: 6, verticalAlign: 'middle' }} />Analyzing…</div>
                : (
                  <div className={`dt-expert-pill ${expertBullish ? 'bull' : 'neutral'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 16, background: expertBullish ? 'rgba(0,255,136,0.1)' : 'rgba(255,255,255,0.05)', color: expertBullish ? '#00ff88' : '#fff', fontWeight: 600, fontSize: 14, marginTop: 8 }}>
                    <ArrowUpRight size={18} />
                    <span>{hasDecisionData && expertPct != null ? `${expertBullish ? 'Bullish' : 'Mixed'} — ${expertPct.toFixed(0)}%` : '—'}</span>
                  </div>
                )
              }
              <div className="dt-subblock-title" style={{ marginTop: 16 }}>Aggregate Verdict</div>
              {decisionLoading
                ? <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: 4 }}>—</div>
                : (
                  <div className={`dt-aggregate ${verdictTone(z?.headline_verdict || verdict)}`} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: '1.2rem', fontWeight: 700, marginTop: 6 }}>
                    <CheckCircle2 size={24} />
                    <span>{hasDecisionData ? (z?.headline_verdict || verdict).toUpperCase() : '—'}</span>
                  </div>
                )
              }
            </div>
          </div>
        </section>

        {(smallCapLoading || smallCapData) && (
          <SmallCapPanel data={smallCapData} loading={smallCapLoading} capBucket={capBucket} />
        )}

        {/* Prediction Markets */}
        <section className="dt-panel" style={{ gridColumn: '1 / -1' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
            <h2 className="dt-panel-title" style={{ margin: 0 }}>Prediction Markets</h2>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Polymarket · Kalshi</span>
            {predMarketsData?.context?.sector && (
              <span style={{ fontSize: '0.72rem', padding: '2px 8px', borderRadius: 6, background: 'rgba(99,102,241,0.12)', color: '#818cf8' }}>
                {predMarketsData.context.sector} · {(predMarketsData.context.indices || []).join(' · ')}
              </span>
            )}
          </div>

          {predMarketsLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', marginTop: 14, fontSize: '0.9rem' }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} /> Fetching live prediction markets…
            </div>
          )}

          {!predMarketsLoading && predMarketsData?.has_relevant_data && (() => {
            const directEvts = (predMarketsData.events || []).filter(e => e.relevance_type !== 'sector');
            const sectorEvts = (predMarketsData.events || []).filter(e => e.relevance_type === 'sector');

            const EventRow = ({ ev }) => {
              const prob = ev.probability != null ? Math.round(ev.probability * 100) : null;
              const bullColor = prob != null ? (prob >= 60 ? '#00ff88' : prob >= 40 ? '#eab308' : '#f87171') : '#94a3b8';
              return (
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px', borderRadius: 10, background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.07)' }}>
                  <span style={{ fontSize: '0.68rem', padding: '2px 7px', borderRadius: 5, background: ev.source === 'Kalshi' ? 'rgba(59,130,246,0.15)' : 'rgba(124,58,237,0.15)', color: ev.source === 'Kalshi' ? '#60a5fa' : '#a78bfa', fontWeight: 600, whiteSpace: 'nowrap', flexShrink: 0 }}>
                    {ev.source}
                  </span>
                  <div style={{ flex: 1, fontSize: '0.875rem', lineHeight: 1.4 }}>
                    {ev.market_question || ev.title}
                  </div>
                  {prob != null && (
                    <div style={{ textAlign: 'right', minWidth: 56, flexShrink: 0 }}>
                      <div style={{ fontSize: '1.15rem', fontWeight: 800, fontFamily: 'monospace', color: bullColor }}>{prob}%</div>
                      <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)' }}>Yes</div>
                    </div>
                  )}
                  {ev.volume > 0 && (
                    <div style={{ textAlign: 'right', minWidth: 56, fontSize: '0.75rem', color: 'var(--text-muted)', flexShrink: 0 }}>
                      ${ev.volume >= 1000 ? `${(ev.volume/1000).toFixed(1)}K` : ev.volume.toFixed(0)} vol
                    </div>
                  )}
                  {ev.url && (
                    <a href={ev.url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--text-muted)', flexShrink: 0 }} title="Open on market">
                      <BarChart3 size={14} />
                    </a>
                  )}
                </div>
              );
            };

            return (
              <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 14 }}>
                {directEvts.length > 0 && (
                  <div>
                    <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
                      {searchUpper}-specific bets
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {directEvts.map((ev, i) => <EventRow key={`d-${i}`} ev={ev} />)}
                    </div>
                  </div>
                )}
                {sectorEvts.length > 0 && (
                  <div>
                    <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
                      Sector / Index context ({(predMarketsData.context?.indices || []).join(', ')})
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {sectorEvts.map((ev, i) => <EventRow key={`s-${i}`} ev={ev} />)}
                    </div>
                  </div>
                )}
                <p style={{ color: 'var(--text-muted)', fontSize: '0.72rem', margin: '2px 0 0' }}>
                  Crowd-sourced probabilities — not financial advice. Sources: Polymarket, Kalshi public APIs.
                </p>
              </div>
            );
          })()}

          {!predMarketsLoading && !predMarketsData?.has_relevant_data && searchUpper && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: 12 }}>
              No active prediction markets found for {searchUpper} on Polymarket or Kalshi.
            </p>
          )}
          {!predMarketsLoading && !searchUpper && (
            <p style={{ color: 'var(--text-muted)', fontSize: '0.9rem', marginTop: 12 }}>Run Analyze to load prediction markets.</p>
          )}
        </section>

        <DashboardScorecardPanel
          data={scorecardData}
          ticker={searchUpper}
          loading={scorecardLoading}
          error={scorecardError}
        />

        {/* Future Price Roadmap */}
        <section className="dt-panel" style={{ gridColumn: '1 / -1' }}>
          <h2 className="dt-panel-title">Future Price Roadmap (3-Year Trajectory)</h2>
          {decisionLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', marginTop: 14, fontSize: '0.9rem' }}>
              <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} /> Building scenario paths…
            </div>
          )}
          <div style={{ marginTop: '16px', height: '300px' }}>
            <div className="dt-roadmap-head" style={{ display: 'flex', gap: '16px', marginBottom: '16px', flexWrap: 'wrap', alignItems: 'center' }}>
              <span className="dt-roadmap-legend"><span className="dot bull" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#00ff88', marginRight: 6 }} /> Bull {scenarioPrices?.bull != null && ` ($${Number(scenarioPrices.bull).toFixed(0)})`}</span>
              <span className="dt-roadmap-legend"><span className="dot base" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#38bdf8', marginRight: 6 }} /> Base {scenarioPrices?.base != null && ` ($${Number(scenarioPrices.base).toFixed(0)})`}</span>
              <span className="dt-roadmap-legend"><span className="dot bear" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#f87171', marginRight: 6 }} /> Bear {scenarioPrices?.bear != null && ` ($${Number(scenarioPrices.bear).toFixed(0)})`}</span>
              {hasDecisionData && spot != null && (
                <span
                  data-testid="dashboard-current-price"
                  data-symbol={decisionData?.ticker || searchUpper}
                  style={{ fontSize: '12px', fontWeight: 600, color: '#fff', marginLeft: 'auto' }}
                >
                  Current price: ${Number(spot).toFixed(2)}
                </span>
              )}
            </div>
            {hasDecisionData && predictedCagrPct != null && (
              <div className="dt-cagr-chip" style={{ display: 'inline-block', padding: '4px 8px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', fontSize: '12px', marginBottom: '16px' }}>Predicted CAGR: {predictedCagrPct}%</div>
            )}
            <div className="dt-chart-box" style={{ height: 'calc(100% - 60px)', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', padding: '16px' }}>
              {roadmapChartData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ReLineChart data={roadmapChartData} margin={{ top: 16, right: 12, left: 4, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis dataKey="t" tick={{ fill: '#8ba0b5', fontSize: 11 }} axisLine={{ stroke: 'rgba(148,163,184,0.2)' }} tickLine={false} />
                    <YAxis tick={{ fill: '#8ba0b5', fontSize: 10 }} axisLine={false} tickLine={false} domain={['auto', 'auto']} tickFormatter={(x) => `$${Math.round(x)}`} />
                    <RechartsTooltip content={chartTooltip} />
                    <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8, color: '#cbd5e1' }} />
                    <Line type="monotone" dataKey="bull" name="Bull case" stroke="#00ff88" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
                    <Line type="monotone" dataKey="base" name="Base case" stroke="#38bdf8" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
                    <Line type="monotone" dataKey="bear" name="Bear case" stroke="#f87171" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
                  </ReLineChart>
                </ResponsiveContainer>
              ) : (
                <div className="dt-chart-empty" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#64748b', fontSize: '14px' }}>Run analysis to load scenario paths</div>
              )}
            </div>
          </div>
        </section>

        {/* Consumer Multi-Factor Details (Trendboards & Signals) */}
        {(traceLoading || traceData) && (
          <section className="dt-panel" style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <h2 className="dt-panel-title">Multi-Factor Analysis Signals</h2>
            {traceLoading && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--text-muted)', fontSize: '0.9rem' }}>
                <Loader2 size={18} style={{ animation: 'spin 1s linear infinite' }} /> Running swarm analysis…
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px' }}>

              {/* Factor Signals using FactorSignalCard style simplified */}
              {Object.entries(traceData.factors || {}).map(([key, factorData], idx) => {
                 const signal = Number(factorData?.trading_signal ?? 0);
                 const sentiment = signal > 0 ? 'bullish' : signal < 0 ? 'bearish' : 'neutral';
                 const titleText = sentiment === 'bullish'
                   ? 'Bullish Signal'
                   : sentiment === 'bearish'
                     ? 'Bearish Signal'
                     : 'No Market Signal';
                 const titleColor = sentiment === 'bullish'
                   ? '#00ff88'
                   : sentiment === 'bearish'
                     ? '#f87171'
                     : '#94a3b8';
                 return (
                   <div key={key} data-testid={`dashboard-factor-${key}`} style={{ padding: '20px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '12px' }}>
                     <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                       <span style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, color: '#94a3b8' }}>
                         {key.replace('_', ' ')}
                       </span>
                       {sentiment === 'bullish'
                         ? <CheckCircle2 size={18} color="#00ff88" />
                         : sentiment === 'bearish'
                           ? <XCircle size={18} color="#f87171" />
                           : <HelpCircle size={18} color="#94a3b8" />}
                     </div>
                     <p style={{ margin: '0 0 8px 0', fontSize: '16px', fontWeight: 600, color: titleColor }}>
                        {titleText}
                        {factorData.confidence != null
                          ? ` (Conf: ${(Number(factorData.confidence) * 100).toFixed(0)}%)`
                          : ''}
                     </p>
                     <p style={{ margin: 0, fontSize: '13px', color: '#cbd5e1', lineHeight: 1.5 }}>
                        {getRationale(key)}
                     </p>
                   </div>
                 );
              })}
            </div>

            {metricsData && (
               <div>
                 <h3 style={{ fontSize: '14px', color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: '20px', marginBottom: '16px' }}>Key Metrics Activity</h3>
                 <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
                    <div style={{ padding: '16px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', borderLeft: '3px solid #38bdf8' }}>
                      <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '8px' }}>RSI (14D)</div>
                      <div style={{ fontSize: '24px', fontWeight: 700 }}>{metricsData.momentum_rsi?.current || 'N/A'}</div>
                    </div>
                    <div style={{ padding: '16px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', borderLeft: '3px solid #00ff88' }}>
                      <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '8px' }}>Inst. Ownership</div>
                      <div style={{ fontSize: '24px', fontWeight: 700 }}>{metricsData.institutional_ownership?.current || 'N/A'}</div>
                    </div>
                    <div style={{ padding: '16px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', borderLeft: '3px solid #f87171' }}>
                      <div style={{ fontSize: '12px', color: '#94a3b8', marginBottom: '8px' }}>Short Interest</div>
                      <div style={{ fontSize: '24px', fontWeight: 700 }}>{metricsData.short_interest?.current || 'N/A'}</div>
                    </div>
                 </div>
               </div>
            )}
          </section>
        )}

        {(debateLoading || debateData || debateError) && (
          <section className="dt-panel" style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: 16 }}>
            <h2 className="dt-panel-title">AI Debate Panel</h2>
            {debateLoading && (
              <div style={{ color: '#94a3b8', fontSize: '0.85rem' }}>
                Running multi-agent debate and synthesizing verdict...
              </div>
            )}
            {debateError && (
              <div style={{
                background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
                borderRadius: 10, padding: '12px 16px', color: '#f87171', fontSize: '0.85rem',
              }}>
                {debateError}
              </div>
            )}
            <DebateThreadPanel result={debateData} loading={debateLoading} />
            {!debateLoading && debateData && <DebateVerdictSummary result={debateData} />}
          </section>
        )}
      </div>

    </div>
  );
}
