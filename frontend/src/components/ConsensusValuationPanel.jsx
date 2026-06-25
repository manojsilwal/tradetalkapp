import React from 'react';
import { HelpCircle } from 'lucide-react';
import { cleanSource } from '../freshness';
import { MomentumInfoTip } from './MomentumInfoTip';

function ProvenanceTip({ provenance, label }) {
  if (!provenance) return label;
  const cleanedSrc = cleanSource(provenance.source);
  const parts = [
    cleanedSrc && `Source: ${cleanedSrc}`,
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

function SemiGauge({ fillRatio, size = 'large' }) {
  const r = 42;
  const cx = 50;
  const cy = 48;
  const startAngle = Math.PI;
  const endAngle = 0;
  const angle = startAngle + fillRatio * (endAngle - startAngle);
  const needleX = cx + r * Math.cos(angle);
  const needleY = cy + r * Math.sin(angle);
  const arcPath = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;

  return (
    <svg
      className={`dt-semi-gauge ${size}`}
      viewBox="0 0 100 56"
      role="img"
      aria-label="Valuation gauge"
    >
      <path
        d={arcPath}
        className="dt-gauge-track"
        fill="none"
        strokeWidth="6"
        strokeLinecap="round"
      />
      <path
        d={arcPath}
        className="dt-gauge-fill"
        fill="none"
        strokeWidth="6"
        strokeLinecap="round"
        strokeDasharray={`${fillRatio * 132} 132`}
      />
      <circle
        cx={needleX}
        cy={needleY}
        r="4.5"
        className="dt-gauge-needle"
      />
    </svg>
  );
}

function valuationArcRatio(pctVsAverage) {
  if (pctVsAverage == null || Number.isNaN(pctVsAverage)) return 0.42;
  const c = Math.max(-35, Math.min(35, pctVsAverage));
  return (c + 35) / 70;
}

function fmtUsd(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `$${Number(v).toFixed(2)}`;
}

function fmtUsdPlainInt(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(0);
}

function fmtGapPct(gap) {
  if (gap == null || Number.isNaN(Number(gap))) return '—';
  const n = Number(gap);
  const sign = n > 0 ? '+' : '';
  if (n > 0) return `${sign}${n.toFixed(1)}% above fair value`;
  if (n < 0) return `${n.toFixed(1)}% below fair value`;
  return 'Near fair value';
}

function fmtDownside(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return '—';
  const n = Number(pct);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

function fmtDecimalPct(v) {
  if (v == null || Number.isNaN(Number(v))) return null;
  return `${(Number(v) * 100).toFixed(1)}%`;
}

const CLASSIFICATION_LABELS = {
  ai_accelerator_platform_leader: 'AI Accelerator Platform Leader',
  platform_reinvestment_supercycle: 'Platform Reinvestment Supercycle',
  asic_substitution_risk: 'ASIC Substitution Risk',
  capex_cycle_dependency: 'Capex-Cycle Dependency',
  roic_normalization_risk: 'ROIC Normalization Risk',
};

function prettyClassification(raw) {
  if (!raw) return null;
  if (CLASSIFICATION_LABELS[raw]) return CLASSIFICATION_LABELS[raw];
  return String(raw)
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/** Valuation models with USD fair values only (excludes Momentum). */
export function valuationFairValueModels(valuation) {
  return (valuation?.models || []).filter((m) => m.name !== 'Momentum');
}

export default function ConsensusValuationPanel({
  valuation,
  hasData,
  loading,
  loadingFallback = null,
  ticker = '',
}) {
  const v = valuation;
  const valFill = valuationArcRatio(v?.pct_vs_average);
  const signal = v?.valuation_signal || v?.gauge_label || '—';
  const fairModels = valuationFairValueModels(v);
  const momentumModel = (v?.models || []).find((m) => m.name === 'Momentum');
  const dcfModel = fairModels.find((m) => m.name?.includes('DCF'));
  const classificationLabel = prettyClassification(
    v?.business_classification || dcfModel?.classification?.business_type,
  );
  const marketExpectation = v?.market_expectation || dcfModel?.market_expectation;
  const impliedGrowth = fmtDecimalPct(dcfModel?.implied_growth);
  const impliedGrowth3y = fmtDecimalPct(dcfModel?.implied_growth_3y);
  const impliedGrowth5y = fmtDecimalPct(dcfModel?.implied_growth_5y);
  const impliedMargin = fmtDecimalPct(dcfModel?.implied_margin);
  const impliedRoic = fmtDecimalPct(dcfModel?.implied_roic);
  const impliedGrowthText = impliedGrowth3y && impliedGrowth5y
    ? `growth ~${impliedGrowth3y} for 3y, or ~${impliedGrowth5y} for 5y, then fade required`
    : impliedGrowth
      ? `growth ${impliedGrowth} (10y flat)`
      : null;
  const hasImplied = impliedGrowthText || impliedMargin || impliedRoic;
  const riskFlags = (v?.risk_flags && v.risk_flags.length ? v.risk_flags : dcfModel?.risk_flags) || [];
  const dcfTiers = v?.dcf_tiers || dcfModel?.dcf_tiers;
  const compositeSignal = v?.composite_signal;
  const impliedMove = Number(v?.implied_downside_pct);
  const impliedMoveLabel = Number.isNaN(impliedMove)
    ? 'Implied move'
    : impliedMove > 0
      ? 'Implied upside'
      : impliedMove < 0
        ? 'Implied downside'
        : 'Implied move';

  if (loading) {
    return loadingFallback;
  }

  return (
    <div className="dt-valuation-split" data-testid="consensus-valuation-panel">
      <div className="dt-valuation-gauge">
        <SemiGauge fillRatio={hasData ? valFill : 0.38} size="large" />
        <div className="dt-gauge-caption">{hasData ? signal : '—'}</div>
        {hasData && v?.valuation_confidence && (
          <div className="dt-gauge-sub">Confidence: {v.valuation_confidence}</div>
        )}
        {hasData && compositeSignal && (
          <div className="dt-gauge-composite" data-testid="composite-signal">{compositeSignal}</div>
        )}
      </div>
      <div className="dt-valuation-detail">
        <dl className="dt-valuation-metrics">
          <div className="dt-valuation-metrics-row">
            <dt>Base fair value</dt>
            <dd>{hasData ? `$${fmtUsdPlainInt(v?.average_fair_value_usd)}` : '—'}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Current price</dt>
            <dd>{hasData ? fmtUsd(v?.current_price_usd) : '—'}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>Valuation gap</dt>
            <dd>{hasData ? fmtGapPct(v?.valuation_gap_pct) : '—'}</dd>
          </div>
          <div className="dt-valuation-metrics-row">
            <dt>{impliedMoveLabel}</dt>
            <dd>{hasData ? fmtDownside(v?.implied_downside_pct) : '—'}</dd>
          </div>
          {hasData && dcfTiers && (
            <div className="dt-valuation-metrics-row dt-valuation-tiers-row">
              <dt>
                <ProvenanceTip
                  provenance={{
                    formula_or_note:
                      'DCF sensitivity range (not a confidence interval): fair value under progressively more optimistic growth/margin assumptions.',
                  }}
                  label="DCF sensitivity range"
                />
              </dt>
              <dd>
                <ul className="dt-dcf-tiers" data-testid="dcf-tiers">
                  {[
                    ['Bear', dcfTiers.bear],
                    ['Conservative', dcfTiers.conservative_base],
                    ['Base', dcfTiers.base],
                    ['Bull', dcfTiers.bull],
                    ['Extreme bull', dcfTiers.extreme_bull],
                  ]
                    .filter(([, val]) => val != null)
                    .map(([label, val]) => (
                      <li key={label} className="dt-dcf-tier">
                        <span className="dt-dcf-tier-label">{label}</span>
                        <span className="dt-dcf-tier-val">${fmtUsdPlainInt(val)}</span>
                      </li>
                    ))}
                </ul>
              </dd>
            </div>
          )}
          {hasData && !dcfTiers && v?.dcf_range_low_usd != null && v?.dcf_range_high_usd != null && (
            <div className="dt-valuation-metrics-row">
              <dt>DCF sensitivity range</dt>
              <dd>
                ${fmtUsdPlainInt(v.dcf_range_low_usd)}–${fmtUsdPlainInt(v.dcf_range_high_usd)}
              </dd>
            </div>
          )}
          {hasData && v?.bull_case_assessment && (
            <div className="dt-valuation-metrics-row">
              <dt>Bull case</dt>
              <dd>{v.bull_case_assessment}</dd>
            </div>
          )}
          {hasData && v?.bear_case_assessment && (
            <div className="dt-valuation-metrics-row">
              <dt>Bear case</dt>
              <dd>{v.bear_case_assessment}</dd>
            </div>
          )}
          {hasData && classificationLabel && (
            <div className="dt-valuation-metrics-row">
              <dt>Business type</dt>
              <dd>{classificationLabel}</dd>
            </div>
          )}
          {hasData && marketExpectation && (
            <div className="dt-valuation-metrics-row">
              <dt>Market is pricing</dt>
              <dd>{marketExpectation}</dd>
            </div>
          )}
          {hasData && hasImplied && (
            <div className="dt-valuation-metrics-row">
              <dt>
                <ProvenanceTip
                  provenance={{
                    source: 'reverse_dcf',
                    formula_or_note:
                      'Reverse DCF: the growth / operating margin / ROIC the current price implies, solved one at a time. Growth is read as a high-growth phase (held, then faded), not a flat 10-year rate.',
                  }}
                  label="Market-implied"
                />
              </dt>
              <dd>
                {[
                  impliedGrowthText,
                  impliedMargin && `margin ${impliedMargin}`,
                  impliedRoic && `ROIC ${impliedRoic}`,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </dd>
            </div>
          )}
        </dl>
        {hasData && riskFlags.length > 0 && (
          <div className="dt-valuation-risk-flags" data-testid="valuation-risk-flags">
            {riskFlags.map((flag) => (
              <span key={flag} className="dt-risk-flag-chip">
                {prettyClassification(flag)}
              </span>
            ))}
          </div>
        )}
        <div className="dt-valuation-models">
          <div className="dt-models-heading">Valuation models</div>
          <ul className="dt-models-list">
            {fairModels.map((m) => (
              <li key={m.name} className="dt-models-li">
                <span className="dt-models-name">
                  <ProvenanceTip provenance={m.provenance} label={`${m.name}:`} />
                </span>
                <span
                  className={
                    m.available && m.fair_value_usd != null ? 'dt-models-val' : 'dt-models-na'
                  }
                >
                  {m.available && m.fair_value_usd != null
                    ? (
                      <>
                        ${fmtUsdPlainInt(m.fair_value_usd)}
                        {m.name?.includes('DCF') && m.scenarios?.bear != null && m.scenarios?.bull != null && (
                          <span className="dt-models-range">
                            ({fmtUsdPlainInt(m.scenarios.bear)}–{fmtUsdPlainInt(m.scenarios.bull)})
                          </span>
                        )}
                      </>
                    )
                    : '—'}
                </span>
              </li>
            ))}
            {momentumModel && (
              <li className="dt-models-li">
                <span className="dt-models-name">
                  <MomentumInfoTip
                    readout={momentumModel.momentum_summary}
                    provenance={momentumModel.provenance}
                    ticker={ticker}
                    valuation={v}
                  />
                </span>
                <span
                  className={
                    momentumModel.available && momentumModel.momentum_score != null
                      ? 'dt-models-val'
                      : 'dt-models-na'
                  }
                >
                  {momentumModel.available && momentumModel.momentum_score != null
                    ? `${Number(momentumModel.momentum_score).toFixed(0)}/100`
                    : hasData
                      ? '—'
                      : '—'}
                </span>
              </li>
            )}
            {hasData && (
              <li className="dt-models-li dt-models-average">
                <span className="dt-models-name">Base fair (avg):</span>
                <span className="dt-models-val">{`$${fmtUsdPlainInt(v?.average_fair_value_usd)}`}</span>
              </li>
            )}
            {!hasData && (
              <li className="dt-models-li dt-models-placeholder">
                <span>Base fair (avg):</span>
                <span>—</span>
              </li>
            )}
          </ul>
        </div>
      </div>
    </div>
  );
}
