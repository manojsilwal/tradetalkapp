// @ts-check
/**
 * Browser E2E for analysis routes. API contracts matching FaultHunter live in `faulthunter-api.spec.js`
 * (see `faulthunter-cases.js` — same ids as `faulthunter/case_bank.py`).
 *
 * Overlap: valuation flow exercises ticker analysis; decision terminal uses AAPL like FaultHunter `decision-aapl-today`.
 */
const { test, expect } = require('@playwright/test');
const {
  dismissOnboarding,
  expectNoGenericFetchFailure,
  expectOneOf,
  waitForDecisionTerminalReady,
} = require('./support');

test.describe('Analysis Surfaces', () => {
  test('valuation dashboard renders verdict and metrics for AAPL', async ({ page }) => {
    await page.goto('/');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder('Ticker').fill('AAPL');
    await page.getByRole('button', { name: 'Analyze' }).click();
    await expectOneOf(page, ['Overall Verdict', 'Elite Investor Valuation Profile', 'Margin of Safety'], 90000);
    await expectNoGenericFetchFailure(page);
  });

  test('decision terminal renders verdict and roadmap for AAPL (FaultHunter: decision-aapl-today)', async ({ page }) => {
    await page.goto('/decision-terminal', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await waitForDecisionTerminalReady(page);
    await page.locator('.dt-ticker-input').fill('AAPL');
    await page.getByRole('button', { name: 'Run analysis' }).click();
    await expectOneOf(page, ['Verdict & sentiment hub', 'Aggregate verdict', 'Future price roadmap'], 120000);
    await expectNoGenericFetchFailure(page);
  });
});
