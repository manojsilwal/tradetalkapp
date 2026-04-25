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

  test('positive: loads and analyzes AAPL', async ({ page }) => {
    const tickerInput = page.locator('.dt-ticker-input');
    await tickerInput.fill('AAPL');
    await page.getByRole('button', { name: 'Run analysis' }).click();

    // Verify loading steps appear and eventually resolve
    await expect(page.getByText('Verdict & sentiment hub')).toBeVisible({ timeout: 120000 });

    // Check specific elements of the terminal
    await expect(page.getByText('Aggregate verdict')).toBeVisible();
    await expect(page.getByText('Future price roadmap')).toBeVisible();

    await expectNoGenericFetchFailure(page);
  });

  test('negative: handles invalid ticker input gracefully', async ({ page }) => {
    const tickerInput = page.locator('.dt-ticker-input');
    await tickerInput.fill('INVALIDTICKER123');
    await page.getByRole('button', { name: 'Run analysis' }).click();

    // The UI should show an error state instead of hanging
    await expect(page.locator('.dt-error-banner, .error-banner')).toBeVisible({ timeout: 60000 });
    await expect(page.locator('.dt-error-banner, .error-banner')).toContainText(/Ticker must be 1–5 Latin letters/i);
  });

  test('negative: handles empty ticker submission', async ({ page }) => {
    const tickerInput = page.locator('.dt-ticker-input');
    await tickerInput.fill('');
    const analyzeBtn = page.getByRole('button', { name: 'Run analysis' });

    await expect(analyzeBtn).toBeDisabled();
  });
});
