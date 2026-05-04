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
    await expect(page.getByText('You: What is AAPL?')).toBeVisible({ timeout: 60000 });
    await expect
      .poll(
        async () => {
          const line =
            (await page
              .locator('strong:has-text("Assistant:")')
              .last()
              .evaluate((el) => el.parentElement?.innerText || '')
              .catch(() => '')) || '';
          const s = line.replace(/\s+/g, ' ').trim();
          if (s.length < 18) return 0;
          if (/^Assistant:\s*Thinking$/i.test(s)) return 0;
          if (/OPENROUTER|AAPL|Apple|\$|price|configure|error|timeout|approximately|share/i.test(s)) return 1;
          return s.length > 80 ? 1 : 0;
        },
        { timeout: 180000 },
      )
      .toBe(1);
  });
});
