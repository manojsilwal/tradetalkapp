import React from 'react';
import { cleanSource } from '../freshness';
import { FreshnessBadge } from './Freshness';

function fmtRatio(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(2);
}

function fmtPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(1)}%`;
}

function fmtInt(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toLocaleString();
}

function fmtUsd(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `$${Number(v).toFixed(2)}`;
}

function biasClass(bias) {
  if (bias === 'bullish') return 'bull';
  if (bias === 'bearish') return 'bear';
  return 'neutral';
}

export default function OptionsFlowPanel({
  options,
  hasData,
  loading,
  loadingFallback = null,
  ticker = '',
}) {
  if (loading && !hasData) {
    return loadingFallback;
  }

  const freshness = options?.as_of
    ? {
        captured_at: options.as_of,
        source: options.source,
        label: options.partial ? 'Partial chain' : 'EOD aggregate',
        is_delayed: true,
      }
    : null;

  if (!hasData || !options) {
    return (
      <div data-testid="options-flow-panel">
        <p className="dt-prompt-banner" style={{ margin: 0 }}>
          Options flow data is unavailable for {ticker || 'this ticker'}. Free providers may be
          rate-limited or the symbol may lack listed options.
        </p>
      </div>
    );
  }

  const unusual = options.unusual_contracts || [];

  return (
    <div className="dt-valuation-split" data-testid="options-flow-panel">
      <div className="dt-valuation-detail" style={{ gridColumn: '1 / -1' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, flexWrap: 'wrap' }}>
          {freshness && <FreshnessBadge freshness={freshness} showEod />}
          {options.source && (
            <span className="dt-expert-pill neutral" title="Data provider">
              {cleanSource(options.source)}
              {options.partial ? ' · partial' : ''}
            </span>
          )}
        </div>

        <dl className="dt-valuation-metrics">
          <div className="dt-valuation-metrics-row">
            <dt>Put/call volume ratio</dt>
            <dd>{fmtRatio(options.put_call_volume_ratio)}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Put/call OI ratio</dt>
            <dd>{fmtRatio(options.put_call_oi_ratio)}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Total call OI</dt>
            <dd>{fmtInt(options.total_call_oi)}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Total put OI</dt>
            <dd>{fmtInt(options.total_put_oi)}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>ATM IV (call / put)</dt>
            <dd>
              {fmtPct(options.iv_atm_call)} / {fmtPct(options.iv_atm_put)}
            </dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>IV skew (put − call)</dt>
            <dd>{fmtPct(options.iv_skew)}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Unusual activity score</dt>
            <dd>{options.unusual_activity_score != null ? `${options.unusual_activity_score}/100` : '—'}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Net premium bias</dt>
            <dd>
              <span className={`dt-expert-pill ${biasClass(options.net_premium_bias)}`}>
                {options.net_premium_bias || 'neutral'}
              </span>
            </dd>
          </div>
        </dl>

        {unusual.length > 0 && (
          <section aria-labelledby="options-unusual-heading">
            <h3 id="options-unusual-heading" className="dt-subblock-title">
              <span className="sr-only">Unusual options activity for {ticker}</span>
              Unusual activity
            </h3>
            <table className="dt-valuation-metrics" style={{ width: '100%', marginTop: 8 }}>
              <caption className="sr-only">
                Contracts with volume to open-interest ratio at or above 3
              </caption>
              <thead>
                <tr>
                  <th scope="col">Strike</th>
                  <th scope="col">Expiry</th>
                  <th scope="col">Type</th>
                  <th scope="col">Vol/OI</th>
                  <th scope="col">Premium</th>
                </tr>
              </thead>
              <tbody>
                {unusual.slice(0, 8).map((row, idx) => (
                  <tr key={`${row.strike}-${row.expiry}-${idx}`}>
                    <td>{row.strike != null ? `$${row.strike}` : '—'}</td>
                    <td>{row.expiry || '—'}</td>
                    <td>{row.type || '—'}</td>
                    <td>{row.vol_oi_ratio != null ? row.vol_oi_ratio : '—'}</td>
                    <td>{fmtUsd(row.premium)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        <p className="dt-disclaimer" style={{ marginTop: 12, marginBottom: 0, fontSize: '0.75rem' }}>
          Research only. Options aggregates use delayed, third-party chain data and may not reflect
          live market conditions.
        </p>
      </div>
    </div>
  );
}
