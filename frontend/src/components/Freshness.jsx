/**
 * Data Trust Layer — shared trust UI components.
 *
 *   <FreshnessBadge>   small chip: Live / Delayed / EOD <date> / Stale / Unverified
 *   <StaleValue>       wraps a price/% number; in strict mode a price-sensitive
 *                      stale value renders an explicit "Stale" state instead of
 *                      the number. A missing envelope renders a non-alarming
 *                      "unverified" marker rather than a bare, unvouched number.
 *   <DataTrustBanner>  page-level summary banner when any source is stale.
 *
 * All three accept a raw backend envelope (`data_freshness`) or a pre-parsed
 * descriptor from `parseFreshness`.
 */
import React from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  parseFreshness,
  shouldHideValue,
  freshnessColors,
  isStrictMode,
  FRESHNESS_STATES,
  formatFreshnessDateTime,
  relativeAgeFromCapturedAt,
  envelopeIsStale,
  cleanSource,
} from '../freshness';

function _resolve(envelopeOrParsed) {
  if (envelopeOrParsed && typeof envelopeOrParsed === 'object' && 'state' in envelopeOrParsed && 'present' in envelopeOrParsed) {
    return envelopeOrParsed; // already parsed
  }
  return parseFreshness(envelopeOrParsed);
}

/**
 * Small freshness chip.
 * @param {{envelope?:object, freshness?:object, showEod?:boolean, title?:string}} props
 *   showEod=false hides the chip for ordinary (non-stale, non-live) EOD data to
 *   avoid badge noise; set true to always show.
 */
export function FreshnessBadge({ envelope, freshness, showEod = false, showUnverified = false, title }) {
  const p = _resolve(freshness ?? envelope);

  if (p.state === FRESHNESS_STATES.UNVERIFIED && !showUnverified) return null;
  if ((p.state === FRESHNESS_STATES.EOD || p.state === FRESHNESS_STATES.HISTORICAL || p.state === FRESHNESS_STATES.REFERENCE) && !showEod) {
    return null;
  }

  const c = freshnessColors(p.state);
  const cleanedSrc = cleanSource(p.source);
  const info = [p.label, p.ageText, cleanedSrc ? `Source: ${cleanedSrc}` : ''].filter(Boolean).join(' · ');
  let hint = title || info;
  if (p.state === FRESHNESS_STATES.DELAYED) {
    hint += ' — Quotes are retrieved via standard public exchange feeds (lagged by 15-20 min as per exchange requirements). Valuation models and portfolio exposure metrics automatically adjust for latency.';
  }
  const isStale = p.state === FRESHNESS_STATES.STALE;

  return (
    <span
      title={hint}
      data-testid="freshness-badge"
      data-freshness-state={p.state}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.4,
        color: c.fg, background: c.bg, border: `1px solid ${c.border}`,
        borderRadius: 6, padding: '2px 6px', whiteSpace: 'nowrap',
        cursor: 'help',
      }}
    >
      {isStale && <AlertTriangle size={11} style={{ flexShrink: 0 }} />}
      {p.label}
    </span>
  );
}

/**
 * Wrap a price/% value so stale price-sensitive data is never shown as current.
 * @param {{envelope?:object, freshness?:object, children:React.ReactNode,
 *          priceSensitive?:boolean, badge?:boolean}} props
 */
export function StaleValue({ envelope, freshness, children, priceSensitive = true, badge = false }) {
  const p = _resolve(freshness ?? envelope);

  if (shouldHideValue(p, { priceSensitive })) {
    const c = freshnessColors(FRESHNESS_STATES.STALE);
    return (
      <span
        data-testid="stale-value"
        data-freshness-state="stale"
        title={`Stale data${p.ageText ? ` · ${p.ageText}` : ''}. Not current — live refresh unavailable.`}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 4,
          color: c.fg, fontWeight: 700, fontSize: '0.85em',
        }}
      >
        <AlertTriangle size={12} style={{ flexShrink: 0 }} />
        Stale{p.asOf ? ` · as of ${p.label.replace(/^EOD /, '')}` : ''}
      </span>
    );
  }

  // Non-strict mode, or non-stale value: show the number. For an unverified
  // P0 value, append a subtle, non-alarming marker instead of hiding it.
  const showUnverifiedDot = priceSensitive && p.state === FRESHNESS_STATES.UNVERIFIED && isStrictMode();

  return (
    <span data-testid="trust-value" data-freshness-state={p.state} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {children}
      {badge && <FreshnessBadge freshness={p} showEod={badge === 'all'} showUnverified={badge === 'all'} />}
      {showUnverifiedDot && (
        <span
          title="Unverified — freshness metadata is unavailable for this value."
          aria-label="unverified"
          style={{ width: 6, height: 6, borderRadius: '50%', background: '#94a3b8', display: 'inline-block', flexShrink: 0 }}
        />
      )}
    </span>
  );
}

/**
 * Page-level banner. Renders only when at least one provided envelope is stale.
 * @param {{envelopes?:Array, envelope?:object, message?:string}} props
 */
export function DataTrustBanner({ envelopes, envelope, message }) {
  const list = (envelopes || (envelope ? [envelope] : [])).map(_resolve);
  const stale = list.filter((p) => p.state === FRESHNESS_STATES.STALE);
  if (stale.length === 0) return null;

  const c = freshnessColors(FRESHNESS_STATES.STALE);
  const worst = stale.reduce((a, b) => ((b.stalenessSeconds || 0) > (a.stalenessSeconds || 0) ? b : a), stale[0]);
  const detail =
    message ||
    `Some figures are not current${worst.ageText ? ` (oldest data ${worst.ageText})` : ''}. Live refresh is unavailable — treat highlighted values as stale.`;

  return (
    <div
      data-testid="data-trust-banner"
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        color: c.fg, background: c.bg, border: `1px solid ${c.border}`,
        borderRadius: 10, padding: '10px 14px', fontSize: '0.9rem', marginBottom: 16,
      }}
    >
      <AlertTriangle size={18} style={{ flexShrink: 0 }} />
      <span><strong>Stale market data.</strong> {detail}</span>
    </div>
  );
}

export default FreshnessBadge;

/**
 * Panel-level "Last updated" with full local date+time and relative age.
 */
export function LastUpdated({ freshness, envelope, label = 'Last updated', className = '' }) {
  const p = _resolve(freshness ?? envelope);
  if (!p.present) return null;
  const captured = freshness?.captured_at ?? envelope?.captured_at ?? p.asOf;
  if (!captured) return null;
  const rel = relativeAgeFromCapturedAt(captured);
  const full = formatFreshnessDateTime(captured);
  const maxS = typeof (freshness ?? envelope)?.policy_max_age_s === 'number'
    ? (freshness ?? envelope).policy_max_age_s
    : null;
  let color = '#64748b';
  if (captured && maxS) {
    const ageS = (Date.now() - new Date(captured).getTime()) / 1000;
    if (ageS > maxS) color = '#f87171';
    else if (ageS > maxS / 2) color = '#fbbf24';
  } else if (envelopeIsStale(freshness ?? envelope)) {
    color = '#fbbf24';
  }
  return (
    <span
      className={className}
      data-testid="last-updated"
      title={full}
      style={{ fontSize: '0.72rem', color, display: 'inline-flex', gap: 6, alignItems: 'center' }}
    >
      <span>{label}: {full}</span>
      {rel ? <span style={{ opacity: 0.85 }}>({rel})</span> : null}
    </span>
  );
}
