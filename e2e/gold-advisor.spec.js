// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Gold Advisor', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND + '/gold');
    await dismissOnboarding(page);
  });

  test('positive: loads gold analysis', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Gold Advisor', exact: true })).toBeVisible();
    // Generate an analysis
    await expect(page.getByRole('heading', { name: 'AI briefing' })).toBeVisible({ timeout: 60000 });
    await expect(page.getByText('Key drivers')).toBeVisible();
  });
});
