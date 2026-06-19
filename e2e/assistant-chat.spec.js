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

    // Local dev often lacks OPENROUTER; production streams then completes with model text.
    await expect(page.getByTestId('user-message').filter({ hasText: 'What is AAPL?' })).toBeVisible({ timeout: 60000 });
    await expect
      .poll(
        async () => {
          const raw = await page.getByTestId('assistant-message').last().innerText().catch(() => '');
          const s = raw.replace(/\s+/g, ' ').trim();
          if (s.length < 18) return 0;
          if (/Thinking/i.test(s)) return 0;
          return 1;
        },
        { timeout: 180000 },
      )
      .toBe(1);
  });
});
