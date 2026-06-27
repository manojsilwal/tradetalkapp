// @ts-check
/**
 * Browser E2E for analysis routes. API contracts matching FaultHunter live in `faulthunter-api.spec.js`
 * (see `faulthunter-cases.js` — same ids as `faulthunter/case_bank.py`).
 *
 * Overlap: valuation flow exercises ticker analysis; decision terminal uses AAPL like FaultHunter `decision-aapl-today`.
 */
const { test, expect } = require('@playwright/test');
const {
  dismissOnboarding,
  expectNoGenericFetchFailure,
  expectOneOf,
  runUnifiedLandingAnalyze,
  waitForDecisionTerminalReady,
} = require('./support');

test.describe('Analysis Surfaces', () => {
  test('valuation dashboard renders verdict and metrics for AAPL', async ({ page }) => {
    await page.goto('/dashboard');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    await runUnifiedLandingAnalyze(page, 'AAPL');
    await expect(page.getByTestId('dashboard-analysis-progress')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('dashboard-verdict-elapsed')).toBeVisible({ timeout: 30000 });
    await expect(page.getByTestId('dashboard-current-price')).toBeVisible({ timeout: 240000 });
    await expect(page.getByText('Verdict & Sentiment Hub')).toBeVisible();
    await expect(page.getByText('Business Quality Scorecard')).toBeVisible();
    await expect(page.getByTestId('consensus-valuation-panel')).toBeVisible({ timeout: 240000 });
    await expect(page.getByTestId('debate-panel-verdict')).toBeVisible({ timeout: 240000 });
    await expect(page.getByTestId('debate-panel-verdict-label')).toBeVisible();
    await expect(page.getByText(/STRONG BUY|BUY|NEUTRAL|SELL|STRONG SELL/)).toBeVisible();
    await expect(page.getByTestId('fundamental-health-banner')).toBeVisible({ timeout: 240000 });
    await expect(page.getByText(/High-quality business|Mixed fundamentals|Weak fundamentals|Insufficient data/)).toBeVisible();
    await expect(page.locator('.dt-health-chip').first()).toBeVisible();
    await expect(page.getByText(/\/100/)).toBeVisible();
    await page.getByTestId('momentum-model-tip-trigger').hover();
    await expect(page.getByTestId('momentum-model-hover-tip')).toBeVisible();
    await expectNoGenericFetchFailure(page);
  });

  test('dashboard renders fast surfaces while the debate slice is still synthesizing (AAPL)', async ({ page }) => {
    await page.goto('/dashboard');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'TradeTalk', exact: true })).toBeVisible({ timeout: 15000 });
    await runUnifiedLandingAnalyze(page, 'AAPL');

    // Fast slices (snapshot/swarm/roadmap + metrics/fundamentals) should land well
    // before the slow multi-agent debate slice — the page is interactive first.
    await expect(page.getByTestId('consensus-valuation-panel')).toBeVisible({ timeout: 120000 });
    await expect(page.getByText('Business Quality Scorecard')).toBeVisible();
    await expect(page.getByTestId('dashboard-debate-panel')).toBeVisible();

    // The debate verdict label only appears once the /decision-terminal/debate
    // slice resolves; it fills in after the fast surfaces are already shown.
    await expect(page.getByTestId('debate-panel-verdict-label')).toBeVisible({ timeout: 240000 });
    await expectNoGenericFetchFailure(page);
  });

  test('decision terminal renders verdict and roadmap for AAPL (FaultHunter: decision-aapl-today)', async ({ page }) => {
    await page.goto('/decision-terminal', { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await waitForDecisionTerminalReady(page);
    await page.locator('.dt-ticker-input').fill('AAPL');
    await page.getByRole('button', { name: 'Run analysis' }).click();
    // Fast snapshot slice should populate valuation/quality before slow verdict finishes.
    await expect(page.getByText('Consensus valuation signal')).toBeVisible({ timeout: 120000 });
    await expectOneOf(page, ['Verdict & sentiment hub', 'Aggregate verdict', 'Future price roadmap'], 240000);
    await expectNoGenericFetchFailure(page);
  });
});
