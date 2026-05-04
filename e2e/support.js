// @ts-check
const { expect } = require('@playwright/test');

async function expectOneOf(page, candidates, timeout = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    for (const candidate of candidates) {
      /** @type {import('@playwright/test').Locator} */
      let locator;
      if (typeof candidate === 'string') {
        locator = page.getByText(candidate, { exact: false });
      } else if (candidate instanceof RegExp) {
        locator = page.getByText(candidate);
      } else {
        locator = candidate(page);
      }
      if (await locator.first().isVisible().catch(() => false)) {
        return locator.first();
      }
    }
    await page.waitForTimeout(250);
  }
  throw new Error(`None of the expected UI markers became visible: ${candidates.map(String).join(', ')}`);
}

async function dismissOnboarding(page) {
  const skip = page.getByRole('button', { name: 'Skip tour' });
  if (await skip.isVisible().catch(() => false)) {
    await skip.click();
    await expect(skip).toBeHidden({ timeout: 10000 });
  }
}

async function expectNoGenericFetchFailure(page) {
  await expect(page.getByText('Failed to fetch')).toHaveCount(0);
}

/**
 * SPA routes lazy-load chunks; wait for Decision Terminal controls after navigation.
 * @param {import('@playwright/test').Page} page
 */
async function waitForDecisionTerminalReady(page) {
  await page.waitForSelector('.dt-ticker-input', { state: 'visible', timeout: 90000 });
}

/**
 * Unified landing (`/`) — UnifiedDashboardUI uses `.dt-search-input` (not legacy `placeholder="Ticker"`).
 */
async function runUnifiedLandingAnalyze(page, ticker) {
  const input = page.locator('.dt-search-input');
  await expect(input).toBeVisible({ timeout: 120000 });
  await input.fill(ticker);
  await page.getByRole('button', { name: /^Analyze$/i }).click();
}

module.exports = {
  dismissOnboarding,
  expectNoGenericFetchFailure,
  expectOneOf,
  waitForDecisionTerminalReady,
  runUnifiedLandingAnalyze,
};
