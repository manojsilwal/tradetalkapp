// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('AI Debate', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND + '/debate');
    await dismissOnboarding(page);
  });

  test('positive: debate runs for valid ticker', async ({ page }) => {
    await expect(page.getByText('AI Investment Debate Panel')).toBeVisible();

    const tickerInput = page.getByPlaceholder('TICKER');
    await tickerInput.fill('MSFT');
    await page.getByRole('button', { name: 'Start Debate' }).click();

    await expect(page.getByText('Bull Analyst')).toBeVisible({ timeout: 120000 });
    await expect(page.getByText('Bear Analyst')).toBeVisible();
  });

  test('negative: handles invalid ticker', async ({ page }) => {
    const tickerInput = page.getByPlaceholder('TICKER');
    await tickerInput.fill('INVALID123');
    await page.getByRole('button', { name: 'Start Debate' }).click();

    // Should show error instead of loading indefinitely
    await expect(page.getByText('Ticker must be 1–5 Latin letters')).toBeVisible({ timeout: 60000 });
  });
});
