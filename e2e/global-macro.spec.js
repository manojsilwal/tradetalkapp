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

  test('renders core visualization components with data', async ({ page }) => {
    await expect(page.getByTestId('macro-credit-stress-card')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('macro-fed-funds-card')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('global-cap-flow-dashboard')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('global-markets-chart')).toBeVisible({ timeout: 30000 });

    // Validate chart renderers produced SVG output.
    await expect(page.locator('[data-testid="global-markets-chart"] svg').first()).toBeVisible({ timeout: 30000 });
  });

});
