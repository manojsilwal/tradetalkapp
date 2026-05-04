// @ts-check
/**
 * Minimal prod smoke (landing, debate result, scorecard result, strategy lab).
 * Run: `npm run e2e:smoke` (set FRONTEND_URL for production).
 * API-only smoke: `FH_PROFILE=smoke E2E_API_BASE_URL=… npm run e2e:smoke:api`.
 * Deeper coverage: full `npm run e2e` or `faulthunter-api.spec.js` without smoke profile.
 */
const { test, expect } = require('@playwright/test');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('TradeTalkApp E2E smoke', () => {
  test('landing page loads', async ({ page }) => {
    await page.goto(FRONTEND);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
  });

  test('AI Debate produces a visible panel result', async ({ page }) => {
    await page.goto(FRONTEND);
    await page.click('text=AI Debate');
    const tickerInput = page.getByPlaceholder('TICKER');
    await expect(tickerInput).toBeVisible({ timeout: 10000 });
    await tickerInput.fill('SPY');
    await page.click('button:has-text("Start Debate")');
    await expect(page.getByText('Panel Verdict')).toBeVisible({ timeout: 180000 });
    await expect(page.getByText(/Confidence:\s*\d+%/)).toBeVisible();
    await expect(page.getByText('Bull Analyst')).toBeVisible();
    await expect(page.getByText('Bear Analyst')).toBeVisible();
    await expect(page.getByText('Macro Economist')).toBeVisible();
    await expect(page.getByText('Copy as Markdown')).toBeVisible();
  });

  test('Risk-Return Scorecard produces visible scored rows', async ({ page }) => {
    await page.goto(`${FRONTEND.replace(/\/$/, '')}/scorecard`);
    await expect(page.getByRole('heading', { name: /Risk-Return Scorecard/i })).toBeVisible({ timeout: 20000 });

    const basketInput = page.getByPlaceholder(/Comma or space separated/i);
    await basketInput.fill('SPY, QQQ');

    const skipLlm = page.getByLabel(/Skip LLM scoring/i);
    if (!(await skipLlm.isChecked())) {
      await skipLlm.check();
    }

    await page.getByRole('button', { name: /Run scorecard/i }).click();
    await expect(page.getByRole('heading', { name: /^Results$/ })).toBeVisible({ timeout: 90000 });
    await expect(page.getByRole('columnheader', { name: /Signal/i })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: /Verdict/i })).toBeVisible();
    await expect(page.getByRole('row', { name: /SPY .*?(Exceptional|Strong buy|Favorable|Balanced|Caution|Avoid)/ })).toBeVisible();
    await expect(page.getByRole('row', { name: /QQQ .*?(Exceptional|Strong buy|Favorable|Balanced|Caution|Avoid)/ })).toBeVisible();
  });

  test('Strategy Lab tab loads', async ({ page }) => {
    await page.goto(FRONTEND);
    await page.click('text=Strategy Lab');
    await expect(page.locator('textarea, input').first()).toBeVisible({ timeout: 10000 });
  });
});
