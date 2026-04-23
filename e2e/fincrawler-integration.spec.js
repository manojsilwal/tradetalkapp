// @ts-check
/**
 * Optional HTTP checks against a running FinCrawler instance (local or deployed).
 *
 * Skips all tests when FINCRAWLER_URL is unset (default in CI / local dev).
 *
 * Run locally (FinCrawler on :10000 with matching API_KEY):
 *   FINCRAWLER_URL=http://127.0.0.1:10000 FINCRAWLER_KEY=<same as FinCrawler API_KEY> npm run e2e:fincrawler
 *
 * Against Render / staging:
 *   FINCRAWLER_URL=https://your-fincrawler.onrender.com FINCRAWLER_KEY=… npm run e2e:fincrawler
 *
 * Polymarket (optional): FinCrawler has no ticker-specific Polymarket route in TradeTalk — prediction markets
 * are read directly from gamma-api.polymarket.com in the backend. To verify your FinCrawler can still
 * pull Polymarket *data* through generic URL scrape (same feed the app filters by ticker keywords):
 *   FINCRAWLER_POLYMARKET_E2E=1 FINCRAWLER_URL=http://127.0.0.1:10000 FINCRAWLER_KEY=… npm run e2e:fincrawler
 */
const { test, expect } = require('@playwright/test');

const FINCRAWLER_URL = (process.env.FINCRAWLER_URL || '').trim().replace(/\/$/, '');
const FINCRAWLER_KEY = (process.env.FINCRAWLER_KEY || '').trim();

/** @param {unknown} j */
function scrapeBodyText(j) {
  if (j == null || typeof j !== 'object') return '';
  const o = /** @type {Record<string, unknown>} */ (j);
  if (o.success === false) return String(o.error || '');
  const data = o.data;
  if (data != null && typeof data === 'object') {
    const d = /** @type {Record<string, unknown>} */ (data);
    return String(d.markdown || d.content || d.text || '');
  }
  return String(o.text || o.content || o.markdown || '');
}

test.describe('FinCrawler integration (optional)', () => {
  test.beforeEach(() => {
    test.skip(!FINCRAWLER_URL, 'Set FINCRAWLER_URL to exercise FinCrawler checks');
  });

  test('GET /health returns ok payload', async ({ request }) => {
    const r = await request.get(`${FINCRAWLER_URL}/health`);
    expect(r.ok(), `HTTP ${r.status()} ${await r.text()}`).toBeTruthy();
    const j = await r.json();
    expect(j.status === 'ok' || j.ok === true).toBeTruthy();
  });

  test('GET /quote?ticker=AAPL returns JSON (200 ok or 422 parse miss)', async ({ request }) => {
    const headers = {};
    if (FINCRAWLER_KEY) {
      headers.Authorization = `Bearer ${FINCRAWLER_KEY}`;
      headers['X-Api-Key'] = FINCRAWLER_KEY;
    }
    const r = await request.get(`${FINCRAWLER_URL}/quote?ticker=AAPL`, {
      headers,
      timeout: 120_000,
    });
    expect([200, 401, 422]).toContain(r.status());
    const text = await r.text();
    let j = {};
    try {
      j = JSON.parse(text);
    } catch {
      throw new Error(`Expected JSON body, got: ${text.slice(0, 200)}`);
    }
    if (r.status() === 401) {
      throw new Error('Unauthorized — set FINCRAWLER_KEY to match FinCrawler API_KEY');
    }
    if (r.status() === 200) {
      expect(j.ok).toBe(true);
      expect(typeof j.price).toBe('number');
      expect(j.price).toBeGreaterThan(0);
    }
    if (r.status() === 422) {
      expect(j.ok === false || j.error).toBeTruthy();
    }
  });
});

test.describe('FinCrawler → Polymarket Gamma via /v1/scrape (extra optional)', () => {
  test.beforeEach(() => {
    test.skip(!FINCRAWLER_URL, 'Set FINCRAWLER_URL');
    test.skip(
      !process.env.FINCRAWLER_POLYMARKET_E2E,
      'Set FINCRAWLER_POLYMARKET_E2E=1 to POST /v1/scrape on the public Gamma events URL',
    );
  });

  test('POST /v1/scrape returns usable text from gamma-api.polymarket.com events feed', async ({
    request,
  }) => {
    test.setTimeout(180_000);
    const headers = { 'Content-Type': 'application/json' };
    if (FINCRAWLER_KEY) {
      headers.Authorization = `Bearer ${FINCRAWLER_KEY}`;
      headers['X-Api-Key'] = FINCRAWLER_KEY;
    }
    const gammaUrl = 'https://gamma-api.polymarket.com/events?closed=false&limit=5';
    const r = await request.post(`${FINCRAWLER_URL}/v1/scrape`, {
      headers,
      data: { url: gammaUrl, formats: ['markdown'] },
      timeout: 180_000,
    });
    expect([200, 401, 422, 502]).toContain(r.status());
    if (r.status() === 401) {
      throw new Error('Unauthorized — set FINCRAWLER_KEY to match FinCrawler API_KEY');
    }
    const raw = await r.text();
    let j = {};
    try {
      j = JSON.parse(raw);
    } catch {
      throw new Error(`Expected JSON from /v1/scrape, got: ${raw.slice(0, 300)}`);
    }
    const text = scrapeBodyText(j).toLowerCase();
    expect(text.length, 'scrape body should not be empty').toBeGreaterThan(40);
    expect(
      /polymarket|gamma|events|market|title|condition|slug|probability/.test(text),
      `expected Polymarket/Gamma-like content, got: ${text.slice(0, 400)}`,
    ).toBeTruthy();
  });
});
