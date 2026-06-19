// @ts-check
/**
 * Minimal prod smoke (landing, debate result, scorecard result, strategy lab).
 * Run: `npm run e2e:smoke` (set FRONTEND_URL for production).
 * API-only smoke: `FH_PROFILE=smoke E2E_API_BASE_URL=… npm run e2e:smoke:api`.
 * Deeper coverage: full `npm run e2e` or `faulthunter-api.spec.js` without smoke profile.
 */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectOneOf } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('TradeTalkApp E2E smoke', () => {
  test('landing page loads', async ({ page }) => {
    await page.goto(FRONTEND);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
  });



  test('mobile bottom nav opens paper portfolio', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(FRONTEND);
    await dismissOnboarding(page);
    await page.getByRole('button', { name: 'Portfolio', exact: true }).click();
    await expect(page).toHaveURL(/\/portfolio/);
    await expectOneOf(
      page,
      [/Unlock Paper Portfolio/i, /Import holdings/i, /Open Positions/i, /Add Position/i],
      30000,
    );
  });

  test('paper portfolio route shows import entry or auth gate', async ({ page }) => {
    await page.goto(`${FRONTEND.replace(/\/$/, '')}/portfolio`);
    await dismissOnboarding(page);
    await expectOneOf(
      page,
      [/Unlock Paper Portfolio/i, /Import holdings/i, /Open Positions/i, /Add Position/i],
      30000,
    );
  });
});
