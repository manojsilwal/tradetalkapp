// @ts-check
/**
 * Optional integration check: logs which data families filled each Decision Terminal block
 * (yfinance vs Stooq vs FinCrawler vs Polymarket, plus LLM/heuristic/datalake notes).
 *
 * Skips unless RUN_DECISION_TERMINAL_DATA_AUDIT=1. Requires a running API (same base as FaultHunter).
 *
 *   RUN_DECISION_TERMINAL_DATA_AUDIT=1 E2E_API_BASE_URL=http://127.0.0.1:8000 npm run e2e:dt-audit
 */
const { test, expect } = require('@playwright/test');

const API_BASE = (
  process.env.E2E_API_BASE_URL ||
  process.env.VITE_API_BASE_URL ||
  'http://127.0.0.1:8000'
).replace(/\/$/, '');

test.describe('Decision Terminal provider audit (optional)', () => {
  test.beforeEach(() => {
    test.skip(
      !process.env.RUN_DECISION_TERMINAL_DATA_AUDIT,
      'Set RUN_DECISION_TERMINAL_DATA_AUDIT=1 to run provider audit against the API',
    );
  });

  test('GET /decision-terminal?audit=1 includes provider_audit for each block', async ({ request }) => {
    test.setTimeout(360000);
    const r = await request.get(`${API_BASE}/decision-terminal`, {
      params: { ticker: 'AAPL', audit: 1 },
      timeout: 360000,
    });
    expect(r.ok(), `HTTP ${r.status()} ${await r.text()}`).toBeTruthy();
    const json = await r.json();
    expect(json.provider_audit, 'provider_audit should be present').toBeTruthy();
    const a = json.provider_audit;
    // eslint-disable-next-line no-console
    console.log('[decision-terminal provider_audit]', JSON.stringify(a, null, 2));

    expect(a.debate_market_pipeline).toBeTruthy();
    expect(a.debate_market_pipeline.spot_provider_family).toBeTruthy();
    expect(['yfinance', 'stooq', 'fincrawler', 'none']).toContain(a.debate_market_pipeline.spot_provider_family);

    expect(a.valuation && a.valuation.panel).toBe('valuation');
    expect(a.quality && a.quality.panel).toBe('quality');
    expect(a.verdict && a.verdict.panel).toBe('verdict');
    expect(a.verdict.prediction_market && a.verdict.prediction_market.provider).toBe('polymarket');
    expect(a.roadmap && a.roadmap.panel).toBe('roadmap');
    expect(typeof a.roadmap.scenario_prices_source).toBe('string');
  });
});
