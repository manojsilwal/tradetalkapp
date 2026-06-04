/**
 * GlobalMarketsChart — Normalized Performance Chart
 *
 * Shows major global equity indices, US bonds/treasuries, gold, and crypto
 * all indexed to 0% at the start of the selected period.
 *
 * Markets covered:
 *   EQUITIES: S&P 500, Nikkei 225, KOSPI, CSI 300, Nifty 50, Euro Stoxx 50,
 *             FTSE 100, DAX, CAC 40, Hang Seng, IBOVESPA
 *   BONDS:    US 20+Yr Treasury (TLT), US Aggregate Bond (AGG)
 *   COMMODITIES: Gold (GC=F)
 *   CRYPTO:   Crypto Total Cap proxy (BTC as bellwether)
 *
 * Data fetching: calls backend /macro/global-markets?period=3M
 * Falls back to client-side yfinance-equivalent via a single API call.
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { API_BASE_URL } from './api';

// ── Market definitions ────────────────────────────────────────────────────────
const MARKETS = [
  // EQUITIES
  { id: 'SPY',  label: 'S&P 500',      region: 'USA',         category: 'EQUITIES',         color: '#3b82f6' },
  { id: 'EWJ',  label: 'Nikkei 225',   region: 'Japan',       category: 'EQUITIES',         color: '#f97316' },
  { id: 'EWY',  label: 'KOSPI',        region: 'South Korea', category: 'EQUITIES',         color: '#06b6d4' },
  { id: 'MCHI', label: 'CSI 300',      region: 'China',       category: 'EQUITIES',         color: '#eab308' },
  { id: 'INDA', label: 'Nifty 50',     region: 'India',       category: 'EQUITIES',         color: '#22c55e' },
  { id: 'FEZ',  label: 'Euro Stoxx 50',region: 'Europe',      category: 'EQUITIES',         color: '#a855f7' },
  { id: 'EWU',  label: 'FTSE 100',     region: 'UK',          category: 'EQUITIES',         color: '#ec4899' },
  { id: 'EWG',  label: 'DAX',          region: 'Germany',     category: 'EQUITIES',         color: '#f43f5e' },
  { id: 'EWQ',  label: 'CAC 40',       region: 'France',      category: 'EQUITIES',         color: '#8b5cf6' },
  // BONDS & TREASURY
  { id: 'TLT',  label: 'US 20+Yr Treasury', region: 'USA',   category: 'BONDS & TREASURY',  color: '#94a3b8' },
  { id: 'AGG',  label: 'US Aggregate Bond',  region: 'USA',  category: 'BONDS & TREASURY',  color: '#cbd5e1' },
  // COMMODITIES
  { id: 'GLD',  label: 'Gold (Spot)',   region: 'Global',     category: 'COMMODITIES',       color: '#fbbf24' },
  // CRYPTO
  { id: 'BTC-USD', label: 'Crypto Total Cap', region: 'Global', category: 'CRYPTO',        color: '#f59e0b' },
];

const CATEGORY_ORDER = ['EQUITIES', 'BONDS & TREASURY', 'COMMODITIES', 'CRYPTO'];

const PERIODS = [
  { id: '1W',  label: '1W',  days: 7   },
  { id: '1M',  label: '1M',  days: 30  },
  { id: '3M',  label: '3M',  days: 90  },
  { id: '6M',  label: '6M',  days: 180 },
  { id: 'YTD', label: 'YTD', days: null },
  { id: '1Y',  label: '1Y',  days: 365 },
];

// ── Custom tooltip ────────────────────────────────────────────────────────────
function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const sorted = [...payload].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  return (
    <div style={{
      background: 'rgba(9,13,24,0.92)',
      backdropFilter: 'blur(12px)',
      WebkitBackdropFilter: 'blur(12px)',
      border: '1px solid rgba(255,255,255,0.12)',
      borderRadius: 12,
      padding: '12px 16px',
      fontSize: '0.8rem',
      width: 280,
      boxShadow: '0 12px 32px rgba(0,0,0,0.6)',
      pointerEvents: 'none',
    }}>
      <div style={{ color: '#94a3b8', marginBottom: 10, fontWeight: 600, fontSize: '0.85rem' }}>{label}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {sorted.map((entry) => {
          const val = entry.value;
          const color = entry.stroke;
          return (
            <div key={entry.dataKey} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, overflow: 'hidden' }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
                <span style={{ color: '#e2e8f0', textOverflow: 'ellipsis', overflow: 'hidden', whiteSpace: 'nowrap' }}>
                  {entry.name}
                </span>
              </div>
              <span style={{ color: val >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700, fontFamily: 'monospace' }}>
                {val >= 0 ? '+' : ''}{(val ?? 0).toFixed(2)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ── Y-axis tick formatter ─────────────────────────────────────────────────────
function yTickFmt(v) {
  return `${v >= 0 ? '+' : ''}${v}%`;
}

// ── Main component ────────────────────────────────────────────────────────────
export default function GlobalMarketsChart() {
  const [period, setPeriod] = useState('3M');
  const [rawData, setRawData] = useState(null); // { dates: [], series: { id: [prices] } }
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [hidden, setHidden] = useState(new Set()); // toggled-off market ids

  // Fetch normalized price series from backend
  const fetchData = useCallback(async (p) => {
    setLoading(true);
    setError(null);
    try {
      const tickers = MARKETS.map(m => m.id).join(',');
      const url = `${API_BASE_URL}/macro/global-markets?period=${p}&tickers=${encodeURIComponent(tickers)}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setRawData(json);
    } catch (e) {
      setError(e.message || 'Failed to load market data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(period); }, [period, fetchData]);

  // Build recharts data array: [{date, SPY: 3.2, TLT: -1.1, ...}]
  const chartData = useMemo(() => {
    if (!rawData?.dates?.length) return [];
    return rawData.dates.map((date, i) => {
      const point = { date };
      MARKETS.forEach(m => {
        const series = rawData.series?.[m.id];
        if (series && series[i] != null) {
          point[m.id] = +series[i].toFixed(3);
        }
      });
      return point;
    });
  }, [rawData]);

  // Latest % change per market (last value in the normalized series)
  const latestChange = useMemo(() => {
    if (!chartData.length) return {};
    const last = chartData[chartData.length - 1];
    const out = {};
    MARKETS.forEach(m => { out[m.id] = last[m.id] ?? null; });
    return out;
  }, [chartData]);

  // Toggle market visibility
  const toggleMarket = (id) => {
    setHidden(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const visibleCount = MARKETS.length - hidden.size;

  // X-axis tick decimation (show ~8 ticks max)
  const xTicks = useMemo(() => {
    if (!rawData?.dates?.length) return [];
    const dates = rawData.dates;
    if (dates.length <= 10) return dates;
    const step = Math.ceil(dates.length / 8);
    return dates.filter((_, i) => i % step === 0 || i === dates.length - 1);
  }, [rawData]);

  // Y domain with padding
  const yDomain = useMemo(() => {
    if (!chartData.length) return [-25, 65];
    let min = 0, max = 0;
    chartData.forEach(row => {
      MARKETS.forEach(m => {
        if (!hidden.has(m.id) && row[m.id] != null) {
          min = Math.min(min, row[m.id]);
          max = Math.max(max, row[m.id]);
        }
      });
    });
    const pad = Math.max((max - min) * 0.12, 5);
    return [Math.floor(min - pad), Math.ceil(max + pad)];
  }, [chartData, hidden]);

  return (
    <div
      className="dash-card glass-panel fade-in"
      data-testid="global-markets-chart"
      style={{
        padding: '28px 28px 20px',
        borderRadius: '20px',
        background: 'rgba(9,13,24,0.75)',
        border: '1px solid rgba(255,255,255,0.07)',
        boxShadow: '0 8px 40px rgba(0,0,0,0.4)',
      }}
    >
      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap', gap: 16, marginBottom: 4 }}>
        <div>
          <h3 style={{ margin: '0 0 4px', fontSize: '1.18rem', fontWeight: 700, color: '#f1f5f9', letterSpacing: '-0.01em' }}>
            Global Markets — Normalized Performance
          </h3>
          <p style={{ margin: 0, fontSize: '0.78rem', color: '#64748b' }}>
            Equities, US bonds &amp; treasuries, gold and total crypto, indexed to the start of the period.
          </p>
        </div>

        {/* Period toggle pills */}
        <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
          {PERIODS.map(p => (
            <button
              key={p.id}
              onClick={() => setPeriod(p.id)}
              style={{
                padding: '5px 11px', borderRadius: 7, fontSize: '0.78rem', fontWeight: 600,
                border: period === p.id ? '1px solid rgba(255,255,255,0.5)' : '1px solid rgba(255,255,255,0.1)',
                background: period === p.id ? 'rgba(255,255,255,0.12)' : 'transparent',
                color: period === p.id ? '#f1f5f9' : '#64748b',
                cursor: 'pointer', transition: 'all 0.15s',
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── Chart ───────────────────────────────────────────────────────── */}
      <div style={{ position: 'relative', marginTop: 16 }}>
        {loading && (
          <div style={{
            position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(9,13,24,0.7)', borderRadius: 12, zIndex: 10,
          }}>
            <div style={{ textAlign: 'center' }}>
              <div style={{ width: 32, height: 32, border: '3px solid rgba(139,92,246,0.3)', borderTop: '3px solid #8b5cf6', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 10px' }} />
              <div style={{ color: '#64748b', fontSize: '0.8rem' }}>Loading market data…</div>
            </div>
          </div>
        )}

        {error && !loading && (
          <div style={{
            height: 340, display: 'flex', alignItems: 'center', justifyContent: 'center',
            color: '#94a3b8', fontSize: '0.85rem', flexDirection: 'column', gap: 8,
          }}>
            <div style={{ color: '#f87171' }}>⚠ {error}</div>
            <button
              onClick={() => fetchData(period)}
              style={{
                background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)',
                color: '#a78bfa', borderRadius: 8, padding: '6px 14px', cursor: 'pointer', fontSize: '0.78rem',
              }}
            >
              Retry
            </button>
          </div>
        )}

        {!error && (
          <div style={{ height: 340, opacity: loading ? 0.3 : 1, transition: 'opacity 0.3s' }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 12, right: 16, left: 0, bottom: 4 }}>
                <CartesianGrid
                  strokeDasharray="0"
                  stroke="rgba(255,255,255,0.04)"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  ticks={xTicks}
                  tick={{ fill: '#475569', fontSize: 11 }}
                  axisLine={{ stroke: 'rgba(255,255,255,0.06)' }}
                  tickLine={false}
                  dy={6}
                />
                <YAxis
                  domain={yDomain}
                  tickFormatter={yTickFmt}
                  tick={{ fill: '#475569', fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <Tooltip
                  content={<CustomTooltip />}
                  cursor={{ stroke: 'rgba(255,255,255,0.2)', strokeWidth: 1 }}
                  wrapperStyle={{ zIndex: 1000 }}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
                {MARKETS.map(m => (
                  <Line
                    key={m.id}
                    type="monotone"
                    dataKey={m.id}
                    name={m.label}
                    stroke={m.color}
                    strokeWidth={hidden.has(m.id) ? 0 : 1.8}
                    dot={false}
                    activeDot={hidden.has(m.id) ? false : { r: 4, stroke: '#ffffff', strokeWidth: 1.5 }}
                    isAnimationActive={false}
                    connectNulls
                    opacity={hidden.has(m.id) ? 0 : 1}
                  />
                ))}

              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* ── Legend ──────────────────────────────────────────────────────── */}
      <div style={{ marginTop: 20 }}>
        {/* Toggle hint */}
        <div style={{ fontSize: '0.72rem', color: '#475569', marginBottom: 14 }}>
          {visibleCount} of {MARKETS.length} markets shown — click to toggle
        </div>

        {/* Grouped legend grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '0 32px' }}>
          {CATEGORY_ORDER.map(cat => {
            const items = MARKETS.filter(m => m.category === cat);
            return (
              <div key={cat}>
                <div style={{
                  fontSize: '0.67rem', fontWeight: 700, letterSpacing: '0.1em',
                  textTransform: 'uppercase', color: '#475569', marginBottom: 10,
                }}>
                  {cat}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {items.map(m => {
                    const change = latestChange[m.id];
                    const isHidden = hidden.has(m.id);
                    const isPos = change != null && change >= 0;
                    return (
                      <button
                        key={m.id}
                        onClick={() => toggleMarket(m.id)}
                        title={`${isHidden ? 'Show' : 'Hide'} ${m.label}`}
                        style={{
                          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                          background: 'none', border: 'none', cursor: 'pointer', padding: '3px 0',
                          opacity: isHidden ? 0.35 : 1, transition: 'opacity 0.15s',
                          textAlign: 'left', width: '100%',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <div style={{
                            width: 10, height: 10, borderRadius: '50%', flexShrink: 0,
                            background: m.color,
                            boxShadow: isHidden ? 'none' : `0 0 5px ${m.color}80`,
                          }} />
                          <div>
                            <div style={{ color: '#e2e8f0', fontSize: '0.82rem', fontWeight: 600, lineHeight: 1.2 }}>
                              {m.label}
                            </div>
                            <div style={{ color: '#475569', fontSize: '0.7rem' }}>{m.region}</div>
                          </div>
                        </div>
                        <div style={{
                          color: change == null ? '#475569' : isPos ? '#22c55e' : '#ef4444',
                          fontWeight: 700, fontSize: '0.82rem', minWidth: 56, textAlign: 'right',
                        }}>
                          {change == null ? '—' : `${isPos ? '+' : ''}${change.toFixed(2)}%`}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
