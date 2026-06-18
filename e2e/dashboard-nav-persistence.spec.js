// @ts-check
/**
 * Stock Analysis keeps in-page loading UI when navigating away and back,
 * and does not spawn duplicate session rows for the same ticker.
 */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, runUnifiedLandingAnalyze } = require('./support');

test.describe('Dashboard navigation persistence', () => {
  test('restores loading progress after leaving and returning to Stock Analysis', async ({ page }) => {
    await page.goto('/dashboard?ticker=AAPL');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'Stock Analysis' })).toBeVisible({ timeout: 15000 });

    const input = page.locator('.dt-search-input');
    await expect(input).toBeVisible({ timeout: 30000 });
    if ((await input.inputValue()).trim().toUpperCase() !== 'AAPL') {
      await runUnifiedLandingAnalyze(page, 'AAPL');
    } else {
      await page.getByRole('button', { name: /^Analyze$/i }).click();
    }

    await expect(page.getByTestId('dashboard-analysis-progress')).toBeVisible({ timeout: 60000 });

    await page.getByRole('button', { name: 'Global Macro' }).click();
    await expect(page).toHaveURL(/\/macro/, { timeout: 15000 });

    await page.getByRole('button', { name: 'Stock Analysis' }).click();
    await expect(page).toHaveURL(/\/dashboard(\?ticker=AAPL)?/, { timeout: 15000 });
    await expect(input).toHaveValue('AAPL', { timeout: 10000 });
    await expect(page.getByTestId('dashboard-analysis-progress')).toBeVisible({ timeout: 15000 });

    const trayToggle = page.getByText(/Active Session/i);
    if (await trayToggle.isVisible().catch(() => false)) {
      await trayToggle.click();
      await expect(page.getByText('AAPL Analysis')).toHaveCount(1, { timeout: 5000 });
    }
  });
});
