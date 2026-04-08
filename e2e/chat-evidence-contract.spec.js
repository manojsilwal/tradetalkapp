// @ts-check
/** Layer 1 — evidence_contract SSE must appear after each assistant turn (Phase A). */
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectNoGenericFetchFailure } = require('./support');

test.describe('Chat evidence contract (Phase A)', () => {
  test('SSE exposes evidence_contract with schema_version after MSFT quote question', async ({ page }) => {
    await page.goto('/chat');
    await dismissOnboarding(page);

    await expect(page.getByRole('heading', { name: 'TradeTalk Assistant' })).toBeVisible({ timeout: 90000 });
    const box = page.getByPlaceholder(/Ask about markets, your portfolio, or strategies/i);
    await expect(box).toBeVisible({ timeout: 60000 });

    await box.fill("What is the current price and today's % change for MSFT? Keep it short.");
    await page.getByRole('button', { name: /Send/i }).click();

    const panel = page.getByTestId('evidence-contract');
    await expect(panel).toBeVisible({ timeout: 240000 });
    await expect(panel).toContainText('"schema_version"');
    await expect(panel).toContainText('"confidence_band"');
    await expect(panel).toContainText('"sources_used"');
    await expectNoGenericFetchFailure(page);
  });

  test('evidence_contract present for a generic greeting (medium band, no abstain)', async ({ page }) => {
    await page.goto('/chat');
    await dismissOnboarding(page);
    await expect(page.getByRole('heading', { name: 'TradeTalk Assistant' })).toBeVisible({ timeout: 90000 });
    const box = page.getByPlaceholder(/Ask about markets, your portfolio, or strategies/i);
    await expect(box).toBeVisible({ timeout: 60000 });

    await box.fill('Hello — just saying hi.');
    await page.getByRole('button', { name: /Send/i }).click();

    const panel = page.getByTestId('evidence-contract');
    await expect(panel).toBeVisible({ timeout: 240000 });
    await expect(panel).toContainText('"confidence_band"');
    await expectNoGenericFetchFailure(page);
  });
});
