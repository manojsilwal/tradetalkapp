// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

test.describe('Macro thematic flow', () => {
  test('interval selector and RRG panel render', async ({ page }) => {
    await page.goto('/macro', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await expect(page.getByTestId('macro-flow-section')).toBeVisible({ timeout: 120000 });
    await expect(page.getByTestId('macro-rrg-chart')).toBeVisible({ timeout: 120000 });
    await page.getByTestId('macro-flow-interval-1m').click();
    await expect(page.getByTestId('macro-flow-interval-1m')).toBeVisible();
    await page.getByTestId('macro-flow-view-sankey').click();
    await expect(page.getByTestId('macro-sankey-panel')).toBeVisible({ timeout: 60000 });
  });
});
