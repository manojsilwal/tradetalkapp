// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Strategy Lab', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND + '/backtest');
    await dismissOnboarding(page);
  });

  test('positive: runs backtest', async ({ page }) => {
    test.setTimeout(360000);
    await expect(page.getByText('Strategy Lab')).toBeVisible();

    const strategyInput = page.getByPlaceholder('e.g. "Buy Mag7 stocks when PE ratio is below 25, sell when PE exceeds 35"');
    await strategyInput.fill('Buy stocks with PE below 20 and revenue growth above 10%, rebalance annually');

    await page.getByRole('button', { name: 'Run Backtest' }).click();

    await expect(page.getByText('Portfolio Summary')).toBeVisible({ timeout: 300000 });
  });
});
