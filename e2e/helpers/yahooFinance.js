// @ts-check
/**
 * Lightweight Yahoo reference helpers used by parity E2E checks.
 * Keep these pure and network-only (no app imports) so they are reusable
 * from both local and CI runs.
 */

/**
 * @typedef {{
 *   symbol: string;
 *   price: number;
 *   previousClose: number|null;
 *   changePct: number|null;
 *   marketState: string;
 *   marketTimeMs: number|null;
 *   ageMs: number|null;
 * }} YahooQuote
 */

/**
 * @typedef {{ unavailable: true; reason: string }} YahooUnavailable
 */

/**
 * @typedef {YahooQuote | YahooUnavailable} YahooResult
 */

/**
 * @param {YahooResult} v
 * @returns {v is YahooUnavailable}
 */
function isUnavailable(v) {
  return Boolean(v && typeof v === 'object' && 'unavailable' in v);
}

/**
 * Fetch quote reference from Yahoo chart endpoint.
 * @param {string} symbol
 * @returns {Promise<YahooResult>}
 */
async function fetchYahooQuote(symbol) {
  const s = String(symbol || '').trim().toUpperCase();
  if (!s) return { unavailable: true, reason: 'symbol empty' };

  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}`;
  let resp;
  try {
    resp = await fetch(url);
  } catch (e) {
    return { unavailable: true, reason: `${s}: network failure: ${String(e)}` };
  }

  if (resp.status === 429) {
    return { unavailable: true, reason: `${s}: rate limited (429)` };
  }
  if (!resp.ok) {
    return { unavailable: true, reason: `${s}: HTTP ${resp.status}` };
  }

  try {
    const json = await resp.json();
    const meta = json?.chart?.result?.[0]?.meta || {};
    const price = Number(meta.regularMarketPrice);
    const previousCloseRaw = meta.chartPreviousClose ?? meta.previousClose;
    const previousClose = previousCloseRaw == null ? null : Number(previousCloseRaw);
    const marketState = String(meta.marketState || 'UNKNOWN');
    const marketTimeMs = meta.regularMarketTime == null ? null : Number(meta.regularMarketTime) * 1000;
    const ageMs = marketTimeMs == null ? null : Date.now() - marketTimeMs;

    if (!Number.isFinite(price) || price <= 0) {
      return { unavailable: true, reason: `${s}: unusable regularMarketPrice` };
    }

    return {
      symbol: s,
      price,
      previousClose: Number.isFinite(previousClose) && previousClose > 0 ? previousClose : null,
      changePct:
        Number.isFinite(previousClose) && previousClose > 0
          ? ((price - previousClose) / previousClose) * 100
          : null,
      marketState,
      marketTimeMs,
      ageMs,
    };
  } catch (e) {
    return { unavailable: true, reason: `${s}: JSON parse failed: ${String(e)}` };
  }
}

/**
 * @param {number} expected
 * @returns {number}
 */
function priceTolerance(expected) {
  const rel = Math.abs(expected) * 0.01;
  return Math.max(1.0, rel);
}

module.exports = {
  fetchYahooQuote,
  isUnavailable,
  priceTolerance,
};
