// @ts-check
/**
 * Predictor API + decision-terminal roadmap augmentation (probabilistic bands).
 *
 * @example
 *   E2E_API_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/predictor.spec.js
 */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, waitForDecisionTerminalReady } = require('./support');

const API_BASE = (
  process.env.E2E_API_BASE_URL ||
  process.env.VITE_API_BASE_URL ||
  'http://127.0.0.1:8000'
).replace(/\/$/, '');

test.describe('Predictor', () => {
  test('GET /predictor/forecast returns bands with ordered quantiles', async ({ request }) => {
    const res = await request.get(`${API_BASE}/predictor/forecast`, {
      params: { ticker: 'AAPL', horizon: '1d,5d,21d,63d' },
      timeout: 120000,
    });
    expect(res.ok()).toBeTruthy();
    const json = await res.json();
    expect(json.status).toBeDefined();
    expect(Array.isArray(json.horizon_bands_usd)).toBeTruthy();
    for (const b of json.horizon_bands_usd) {
      expect(b.q10_usd <= b.q50_usd && b.q50_usd <= b.q90_usd).toBeTruthy();
    }
    expect(String(json.disclaimer || '').length).toBeGreaterThan(10);
  });

  test('openapi exposes predictor route', async ({ request }) => {
    const res = await request.get(`${API_BASE}/openapi.json`, { timeout: 60000 });
    expect(res.ok()).toBeTruthy();
    const spec = await res.json();
    const paths = spec.paths || {};
    expect(paths['/predictor/forecast']).toBeDefined();
  });

  test('decision-terminal UI shows roadmap after run', async ({ page }) => {
    await page.goto('/decision-terminal', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await waitForDecisionTerminalReady(page);
    await page.locator('.dt-ticker-input').fill('AAPL');
    await page.getByRole('button', { name: 'Run analysis' }).click();
    await expect(page.getByRole('heading', { name: /Future price roadmap/i })).toBeVisible({
      timeout: 240000,
    });
  });
});
