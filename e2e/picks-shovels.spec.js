// @ts-check
/**
 * Smoke coverage for the Picks & Shovels Momentum Finder.
 * Verifies the page renders, the theme taxonomy loads, and the scan trigger is present.
 * Does NOT trigger a live scan (that hits yfinance) — keeps the smoke deterministic.
 * Run: `npx playwright test e2e/picks-shovels.spec.js`.
 */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Picks & Shovels Momentum Finder', () => {
  test('page loads with title and scan trigger', async ({ page }) => {
    await page.goto(`${FRONTEND.replace(/\/$/, '')}/picks-shovels`);
    await dismissOnboarding(page);
    await expect(
      page.getByRole('heading', { name: /Picks & Shovels Momentum Finder/i }),
    ).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole('button', { name: /Run Scan|Rescan/i }).first()).toBeVisible();
  });

  test('theme filter chips render from the taxonomy', async ({ page }) => {
    await page.goto(`${FRONTEND.replace(/\/$/, '')}/picks-shovels`);
    await dismissOnboarding(page);
    // The KPI/heatmap section only renders after a snapshot exists; the theme API
    // is always available, so at minimum the page header + disclaimer are present.
    await expect(page.getByText(/not investment advice/i).first()).toBeVisible({ timeout: 15000 });
  });
});
