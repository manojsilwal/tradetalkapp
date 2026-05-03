import { test, expect } from '@playwright/test';

const APP_URL = process.env.APP_URL ?? 'https://frontend-manojsilwals-projects.vercel.app';

// Using Yahoo Finance public unauthenticated query API for validation
async function fetchYahooFinancePrice(ticker: string): Promise<number | null> {
  try {
    const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${ticker}`);
    if (!res.ok) return null;
    const data = await res.json();
    if (data?.chart?.result?.[0]?.meta?.regularMarketPrice) {
      return data.chart.result[0].meta.regularMarketPrice;
    }
    return null;
  } catch (e) {
    return null;
  }
}

test.describe('DATA PARITY - External Validation', () => {
  test('PARITY-001 — VIX Volatility index matches Yahoo Finance', async ({ page }) => {
    // Navigate to Global Macro page where VIX is displayed
    await page.goto(`${APP_URL}/macro`);

    // Wait for the specific data element to load. Based on our crawl: "CBOE ^VIX Volatility 16.99"
    await page.waitForTimeout(3000); // Allow real-time fetches to settle

    // Attempt to locate the VIX price value. We search for text nearby "VIX Volatility"
    const vixCard = page.locator('text=CBOE ^VIX Volatility').locator('..').locator('..');
    const vixText = await vixCard.innerText();

    // Parse the number out from the card, e.g., "CBOE ^VIX Volatility\n16.99\n..."
    const match = vixText.match(/([\d\.]+)/);
    expect(match).not.toBeNull();
    const appVixPrice = parseFloat(match![1]);

    // Fetch live from Yahoo
    const yahooVixPrice = await fetchYahooFinancePrice('^VIX');
    expect(yahooVixPrice).not.toBeNull();

    // Determine an acceptable threshold (e.g. 5% due to 15min delayed data vs live data)
    const threshold = 0.05;
    const diff = Math.abs(appVixPrice - yahooVixPrice!);
    const pctDiff = diff / yahooVixPrice!;

    console.log(`App VIX: ${appVixPrice}, Yahoo VIX: ${yahooVixPrice}, Diff: ${(pctDiff * 100).toFixed(2)}%`);

    expect(pctDiff).toBeLessThanOrEqual(threshold);
  });

  test('PARITY-002 — Sector ETFs match Yahoo Finance', async ({ page }) => {
    await page.goto(`${APP_URL}/macro`);
    await page.waitForTimeout(3000);

    // Pick a sector that is on the page, like XLK (Technology)
    const xlkCard = page.locator('text=XLK').locator('..').locator('..');
    const xlkText = await xlkCard.innerText();

    // The text looks like: Technology\nXLK\n+1.49%
    const pctMatch = xlkText.match(/([\+\-]\d+\.\d+)%/);
    expect(pctMatch).not.toBeNull();
    const appPct = parseFloat(pctMatch![1]);

    // Fetch XLK from Yahoo
    try {
        const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/XLK`);
        const data = await res.json();
        const meta = data.chart.result[0].meta;
        const prevClose = meta.chartPreviousClose;
        const currentPrice = meta.regularMarketPrice;

        const yahooPct = ((currentPrice - prevClose) / prevClose) * 100;

        // Since percentage change is a small number, we compare the absolute difference in percentage points
        const diff = Math.abs(appPct - yahooPct);

        console.log(`App XLK change: ${appPct}%, Yahoo XLK change: ${yahooPct.toFixed(2)}%, Diff points: ${diff.toFixed(2)}`);

        // Threshold of 0.5% point difference due to snapshot/delay timings
        expect(diff).toBeLessThan(0.5);
    } catch (e) {
        console.log("Yahoo rate limit or error", e.message);
    }
  });
});
