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
 * @typedef {{
 *   symbol: string;
 *   price: number|null;
 *   previousClose: number|null;
 *   changePct: number|null;
 *   marketCap: number|null;
 *   forwardPE: number|null;
 *   trailingPE: number|null;
 *   beta: number|null;
 *   revenueGrowthPct: number|null;
 *   dividendYieldPct: number|null;
 * }} YahooSummary
 */

/**
 * @typedef {{ unavailable: true; reason: string }} YahooUnavailable
 */

/**
 * @typedef {YahooQuote | YahooSummary | YahooUnavailable} YahooResult
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

/**
 * Fetch Yahoo quote-summary fundamentals used by app parity checks.
 * @param {string} symbol
 * @returns {Promise<YahooSummary | YahooUnavailable>}
 */
async function fetchYahooSummary(symbol) {
  const s = String(symbol || '').trim().toUpperCase();
  if (!s) return { unavailable: true, reason: 'symbol empty' };

  const modules = [
    'price',
    'summaryDetail',
    'defaultKeyStatistics',
    'financialData',
  ].join(',');
  const url = `https://query1.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(s)}?modules=${modules}`;
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
    const result = json?.quoteSummary?.result?.[0];
    if (!result) return { unavailable: true, reason: `${s}: empty quoteSummary` };
    const price = result.price || {};
    const detail = result.summaryDetail || {};
    const stats = result.defaultKeyStatistics || {};
    const financial = result.financialData || {};
    const raw = (v) => {
      if (v == null) return null;
      if (typeof v === 'object' && 'raw' in v) return Number(v.raw);
      return Number(v);
    };
    const pct = (v) => {
      const n = raw(v);
      return Number.isFinite(n) ? n * 100 : null;
    };
    const priceNow = raw(price.regularMarketPrice);
    const previousClose = raw(detail.previousClose) ?? raw(price.regularMarketPreviousClose);
    const changePct =
      Number.isFinite(priceNow) && Number.isFinite(previousClose) && previousClose > 0
        ? ((priceNow - previousClose) / previousClose) * 100
        : null;

    return {
      symbol: s,
      price: Number.isFinite(priceNow) && priceNow > 0 ? priceNow : null,
      previousClose: Number.isFinite(previousClose) && previousClose > 0 ? previousClose : null,
      changePct,
      marketCap: finiteOrNull(raw(price.marketCap) ?? raw(detail.marketCap)),
      forwardPE: finiteOrNull(raw(stats.forwardPE) ?? raw(detail.forwardPE)),
      trailingPE: finiteOrNull(raw(summaryValue(detail.trailingPE))),
      beta: finiteOrNull(raw(summaryValue(detail.beta))),
      revenueGrowthPct: pct(financial.revenueGrowth),
      dividendYieldPct: pct(detail.dividendYield),
    };
  } catch (e) {
    return { unavailable: true, reason: `${s}: JSON parse failed: ${String(e)}` };
  }
}

/**
 * @param {unknown} v
 * @returns {unknown}
 */
function summaryValue(v) {
  return v && typeof v === 'object' && 'raw' in v ? v.raw : v;
}

/**
 * @param {unknown} v
 * @returns {number|null}
 */
function finiteOrNull(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * Fetch adjusted close series from Yahoo chart endpoint.
 * @param {string} symbol
 * @param {{range?: string, interval?: string}} [opts]
 * @returns {Promise<{symbol: string, dates: string[], closes: number[]} | YahooUnavailable>}
 */
async function fetchYahooCloseSeries(symbol, opts = {}) {
  const s = String(symbol || '').trim().toUpperCase();
  if (!s) return { unavailable: true, reason: 'symbol empty' };
  const range = opts.range || '1mo';
  const interval = opts.interval || '1d';
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(s)}?range=${encodeURIComponent(range)}&interval=${encodeURIComponent(interval)}`;
  let resp;
  try {
    resp = await fetch(url);
  } catch (e) {
    return { unavailable: true, reason: `${s}: network failure: ${String(e)}` };
  }
  if (resp.status === 429) return { unavailable: true, reason: `${s}: rate limited (429)` };
  if (!resp.ok) return { unavailable: true, reason: `${s}: HTTP ${resp.status}` };

  try {
    const json = await resp.json();
    const result = json?.chart?.result?.[0];
    const timestamps = result?.timestamp || [];
    const closesRaw = result?.indicators?.quote?.[0]?.close || [];
    const rows = timestamps
      .map((ts, i) => ({ ts: Number(ts), close: Number(closesRaw[i]) }))
      .filter((r) => Number.isFinite(r.ts) && Number.isFinite(r.close) && r.close > 0);
    if (!rows.length) return { unavailable: true, reason: `${s}: no closes` };
    return {
      symbol: s,
      dates: rows.map((r) => new Date(r.ts * 1000).toISOString().slice(0, 10)),
      closes: rows.map((r) => r.close),
    };
  } catch (e) {
    return { unavailable: true, reason: `${s}: JSON parse failed: ${String(e)}` };
  }
}

module.exports = {
  fetchYahooCloseSeries,
  fetchYahooQuote,
  fetchYahooSummary,
  isUnavailable,
  priceTolerance,
};
