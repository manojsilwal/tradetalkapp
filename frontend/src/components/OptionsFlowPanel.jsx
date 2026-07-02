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

function fmtPctNum(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${Number(v).toFixed(1)}%`;
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
  const spot = options.spot_price_usd;
  const moveUsd = options.expected_move_usd;
  const movePct = options.expected_move_pct;

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

        {options.narrative_summary && (
          <p
            style={{
              fontSize: '0.82rem',
              lineHeight: 1.45,
              margin: '0 0 12px',
              padding: '10px 12px',
              borderRadius: 8,
              background: 'rgba(99,102,241,0.1)',
              border: '1px solid rgba(99,102,241,0.25)',
              color: '#c7d2fe',
            }}
          >
            {options.narrative_summary}
          </p>
        )}

        {(options.call_oi_pct != null || options.call_volume_pct != null) && (
          <section style={{ marginBottom: 14 }}>
            <h3 className="dt-subblock-title">Bull vs bear contracts</h3>
            <table className="dt-valuation-metrics" style={{ width: '100%', marginTop: 6, fontSize: '0.78rem' }}>
              <thead>
                <tr>
                  <th scope="col" />
                  <th scope="col">Calls (bull)</th>
                  <th scope="col">Puts (bear)</th>
                  <th scope="col">P/C ratio</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">Open interest</th>
                  <td>
                    {fmtInt(options.total_call_oi)} ({fmtPctNum(options.call_oi_pct)})
                  </td>
                  <td>
                    {fmtInt(options.total_put_oi)} ({fmtPctNum(options.put_oi_pct)})
                  </td>
                  <td>
                    {fmtRatio(options.put_call_oi_ratio)}
                    {options.oi_sentiment && (
                      <span className={`dt-expert-pill ${biasClass(options.oi_sentiment)}`} style={{ marginLeft: 6 }}>
                        {options.oi_sentiment}
                      </span>
                    )}
                  </td>
                </tr>
                <tr>
                  <th scope="row">Volume</th>
                  <td>
                    {fmtInt(options.total_call_volume)} ({fmtPctNum(options.call_volume_pct)})
                  </td>
                  <td>
                    {fmtInt(options.total_put_volume)} ({fmtPctNum(options.put_volume_pct)})
                  </td>
                  <td>
                    {fmtRatio(options.put_call_volume_ratio)}
                    {options.volume_sentiment && options.volume_sentiment !== options.oi_sentiment && (
                      <span className={`dt-expert-pill ${biasClass(options.volume_sentiment)}`} style={{ marginLeft: 6 }}>
                        {options.volume_sentiment}
                      </span>
                    )}
                  </td>
                </tr>
              </tbody>
            </table>
          </section>
        )}

        {(movePct != null || options.iv_rank_proxy != null) && (
          <dl className="dt-valuation-metrics" style={{ marginBottom: 12 }}>
            {movePct != null && (
              <div className="dt-valuation-metrics-row">
                <dt>Expected move ({options.nearest_expiry || 'nearest'})</dt>
                <dd>
                  ±{fmtPctNum(movePct)}
                  {moveUsd != null && spot != null && (
                    <span style={{ opacity: 0.85 }}>
                      {' '}
                      (±{fmtUsd(moveUsd)} → ~{fmtUsd(spot - moveUsd)}–{fmtUsd(spot + moveUsd)})
                    </span>
                  )}
                </dd>
              </div>
            )}
            {options.iv_rank_proxy != null && (
              <div className="dt-valuation-metrics-row">
                <dt>IV percentile (chain proxy)</dt>
                <dd>{fmtPctNum(options.iv_rank_proxy)}</dd>
              </div>
            )}
            {options.near_expiry_flag && options.near_expiry_oi_pct != null && (
              <div className="dt-valuation-metrics-row">
                <dt>Near expiry (7d OI)</dt>
                <dd>{fmtPctNum(options.near_expiry_oi_pct)} of total OI</dd>
              </div>
            )}
          </dl>
        )}

        {(options.top_call_strikes?.length > 0 || options.top_put_strikes?.length > 0) && (
          <section style={{ marginBottom: 12 }}>
            <h3 className="dt-subblock-title">Strike walls</h3>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px 24px', fontSize: '0.78rem' }}>
              {options.top_call_strikes?.length > 0 && (
                <div>
                  <strong>Call resistance:</strong>{' '}
                  {options.top_call_strikes.map((r) => `$${r.strike} (${fmtInt(r.open_interest)} OI)`).join(', ')}
                </div>
              )}
              {options.top_put_strikes?.length > 0 && (
                <div>
                  <strong>Put support:</strong>{' '}
                  {options.top_put_strikes.map((r) => `$${r.strike} (${fmtInt(r.open_interest)} OI)`).join(', ')}
                </div>
              )}
            </div>
          </section>
        )}

        <dl className="dt-valuation-metrics">
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
