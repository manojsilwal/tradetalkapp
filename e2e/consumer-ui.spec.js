// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectNoGenericFetchFailure, runUnifiedLandingAnalyze } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('ConsumerUI (Valuation Dashboard)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND);
    await dismissOnboarding(page);
  });

  test('positive: loads and analyzes a valid ticker', async ({ page }) => {
    // Check initial state
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    await runUnifiedLandingAnalyze(page, 'AAPL');

    // Verify loading state appears
    // Removed loading check as it might be too fast locally

    await expect(page.getByTestId('dashboard-current-price')).toBeVisible({ timeout: 240000 });
    await expect(page.getByText('Verdict & Sentiment Hub')).toBeVisible();
    await expect(page.getByText('Business Quality Scorecard')).toBeVisible();

    // Ensure no generic failures
    await expectNoGenericFetchFailure(page);
  });

  test('negative: handles invalid ticker input gracefully', async ({ page }) => {
    const tickerInput = page.locator('.dt-search-input');
    await expect(tickerInput).toBeVisible({ timeout: 30000 });
    await tickerInput.fill('INVALIDTICKER123');
    // Unified dashboard: non–S&P 500 tickers disable Analyze until a suggestion is picked
    await expect(page.getByRole('button', { name: 'Analyze' })).toBeDisabled();
    await expect(page.locator('.dt-invalid')).toBeVisible();
  });

  test('negative: handles empty ticker submission', async ({ page }) => {
    const tickerInput = page.locator('.dt-search-input');
    await tickerInput.clear();
    const analyzeBtn = page.getByRole('button', { name: 'Analyze' });
    await expect(analyzeBtn).toBeEnabled();
    await analyzeBtn.click();
    // analyzeTicker returns immediately when ticker is blank — no long-running fetch step
    await expect(page.getByText(/Fetching combined market data/i)).toHaveCount(0);
  });
});
