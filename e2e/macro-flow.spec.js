// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

test.describe('Macro thematic flow', () => {
  test('sector flow and interval selector render', async ({ page }) => {
    await page.goto('/macro', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await expect(page.getByTestId('macro-flow-section')).toBeVisible({ timeout: 120000 });
    await expect(page.getByTestId('macro-sector-flow-panel')).toBeVisible({ timeout: 120000 });
    await page.getByTestId('macro-flow-interval-1m').click();
    await expect(page.getByTestId('macro-flow-interval-1m')).toBeVisible();
  });

  test('stock-level flow graph renders', async ({ page }) => {
    await page.goto('/macro', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await expect(page.getByTestId('macro-flow-section')).toBeVisible({ timeout: 120000 });
    await page.getByTestId('macro-flow-view-stock').click();
    await expect(page.getByTestId('macro-stock-flow-graph')).toBeVisible({ timeout: 180000 });
  });
});
