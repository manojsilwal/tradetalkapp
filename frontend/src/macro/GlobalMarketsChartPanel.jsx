import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { Loader2 } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';
import { formatPct } from './macroUtils';

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const sorted = [...payload].sort((a, b) => (b.value ?? 0) - (a.value ?? 0));
  return (
    <div className="macro-chart-tooltip">
      <div className="macro-chart-tooltip-date">{label}</div>
      {sorted.map((entry) => (
        <div key={entry.dataKey} className="macro-chart-tooltip-row">
          <span style={{ color: entry.color }}>{entry.name}</span>
          <span className={entry.value >= 0 ? 'pos' : 'neg'}>
            {entry.value >= 0 ? '+' : ''}{(entry.value ?? 0).toFixed(2)}%
          </span>
        </div>
      ))}
    </div>
  );
}

function yTickFmt(v) {
  return `${v >= 0 ? '+' : ''}${v}%`;
}

/**
 * Embeddable normalized performance chart for Global Capital Flows.
 * Indexed to 0% at period start via GET /macro/global-markets.
 */
export default function GlobalMarketsChartPanel({
  markets,
  period,
  title = 'Global_capital_flows',
  subtitle,
  dataTestId = 'global-markets-chart',
  onSeriesLoaded,
  compactLegend = false,
}) {
  const [rawData, setRawData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [hidden, setHidden] = useState(new Set());

  const fetchData = useCallback(async () => {
    if (!markets?.length || !period) return;
    setLoading(true);
    setError(null);
    try {
      const tickers = markets.map((m) => m.id).join(',');
      const url = `${API_BASE_URL}/macro/global-markets?period=${period}&tickers=${encodeURIComponent(tickers)}`;
      const json = await apiFetch(url);
      setRawData(json);
      if (onSeriesLoaded) {
        const returns = {};
        const series = json?.series || {};
        Object.keys(series).forEach((sym) => {
          const vals = series[sym];
          if (vals?.length) returns[sym] = vals[vals.length - 1];
        });
        onSeriesLoaded(returns, json);
      }
    } catch (e) {
      setError(e.message || 'Failed to load market data');
    } finally {
      setLoading(false);
    }
  }, [markets, period, onSeriesLoaded]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const chartData = useMemo(() => {
    if (!rawData?.dates?.length) return [];
    return rawData.dates.map((date, i) => {
      const point = { date };
      markets.forEach((m) => {
        const series = rawData.series?.[m.id];
        if (series && series[i] != null) point[m.id] = +series[i].toFixed(3);
      });
      return point;
    });
  }, [rawData, markets]);

  const latestChange = useMemo(() => {
    if (!chartData.length) return {};
    const last = chartData[chartData.length - 1];
    const out = {};
    markets.forEach((m) => { out[m.id] = last[m.id] ?? null; });
    return out;
  }, [chartData, markets]);

  const xTicks = useMemo(() => {
    if (!rawData?.dates?.length) return [];
    const dates = rawData.dates;
    if (dates.length <= 8) return dates;
    const step = Math.ceil(dates.length / 6);
    return dates.filter((_, i) => i % step === 0 || i === dates.length - 1);
  }, [rawData]);

  const yDomain = useMemo(() => {
    if (!chartData.length) return [-15, 15];
    let min = 0;
    let max = 0;
    chartData.forEach((row) => {
      markets.forEach((m) => {
        if (!hidden.has(m.id) && row[m.id] != null) {
          min = Math.min(min, row[m.id]);
          max = Math.max(max, row[m.id]);
        }
      });
    });
    const pad = Math.max((max - min) * 0.12, 3);
    return [Math.floor(min - pad), Math.ceil(max + pad)];
  }, [chartData, hidden, markets]);

  const toggleMarket = (id) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const visibleMarkets = markets.filter((m) => !hidden.has(m.id));

  return (
    <div data-testid={dataTestId}>
      <div className="macro-panel-header">
        <span>{title}</span>
        <span>{subtitle || `${period} · indexed to start`}</span>
      </div>

      <div className="macro-chart-wrap macro-chart-wrap-tall">
        {loading && (
          <div className="macro-loading"><Loader2 size={20} className="spinner" /> Loading market data…</div>
        )}
        {error && !loading && (
          <div className="macro-loading">
            <span style={{ color: '#fca5a5' }}>{error}</span>
            <button type="button" className="macro-pill active" style={{ marginTop: 8 }} onClick={fetchData}>
              Retry
            </button>
          </div>
        )}
        {!error && !loading && chartData.length === 0 && (
          <div className="macro-loading">Chart data unavailable.</div>
        )}
        {!error && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={compactLegend ? 220 : 260}>
            <LineChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="rgba(255,255,255,0.04)" vertical={false} />
              <XAxis
                dataKey="date"
                ticks={xTicks}
                tick={{ fill: '#64748b', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                domain={yDomain}
                tickFormatter={yTickFmt}
                tick={{ fill: '#64748b', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ stroke: 'rgba(255,255,255,0.15)', strokeWidth: 1 }} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" strokeDasharray="4 4" />
              {visibleMarkets.map((m) => (
                <Line
                  key={m.id}
                  type="monotone"
                  dataKey={m.id}
                  name={m.label}
                  stroke={m.color}
                  strokeWidth={1.6}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>

      <div className="macro-markets-legend">
        <div className="macro-markets-legend-hint">
          {visibleMarkets.length} of {markets.length} shown — click to toggle
        </div>
        <div className={compactLegend ? 'macro-markets-legend-grid compact' : 'macro-markets-legend-grid'}>
          {markets.map((m) => {
            const change = latestChange[m.id];
            const isHidden = hidden.has(m.id);
            const tone = change == null ? '' : change >= 0 ? 'pos' : 'neg';
            return (
              <button
                key={m.id}
                type="button"
                className={`macro-markets-legend-item ${isHidden ? 'hidden' : ''}`}
                onClick={() => toggleMarket(m.id)}
                title={`${isHidden ? 'Show' : 'Hide'} ${m.label}`}
              >
                <span className="macro-markets-dot" style={{ background: m.color, boxShadow: isHidden ? 'none' : `0 0 6px ${m.color}80` }} />
                <span className="macro-markets-label">{m.label}</span>
                <span className={`macro-markets-change ${tone}`}>
                  {change == null ? '—' : formatPct(change, 2)}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
