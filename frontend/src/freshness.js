/**
 * Data Trust Layer — frontend freshness logic.
 *
 * Parses the backend `DataFreshness` envelope (and the legacy daily-brief
 * `data_freshness` block) into a small, render-ready descriptor. Pure module —
 * no JSX — so it can be reused by any component.
 *
 * State model:
 *   live       — real-time value during an open session
 *   delayed    — intraday but lagged, or a degraded/fallback source
 *   eod        — end-of-day value for the last completed session
 *   historical — historical/derived analytics (not a spot quote)
 *   reference  — static/slow reference data (never "live")
 *   stale      — violates its data class SLA (must not be shown as current)
 *   unverified — no envelope present; we cannot vouch for the number
 */

export const FRESHNESS_STATES = Object.freeze({
  LIVE: 'live',
  DELAYED: 'delayed',
  EOD: 'eod',
  HISTORICAL: 'historical',
  REFERENCE: 'reference',
  STALE: 'stale',
  UNVERIFIED: 'unverified',
});

const _LIVE_SOURCES = ['realtime_overlay', 'market_intel_live', 'market_intel'];

/** Default max age for home_live clock-age envelopes (matches FRESHNESS_HOME_MAX_S). */
export const HOME_LIVE_MAX_AGE_S = 3600;

function isClockAgeEnvelope(envelope) {
  if (!envelope || typeof envelope !== 'object') return false;
  if (envelope.data_class === 'home_live') {
    return Boolean(envelope.captured_at && !envelope.expected_last_session);
  }
  // Any age-mode envelope with captured_at + policy_max_age_s gets client clock check.
  return Boolean(
    envelope.captured_at
    && typeof envelope.policy_max_age_s === 'number'
    && envelope.policy_max_age_s > 0
    && !envelope.expected_last_session,
  );
}

/**
 * True when a freshness envelope is stale (session-day or clock-age).
 */
export function envelopeIsStale(envelope) {
  if (!envelope || typeof envelope !== 'object') return true;
  if (isClockAgeEnvelope(envelope)) {
    if (envelope.is_stale) return true;
    if (envelope.captured_at) {
      const cap = new Date(envelope.captured_at);
      if (!Number.isNaN(cap.getTime())) {
        const ageS = (Date.now() - cap.getTime()) / 1000;
        const maxS = typeof envelope.policy_max_age_s === 'number'
          ? envelope.policy_max_age_s
          : HOME_LIVE_MAX_AGE_S;
        if (ageS > maxS) return true;
      }
    }
    return false;
  }
  return !!envelope.is_stale;
}

/**
 * Kill switch: set VITE_DATA_TRUST_STRICT=0 to fall back to badge-only mode
 * (never hide a value), e.g. if strict hiding causes a production issue.
 */
export function isStrictMode() {
  try {
    const v = import.meta?.env?.VITE_DATA_TRUST_STRICT;
    return v !== '0' && v !== 'false' && v !== false;
  } catch {
    return true;
  }
}

export function formatFreshnessDate(iso) {
  if (!iso) return '';
  try {
    const d = new Date(String(iso).length <= 10 ? `${iso}T00:00:00` : iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return String(iso);
  }
}

/** Full local date+time for panel "Last updated" labels. */
export function formatFreshnessDateTime(iso) {
  if (!iso) return '';
  try {
    const d = new Date(String(iso).length <= 10 ? `${iso}T00:00:00` : iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    });
  } catch {
    return String(iso);
  }
}

export function relativeAgeFromCapturedAt(capturedAtIso, nowMs = Date.now()) {
  if (!capturedAtIso) return '';
  try {
    const cap = new Date(capturedAtIso);
    if (Number.isNaN(cap.getTime())) return '';
    const sec = Math.floor((nowMs - cap.getTime()) / 1000);
    if (sec < 5) return 'just now';
    if (sec < 60) return `${sec}s ago`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min}m ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr}h ago`;
    const days = Math.floor(hr / 24);
    return `${days}d ago`;
  } catch {
    return '';
  }
}

function _ageText(seconds) {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return '';
  const mins = Math.floor(seconds / 60);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

/**
 * Normalize a backend envelope (new or legacy) into a render descriptor.
 * @returns {{state:string,label:string,ageText:string,asOf:string|null,
 *            source:string|null,isStale:boolean,degraded:boolean,
 *            stalenessSeconds:number|null,present:boolean}}
 */
export function parseFreshness(envelope) {
  if (!envelope || typeof envelope !== 'object') {
    return {
      state: FRESHNESS_STATES.UNVERIFIED,
      label: 'Unverified',
      ageText: '',
      asOf: null,
      source: null,
      isStale: false,
      degraded: false,
      stalenessSeconds: null,
      present: false,
    };
  }

  const isStale = !!envelope.is_stale;
  const degraded = !!envelope.degraded;
  const tier = envelope.tier || null;
  const source = envelope.source || null;

  const asOf =
    envelope.as_of ||
    envelope.db_latest_date ||
    envelope.captured_at ||
    envelope.expected_as_of ||
    envelope.expected_last_session ||
    null;

  const stalenessSeconds =
    typeof envelope.staleness_seconds === 'number'
      ? envelope.staleness_seconds
      : typeof envelope.staleness_days === 'number'
      ? envelope.staleness_days * 86400
      : null;

  let state;
  if (isStale) {
    state = FRESHNESS_STATES.STALE;
  } else if (degraded) {
    // A fallback/lower-confidence source is never presented as fully "Live".
    state = FRESHNESS_STATES.DELAYED;
  } else if (tier && Object.values(FRESHNESS_STATES).includes(tier)) {
    state = tier; // live | delayed | eod | historical | reference
  } else {
    // Legacy block: infer from source.
    state = _LIVE_SOURCES.includes(source) ? FRESHNESS_STATES.LIVE : FRESHNESS_STATES.EOD;
  }

  const labels = {
    [FRESHNESS_STATES.LIVE]: 'Live',
    [FRESHNESS_STATES.DELAYED]: 'Delayed',
    [FRESHNESS_STATES.EOD]: asOf ? `EOD ${formatFreshnessDate(asOf)}` : 'EOD',
    [FRESHNESS_STATES.HISTORICAL]: 'Historical',
    [FRESHNESS_STATES.REFERENCE]: 'Reference',
    [FRESHNESS_STATES.STALE]: 'Stale',
    [FRESHNESS_STATES.UNVERIFIED]: 'Unverified',
  };

  return {
    state,
    label: labels[state] || state,
    ageText: _ageText(stalenessSeconds),
    asOf,
    source,
    isStale,
    degraded,
    stalenessSeconds,
    present: true,
  };
}

/**
 * Strict rule: a price-sensitive value that is stale must NOT render the number.
 */
export function shouldHideValue(parsed, { priceSensitive = true } = {}) {
  if (!isStrictMode()) return false;
  return !!(priceSensitive && parsed && parsed.state === FRESHNESS_STATES.STALE);
}

/**
 * True when trade_date is materially behind expected_last_session (legacy block or envelope).
 */
export function isSessionDateStale(tradeDateIso, envelope, toleranceDays = 2) {
  if (envelope && isClockAgeEnvelope(envelope)) {
    return envelopeIsStale(envelope);
  }
  if (!tradeDateIso) return true;
  const expected =
    envelope?.expected_last_session ||
    envelope?.expected_as_of ||
    null;
  if (!expected) return false;
  try {
    const td = new Date(String(tradeDateIso).length <= 10 ? `${tradeDateIso}T00:00:00` : tradeDateIso);
    const ex = new Date(String(expected).length <= 10 ? `${expected}T00:00:00` : expected);
    if (Number.isNaN(td.getTime()) || Number.isNaN(ex.getTime())) return false;
    const tol = typeof envelope?.tolerance_days === 'number' ? envelope.tolerance_days : toleranceDays;
    const behindDays = Math.floor((ex - td) / 86400000);
    return behindDays >= tol;
  } catch {
    return false;
  }
}

/** Movers/brief tables should not render until session date aligns with freshness envelope. */
export function isBriefSessionTrustworthy(briefPayload) {
  if (!briefPayload) return false;
  const env = briefPayload.data_freshness;
  if (!env) return false;
  if (isClockAgeEnvelope(env)) {
    return !envelopeIsStale(env);
  }
  if (env?.is_stale) return false;
  if (isSessionDateStale(briefPayload.trade_date, env)) return false;
  if (!env && briefPayload.trade_date) return false;
  return true;
}

/** Avoid hammering /daily-brief when live data is unavailable (stale_unavailable). */
export function shouldSkipDailyBriefRefetch(briefPayload, lastFetchMs, forceRefresh = false) {
  if (forceRefresh) return false;
  if (!briefPayload || !lastFetchMs) return false;
  if (isBriefSessionTrustworthy(briefPayload)) return true;
  const cooldownMs = 120_000;
  if (briefPayload.stale_unavailable && Date.now() - lastFetchMs < cooldownMs) return true;
  // Brief debounce for any settled payload (trustworthy or not).
  if (Date.now() - lastFetchMs < 15_000) return true;
  return false;
}

/** Visual palette per state (kept here so badge + value styling stay in sync). */
export function freshnessColors(state) {
  switch (state) {
    case FRESHNESS_STATES.LIVE:
      return { fg: '#34d399', bg: 'rgba(52,211,153,0.12)', border: 'rgba(52,211,153,0.35)' };
    case FRESHNESS_STATES.DELAYED:
      return { fg: '#60a5fa', bg: 'rgba(96,165,250,0.12)', border: 'rgba(96,165,250,0.35)' };
    case FRESHNESS_STATES.STALE:
      return { fg: '#fbbf24', bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.35)' };
    case FRESHNESS_STATES.UNVERIFIED:
      return { fg: '#94a3b8', bg: 'rgba(148,163,184,0.10)', border: 'rgba(148,163,184,0.30)' };
    case FRESHNESS_STATES.HISTORICAL:
    case FRESHNESS_STATES.REFERENCE:
    case FRESHNESS_STATES.EOD:
    default:
      return { fg: '#94a3b8', bg: 'rgba(148,163,184,0.10)', border: 'rgba(148,163,184,0.25)' };
  }
}

/**
 * Maps internal raw source adapter names to friendly user-facing labels.
 * If the source is an internal technical identifier with no user value,
 * it returns an empty string to avoid exposing developer details.
 */
export function cleanSource(src) {
  if (!src || typeof src !== 'string') return '';
  const lower = src.toLowerCase().trim();
  if (lower === 'yahoo_fast_info' || lower === 'yfinance' || lower === 'yahoo_chart' || lower === 'yfinance_history' || lower === 'yfinance_info' || lower === 'yfinance_movers') {
    return 'Yahoo Finance';
  }
  if (lower === 'fred') return 'FRED';
  if (lower === 'data_lake' || lower === 'datalake') return 'Internal Data Lake';
  if (lower === 'market_intel' || lower === 'market_intel_live') return 'Market Intelligence';
  if (lower === 'realtime_overlay') return 'Real-time Feed';
  if (lower === 'slickcharts_live') return 'Slickcharts';
  if (lower === 'sp500_screener') return 'S&P 500 Screener';
  if (lower === 'yfinance/fred') return 'Yahoo Finance & FRED';
  if (lower === 'stooq') return 'Stooq';
  if (lower === 'fincrawler') return 'Financial Crawler';
  if (lower === 'yfinance_or_datalake') return 'Yahoo Finance / Data Lake';
  if (lower === 'metric_primitives') return 'Market Metrics';
  if (['heuristic', 'none', 'unknown', 'not_implemented', 'snapshot'].includes(lower)) return '';
  // Clean underscores/hyphens from other custom sources
  return src.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

