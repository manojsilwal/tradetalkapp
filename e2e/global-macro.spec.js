// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Global Macro Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND + '/macro');
    await dismissOnboarding(page);
  });

  test('positive: loads macro data and charts', async ({ page }) => {
    await expect(page.getByText('Global Macro')).toBeVisible();
    await expect(page.getByText('Market Regime')).toBeVisible({ timeout: 30000 });
  });
});
