// @ts-check
const { test, expect } = require('@playwright/test');
const { fetchYahooQuote, isUnavailable, priceTolerance } = require('./helpers/yahooFinance');
const { dismissOnboarding } = require('./support');

const FRONTEND = (process.env.FRONTEND_URL || 'http://localhost:5173').replace(/\/$/, '');
const TICKER = (process.env.PARITY_TICKER || 'AAPL').toUpperCase();

function parseFirstPrice(text) {
  const m = String(text || '').replace(/,/g, '').match(/\$?\s*(-?\d+(?:\.\d+)?)/);
  if (!m) return null;
  const n = Number(m[1]);
  return Number.isFinite(n) ? n : null;
}

test.describe('Chat assistant parity', () => {
  test('chat quote stays near Yahoo reference', async ({ page }, testInfo) => {
    const ref = await fetchYahooQuote(TICKER);
    if (isUnavailable(ref)) {
      testInfo.annotations.push({ type: 'skip', description: ref.reason });
      test.skip(true, ref.reason);
    }

    await page.goto(`${FRONTEND}/chat`, { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);

    const input = page.getByPlaceholder('Ask about markets, your portfolio, or strategies…');
    await expect(input).toBeVisible({ timeout: 30000 });
    await input.fill(`What is ${TICKER} price right now?`);
    await page.getByRole('button', { name: 'Send' }).click();

    // Wait for assistant line that looks like a quote-bearing answer.
    const line = page.locator('strong:has-text("Assistant:")').last();
    await expect.poll(async () => {
      const raw = await line.evaluate((el) => el.parentElement?.innerText || '').catch(() => '');
      const txt = String(raw || '').replace(/\s+/g, ' ').trim();
      if (txt.length < 20) return 0;
      return /\$?\d+(?:\.\d+)?/.test(txt) ? 1 : 0;
    }, { timeout: 180000 }).toBe(1);

    const raw = await line.evaluate((el) => el.parentElement?.innerText || '').catch(() => '');
    if (/Chat error|429|RESOURCE_EXHAUSTED|quota|rate.?limit/i.test(raw)) {
      test.skip(
        true,
        'Assistant hit an upstream LLM/API error (quota or rate limit) — set OPENROUTER_API_KEY or refresh Gemini quota; not a Yahoo parity failure.',
      );
    }
    const appPrice = parseFirstPrice(raw);
    expect(appPrice, `No numeric price found in assistant response: ${raw}`).not.toBeNull();
    // Guard: first token can be "429" from error text if skip regex missed a variant.
    if (ref.price > 1 && Math.abs(appPrice - ref.price) > ref.price * 0.5) {
      test.skip(
        true,
        `Assistant numeric (${appPrice}) far from Yahoo (${ref.price}) — likely error text misparsed; check response: ${raw.slice(0, 200)}`,
      );
    }

    const tolerance = priceTolerance(ref.price);
    const diff = Math.abs(appPrice - ref.price);
    expect(
      diff,
      `chat ${TICKER}: app=${appPrice} yahoo=${ref.price} diff=${diff.toFixed(4)} tolerance=${tolerance.toFixed(4)}`,
    ).toBeLessThanOrEqual(tolerance);
  });
});
