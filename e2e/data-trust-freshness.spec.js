// @ts-check
// Data Trust Layer — stale-scenario E2E.
//
// Rather than depend on the live freshness of the backend's data lake (which
// varies), we intercept the /macro response and inject a *stale* data_freshness
// envelope, then assert the shared trust UI surfaces it. A companion test injects
// a *live* envelope and asserts no stale marker appears.
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

// Match the bare `/macro` endpoint (not /macro/global-markets, /macro/flow, …).
function isMacroEndpoint(url) {
  try {
    const u = new URL(url);
    return /\/macro$/.test(u.pathname);
  } catch {
    return false;
  }
}

async function routeMacroWithFreshness(page, freshness) {
  await page.route('**/macro*', async (route) => {
    if (!isMacroEndpoint(route.request().url())) {
      return route.fallback();
    }
    let body;
    try {
      const resp = await route.fetch();
      body = await resp.json();
    } catch {
      // Backend unavailable — synthesize a minimal valid macro payload.
      body = { market_regime: 'risk_on', vix_level: 14.2, credit_stress_index: 0.3 };
    }
    body.data_freshness = freshness;
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
}

test.describe('Data Trust Layer — freshness indicators', () => {
  test('stale macro data surfaces a Stale freshness badge', async ({ page }) => {
    await routeMacroWithFreshness(page, {
      data_class: 'macro_spot',
      source: 'test_injected',
      tier: 'eod',
      as_of: '2024-01-02',
      is_stale: true,
      staleness_seconds: 60 * 60 * 24 * 30,
      degraded: false,
    });

    await page.goto(FRONTEND + '/macro');
    await dismissOnboarding(page);

    const badge = page.locator('[data-testid="freshness-badge"][data-freshness-state="stale"]');
    await expect(badge.first()).toBeVisible({ timeout: 30000 });
  });

  test('live macro data shows no stale marker', async ({ page }) => {
    await routeMacroWithFreshness(page, {
      data_class: 'live_quote',
      source: 'test_injected',
      tier: 'live',
      is_stale: false,
      degraded: false,
      staleness_seconds: 5,
    });

    await page.goto(FRONTEND + '/macro');
    await dismissOnboarding(page);

    // The Core Macro Indicators card must render (data loaded) ...
    await expect(page.getByTestId('macro-vix-card')).toBeVisible({ timeout: 30000 });
    // ... and there must be no stale badge anywhere on the page.
    await expect(
      page.locator('[data-testid="freshness-badge"][data-freshness-state="stale"]')
    ).toHaveCount(0);
  });
});
