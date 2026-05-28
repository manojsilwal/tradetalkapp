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
    await expect(page.getByTestId('macro-consumer-spending-chart')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('macro-cash-reserves-chart')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('macro-regime-impact-matrix')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('macro-capital-flows')).toBeVisible({ timeout: 30000 });

    // Validate chart renderers produced SVG output.
    await expect(page.locator('[data-testid="macro-consumer-spending-chart"] svg').first()).toBeVisible();
    await expect(page.locator('[data-testid="macro-cash-reserves-chart"] svg').first()).toBeVisible();

    // Ensure at least one capital flow tile and value change badge rendered.
    const cards = page.locator('[data-testid^="macro-flow-"]:not([data-testid$="-change"])');
    await expect(cards.first()).toBeVisible();
    const changes = page.locator('[data-testid^="macro-flow-"][data-testid$="-change"]');
    await expect(changes.first()).toBeVisible();
  });
});
