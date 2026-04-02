// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding, expectNoGenericFetchFailure } = require('./support');

test.describe('Assistant Numeric Smoke', () => {
  test('chat returns numeric quote-style content for MSFT', async ({ page }) => {
    await page.goto('/chat');
    await dismissOnboarding(page);

    await expect(page.getByText('TradeTalk Assistant')).toBeVisible({ timeout: 30000 });
    const box = page.getByPlaceholder(/Ask about markets, your portfolio, or strategies/i);
    await expect(box).toBeVisible({ timeout: 30000 });

    await box.fill("What is the current price and today's % change for MSFT? Keep it short.");
    await page.getByRole('button', { name: /Send/i }).click();

    // Backend emits a structured quote_card SSE before streaming; ChatUI renders data-testid="quote-card".
    const card = page.getByTestId('quote-card');
    await expect(card).toBeVisible({ timeout: 180000 });
    await expect(card.getByText(/LIVE QUOTE · MSFT/i)).toBeVisible();
    await expect(card).toContainText(/\d/);
    await expectNoGenericFetchFailure(page);
  });
});
