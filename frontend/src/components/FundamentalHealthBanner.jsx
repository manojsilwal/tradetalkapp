import React from 'react';

function toneClass(tone) {
  switch (tone) {
    case 'positive':
      return 'dt-tone-ok';
    case 'caution':
      return 'dt-tone-warn';
    case 'negative':
      return 'dt-tone-negative';
    default:
      return 'dt-tone-muted';
  }
}

export function MetricHealthChip({ assessment, className = '' }) {
  if (!assessment?.label) return null;
  return (
    <span
      className={`dt-health-chip ${toneClass(assessment.tone)} ${className}`}
      title={assessment.detail || assessment.label}
    >
      {assessment.label}
    </span>
  );
}

export default function FundamentalHealthBanner({
  health,
  className = '',
  testId = 'fundamental-health-banner',
}) {
  if (!health?.headline) return null;

  return (
    <div className={`dt-fundamental-health-banner ${className}`} data-testid={testId}>
      <div className={`dt-fundamental-health-headline ${toneClass(health.tone)}`}>
        {health.headline}
      </div>
      {health.summary && (
        <p className="dt-fundamental-health-summary">{health.summary}</p>
      )}
      {health.macro_note && (
        <p className="dt-fundamental-health-macro">{health.macro_note}</p>
      )}
      <p className="dt-fundamental-health-framing">
        Fundamental quality view — not a buy/sell rating.
      </p>
    </div>
  );
}
