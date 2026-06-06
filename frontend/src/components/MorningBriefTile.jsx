import React from 'react';
import './YourMorningHero.css';

const CHIP_STYLES = {
  LIFT: 'ym-chip-lift',
  DRAG: 'ym-chip-drag',
  EXPOSURE: 'ym-chip-exposure',
  PENDING: 'ym-chip-pending',
  FLAT: 'ym-chip-flat',
};

function impactBarWidth(card) {
  const impact = Math.abs(Number(card.portfolio_impact_pct) || 0);
  return `${Math.min(100, Math.max(8, impact * 25))}%`;
}

export default function MorningBriefTile({ card, onOpen }) {
  const isMacro = card.type === 'macro_sector_watch';
  const direction = card.direction || 'flat';
  const accentClass =
    direction === 'up' ? 'ym-tile-up' : direction === 'down' ? 'ym-tile-down' : 'ym-tile-neutral';
  const chipClass = CHIP_STYLES[card.chip] || CHIP_STYLES.FLAT;
  const metricClass =
    card.primary_metric && String(card.primary_metric).startsWith('+')
      ? 'ym-metric-up'
      : card.primary_metric && String(card.primary_metric).startsWith('-')
        ? 'ym-metric-down'
        : 'ym-metric-neutral';

  const label = isMacro ? (card.sector_name || card.title) : (card.symbol || card.title);

  return (
    <button type="button" className={`ym-tile ${accentClass}`} onClick={() => onOpen(card)}>
      <div className="ym-tile-top">
        <span className="ym-tile-ticker">{label}</span>
        <span className={`ym-chip ${chipClass}`}>{card.chip || 'FLAT'}</span>
      </div>

      {isMacro ? (
        <div className="ym-tile-macro">
          <span className={`ym-tile-metric ${metricClass}`}>{card.primary_metric}</span>
          <span className="ym-tile-sublabel">of portfolio</span>
          <div className="ym-allocation-track">
            <div
              className="ym-allocation-fill"
              style={{ width: `${Math.min(100, Number(card.allocation_pct) || 0)}%` }}
            />
          </div>
        </div>
      ) : (
        <div className="ym-tile-metric-wrap">
          <span className={`ym-tile-metric ${metricClass}`}>{card.primary_metric}</span>
          <span className="ym-tile-sublabel">today</span>
        </div>
      )}

      {!isMacro && card.impact_label && (
        <div className="ym-impact-row">
          <div className="ym-impact-track">
            <div className="ym-impact-fill" style={{ width: impactBarWidth(card) }} />
          </div>
          <span className="ym-impact-label">{card.impact_label}</span>
        </div>
      )}
    </button>
  );
}
