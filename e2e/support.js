// @ts-check
const { expect } = require('@playwright/test');

async function expectOneOf(page, candidates, timeout = 20000) {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    for (const candidate of candidates) {
      const locator = typeof candidate === 'string'
        ? page.getByText(candidate, { exact: false })
        : candidate(page);
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

module.exports = {
  dismissOnboarding,
  expectNoGenericFetchFailure,
  expectOneOf,
};
