// @ts-check
const { test, expect } = require('@playwright/test');

const FRONTEND = process.env.FRONTEND_URL || 'https://frontend-manojsilwals-projects.vercel.app';

async function dismissOnboardingIfPresent(page) {
  const skip = page.getByRole('button', { name: /skip tour/i });
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
  }
}

test.describe('first-run UX', () => {
  test('sidebar navigation works without clicking Skip (overlay is non-blocking)', async ({ page }) => {
    await page.goto(`${FRONTEND}/`);
    await page.waitForTimeout(500);
    const welcome = page.getByText('Welcome to TradeTalk');
    await expect(welcome).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /Strategy Lab/i }).click();
    await expect(page).toHaveURL(/\/backtest/, { timeout: 15000 });
    await expect(welcome).not.toBeVisible({ timeout: 5000 });
  });

  test('Skip tour dismisses overlay', async ({ page }) => {
    await page.goto(`${FRONTEND}/`);
    await expect(page.getByText('Welcome to TradeTalk')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /skip tour/i }).click();
    await expect(page.getByText('Welcome to TradeTalk')).not.toBeVisible({ timeout: 3000 });
  });
});

test.describe('analysis actions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND);
    await dismissOnboardingIfPresent(page);
  });

  test('AI Debate loads and starts debate flow', async ({ page }) => {
    await page.getByRole('button', { name: /AI Debate/i }).click();
    await expect(page).toHaveURL(/\/debate/, { timeout: 15000 });
    const tickerInput = page.getByPlaceholder(/TICKER/i);
    await expect(tickerInput).toBeVisible({ timeout: 10000 });
    await tickerInput.fill('SPY');
    await page.getByRole('button', { name: /Start Debate/i }).click();
    await expect(page.getByText(/Bull Analyst|Bear Analyst|Analyst/i).first()).toBeVisible({
      timeout: 180000,
    });
  });
});

test.describe('backtest reliability', () => {
  test('Strategy Lab runs a preset backtest or shows structured error (not raw Failed to fetch)', async ({
    page,
  }) => {
    await page.goto(`${FRONTEND}/backtest`);
    await dismissOnboardingIfPresent(page);
    await expect(page.getByText(/PROVEN STRATEGY PRESETS|Strategy Lab/i).first()).toBeVisible({
      timeout: 20000,
    });
    await page.locator('button').filter({ hasText: 'Fama-French Quality' }).first().click({ timeout: 20000 });
    await page.getByRole('button', { name: /Run Backtest/i }).click();
    await expect(page.locator('text=Failed to fetch')).toHaveCount(0, { timeout: 120000 });
    const ok =
      (await page.getByText(/Total Return|CAGR|Sharpe/i).first().isVisible().catch(() => false)) ||
      (await page
        .getByText(/Backtest run:|Validation request:|Network error|timed out|HTTP \d+/i)
        .first()
        .isVisible()
        .catch(() => false));
    expect(ok).toBeTruthy();
  });
});

test.describe('portfolio (unauthenticated)', () => {
  test('portfolio shows auth or summary surface', async ({ page }) => {
    await page.goto(`${FRONTEND}/portfolio`);
    await dismissOnboardingIfPresent(page);
    const gate = page.getByText(/sign in|Sign in|unlock|paper portfolio/i);
    const summary = page.getByText(/Portfolio Value|vs SPY|Open Positions/i);
    await expect(gate.or(summary).first()).toBeVisible({ timeout: 15000 });
  });
});
