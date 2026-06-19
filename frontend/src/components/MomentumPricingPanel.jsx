import React, { useMemo } from 'react';
import { Loader2, TrendingUp, AlertTriangle, ShieldCheck } from 'lucide-react';

const DISCLAIMER =
  'Educational model output only — not financial advice. Uses the latest market-data snapshot available from connected providers.';

export function extractMomentumReadout(valuation) {
  const model = (valuation?.models || []).find((m) => m.name === 'Momentum');
  return model?.momentum_summary ?? null;
}

function fmtScore(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(1);
}

function ScorePill({ label, value, accent }) {
  return (
    <div className="mom-score-pill">
      <div className="mom-score-pill-label">{label}</div>
      <div className="mom-score-pill-value" style={accent ? { color: accent } : undefined}>
        {fmtScore(value)}
      </div>
    </div>
  );
}

export default function MomentumPricingPanel({ readout, loading, ticker, className = '' }) {
  const summaryJson = useMemo(() => {
    if (!readout) return null;
    return {
      ticker: readout.ticker || ticker,
      latest_price_used: readout.latest_price_used ?? readout.indicators?.close,
      momentum_pricing_score: readout.momentum_pricing_score,
      downside_exposure_score: readout.downside_exposure_score,
      decision_quality_score: readout.decision_quality_score,
      classification: readout.classification,
      crash_risk: readout.crash_risk,
      model_read: readout.model_read,
    };
  }, [readout, ticker]);

  if (loading) {
    return (
      <section className={`dt-panel mom-panel ${className}`} data-testid="momentum-pricing-panel">
        <h2 className="dt-panel-title">Momentum Stock Pricing Analysis</h2>
        <div className="mom-loading">
          <Loader2 className="spinner" size={22} />
          <span>Computing momentum model…</span>
        </div>
      </section>
    );
  }

  if (!readout) {
    return (
      <section className={`dt-panel mom-panel ${className}`} data-testid="momentum-pricing-panel">
        <h2 className="dt-panel-title">Momentum Stock Pricing Analysis</h2>
        <p className="mom-disclaimer">{DISCLAIMER}</p>
        <p className="mom-empty">Run analysis to load the momentum pricing snapshot.</p>
      </section>
    );
  }

  const sym = readout.ticker || ticker || '—';
  const breakdown = readout.component_breakdown || [];
  const techRows = readout.technical_positioning || [];
  const zones = readout.downside?.downside_zones || [];
  const activeFlags = readout.risk_flags_active || readout.risk_flags || [];
  const clearFlags = readout.risk_flags_clear || [];

  return (
    <section className={`dt-panel mom-panel ${className}`} data-testid="momentum-pricing-panel">
      <div className="mom-head">
        <h2 className="dt-panel-title">
          {sym} Momentum Stock Pricing Analysis
        </h2>
        <p className="mom-disclaimer">{DISCLAIMER}</p>
        {readout.as_of_date && (
          <p className="mom-as-of">Pricing snapshot as of {readout.as_of_date}</p>
        )}
      </div>

      {/* 1. Final model output */}
      <div className="mom-block">
        <h3 className="mom-block-title">1. Final Model Output</h3>
        <div className="mom-score-grid">
          <ScorePill label="Momentum score" value={readout.momentum_pricing_score} accent="#00ff88" />
          <ScorePill label="Downside exposure" value={readout.downside_exposure_score} accent="#fbbf24" />
          <ScorePill label="Decision quality" value={readout.decision_quality_score} accent="#8b5cf6" />
        </div>
        <div className="mom-classification-row">
          <span className="mom-badge">{readout.classification || '—'}</span>
          {readout.crash_risk && (
            <span className="mom-badge muted">Crash risk: {readout.crash_risk}</span>
          )}
        </div>
        {readout.model_read && <p className="mom-read">{readout.model_read}</p>}
        {summaryJson && (
          <pre className="mom-json" aria-label="Momentum model JSON summary">
            {JSON.stringify(summaryJson, null, 2)}
          </pre>
        )}
      </div>

      {/* 2. Score breakdown */}
      {breakdown.length > 0 && (
        <div className="mom-block">
          <h3 className="mom-block-title">2. Momentum Score Breakdown</h3>
          <div className="mom-table-wrap">
            <table className="mom-table">
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Weight</th>
                  <th>Score</th>
                  <th>Read</th>
                </tr>
              </thead>
              <tbody>
                {breakdown.map((row) => (
                  <tr key={row.component}>
                    <td>{row.component}</td>
                    <td>{row.weight_pct}%</td>
                    <td className="mom-num">{fmtScore(row.score)}</td>
                    <td className="mom-read-cell">{row.read}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mom-footnote">
            Final Momentum Pricing Score: <strong>{fmtScore(readout.momentum_pricing_score)} / 100</strong>
          </p>
        </div>
      )}

      {/* 3. Technical positioning */}
      {techRows.length > 0 && (
        <div className="mom-block">
          <h3 className="mom-block-title">3. Absolute Price Momentum — Key Levels</h3>
          <div className="mom-table-wrap">
            <table className="mom-table mom-table-compact">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Value</th>
                </tr>
              </thead>
              <tbody>
                {techRows.map((row) => (
                  <tr key={row.metric}>
                    <td>{row.metric}</td>
                    <td className="mom-num">{row.value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {readout.subscores?.absolute_price_momentum != null && (
            <p className="mom-footnote">
              Absolute Momentum Score: <strong>{fmtScore(readout.subscores.absolute_price_momentum)} / 100</strong>
            </p>
          )}
        </div>
      )}

      {/* 4–6. Component highlights */}
      <div className="mom-block mom-mini-grid">
        {readout.subscores?.relative_momentum != null && (
          <div className="mom-mini-card">
            <TrendingUp size={16} />
            <div className="mom-mini-title">Relative Momentum</div>
            <div className="mom-mini-score">{fmtScore(readout.subscores.relative_momentum)} / 100</div>
            <p className="mom-mini-text">
              Separates broad-market strength from industry leadership using 6-month excess returns vs SPY and sector.
            </p>
          </div>
        )}
        {readout.subscores?.capital_flow_confirmation != null && (
          <div className="mom-mini-card">
            <div className="mom-mini-title">Capital Flow Confirmation</div>
            <div className="mom-mini-score">{fmtScore(readout.subscores.capital_flow_confirmation)} / 100</div>
            <p className="mom-mini-text">
              Relative volume, CMF, OBV slope, and anchored VWAP — supportive when elevated volume confirms trend.
            </p>
          </div>
        )}
      </div>

      {/* 7. Downside zones */}
      {(zones.length > 0 || readout.downside) && (
        <div className="mom-block">
          <h3 className="mom-block-title">Downside Exposure Analysis</h3>
          <p className="mom-read">
            Downside Exposure Score: <strong>{fmtScore(readout.downside_exposure_score)} / 100</strong>
            {readout.downside?.mild_pullback_estimate && (
              <> · Mild pullback {readout.downside.mild_pullback_estimate}</>
            )}
            {readout.downside?.trend_damage_estimate && (
              <> · Trend damage {readout.downside.trend_damage_estimate}</>
            )}
          </p>
          {zones.length > 0 && (
            <div className="mom-table-wrap">
              <table className="mom-table">
                <thead>
                  <tr>
                    <th>Zone</th>
                    <th>Level</th>
                    <th>Pullback</th>
                    <th>Meaning</th>
                  </tr>
                </thead>
                <tbody>
                  {zones.map((z) => (
                    <tr key={z.label}>
                      <td>{z.label}</td>
                      <td className="mom-num">${Number(z.level_usd).toFixed(2)}</td>
                      <td className="mom-num">{z.pullback_pct != null ? `${z.pullback_pct}%` : '—'}</td>
                      <td>{z.meaning}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* 8. Risk flags */}
      {(activeFlags.length > 0 || clearFlags.length > 0) && (
        <div className="mom-block mom-flags">
          {activeFlags.length > 0 && (
            <div className="mom-flag-col">
              <h3 className="mom-block-title">
                <AlertTriangle size={16} /> Active risk flags
              </h3>
              <ul>
                {activeFlags.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </div>
          )}
          {clearFlags.length > 0 && (
            <div className="mom-flag-col clear">
              <h3 className="mom-block-title">
                <ShieldCheck size={16} /> Not triggered
              </h3>
              <ul>
                {clearFlags.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* 9. Agent summary */}
      <div className="mom-block mom-summary">
        <h3 className="mom-block-title">Final Agent Summary</h3>
        <p className="mom-read">{readout.final_agent_narrative || readout.agent_summary}</p>
      </div>
    </section>
  );
}
