import React, { useState, useCallback, useMemo, useId } from 'react';
import {
  Loader2,
  HelpCircle,
  Bell,
  User,
  TrendingUp,
  Shield,
  CircleDollarSign,
  Wallet,
  PieChart,
  Scale,
  CheckCircle2,
  ArrowUpRight,
} from 'lucide-react';
import {
  LineChart as ReLineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts';
import { API_BASE_URL, apiFetch } from './api';
import { SP500_TICKERS } from './sp500';
import './DecisionTerminalUI.css';

const QUALITY_ICONS = {
  roic: TrendingUp,
  moat: Shield,
  fcf: CircleDollarSign,
  debt: Wallet,
  margin: PieChart,
  current_ratio: Scale,
};

/** Semicircular gauge — fillRatio 0..1 (green arc). */
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

export default function DecisionTerminalUI() {
  const [ticker, setTicker] = useState('AAPL');
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const searchUpper = ticker.trim().toUpperCase();
  const isValid = !searchUpper || SP500_TICKERS.includes(searchUpper);
  const suggestions = useMemo(() => {
    if (isValid || !searchUpper) return [];
    return SP500_TICKERS.filter(t => t.startsWith(searchUpper) || t.includes(searchUpper)).slice(0, 4);
  }, [searchUpper, isValid]);


  const run = useCallback(async () => {
    setLoading(true);
    setError(null);
    setPayload(null);
    try {
      const data = await apiFetch(
        `${API_BASE_URL}/decision-terminal?ticker=${encodeURIComponent(ticker.trim())}`,
      );
      setPayload(data);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  const v = payload?.valuation;
  const q = payload?.quality;
  const z = payload?.verdict;
  const r = payload?.roadmap;
  const hasData = !!payload;

  const bigGaugeFill = useMemo(() => valuationArcRatio(v?.pct_vs_average), [v?.pct_vs_average]);
  const pmFill = useMemo(
    () => (z?.polymarket_gated_out ? 0.35 : polymarketArcRatio(z?.prediction_market_bullish_pct)),
    [z?.polymarket_gated_out, z?.prediction_market_bullish_pct],
  );

  const roadmapChartData =
    r && v?.current_price_usd != null && Number(v.current_price_usd) > 0
      ? [
          {
            t: `Now ($${Number(v.current_price_usd).toFixed(2)})`,
            bull: v.current_price_usd,
            base: v.current_price_usd,
            bear: v.current_price_usd,
          },
          {
            t: '3Y',
            bull: r.bull_price_usd ?? v.current_price_usd,
            base: r.base_price_usd ?? v.current_price_usd,
            bear: r.bear_price_usd ?? v.current_price_usd,
          },
        ]
      : [];

  const dotLeft = sliderPosition(v?.current_price_usd, r?.bear_price_usd, r?.bull_price_usd);
  const spot = v?.current_price_usd;

  const chartTooltip = ({ active, payload: rows }) => {
    if (!active || !rows?.length) return null;
    const cagr = r?.predicted_cagr_base_pct;
    return (
      <div className="dt-chart-tooltip">
        {cagr != null && <div className="dt-chart-tooltip-cagr">Predicted CAGR: {cagr}%</div>}
        {rows.map((row) => (
          <div key={row.dataKey} className="dt-chart-tooltip-row">
            <span style={{ color: row.color }}>{row.name}</span>
            <span>${Number(row.value).toFixed(2)}</span>
          </div>
        ))}
      </div>
    );
  };

  const expertPct = z?.expert_bullish_pct;
  const expertBullish = expertPct != null && expertPct >= 55;

  return (
    <div className="dt-page-bleed dt-page fade-in">
      <div className="dt-main">
        <header className="dt-topbar">
          <div className="dt-topbar-right" style={{ width: '100%', justifyContent: 'flex-start' }}>
            <div className="dt-topbar-run">
              <input
                type="text"
                value={ticker}
                onChange={(e) => setTicker(e.target.value)}
                placeholder="TICKER"
                maxLength={8}
                className="dt-ticker-input"
              />
              <button type="button" className="dt-run-btn" onClick={run} disabled={loading || !searchUpper || !isValid}>
                {loading ? <Loader2 className="spinner" size={18} /> : 'Run analysis'}
              </button>
            </div>
          </div>
        </header>

        {(!isValid && searchUpper) && (
          <div className="dt-error-banner" style={{ marginTop: '-8px' }}>
            Incorrect ticker (not in S&P 500). 
            {suggestions.length > 0 && ` Did you mean: ${suggestions.join(', ')}?`}
          </div>
        )}

        {payload?.disclaimer && <div className="dt-disclaimer">{payload.disclaimer}</div>}
        {(payload?.market_data_degraded ||
          (payload?.spot_price_source &&
            payload.spot_price_source !== 'yfinance_history')) && (
          <div className="dt-disclaimer dt-market-degraded">
            Spot price uses a fallback source ({payload?.spot_price_source || 'unknown'}). Momentum and
            some metrics may be incomplete versus a full Yahoo history pull.
          </div>
        )}
        {error && <div className="dt-error-banner">{error}</div>}

        {!hasData && !loading && !error && (
          <div className="dt-prompt-banner">
            Enter a ticker and run analysis. First load may take a minute (swarm + debate).
          </div>
        )}

        <div className="dt-dash-grid">
          {/* —— Consensus valuation —— */}
          <section className="dt-panel">
            <h2 className="dt-panel-title">Consensus valuation signal</h2>
            <div className="dt-valuation-split">
              <div className="dt-valuation-gauge">
                <SemiGauge fillRatio={hasData ? bigGaugeFill : 0.38} size="large" />
                <div className="dt-gauge-caption">
                  {hasData ? v?.gauge_label || '—' : '—'}
                </div>
                {hasData && spot != null && (
                  <div className="dt-gauge-sub">
                    Spot ${Number(spot).toFixed(2)}
                    {v?.average_fair_value_usd != null && (
                      <> · Avg fair ${Number(v.average_fair_value_usd).toFixed(2)}</>
                    )}
                  </div>
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
                          : hasData
                            ? '—'
                            : '—'}
                      </span>
                    </li>
                  ))}
                  {hasData && (
                    <li className="dt-models-li dt-models-average">
                      <span className="dt-models-name">Average:</span>
                      <span className="dt-models-val">
                        {v?.average_fair_value_usd != null
                          ? `$${Number(v.average_fair_value_usd).toFixed(0)}`
                          : '—'}
                      </span>
                    </li>
                  )}
                  {!hasData && (
                    <>
                      <li className="dt-models-li dt-models-placeholder"><span>Average:</span><span>—</span></li>
                    </>
                  )}
                </ul>
              </div>
            </div>
          </section>

          {/* —— Quality scorecard —— */}
          <section className="dt-panel">
            <h2 className="dt-panel-title">Business quality scorecard</h2>
            <div className="dt-quality-3x2">
              {(q?.rows || []).map((row) => {
                const IconComp = QUALITY_ICONS[row.id] || TrendingUp;
                const st = (row.status_label || '').toLowerCase();
                const tone =
                  st.includes('good') && !st.includes('strong')
                    ? 'warn'
                    : ['excellent', 'strong', 'robust', 'low', 'high'].some((k) => st.includes(k))
                      ? 'ok'
                      : 'muted';
                return (
                  <div key={row.id} className="dt-q-tile">
                    <div className="dt-q-tile-icon">
                      <IconComp size={22} strokeWidth={1.6} />
                    </div>
                    <div className="dt-q-tile-body">
                      <div className="dt-q-tile-label">
                        <ProvenanceTip provenance={row.provenance} label={row.label} />
                      </div>
                      <div className="dt-q-tile-value">{row.value_label || '—'}</div>
                      <div className={`dt-q-tile-status dt-tone-${tone}`}>{row.status_label || '—'}</div>
                    </div>
                  </div>
                );
              })}
              {!hasData &&
                ['ROIC', 'Moat', 'FCF', 'Debt', 'Margin', 'Current ratio'].map((label) => (
                  <div key={label} className="dt-q-tile dt-q-tile-empty">
                    <div className="dt-q-tile-icon muted">
                      <TrendingUp size={22} />
                    </div>
                    <div className="dt-q-tile-body">
                      <div className="dt-q-tile-label">{label}</div>
                      <div className="dt-q-tile-value">—</div>
                      <div className="dt-q-tile-status dt-tone-muted">—</div>
                    </div>
                  </div>
                ))}
            </div>
          </section>

          {/* —— Verdict & sentiment —— */}
          <section className="dt-panel">
            <h2 className="dt-panel-title">Verdict & sentiment hub</h2>
            <div className="dt-verdict-split">
              <div className="dt-pm-block">
                <div className="dt-subblock-title">Prediction market sentiment</div>
                <div className="dt-pm-gauge-wrap">
                  <SemiGauge fillRatio={hasData ? pmFill : 0.5} size="small" />
                  <div className="dt-pm-label">
                    {hasData && !z?.polymarket_gated_out && z?.prediction_market_bullish_pct != null
                      ? `${z.prediction_market_bullish_pct}% Bullish`
                      : hasData
                        ? 'No gated market'
                        : '—'}
                  </div>
                </div>
                {hasData && z?.prediction_market_event_title && !z?.polymarket_gated_out && (
                  <p className="dt-pm-event">{z.prediction_market_event_title.slice(0, 100)}</p>
                )}
              </div>
              <div className="dt-verdict-col">
                <div className="dt-subblock-title">Overall expert consensus</div>
                <div className={`dt-expert-pill ${expertBullish ? 'bull' : 'neutral'}`}>
                  <ArrowUpRight size={18} className="dt-expert-arrow" />
                  <span>
                    {hasData && expertPct != null
                      ? `${expertBullish ? 'Bullish' : 'Mixed'} — ${expertPct.toFixed(0)}%`
                      : '—'}
                  </span>
                </div>
                <div className="dt-subblock-title dt-mt">Aggregate verdict</div>
                <div className={`dt-aggregate ${verdictTone(z?.headline_verdict)}`}>
                  <CheckCircle2 size={28} className="dt-aggregate-check" />
                  <span>{hasData ? (z?.headline_verdict || '—').toUpperCase() : '—'}</span>
                </div>
                {hasData && (z?.fusion_note || z?.debate_verdict) && (
                  <div className="dt-verdict-meta">
                    <span>
                      Debate {z.debate_verdict} · Swarm {z.swarm_verdict}
                    </span>
                    {z.fusion_note && <p>{z.fusion_note}</p>}
                  </div>
                )}
              </div>
            </div>
          </section>

          {/* —— Roadmap —— */}
          <section className="dt-panel">
            <h2 className="dt-panel-title">Future price roadmap (3-year trajectory)</h2>
            <div className="dt-roadmap-head">
              <span className="dt-roadmap-legend">
                <span className="dot bull" /> Bull
                {r?.bull_price_usd != null && ` ($${Number(r.bull_price_usd).toFixed(0)})`}
              </span>
              <span className="dt-roadmap-legend">
                <span className="dot base" /> Base
                {r?.base_price_usd != null && ` ($${Number(r.base_price_usd).toFixed(0)})`}
              </span>
              <span className="dt-roadmap-legend">
                <span className="dot bear" /> Bear
                {r?.bear_price_usd != null && ` ($${Number(r.bear_price_usd).toFixed(0)})`}
              </span>
            </div>
            {hasData && r?.predicted_cagr_base_pct != null && (
              <div className="dt-cagr-chip">Predicted CAGR: {r.predicted_cagr_base_pct}%</div>
            )}
            <div className="dt-chart-box">
              {roadmapChartData.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <ReLineChart data={roadmapChartData} margin={{ top: 16, right: 12, left: 4, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.12)" vertical={false} />
                    <XAxis
                      dataKey="t"
                      tick={{ fill: '#8ba0b5', fontSize: 11 }}
                      axisLine={{ stroke: 'rgba(148,163,184,0.2)' }}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fill: '#8ba0b5', fontSize: 10 }}
                      axisLine={false}
                      tickLine={false}
                      domain={['auto', 'auto']}
                      tickFormatter={(x) => `$${Math.round(x)}`}
                    />
                    <Tooltip content={chartTooltip} />
                    <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8, color: '#cbd5e1' }} />
                    <Line type="monotone" dataKey="bull" name="Bull case" stroke="#00ff88" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
                    <Line type="monotone" dataKey="base" name="Base case" stroke="#38bdf8" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
                    <Line type="monotone" dataKey="bear" name="Bear case" stroke="#f87171" strokeWidth={2} dot={{ r: 3 }} strokeDasharray="5 5" />
                  </ReLineChart>
                </ResponsiveContainer>
              ) : (
                <div className="dt-chart-empty">Run analysis to load scenario paths</div>
              )}
            </div>
            {(r?.assumptions || []).length > 0 && (
              <ul className="dt-assumptions">
                {r.assumptions.map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
              </ul>
            )}
            <div className="dt-slider-section">
              <div className="dt-slider-rail-labels">
                <span className="sell">Sell over</span>
                <span className="neutral">Neutral</span>
                <span className="buy">Buy under</span>
              </div>
              <div className="dt-slider-track">
                <div className="dt-slider-gradient" />
                <div
                  className="dt-slider-knob"
                  style={{ left: `${dotLeft}%` }}
                  title="Vs bear–bull scenario band"
                />
              </div>
              {hasData && spot != null && (
                <div className="dt-slider-price" style={{ left: `${dotLeft}%` }}>
                  Current price: ${Number(spot).toFixed(2)}
                </div>
              )}
            </div>
            {hasData && r?.provenance && (
              <p className="dt-roadmap-prov">
                {r.used_heuristic_fallback ? 'Heuristic / fallback scenarios' : 'Model-assisted scenarios'} ·{' '}
                {r.provenance.source}
              </p>
            )}
          </section>
        </div>

        {payload?.generated_at_utc && (
          <footer className="dt-footer-meta">
            UTC {payload.generated_at_utc} · refresh ~{payload.cache_ttl_seconds}s
          </footer>
        )}
      </div>
    </div>
  );
}
