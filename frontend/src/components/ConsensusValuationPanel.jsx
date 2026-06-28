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
  street_far_below_consensus: 'Our Fair Value Far Above Street',
  street_far_above_consensus: 'Our Fair Value Far Below Street',
};

function prettyClassification(raw) {
  if (!raw) return null;
  if (CLASSIFICATION_LABELS[raw]) return CLASSIFICATION_LABELS[raw];
  return String(raw)
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

function fmtSignedPct(pct) {
  if (pct == null || Number.isNaN(Number(pct))) return '—';
  const n = Number(pct);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

function fmtRecommendation(consensus) {
  if (!consensus) return null;
  if (consensus.recommendation_key) {
    return String(consensus.recommendation_key).replace(/_/g, ' ');
  }
  if (consensus.recommendation_mean != null) {
    const n = Number(consensus.recommendation_mean);
    if (n <= 1.5) return 'Strong Buy';
    if (n <= 2.5) return 'Buy';
    if (n <= 3.5) return 'Hold';
    if (n <= 4.5) return 'Underperform';
    return 'Sell';
  }
  return null;
}

function fmtStreetTargetRange(consensus) {
  if (!consensus) return null;
  const low = consensus.low_target_usd;
  const high = consensus.high_target_usd;
  const n = consensus.num_analysts;
  const range =
    low != null && high != null
      ? `$${fmtUsdPlainInt(low)}–$${fmtUsdPlainInt(high)}`
      : null;
  const suffix = n != null ? ` (${n} analysts)` : '';
  if (range) return `${range}${suffix}`;
  if (n != null) return `${n} analysts`;
  return null;
}

function fmtOurVsStreetNote(consensus) {
  if (!consensus?.divergence_flag || consensus.our_vs_street_pct == null) return null;
  const ratio = (1 + Number(consensus.our_vs_street_pct) / 100).toFixed(1);
  if (Number(consensus.our_vs_street_pct) > 0) {
    return `Our base fair value is ${ratio}x the Street mean target`;
  }
  return `Our base fair value is ${(100 / (100 + Number(consensus.our_vs_street_pct))).toFixed(1)}x the Street mean target`;
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
  compact = false,
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
  const analystConsensus = v?.analyst_consensus;
  const streetRec = fmtRecommendation(analystConsensus);
  const streetRange = fmtStreetTargetRange(analystConsensus);
  const ourVsStreetNote = fmtOurVsStreetNote(analystConsensus);
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
    <div className={`dt-valuation-split ${compact ? 'dt-valuation-compact' : ''}`} data-testid="consensus-valuation-panel">
      {!compact && (
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
      )}
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
          
          {hasData && analystConsensus?.mean_target_usd != null && (
            <div
              className="dt-valuation-metrics-row dt-analyst-consensus"
              data-testid="analyst-consensus"
            >
              <dt>
                <ProvenanceTip
                  provenance={analystConsensus.provenance}
                  label="Wall St consensus"
                />
              </dt>
              <dd>
                <div className="dt-analyst-consensus-mean">
                  Mean target ${fmtUsdPlainInt(analystConsensus.mean_target_usd)}
                  {analystConsensus.street_vs_price_pct != null && (
                    <span className="dt-analyst-vs-price">
                      {' '}
                      ({fmtSignedPct(analystConsensus.street_vs_price_pct)} vs price)
                    </span>
                  )}
                </div>
                {streetRange && (
                  <div className="dt-analyst-consensus-range">{streetRange}</div>
                )}
                {streetRec && (
                  <div className="dt-analyst-consensus-rec">Recommendation: {streetRec}</div>
                )}
                {analystConsensus.our_vs_street_pct != null && (
                  <div className="dt-analyst-consensus-gap">
                    Our vs Street: {fmtSignedPct(analystConsensus.our_vs_street_pct)}
                  </div>
                )}
                {analystConsensus.divergence_flag && ourVsStreetNote && (
                  <div
                    className="dt-analyst-divergence-chip"
                    data-testid="analyst-divergence-flag"
                    title={ourVsStreetNote}
                  >
                    {ourVsStreetNote}
                  </div>
                )}
              </dd>
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
