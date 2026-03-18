// @ts-check
const { test, expect } = require('@playwright/test');

const FRONTEND = process.env.FRONTEND_URL || 'https://frontend-manojsilwals-projects.vercel.app';

test.describe('TradeTalkApp E2E smoke', () => {
  test('landing page loads', async ({ page }) => {
    await page.goto(FRONTEND);
    await expect(page.getByRole('heading', { name: 'K2-Optimus', exact: true })).toBeVisible({ timeout: 15000 });
  });

  test('AI Debate tab loads and can trigger debate', async ({ page }) => {
    await page.goto(FRONTEND);
    await page.click('text=AI Debate');
    const tickerInput = page.getByPlaceholder('TICKER');
    await expect(tickerInput).toBeVisible({ timeout: 10000 });
    await tickerInput.fill('SPY');
    await page.click('button:has-text("Start Debate")');
    // Wait for debate to complete — agent cards appear
    await expect(page.getByText('Bull Analyst')).toBeVisible({ timeout: 180000 });
  });

  test('Strategy Lab tab loads', async ({ page }) => {
    await page.goto(FRONTEND);
    await page.click('text=Strategy Lab');
    await expect(page.locator('textarea, input').first()).toBeVisible({ timeout: 10000 });
  });
});
