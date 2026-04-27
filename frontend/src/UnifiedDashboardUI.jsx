import React, { useState, useMemo, useId, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { TrendingUp, Shield, CircleDollarSign, Wallet, PieChart, Scale, CheckCircle2, ArrowUpRight, HelpCircle, Loader2, Search, Zap, CheckCircle, BarChart3, TrendingDown, Target, Activity, ShieldAlert, XCircle } from 'lucide-react';
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip as RechartsTooltip, LineChart as ReLineChart, Line, Legend } from 'recharts';
import { API_BASE_URL, apiFetch } from './api';
import { useAnalysisHistory } from './AnalysisContext';
import { SP500_TICKERS } from './sp500';
import './DecisionTerminalUI.css';

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

function sliderPosition(price, bear, bull) {
  if (price == null || bear == null || bull == null || bull <= bear) return 50;
  const p = ((price - bear) / (bull - bear)) * 100;
  return Math.min(90, Math.max(10, p));
}

export default function UnifiedDashboardUI() {
  const navigate = useNavigate();
  const [ticker, setTicker] = useState('AAPL');

  // From Consumer UI
  const [traceData, setTraceData] = useState(null);
  const [metricsData, setMetricsData] = useState(null);

  // From Decision Terminal
  const [decisionData, setDecisionData] = useState(null);

  const [loading, setLoading] = useState(false);
  const [loadingStep, setLoadingStep] = useState('');
  const [error, setError] = useState(null);

  const { addAnalysis } = useAnalysisHistory();

  const searchUpper = ticker.trim().toUpperCase();
  const isValid = !searchUpper || SP500_TICKERS.includes(searchUpper);
  const suggestions = useMemo(() => {
    if (isValid || !searchUpper) return [];
    return SP500_TICKERS.filter(t => t.startsWith(searchUpper) || t.includes(searchUpper)).slice(0, 4);
  }, [searchUpper, isValid]);

  const analyzeTicker = async (overrideTicker = ticker) => {
    if (!overrideTicker) return;
    setTicker(overrideTicker.toUpperCase());
    setLoading(true);
    setError(null);
    setLoadingStep('Fetching combined market data...');
    try {
      const [traceRes, metricsRes, decisionRes] = await Promise.all([
        apiFetch(`${API_BASE_URL}/trace?ticker=${overrideTicker}`),
        apiFetch(`${API_BASE_URL}/metrics/${overrideTicker}`),
        apiFetch(`${API_BASE_URL}/decision-terminal?ticker=${overrideTicker}`).catch(() => null)
      ]);

      setTraceData(traceRes);
      setMetricsData(metricsRes?.metrics);
      setDecisionData(decisionRes);

      addAnalysis(overrideTicker.toUpperCase(), { trace: traceRes, metrics: metricsRes?.metrics, dt: decisionRes });
    } catch (err) {
      setError(typeof err.message === 'string' ? err.message : JSON.stringify(err.message));
    } finally {
      setLoadingStep('');
      setLoading(false);
    }
  };

  // Decision Terminal Extracted Variables
  const hasDecisionData = decisionData != null;
  const v = decisionData?.valuation;
  const q = decisionData?.quality;
  const z = decisionData?.verdict;
  const r = decisionData?.roadmap;

  const valFill = valuationArcRatio(v?.spot_vs_average_pct);
  const pmFill = polymarketArcRatio(z?.prediction_market_bullish_pct);

  const expertPct = z?.expert_consensus_bullish_pct;
  const expertBullish = expertPct != null && expertPct >= 50;

  const spot = v?.spot_price_usd;
  const dotLeft = sliderPosition(spot, r?.bear_price_usd, r?.bull_price_usd);

  const roadmapChartData = useMemo(() => {
    if (!r || r.bull_price_usd == null) return [];
    const sy = new Date().getFullYear();
    const data = [{ t: `${sy}`, base: spot, bull: spot, bear: spot }];
    for (let i = 1; i <= 3; i++) {
      data.push({
        t: `${sy + i}`,
        base: spot + (r.base_price_usd - spot) * (i / 3),
        bull: spot + (r.bull_price_usd - spot) * (i / 3),
        bear: spot + (r.bear_price_usd - spot) * (i / 3),
      });
    }
    return data;
  }, [r, spot]);

  // Derived from trace / metrics
  const isBullish = traceData?.global_signal >= 2;
  const verdict = traceData?.global_verdict || "AWAITING ANALYSIS";

  const getRationale = (factorKey) => {
    if (!traceData || !traceData.factors[factorKey]) return "Awaiting Scan...";
    const factorData = traceData.factors[factorKey];
    const historyLen = factorData.history.length;
    if (historyLen < 2) return factorData.rationale || "No trace found.";
    return factorData.history[historyLen - 2].content.substring(0, 110) + "...";
  }

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
              className={`dt-search-input ${!isValid && searchUpper ? 'dt-invalid' : ''}`}
              style={{ width: '160px', padding: '10px 14px', borderRadius: '8px', border: '1px solid rgba(255,255,255,0.1)', background: 'rgba(0,0,0,0.2)', color: 'white' }}
              onKeyDown={(e) => { if (e.key === 'Enter' && isValid) analyzeTicker(ticker); }}
            />
            <button
              onClick={() => analyzeTicker(ticker)}
              disabled={loading || (!isValid && searchUpper)}
              style={{ padding: '10px 20px', borderRadius: '8px', border: 'none', background: 'var(--accent-blue)', color: 'white', fontWeight: 600, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 }}
            >
              {loading ? <Loader2 className="spinner" size={16} /> : <Search size={16} />}
              Analyze
            </button>
          </div>
          {!isValid && searchUpper && suggestions.length > 0 && (
            <div className="dt-suggestions" style={{ position: 'absolute', top: '100%', left: 0, width: '160px', background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', marginTop: '4px', zIndex: 10 }}>
              <div style={{ padding: '8px', fontSize: '11px', color: '#94a3b8' }}>S&P 500 Tickers only</div>
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
        <div style={{ textAlign: 'center', padding: '40px' }}>
          <Loader2 size={32} style={{ animation: 'spin 1s linear infinite', color: '#3b82f6', margin: '0 auto' }} />
          <p style={{ color: '#94a3b8', marginTop: 16, fontSize: 14 }}>{loadingStep}</p>
        </div>
      )}

      {error && (
        <div className="glass-panel" style={{ borderColor: 'var(--accent-red)', padding: '16px', borderRadius: '8px', background: 'rgba(239, 68, 68, 0.1)' }}>
          <p style={{ color: 'var(--accent-red)', margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}><ShieldAlert size={18} /> {error}</p>
        </div>
      )}

      {/* Main Content Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(400px, 1fr))', gap: '20px' }}>

        {/* Quality Scorecard */}
        <section className="dt-panel" style={{ gridColumn: 'span 1' }}>
          <h2 className="dt-panel-title">Business Quality Scorecard</h2>
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
          <div className="dt-verdict-split" style={{ display: 'flex', gap: '24px', marginTop: '16px', alignItems: 'center' }}>
            <div className="dt-pm-block" style={{ flex: 1, textAlign: 'center' }}>
              <div className="dt-subblock-title" style={{ marginBottom: '12px' }}>Prediction Market Sentiment</div>
              <div className="dt-pm-gauge-wrap" style={{ position: 'relative', display: 'inline-block' }}>
                <SemiGauge fillRatio={hasDecisionData ? pmFill : 0.5} size="small" />
                <div className="dt-pm-label" style={{ position: 'absolute', bottom: '8px', left: '50%', transform: 'translateX(-50%)', fontWeight: 600 }}>
                  {hasDecisionData && !z?.polymarket_gated_out && z?.prediction_market_bullish_pct != null
                    ? `${z.prediction_market_bullish_pct}% Bullish`
                    : hasDecisionData ? 'No gated market' : '—'}
                </div>
              </div>
            </div>

            <div className="dt-verdict-col" style={{ flex: 1 }}>
              <div className="dt-subblock-title">Overall Expert Consensus</div>
              <div className={`dt-expert-pill ${expertBullish ? 'bull' : 'neutral'}`} style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '6px 12px', borderRadius: '16px', background: expertBullish ? 'rgba(0,255,136,0.1)' : 'rgba(255,255,255,0.05)', color: expertBullish ? '#00ff88' : '#fff', fontWeight: 600, fontSize: '14px', marginBottom: '16px' }}>
                <ArrowUpRight size={18} className="dt-expert-arrow" />
                <span>
                  {hasDecisionData && expertPct != null ? `${expertBullish ? 'Bullish' : 'Mixed'} — ${expertPct.toFixed(0)}%` : '—'}
                </span>
              </div>
              <div className="dt-subblock-title">Aggregate Verdict</div>
              <div className={`dt-aggregate ${verdictTone(z?.headline_verdict || verdict)}`} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '1.2rem', fontWeight: 700 }}>
                <CheckCircle2 size={24} className="dt-aggregate-check" />
                <span>{hasDecisionData ? (z?.headline_verdict || verdict).toUpperCase() : '—'}</span>
              </div>
            </div>
          </div>
        </section>

        {/* Future Price Roadmap */}
        <section className="dt-panel" style={{ gridColumn: '1 / -1' }}>
          <h2 className="dt-panel-title">Future Price Roadmap (3-Year Trajectory)</h2>
          <div style={{ marginTop: '16px', height: '300px' }}>
            <div className="dt-roadmap-head" style={{ display: 'flex', gap: '16px', marginBottom: '16px' }}>
              <span className="dt-roadmap-legend"><span className="dot bull" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#00ff88', marginRight: 6 }} /> Bull {r?.bull_price_usd != null && ` ($${Number(r.bull_price_usd).toFixed(0)})`}</span>
              <span className="dt-roadmap-legend"><span className="dot base" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#38bdf8', marginRight: 6 }} /> Base {r?.base_price_usd != null && ` ($${Number(r.base_price_usd).toFixed(0)})`}</span>
              <span className="dt-roadmap-legend"><span className="dot bear" style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#f87171', marginRight: 6 }} /> Bear {r?.bear_price_usd != null && ` ($${Number(r.bear_price_usd).toFixed(0)})`}</span>
            </div>
            {hasDecisionData && r?.predicted_cagr_base_pct != null && (
              <div className="dt-cagr-chip" style={{ display: 'inline-block', padding: '4px 8px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', fontSize: '12px', marginBottom: '16px' }}>Predicted CAGR: {r.predicted_cagr_base_pct}%</div>
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

            <div className="dt-slider-section" style={{ position: 'relative', marginTop: '30px', paddingBottom: '20px' }}>
              <div className="dt-slider-rail-labels" style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: '#94a3b8', marginBottom: '8px' }}>
                <span className="sell">Sell over</span>
                <span className="neutral">Neutral</span>
                <span className="buy">Buy under</span>
              </div>
              <div className="dt-slider-track" style={{ height: '6px', background: 'linear-gradient(to right, #f87171, #eab308, #00ff88)', borderRadius: '3px', position: 'relative' }}>
                <div className="dt-slider-knob" style={{ width: '12px', height: '12px', background: '#fff', borderRadius: '50%', position: 'absolute', top: '50%', transform: 'translate(-50%, -50%)', left: `${dotLeft}%`, boxShadow: '0 0 10px rgba(0,0,0,0.5)' }} title="Vs bear–bull scenario band" />
              </div>
              {hasDecisionData && spot != null && (
                <div className="dt-slider-price" style={{ position: 'absolute', left: `${dotLeft}%`, transform: 'translateX(-50%)', top: '30px', fontSize: '12px', fontWeight: 600, color: '#fff', whiteSpace: 'nowrap' }}>
                  Current price: ${Number(spot).toFixed(2)}
                </div>
              )}
            </div>
          </div>
        </section>

        {/* Consumer Multi-Factor Details (Trendboards & Signals) */}
        {traceData && (
          <section className="dt-panel" style={{ gridColumn: '1 / -1', display: 'flex', flexDirection: 'column', gap: '20px' }}>
            <h2 className="dt-panel-title">Multi-Factor Analysis Signals</h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px' }}>

              {/* Factor Signals using FactorSignalCard style simplified */}
              {Object.entries(traceData.factors || {}).map(([key, factorData], idx) => {
                 const isPositive = factorData.trading_signal > 0;
                 return (
                   <div key={key} style={{ padding: '20px', background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '12px' }}>
                     <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                       <span style={{ fontSize: '12px', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, color: '#94a3b8' }}>
                         {key.replace('_', ' ')}
                       </span>
                       {isPositive ? <CheckCircle2 size={18} color="#00ff88" /> : <XCircle size={18} color="#f87171" />}
                     </div>
                     <p style={{ margin: '0 0 8px 0', fontSize: '16px', fontWeight: 600, color: isPositive ? '#00ff88' : '#f87171' }}>
                        {isPositive ? 'Bullish Signal' : 'Bearish Signal'} (Conf: {(factorData.confidence * 100).toFixed(0)}%)
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
      </div>

    </div>
  );
}
