// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectNoGenericFetchFailure, expectOneOf } = require('./support');

test.describe('Analysis Surfaces', () => {
  test('valuation dashboard renders verdict and metrics for AAPL', async ({ page }) => {
    await page.goto('/');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder('Ticker').fill('AAPL');
    await page.getByRole('button', { name: 'Analyze' }).click();
    await expectOneOf(page, ['Overall Verdict', 'Elite Investor Valuation Profile', 'Margin of Safety'], 90000);
    await expectNoGenericFetchFailure(page);
  });

  test('decision terminal renders verdict and roadmap for NVDA', async ({ page }) => {
    await page.goto('/decision-terminal');
    await dismissOnboarding(page);
    await expect(page.getByPlaceholder('TICKER')).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder('TICKER').fill('NVDA');
    await page.getByRole('button', { name: 'Run analysis' }).click();
    await expectOneOf(page, ['Verdict & sentiment hub', 'Aggregate verdict', 'Future price roadmap'], 120000);
    await expectNoGenericFetchFailure(page);
  });
});
