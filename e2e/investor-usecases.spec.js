// @ts-check
/**
 * End-user flows in the browser. FaultHunter-equivalent HTTP checks: `e2e/faulthunter-api.spec.js` + `faulthunter-cases.js`.
 * Feature map: macro → `/macro`, gold → `/gold` (API `/advisor/gold`), debate → `/debate`, backtest → `/backtest`.
 */
const { test, expect } = require('@playwright/test');
const {
  dismissOnboarding,
  expectNoGenericFetchFailure,
  expectOneOf,
  waitForDecisionTerminalReady,
} = require('./support');

test.describe('Investor Use Cases', () => {
  test('valuation dashboard analyzes a ticker', async ({ page }) => {
    await page.goto('/');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder('Ticker').fill('AAPL');
    await page.getByRole('button', { name: 'Analyze' }).click();
    await expectOneOf(page, ['Overall Verdict', 'Elite Investor Valuation Profile', 'Margin of Safety'], 90000);
    await expectNoGenericFetchFailure(page);
  });

  test('decision terminal returns a verdict panel (FaultHunter: decision-aapl-today)', async ({ page }) => {
    await page.goto('/decision-terminal', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await waitForDecisionTerminalReady(page);
    await page.locator('.dt-ticker-input').fill('AAPL');
    await page.getByRole('button', { name: 'Run analysis' }).click();
    await expectOneOf(page, ['Verdict & sentiment hub', 'Aggregate verdict', 'Future price roadmap'], 120000);
    await expectNoGenericFetchFailure(page);
  });

  test('macro page loads key investor context (FaultHunter: macro-allocation-week)', async ({ page }) => {
    await page.goto('/macro', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'Global Macroeconomic Grounding' })).toBeVisible({
      timeout: 60000,
    });
    await expect(page.getByRole('heading', { name: 'Live Sector Rotation' })).toBeVisible({ timeout: 60000 });
    await expect(page.getByRole('heading', { name: 'Global Capital Flows' })).toBeVisible({ timeout: 60000 });
  });

  test('gold advisor shows macro inputs and briefing (FaultHunter: gold-hedge-week)', async ({ page }) => {
    await page.goto('/gold');
    await dismissOnboarding(page);
    await expect(page.getByText('Gold Advisor')).toBeVisible({ timeout: 30000 });
    await expectOneOf(page, ['AI briefing', 'DXY', '10Y TIPS real yield %'], 150000);
    await expectNoGenericFetchFailure(page);
  });

  test('debate flow produces a panel verdict (FaultHunter: debate-tsla-thesis)', async ({ page }) => {
    await page.goto('/debate');
    await dismissOnboarding(page);
    await expect(page.getByPlaceholder('TICKER')).toBeVisible({ timeout: 15000 });
    await page.getByPlaceholder('TICKER').fill('TSLA');
    await page.getByRole('button', { name: 'Start Debate' }).click();
    await expectOneOf(page, ['Panel Verdict', 'Bull Analyst', 'Moderator'], 180000);
    await expectNoGenericFetchFailure(page);
  });

  test('observer swarm trace loads for NVDA (FaultHunter: trace-nvda-today)', async ({ page }) => {
    test.setTimeout(180000);
    await page.goto('/observer');
    await dismissOnboarding(page);
    await page.getByPlaceholder('Ticker').fill('NVDA');
    await page.getByRole('button', { name: /Run Trace/i }).click();
    await expect(page.getByText('Short Sellers')).toBeVisible({ timeout: 120000 });
    await expectNoGenericFetchFailure(page);
  });

  test('strategy lab loads and accepts a backtest prompt (FaultHunter: backtest-dual-momentum-5y)', async ({ page }) => {
    test.setTimeout(420000);
    await page.goto('/backtest');
    await dismissOnboarding(page);
    await expect(page.getByText('Strategy Lab')).toBeVisible({ timeout: 30000 });
    const prompt = page.getByPlaceholder(/Buy Mag7 stocks/i);
    await expect(prompt).toBeVisible({ timeout: 30000 });
    await prompt.fill('Buy stocks trading above their 200-day moving average each year and rebalance annually');
    await page.getByRole('button', { name: /Run Backtest/i }).click();
    // Client aborts POST at BACKTEST_POST_TIMEOUT_MS (300s); wait slightly longer so we see results or the stalled banner.
    await expectOneOf(page, ['PARSED STRATEGY', 'CAGR', 'Max Drawdown', 'Sharpe Ratio', 'Backtest run:', 'Request ID:'], 360000);
    await expectNoGenericFetchFailure(page);
  });

  test('assistant opens a session and accepts a portfolio-level question', async ({ page }) => {
    test.setTimeout(480000);
    await page.goto('/chat');
    await dismissOnboarding(page);
    await expect(page.getByText('TradeTalk Assistant')).toBeVisible({ timeout: 30000 });
    const input = page.getByPlaceholder(/Ask about markets, your portfolio, or strategies/i);
    await expect(input).toBeVisible({ timeout: 180000 });
    await expect(page.getByText('Failed to fetch')).toHaveCount(0);
    await input.fill('What does an inverted yield curve mean for tech stocks?');
    await page.getByRole('button', { name: /Send/i }).click();
    await expectOneOf(
      page,
      [
        (p) => p.locator('strong').filter({ hasText: 'Assistant:' }),
        'Assistant:',
        'typing...',
        /yield\s+curve|inverted|recession|bond|tech\s+stock/i,
      ],
      180000,
    );
    await expectNoGenericFetchFailure(page);
  });

  test('portfolio and daily challenge are reachable for the investor workflow', async ({ page }) => {
    await page.goto('/portfolio');
    await dismissOnboarding(page);
    await expectOneOf(page, ['Open Positions', 'Unlock Paper Portfolio', 'Add Position'], 30000);

    await page.goto('/challenge');
    await dismissOnboarding(page);
    await expectOneOf(page, ['DAILY CHALLENGE', 'Unlock Daily Challenge', 'Today\'s Challenge'], 30000);
  });
});
