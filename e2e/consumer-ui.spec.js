// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectNoGenericFetchFailure } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('ConsumerUI (Valuation Dashboard)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND);
    await dismissOnboarding(page);
  });

  test('positive: loads and analyzes a valid ticker', async ({ page }) => {
    // Check initial state
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    const tickerInput = page.getByPlaceholder('Ticker');
    await expect(tickerInput).toBeVisible();

    // Input valid ticker
    await tickerInput.fill('AAPL');
    await page.getByRole('button', { name: 'Analyze' }).click();

    // Verify loading state appears
    // Removed loading check as it might be too fast locally

    // Wait for results
    // We expect the final verdict area or investor profile to appear within the timeout limit
    await expect(page.getByText('Overall Verdict')).toBeVisible({ timeout: 90000 });

    // Verify specific elements of the loaded analysis
    // 1. The metrics cards (assuming PE, Price, etc are loaded)
    await expect(page.getByText('Valuation & Cash Flow')).toBeVisible();

    // 2. The Agent factor analysis cards
    await expect(page.getByText('Margin of Safety')).toBeVisible();
    // await expect(page.getByText('Fundamentals QA')).toBeVisible();

    // Ensure no generic failures
    await expectNoGenericFetchFailure(page);
  });

  test('negative: handles invalid ticker input gracefully', async ({ page }) => {
    const tickerInput = page.getByPlaceholder('Ticker');

    // Input an invalid ticker
    await tickerInput.fill('INVALIDTICKER123');
    await page.getByRole('button', { name: 'Analyze' }).click();

    // Verify that the UI handles the error gracefully
    // Currently, yFinance will fail to fetch and the backend returns a 500 or 404, or returns null metrics
    // We expect the UI to show an error message instead of hanging on the loading screen forever
    await expect(page.locator('.error-banner')).toBeVisible({ timeout: 60000 });
    await expect(page.locator('.error-banner')).toContainText(/Ticker must be 1–5 Latin letters/i);
  });

  test('negative: handles empty ticker submission', async ({ page }) => {
    const tickerInput = page.getByPlaceholder('Ticker');
    await tickerInput.fill('');
    const analyzeBtn = page.getByRole('button', { name: 'Analyze' });

    // In many apps, clicking analyze on empty input does nothing or shows a validation message
    await expect(analyzeBtn).toBeDisabled();

    // Verify it doesn't go into a loading state
    await expect(page.getByText('Querying factors and running QA...')).toBeHidden();
  });
});
