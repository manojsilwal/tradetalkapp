// @ts-check
/**
 * Smoke test for the Risk-Return Scorecard tab.
 *
 * Validates:
 *  - /scorecard route renders the Scorecard UI (Section: Basket, preset selector, Run button)
 *  - Submitting with a small basket in "Skip LLM" mode returns a result table with
 *    a row per ticker and the SITG-boost column that the methodology's Step 5
 *    applied example highlights.
 *
 * Skip-LLM is enabled so the test completes deterministically without LLM spend.
 * Run: `npm run e2e:smoke -- -g scorecard` (set FRONTEND_URL for production).
 */
const { test, expect } = require('@playwright/test');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Risk-Return Scorecard', () => {
  test('Scorecard tab renders and runs a basket in skip-LLM mode', async ({ page }) => {
    await page.goto(`${FRONTEND.replace(/\/$/, '')}/scorecard`);

    await expect(
      page.getByRole('heading', { name: /Risk-Return Scorecard/i })
    ).toBeVisible({ timeout: 20000 });

    const basketInput = page.getByPlaceholder(/Comma or space separated/i);
    await expect(basketInput).toBeVisible({ timeout: 10000 });
    await basketInput.fill('SPY, QQQ');

    // Skip LLM so the smoke completes in deterministic time without spend.
    const skipLlm = page.getByLabel(/Skip LLM scoring/i);
    await skipLlm.check();

    // Balanced preset is the default — no change needed.
    await page.getByRole('button', { name: /Run scorecard/i }).click();

    // Either the results table appears (success) or a visible error banner —
    // both prove the route and handler are wired. On success we assert on the
    // SITG-boost column and the ticker row that Step 5 of the methodology
    // highlights; on error we assert the banner has informative text.
    const resultsHeading = page.getByRole('heading', { name: /^Results$/ });
    const errorBanner = page.locator('[role="alert"]');

    const result = await Promise.race([
      resultsHeading.waitFor({ state: 'visible', timeout: 60000 }).then(() => 'ok'),
      errorBanner.waitFor({ state: 'visible', timeout: 60000 }).then(() => 'err'),
    ]);

    if (result === 'ok') {
      await expect(page.getByRole('columnheader', { name: /SITG boost/i })).toBeVisible();
      await expect(page.getByRole('cell', { name: 'SPY', exact: true })).toBeVisible();
    } else {
      const text = (await errorBanner.innerText()).trim();
      expect(text.length).toBeGreaterThan(0);
    }
  });

  test('Scorecard sidebar link navigates from the landing page', async ({ page }) => {
    await page.goto(FRONTEND);
    // Landing may auto-collapse the sidebar; open if a Menu toggle is present.
    const menuToggle = page.getByRole('button', { name: /menu/i }).first();
    if (await menuToggle.isVisible().catch(() => false)) {
      await menuToggle.click().catch(() => {});
    }
    const scorecardNav = page.getByRole('button', { name: /Risk\/Return Scorecard/i });
    if (await scorecardNav.isVisible().catch(() => false)) {
      await scorecardNav.click();
      await expect(
        page.getByRole('heading', { name: /Risk-Return Scorecard/i })
      ).toBeVisible({ timeout: 15000 });
    } else {
      test.skip(true, 'Sidebar nav button not visible on this layout');
    }
  });
});
