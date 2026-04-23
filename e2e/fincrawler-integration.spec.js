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
 */
const { test, expect } = require('@playwright/test');

const FINCRAWLER_URL = (process.env.FINCRAWLER_URL || '').trim().replace(/\/$/, '');
const FINCRAWLER_KEY = (process.env.FINCRAWLER_KEY || '').trim();

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
