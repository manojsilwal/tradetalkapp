import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Download, Loader2, RefreshCw } from 'lucide-react';
import GlobalMarketsChartPanel from './GlobalMarketsChartPanel';
import ValueChainSpendPanel from './ValueChainSpendPanel';
import { marketsForTab, marketTickerList } from './globalMarketsConfig';
import {
  formatCompactUSD,
  formatLargeUSD,
  formatPct,
  computeRotationVelocity,
  computeFlowConcentration,
  regimeLabel,
  forecastConfidence,
  buildSectorRows,
  buildIntlBucketRows,
  mapMarketsPeriod,
  bucketPeriodKey,
  PERIOD_OPTIONS,
} from './macroUtils';

const TABS = [
  { id: 'sector', label: 'US Sector Rotation' },
  { id: 'intl', label: "Int'l ETF Flow" },
];

function heatmapTone(flowUsd) {
  if (flowUsd == null) return 'neutral';
  if (flowUsd > 0) return 'pos';
  if (flowUsd < 0) return 'neg';
  return 'neutral';
}

export default function GlobalCapFlowDashboard({
  macroData,
  loading,
  onRefresh,
  refreshing,
}) {
  const [tab, setTab] = useState('sector');
  const [period, setPeriod] = useState('YTD');
  const [periodReturns, setPeriodReturns] = useState({});
  const [seriesError, setSeriesError] = useState(null);

  const activeMarkets = useMemo(() => marketsForTab(tab), [tab]);
  const marketsPeriod = mapMarketsPeriod(period);

  const handleSeriesLoaded = useCallback((returns) => {
    setPeriodReturns(returns);
  }, []);

  const sectorRows = useMemo(
    () => buildSectorRows(macroData?.sectors, periodReturns),
    [macroData?.sectors, periodReturns],
  );

  const intlRows = useMemo(() => {
    const buckets = macroData?.reconciled_capital_flows?.buckets;
    const fromBuckets = buildIntlBucketRows(buckets, bucketPeriodKey(period));
    if (fromBuckets.length > 0) return fromBuckets;
    return activeMarkets
      .filter((m) => periodReturns[m.id] != null)
      .map((m) => ({
        symbol: m.id,
        name: m.label,
        pctChange: periodReturns[m.id],
        flowUsd: null,
        isBenchmark: m.id === 'SPY',
      }))
      .sort((a, b) => {
        if (a.symbol === 'SPY') return -1;
        if (b.symbol === 'SPY') return 1;
        return (b.pctChange ?? 0) - (a.pctChange ?? 0);
      });
  }, [macroData?.reconciled_capital_flows, period, periodReturns, activeMarkets]);

  const heatmapRows = tab === 'sector' ? sectorRows : intlRows;

  const usTotalFlow = useMemo(() => {
    if (tab === 'intl') {
      const fromFlows = intlRows.reduce((sum, r) => sum + (r.flowUsd ?? 0), 0);
      if (fromFlows !== 0 || intlRows.some((r) => r.flowUsd != null)) return fromFlows;
      return null;
    }
    const usBuckets = macroData?.reconciled_capital_flows?.buckets?.filter((b) => b.is_us_destination) || [];
    if (usBuckets.length) {
      return usBuckets.reduce((sum, b) => sum + (b.component_flow_usd ?? 0), 0);
    }
    return sectorRows.reduce((sum, r) => sum + (r.flowUsd ?? 0), 0);
  }, [tab, sectorRows, intlRows, macroData?.reconciled_capital_flows]);

  const rotationVelocity = computeRotationVelocity(tab === 'sector' ? sectorRows : intlRows);
  const flowConcentration = computeFlowConcentration(tab === 'sector' ? sectorRows : intlRows);
  const regime = regimeLabel(macroData?.market_regime, macroData?.credit_stress_index);
  const forecast = forecastConfidence(macroData?.vix_level, macroData?.credit_stress_index);

  const intlLeaders = useMemo(() => {
    if (tab !== 'intl') return [];
    return activeMarkets
      .map((m) => ({ symbol: m.id, name: m.label, pct: periodReturns[m.id] }))
      .filter((r) => r.pct != null)
      .sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0))
      .slice(0, 6);
  }, [tab, activeMarkets, periodReturns]);

  const handleDownload = () => {
    const payload = {
      tab,
      period,
      usTotalFlow,
      rotationVelocity,
      regime: regime.label,
      sectors: sectorRows,
      intl: intlRows,
      topMovers: tab === 'sector' ? null : intlLeaders,
      markets: marketTickerList(activeMarkets),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `global_cap_flow_${tab}_${period.toLowerCase()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const chartSubtitle = tab === 'sector'
    ? 'US sectors · SPY · treasuries'
    : 'SPY vs intl equities · gold · crypto';

  return (
    <div data-testid="global-cap-flow-dashboard">
      <div className="macro-header">
        <div className="macro-title-block">
          <h1>GLOBAL_CAP_FLOW</h1>
          <p className="macro-subtitle">Data_stream :: rotation_analysis</p>
          <p className="macro-page-label">Global Macro</p>
        </div>
        <div className="macro-toolbar">
          <div className="macro-pill-group" role="group" aria-label="Time range">
            {PERIOD_OPTIONS.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`macro-pill ${period === p.id ? 'active' : ''}`}
                onClick={() => setPeriod(p.id)}
              >
                {p.label}
              </button>
            ))}
          </div>
          <button
            type="button"
            className="macro-icon-btn"
            onClick={() => { onRefresh?.(); }}
            disabled={refreshing}
            aria-label="Refresh data"
          >
            {refreshing ? <Loader2 size={16} className="spinner" /> : <RefreshCw size={16} />}
          </button>
          <button type="button" className="macro-icon-btn" onClick={handleDownload} aria-label="Download snapshot">
            <Download size={16} />
          </button>
        </div>
      </div>

      <div className="macro-tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`macro-tab ${tab === t.id ? 'active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {seriesError && <div className="macro-error">{seriesError}</div>}

      <div className="macro-metrics">
        <div className="macro-metric-card">
          <div className="macro-metric-label">{tab === 'intl' ? 'Total Intl Flow' : 'Total US Flow'}</div>
          <div className={`macro-metric-value ${usTotalFlow == null ? '' : usTotalFlow >= 0 ? 'pos' : 'neg'}`}>
            {usTotalFlow != null ? (
              <>
                {formatLargeUSD(Math.abs(usTotalFlow))}
                {usTotalFlow >= 0 ? ' ↑' : ' ↓'}
              </>
            ) : '—'}
          </div>
        </div>
        <div className="macro-metric-card">
          <div className="macro-metric-label">Rotation Velocity</div>
          <div className="macro-metric-value">
            {rotationVelocity != null ? `${rotationVelocity.toFixed(2)} SD` : '—'}
          </div>
        </div>
        <div className="macro-metric-card" data-testid="macro-regime-metric">
          <div className="macro-metric-label">Market Regime</div>
          <span className={`macro-regime-badge ${regime.tone}`}>{regime.label}</span>
        </div>
        <div className="macro-metric-card">
          <div className="macro-metric-label">Flow Concentration</div>
          <div className="macro-metric-value">{flowConcentration != null ? flowConcentration.toFixed(1) : '—'}</div>
          <div className="macro-concentration-bar">
            <div
              className="macro-concentration-fill"
              style={{ width: `${Math.min(flowConcentration ?? 0, 100)}%` }}
            />
          </div>
        </div>
        <div className="macro-metric-card">
          <div className="macro-metric-label">Forecast</div>
          <div className={`macro-metric-value ${forecast.tone === 'high' ? 'pos' : forecast.tone === 'low' ? 'neg' : ''}`}>
            {forecast.label}
          </div>
        </div>
      </div>

      <div className="macro-grid">
        <div className="macro-panel" data-testid="macro-sector-heatmap">
          <div className="macro-panel-header">
            <span>{tab === 'intl' ? '# Intl_etf_flow' : '# Sector_heatmap'}</span>
            <span>Net_flow_{period.toLowerCase()}</span>
          </div>
          <div className="macro-panel-body">
            {loading && !macroData ? (
              <div className="macro-loading"><Loader2 size={20} className="spinner" /> Loading…</div>
            ) : heatmapRows.length === 0 ? (
              <div className="macro-loading">No flow data for this view.</div>
            ) : (
              heatmapRows.map((row) => {
                const tone = heatmapTone(row.flowUsd ?? row.pctChange);
                const displayValue = row.flowUsd != null
                  ? formatCompactUSD(row.flowUsd)
                  : formatPct(row.pctChange);
                return (
                  <div key={row.symbol} className={`macro-heatmap-row ${tone}${row.isBenchmark ? ' benchmark' : ''}`}>
                    <span className="macro-heatmap-symbol">{row.symbol}</span>
                    <span className="macro-heatmap-name">{row.name}</span>
                    <span className={`macro-heatmap-flow ${tone === 'pos' ? 'pos' : tone === 'neg' ? 'neg' : ''}`}>
                      {displayValue}
                    </span>
                  </div>
                );
              })
            )}
          </div>
          <p className="macro-footnote" style={{ padding: '0 12px 10px' }}>
            {tab === 'intl'
              ? 'SPY pinned as US benchmark; intl bucket flows from reconciled proxies; % shown when $ unavailable.'
              : 'Flow estimates use ETF AUM proxy × period return.'}
          </p>
        </div>

        <div className={`macro-right-stack ${tab === 'sector' ? 'macro-right-stack-single' : ''}`}>
          <div className="macro-panel macro-panel-chart" data-testid="macro-momentum-chart">
            <GlobalMarketsChartPanel
              key={`${tab}-${marketsPeriod}`}
              markets={activeMarkets}
              period={marketsPeriod}
              title="Global_capital_flows"
              subtitle={`${period} · ${chartSubtitle}`}
              dataTestId="global-markets-chart"
              onSeriesLoaded={handleSeriesLoaded}
              compactLegend
            />
          </div>

          {tab === 'intl' && (
            <div className="macro-panel" data-testid="macro-top-beneficiaries">
              <div className="macro-panel-header">
                <span>Top_intl_performers :: period_leaders</span>
              </div>
              <div className="macro-panel-body" style={{ padding: 0 }}>
                {intlLeaders.length === 0 ? (
                  <div className="macro-loading">Loading intl performance…</div>
                ) : (
                  <table className="macro-table">
                    <thead>
                      <tr>
                        <th>Ticker</th>
                        <th>Market</th>
                        <th>Period_move</th>
                      </tr>
                    </thead>
                    <tbody>
                      {intlLeaders.map((row) => {
                        const tone = (row.pct ?? 0) >= 0 ? 'pos' : 'neg';
                        return (
                          <tr key={row.symbol}>
                            <td className="ticker">{row.symbol}</td>
                            <td>{row.name}</td>
                            <td className={`move ${tone}`}>{formatPct(row.pct, 2)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {tab === 'sector' && (
        <div className="macro-section" style={{ marginTop: 12 }}>
          <ValueChainSpendPanel />
        </div>
      )}
    </div>
  );
}
