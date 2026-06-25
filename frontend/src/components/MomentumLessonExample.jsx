import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, ExternalLink } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';
import { extractMomentumReadout } from '../components/MomentumInfoTip';
import { readMomentumAcademyContext } from '../utils/momentumAcademyContext';

function fmtScore(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(1);
}

function fmtUsd(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `$${Number(v).toFixed(2)}`;
}

/**
 * Stock-specific worked example for L2M6 when opened from dashboard momentum ?.
 * Generic intro when user lands on Academy without a ticker context.
 */
export default function MomentumLessonExample({ ticker }) {
  const sym = (ticker || '').trim().toUpperCase();
  const [readout, setReadout] = useState(null);
  const [valuationSnap, setValuationSnap] = useState(null);
  const [loading, setLoading] = useState(Boolean(sym));
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!sym) {
      setReadout(null);
      setValuationSnap(null);
      setLoading(false);
      return;
    }

    const cached = readMomentumAcademyContext(sym);
    if (cached?.readout) {
      setReadout(cached.readout);
      setValuationSnap(cached.valuation);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    apiFetch(`${API_BASE_URL}/decision-terminal/snapshot?ticker=${encodeURIComponent(sym)}`)
      .then((payload) => {
        if (cancelled) return;
        setReadout(extractMomentumReadout(payload?.valuation));
        setValuationSnap(
          payload?.valuation
            ? {
                current_price_usd: payload.valuation.current_price_usd,
                valuation_gap_pct: payload.valuation.valuation_gap_pct,
                valuation_signal: payload.valuation.valuation_signal,
                average_fair_value_usd: payload.valuation.average_fair_value_usd,
              }
            : null,
        );
      })
      .catch(() => {
        if (!cancelled) setError('Could not load a live snapshot for this ticker.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [sym]);

  if (!sym) {
    return (
      <section className="acad-momentum-example acad-momentum-example--generic" data-testid="momentum-lesson-generic">
        <h3 className="acad-momentum-example-title">How this lesson works</h3>
        <p className="acad-momentum-example-lead">
          This module teaches the momentum pricing model in general — scores, downside zones,
          and how to pair momentum with fair value.
        </p>
        <p className="acad-momentum-example-tip">
          <strong>Want a live worked example?</strong> Run any ticker on the Unified Dashboard,
          open <em>Consensus Valuation Signal</em>, and click the <strong>Momentum ?</strong> icon.
          You&apos;ll return here with that stock&apos;s snapshot explained step by step.
        </p>
      </section>
    );
  }

  if (loading) {
    return (
      <section className="acad-momentum-example" data-testid="momentum-lesson-example">
        <div className="acad-momentum-example-loading">
          <Loader2 className="spinner" size={20} />
          <span>Loading {sym} momentum snapshot…</span>
        </div>
      </section>
    );
  }

  if (error || !readout) {
    return (
      <section className="acad-momentum-example acad-momentum-example--warn" data-testid="momentum-lesson-example">
        <h3 className="acad-momentum-example-title">Worked example: {sym}</h3>
        <p className="acad-momentum-example-lead">
          {error || 'No momentum data for this ticker yet.'}
        </p>
        <Link to={`/dashboard?ticker=${encodeURIComponent(sym)}`} className="acad-momentum-dashboard-link">
          <ExternalLink size={14} />
          Run analysis on {sym} in the dashboard
        </Link>
      </section>
    );
  }

  const zones = readout.downside?.downside_zones || [];
  const activeFlags = readout.risk_flags_active || readout.risk_flags || [];
  const narrative = readout.final_agent_narrative || readout.agent_summary;

  return (
    <section className="acad-momentum-example" data-testid="momentum-lesson-example">
      <div className="acad-momentum-example-head">
        <h3 className="acad-momentum-example-title">Worked example: {sym}</h3>
        <p className="acad-momentum-example-lead">
          You opened this lesson from <strong>{sym}</strong> on your dashboard. Below is the same
          momentum snapshot, with notes on how to read each line before you practice on other names.
        </p>
        <Link
          to={`/dashboard?ticker=${encodeURIComponent(sym)}`}
          className="acad-momentum-dashboard-link"
        >
          <ExternalLink size={14} />
          Back to {sym} on dashboard
        </Link>
      </div>

      {valuationSnap?.current_price_usd != null && (
        <div className="acad-momentum-valuation-strip">
          <span>Price {fmtUsd(valuationSnap.current_price_usd)}</span>
          {valuationSnap.average_fair_value_usd != null && (
            <span>Base fair ${Number(valuationSnap.average_fair_value_usd).toFixed(0)}</span>
          )}
          {valuationSnap.valuation_gap_pct != null && (
            <span>
              Gap {valuationSnap.valuation_gap_pct > 0 ? '+' : ''}
              {Number(valuationSnap.valuation_gap_pct).toFixed(1)}%
            </span>
          )}
          {valuationSnap.valuation_signal && (
            <span className="acad-momentum-val-signal">{valuationSnap.valuation_signal}</span>
          )}
        </div>
      )}

      <div className="acad-momentum-score-grid">
        <div>
          <div className="acad-momentum-score-label">Momentum</div>
          <div className="acad-momentum-score-value">{fmtScore(readout.momentum_pricing_score)}</div>
          <div className="acad-momentum-score-note">Composite trend + flow strength (0–100)</div>
        </div>
        <div>
          <div className="acad-momentum-score-label">Downside exposure</div>
          <div className="acad-momentum-score-value">{fmtScore(readout.downside_exposure_score)}</div>
          <div className="acad-momentum-score-note">Pullback / trend-break risk — not fair value</div>
        </div>
        <div>
          <div className="acad-momentum-score-label">Decision quality</div>
          <div className="acad-momentum-score-value">{fmtScore(readout.decision_quality_score)}</div>
          <div className="acad-momentum-score-note">Setup quality after downside penalty</div>
        </div>
      </div>

      {(readout.classification || readout.crash_risk) && (
        <p className="acad-momentum-meta">
          {readout.classification && <span className="acad-momentum-badge">{readout.classification}</span>}
          {readout.crash_risk && (
            <span className="acad-momentum-badge muted">Crash risk: {readout.crash_risk}</span>
          )}
        </p>
      )}

      {readout.model_read && <p className="acad-momentum-read">{readout.model_read}</p>}

      {readout.subscores && (
        <ul className="acad-momentum-subscores">
          {readout.subscores.absolute_price_momentum != null && (
            <li>
              <strong>Absolute momentum</strong> {fmtScore(readout.subscores.absolute_price_momentum)}/100 —
              price vs key technical levels
            </li>
          )}
          {readout.subscores.relative_momentum != null && (
            <li>
              <strong>Relative momentum</strong> {fmtScore(readout.subscores.relative_momentum)}/100 —
              excess return vs SPY and sector
            </li>
          )}
          {readout.subscores.capital_flow_confirmation != null && (
            <li>
              <strong>Capital flow</strong> {fmtScore(readout.subscores.capital_flow_confirmation)}/100 —
              volume, CMF, OBV, VWAP confirmation
            </li>
          )}
        </ul>
      )}

      {zones.length > 0 && (
        <div className="acad-momentum-zones">
          <div className="acad-momentum-zones-title">Downside zones for {sym}</div>
          <ul>
            {zones.map((z) => (
              <li key={z.label}>
                <strong>{z.label}</strong> ${Number(z.level_usd).toFixed(2)}
                {z.pullback_pct != null ? ` · ${z.pullback_pct}% pullback` : ''}
                {z.meaning ? ` — ${z.meaning}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      {activeFlags.length > 0 && (
        <div className="acad-momentum-flags">
          <div className="acad-momentum-flags-title">Active risk flags</div>
          <ul>
            {activeFlags.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </div>
      )}

      {narrative && (
        <p className="acad-momentum-narrative">
          <strong>Agent read for {sym}:</strong> {narrative}
        </p>
      )}

      {readout.as_of_date && (
        <p className="acad-momentum-as-of">Snapshot as of {readout.as_of_date}</p>
      )}
    </section>
  );
}
