// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectNoGenericFetchFailure, waitForDecisionTerminalReady } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Decision Terminal', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND + '/decision-terminal', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await waitForDecisionTerminalReady(page);
  });

  test('positive: progressively loads AAPL panels (snapshot before verdict)', async ({ page }) => {
    const tickerInput = page.locator('.dt-ticker-input');
    await tickerInput.fill('AAPL');
    await page.getByRole('button', { name: 'Run analysis' }).click();

    // Fast snapshot slice fills valuation + quality first.
    await expect(page.getByText('Consensus valuation signal')).toBeVisible({ timeout: 120000 });
    await expect(page.getByText('Business quality scorecard')).toBeVisible();

    // Slow verdict + roadmap slices stream in independently afterwards.
    await expect(page.getByText('Verdict & sentiment hub')).toBeVisible({ timeout: 240000 });
    await expect(page.getByText('Aggregate verdict')).toBeVisible();
    await expect(page.getByText('Future price roadmap')).toBeVisible();

    await expectNoGenericFetchFailure(page);
  });

  test('negative: handles invalid ticker input gracefully', async ({ page }) => {
    const tickerInput = page.locator('.dt-ticker-input');
    await tickerInput.fill('INVALIDTICKER123');
    await expect(page.getByRole('button', { name: 'Run analysis' })).toBeDisabled();
    await expect(page.locator('.dt-error-banner')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('.dt-error-banner')).toContainText(/Incorrect ticker \(not in S&P 500\)/i);
  });

  test('negative: handles empty ticker submission', async ({ page }) => {
    const tickerInput = page.locator('.dt-ticker-input');
    await tickerInput.fill('');
    const analyzeBtn = page.getByRole('button', { name: 'Run analysis' });

    await expect(analyzeBtn).toBeDisabled();
  });
});
