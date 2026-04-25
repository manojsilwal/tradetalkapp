// @ts-check
const { test, expect } = require('@playwright/test');
const { dismissOnboarding } = require('./support');

const FRONTEND = process.env.FRONTEND_URL || 'http://localhost:5173';

test.describe('Assistant Chat', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(FRONTEND + '/chat');
    await dismissOnboarding(page);
  });

  test('positive: chat loads and can send message', async ({ page }) => {
    await expect(page.getByText('TradeTalk Assistant')).toBeVisible();
    const input = page.getByPlaceholder('Ask about markets, your portfolio, or strategies…');
    await input.fill('What is AAPL?');
    await page.getByRole('button', { name: 'Send' }).click();

    // We expect either an answer or an error message (since LLM is not mocked)
    await expect(page.getByText('You: What is AAPL?')).toBeVisible();
    await expect(page.getByText('Assistant: Chat requires OPENROUTER_API_KEY')).toBeVisible();
  });
});
