// @ts-check
/**
 * Full SPA manual-style tour: every nav route loads with a recognizable shell.
 *
 * Prerequisites: FastAPI on VITE_DEV_PROXY_TARGET (default :8000) and Vite on :5173.
 *
 *   # terminal 1
 *   cd tradetalkapp && PYTHONPATH=. uvicorn backend.main:app --host 127.0.0.1 --port 8000
 *   # terminal 2
 *   cd tradetalkapp/frontend && npm run dev
 *   # terminal 3 — headless report
 *   cd tradetalkapp && npm run e2e:ui-tour
 *
 * Visual / “manual” pass in a real browser window:
 *   npm run e2e:ui-tour:headed
 */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

test.describe.configure({ mode: 'serial', timeout: 420000 });

test.describe('UI manual tour (all routes)', () => {
  test.beforeEach(async ({ page }) => {
    page.on('response', (res) => {
      if (res.status() >= 500) {
        // eslint-disable-next-line no-console
        console.warn('[tour] HTTP', res.status(), res.url());
      }
    });
  });

  test('01 Valuation Dashboard /', async ({ page }) => {
    await page.goto('/');
    await dismissOnboarding(page);
    await expect(page.getByRole('button', { name: /Analyze/i })).toBeVisible({ timeout: 30000 });
  });

  test('02 Decision Terminal', async ({ page }) => {
    await page.goto('/decision-terminal');
    await dismissOnboarding(page);
    await expect(page.locator('.dt-ticker-input')).toBeVisible({ timeout: 60000 });
    await expect(page.getByRole('button', { name: /Run analysis/i })).toBeVisible();
  });

  test('03 Global Macro', async ({ page }) => {
    await page.goto('/macro');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: /Global Macroeconomic Grounding/i })).toBeVisible({
      timeout: 120000,
    });
  });

  test('04 Gold Advisor', async ({ page }) => {
    await page.goto('/gold');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'Gold Advisor' })).toBeVisible({ timeout: 60000 });
  });

  test('05 Assistant (chat)', async ({ page }) => {
    await page.goto('/chat');
    await dismissOnboarding(page);
    await expect(page.getByTestId('chat-input')).toBeVisible({ timeout: 60000 });
  });

  test('06 AI Debate', async ({ page }) => {
    await page.goto('/debate');
    await dismissOnboarding(page);
    await expect(page.getByPlaceholder('TICKER')).toBeVisible({ timeout: 30000 });
  });

  test('07 Strategy Lab', async ({ page }) => {
    await page.goto('/backtest');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'Strategy Lab' })).toBeVisible({ timeout: 30000 });
  });

  test('08 Risk-Return Scorecard', async ({ page }) => {
    await page.goto('/scorecard');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'Risk-Return Scorecard' })).toBeVisible({
      timeout: 30000,
    });
  });

  test('09 Developer Trace', async ({ page }) => {
    await page.goto('/observer');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: /AI Agent Trace Log/i })).toBeVisible({
      timeout: 30000,
    });
  });

  test('10 System Map', async ({ page }) => {
    await page.goto('/systemmap');
    await dismissOnboarding(page);
    await expect(page.getByText('System Architecture Map')).toBeVisible({ timeout: 30000 });
  });

  test('11 Daily Challenge (AuthGate when logged out)', async ({ page }) => {
    await page.goto('/challenge');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: /Unlock Daily Challenges/i })).toBeVisible({
      timeout: 30000,
    });
  });

  test('12 Paper Portfolio (AuthGate when logged out)', async ({ page }) => {
    await page.goto('/portfolio');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: /Unlock Paper Portfolio/i })).toBeVisible({
      timeout: 30000,
    });
  });

  test('13 Learning Path (AuthGate when logged out)', async ({ page }) => {
    await page.goto('/learning');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: /Unlock Learning Path/i })).toBeVisible({
      timeout: 30000,
    });
  });

  test('14 System Diagrams', async ({ page }) => {
    await page.goto('/system-diagrams');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: /TradeTalk System Diagrams/i })).toBeVisible({
      timeout: 30000,
    });
  });
});
