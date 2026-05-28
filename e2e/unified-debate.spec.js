// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, runUnifiedLandingAnalyze } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Unified dashboard debate integration', () => {
  test('search auto-runs debate and renders transcript panel', async ({ page }) => {
    await page.goto(`${FRONTEND.replace(/\/$/, '')}/`);
    await dismissOnboarding(page);
    const unifiedNav = page.getByText('Unified Dashboard', { exact: false });
    if (await unifiedNav.first().isVisible().catch(() => false)) {
      await unifiedNav.first().click();
    }

    await runUnifiedLandingAnalyze(page, 'AAPL');

    await expect(page.getByRole('heading', { name: /AI Debate Panel/i })).toBeVisible({ timeout: 180000 });
    await expect(page.getByText(/Running multi-agent debate and synthesizing verdict/i)).toBeVisible({ timeout: 180000 });
    await expect(page.locator('section:has-text("AI Debate Panel")').locator('div').first()).toBeVisible();
  });
});

