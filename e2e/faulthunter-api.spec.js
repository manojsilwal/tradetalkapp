// @ts-check
/**
 * API contract tests aligned with FaultHunter (`faulthunter/case_bank.py`).
 * Uses Playwright request (no browser) — same HTTP surface FaultHunter evaluates.
 *
 * Browser E2E uses Playwright baseURL `http://localhost:5173` (Vite). This file calls the **API** directly; keep:
 *   E2E_API_BASE_URL=http://127.0.0.1:8000
 *   Run against local API (with backend running):
 *   E2E_API_BASE_URL=http://127.0.0.1:8000 npx playwright test e2e/faulthunter-api.spec.js
 *
 * Run smoke subset only:
 *   FH_PROFILE=smoke npx playwright test e2e/faulthunter-api.spec.js
 */
const { test, expect } = require('@playwright/test');
const { ALL_DAILY_CASES, SMOKE_CASES } = require('./faulthunter-cases');

const API_BASE = (
  process.env.E2E_API_BASE_URL ||
  process.env.VITE_API_BASE_URL ||
  'http://127.0.0.1:8000'
).replace(/\/$/, '');

const PROFILE = (process.env.FH_PROFILE || 'daily').toLowerCase();
const CASES = PROFILE === 'smoke' ? SMOKE_CASES : ALL_DAILY_CASES;

/** @param {Record<string, unknown>} obj @param {string} dotted */
function getNested(obj, dotted) {
  return dotted.split('.').reduce((o, k) => (o != null && typeof o === 'object' && k in o ? /** @type {Record<string, unknown>} */ (o)[k] : undefined), obj);
}

test.describe.configure({ mode: 'serial' });

test.describe(`FaultHunter API parity (profile=${PROFILE})`, () => {
  for (const c of CASES) {
    test(`${c.id} — ${c.method} ${c.path}`, async ({ request }) => {
      const budget = Math.max(
        c.slowLatencyMs + 5000,
        c.path === '/decision-terminal' ? 240000 : 120000,
      );
      // Extra slack for deployed APIs (Render cold start / macro sub-fetch 504) + one retry round-trip.
      test.setTimeout(
        c.id.includes('backtest')
          ? 420000
          : c.path === '/decision-terminal'
            ? 540000
            : budget + 120000,
      );

      const url = `${API_BASE}${c.path}`;
      /** @type {import('@playwright/test').APIResponse} */
      let res;
      const fetchOnce = async () => {
        if (c.method === 'GET') {
          return request.get(url, { params: c.params || {}, timeout: budget });
        }
        return request.post(url, {
          data: c.params || {},
          headers: { 'Content-Type': 'application/json' },
          timeout: budget,
        });
      };
      res = await fetchOnce();
      if ([502, 503, 504].includes(res.status())) {
        await new Promise((r) => setTimeout(r, 5000));
        res = await fetchOnce();
      }

      if (!res.ok()) {
        const errText = await res.text().catch(() => '');
        throw new Error(`${c.id}: HTTP ${res.status()} ${errText.slice(0, 800)}`);
      }
      const json = await res.json();

      for (const field of c.requiredFields) {
        const v = getNested(json, field);
        expect(v, `${c.id}: missing or null field "${field}"`).toBeDefined();
      }
    });
  }
});
