import { expect, test, type Locator, type Page, type TestInfo } from '@playwright/test';

const APP_URL = (process.env.APP_URL ?? 'https://frontend-manojsilwals-projects.vercel.app').replace(/\/$/, '');

const PRICE_TOLERANCE_PCT = 0.01;
const PRICE_TOLERANCE_ABS = 1.0;
const MACRO_TOLERANCE_PCT = 0.05;
const PCT_POINT_TOLERANCE = 0.75;
const MAX_REFERENCE_AGE_MS = 75 * 60 * 1000;
const ACTIVE_MARKET_STATES = new Set(['PRE', 'REGULAR', 'POST']);
const DECISION_TICKER = process.env.PARITY_TICKER ?? 'AAPL';
const SCORECARD_TICKERS = (process.env.PARITY_SCORECARD_TICKERS ?? 'AAPL,MSFT')
  .split(',')
  .map((ticker) => ticker.trim().toUpperCase())
  .filter(Boolean)
  .slice(0, 3);

const MACRO_SECTOR_SYMBOLS = ['XLK', 'XLF', 'XLV', 'XLE', 'XLC', 'XLRE', 'XME'];
const MACRO_FLOW_SYMBOLS = ['SPY', 'EFA', 'EWJ', 'TLT', 'GLD', 'BIL'];

type YahooQuote = {
  symbol: string;
  price: number;
  previousClose: number | null;
  changePct: number | null;
  marketTime: number | null;
  marketState: string;
  ageMs: number | null;
};

type YahooUnavailable = {
  unavailable: true;
  reason: string;
};

type YahooResult = YahooQuote | YahooUnavailable;

function isUnavailable(result: YahooResult): result is YahooUnavailable {
  return 'unavailable' in result;
}

function numberFromText(text: string): number {
  const match = text.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
  if (!match) throw new Error(`No numeric value found in: ${text}`);
  return Number(match[0]);
}

function priceTolerance(expected: number): number {
  return Math.max(PRICE_TOLERANCE_ABS, Math.abs(expected) * PRICE_TOLERANCE_PCT);
}

async function textNumber(locator: Locator): Promise<number> {
  await expect(locator).toBeVisible({ timeout: 120_000 });
  return numberFromText(await locator.innerText());
}

async function dismissOnboarding(page: Page): Promise<void> {
  const skip = page.getByRole('button', { name: /Skip tour/i });
  if (await skip.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await skip.click();
    await expect(skip).toBeHidden({ timeout: 10_000 });
  }
}

async function fetchYahooQuote(symbol: string): Promise<YahooResult> {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`;
  let response: Response;
  try {
    response = await fetch(url);
  } catch (error) {
    return { unavailable: true, reason: `${symbol}: Yahoo network failure: ${String(error)}` };
  }

  if (response.status === 429) {
    return { unavailable: true, reason: `${symbol}: Yahoo rate limited this run (HTTP 429)` };
  }
  if (!response.ok) {
    return { unavailable: true, reason: `${symbol}: Yahoo returned HTTP ${response.status}` };
  }

  try {
    const data = await response.json();
    const result = data?.chart?.result?.[0];
    const meta = result?.meta ?? {};
    const price = Number(meta.regularMarketPrice);
    const previousCloseRaw = meta.chartPreviousClose ?? meta.previousClose;
    const previousClose = previousCloseRaw == null ? null : Number(previousCloseRaw);
    const marketTime = meta.regularMarketTime == null ? null : Number(meta.regularMarketTime) * 1000;
    const marketState = String(meta.marketState ?? 'UNKNOWN');
    const ageMs = marketTime == null ? null : Date.now() - marketTime;

    if (!Number.isFinite(price) || price <= 0) {
      return { unavailable: true, reason: `${symbol}: Yahoo response had no usable regularMarketPrice` };
    }

    return {
      symbol,
      price,
      previousClose: Number.isFinite(previousClose) && previousClose > 0 ? previousClose : null,
      changePct:
        Number.isFinite(previousClose) && previousClose > 0
          ? ((price - previousClose) / previousClose) * 100
          : null,
      marketTime,
      marketState,
      ageMs,
    };
  } catch (error) {
    return { unavailable: true, reason: `${symbol}: Yahoo JSON parse failed: ${String(error)}` };
  }
}

function skipIfYahooUnavailable(testInfo: TestInfo, refs: YahooResult[]): YahooQuote[] {
  const unavailable = refs.filter(isUnavailable);
  if (unavailable.length > 0) {
    const reason = unavailable.map((r) => r.reason).join('; ');
    testInfo.annotations.push({ type: 'skip', description: reason });
    test.skip(true, reason);
  }

  const quotes = refs as YahooQuote[];
  const stale = quotes.filter(
    (q) => ACTIVE_MARKET_STATES.has(q.marketState) && q.ageMs != null && q.ageMs > MAX_REFERENCE_AGE_MS,
  );
  if (stale.length > 0) {
    const reason = stale
      .map((q) => `${q.symbol}: Yahoo reference is ${Math.round((q.ageMs ?? 0) / 60_000)} minutes old`)
      .join('; ');
    testInfo.annotations.push({ type: 'skip', description: reason });
    test.skip(true, reason);
  }

  return quotes;
}

function expectPriceClose(actual: number, ref: YahooQuote, label: string): void {
  const tolerance = priceTolerance(ref.price);
  const diff = Math.abs(actual - ref.price);
  expect(
    diff,
    `${label}: app=${actual} yahoo=${ref.price} diff=${diff.toFixed(4)} tolerance=${tolerance.toFixed(4)}`,
  ).toBeLessThanOrEqual(tolerance);
}

function expectMacroClose(actual: number, ref: YahooQuote, label: string): void {
  const tolerance = Math.abs(ref.price) * MACRO_TOLERANCE_PCT;
  const diff = Math.abs(actual - ref.price);
  expect(
    diff,
    `${label}: app=${actual} yahoo=${ref.price} diff=${diff.toFixed(4)} tolerance=${tolerance.toFixed(4)}`,
  ).toBeLessThanOrEqual(tolerance);
}

function expectPctClose(actual: number, ref: YahooQuote, label: string): void {
  expect(ref.changePct, `${label}: Yahoo did not provide previous close for daily percent`).not.toBeNull();
  const expected = ref.changePct as number;
  const diff = Math.abs(actual - expected);
  expect(
    diff,
    `${label}: app=${actual.toFixed(3)}% yahoo=${expected.toFixed(3)}% diff=${diff.toFixed(3)}pp tolerance=${PCT_POINT_TOLERANCE}pp`,
  ).toBeLessThanOrEqual(PCT_POINT_TOLERANCE);
}

async function runDashboard(page: Page, ticker = DECISION_TICKER): Promise<void> {
  await page.goto(`${APP_URL}/`, { waitUntil: 'domcontentloaded' });
  await dismissOnboarding(page);
  await page.locator('.dt-search-input').fill(ticker);
  await page.getByRole('button', { name: /^Analyze$/i }).click();
  await expect(page.getByTestId('dashboard-current-price')).toBeVisible({ timeout: 240_000 });
}

test.describe.configure({ mode: 'serial' });

test.describe('Production Yahoo parity', () => {
  test.beforeEach(() => {
    test.setTimeout(360_000);
  });

  test('Unified Dashboard current price matches Yahoo', async ({ page }, testInfo) => {
    const [ref] = skipIfYahooUnavailable(testInfo, [await fetchYahooQuote(DECISION_TICKER)]);

    await runDashboard(page);
    const appPrice = await textNumber(page.getByTestId('dashboard-current-price'));
    expectPriceClose(appPrice, ref, `Dashboard ${DECISION_TICKER} current price`);
  });

  test('Macro VIX, sector ETFs, and capital-flow ETFs match Yahoo', async ({ page }, testInfo) => {
    const symbols = ['^VIX', ...MACRO_SECTOR_SYMBOLS, ...MACRO_FLOW_SYMBOLS];
    const refs = skipIfYahooUnavailable(testInfo, await Promise.all(symbols.map(fetchYahooQuote)));
    const bySymbol = new Map(refs.map((quote) => [quote.symbol, quote]));

    await page.goto(`${APP_URL}/macro`, { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await expect(page.getByTestId('macro-vix-value')).toBeVisible({ timeout: 120_000 });

    const appVix = await textNumber(page.getByTestId('macro-vix-value'));
    expectMacroClose(appVix, bySymbol.get('^VIX') as YahooQuote, 'Macro ^VIX');

    for (const symbol of MACRO_SECTOR_SYMBOLS) {
      const appPct = await textNumber(page.getByTestId(`macro-sector-${symbol}-change`));
      expectPctClose(appPct, bySymbol.get(symbol) as YahooQuote, `Macro sector ${symbol}`);
    }

    for (const symbol of MACRO_FLOW_SYMBOLS) {
      const appPct = await textNumber(page.getByTestId(`macro-flow-${symbol}-change`));
      expectPctClose(appPct, bySymbol.get(symbol) as YahooQuote, `Macro capital flow ${symbol}`);
    }
  });

  test('Gold Advisor market snapshot matches Yahoo', async ({ page }, testInfo) => {
    const refs = skipIfYahooUnavailable(testInfo, await Promise.all([
      fetchYahooQuote('GC=F'),
      fetchYahooQuote('DX-Y.NYB'),
      fetchYahooQuote('^VIX'),
      fetchYahooQuote('^TNX'),
    ]));
    const bySymbol = new Map(refs.map((quote) => [quote.symbol, quote]));

    await page.goto(`${APP_URL}/gold`, { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await expect(page.getByTestId('gold-metric-gold-gc-f-last-value')).toBeVisible({ timeout: 180_000 });

    expectPriceClose(
      await textNumber(page.getByTestId('gold-metric-gold-gc-f-last-value')),
      bySymbol.get('GC=F') as YahooQuote,
      'Gold Advisor GC=F last',
    );
    expectMacroClose(
      await textNumber(page.getByTestId('gold-metric-dxy-value')),
      bySymbol.get('DX-Y.NYB') as YahooQuote,
      'Gold Advisor DXY',
    );
    expectMacroClose(
      await textNumber(page.getByTestId('gold-metric-vix-value')),
      bySymbol.get('^VIX') as YahooQuote,
      'Gold Advisor VIX',
    );
    // Yahoo ^TNX `regularMarketPrice` is already quoted in yield percent (e.g. ~4.37), same units as the Gold UI.
    expectMacroClose(
      await textNumber(page.getByTestId('gold-metric-10y-nominal-value')),
      bySymbol.get('^TNX') as YahooQuote,
      'Gold Advisor 10Y nominal',
    );
  });

  test('Scorecard user flow renders visible results for a Yahoo-backed basket', async ({ page }) => {
    await page.goto(`${APP_URL}/scorecard`, { waitUntil: 'domcontentloaded' });
    await dismissOnboarding(page);
    await page.getByPlaceholder(/Comma or space separated/i).fill(SCORECARD_TICKERS.join(', '));
    const skipLlm = page.getByLabel(/Skip LLM scoring/i);
    if (!(await skipLlm.isChecked())) {
      await skipLlm.check();
    }
    await page.getByRole('button', { name: /Run scorecard/i }).click();

    for (const symbol of SCORECARD_TICKERS) {
      const row = page.getByTestId(`scorecard-row-${symbol}`);
      await expect(row).toBeVisible({ timeout: 180_000 });
      await expect(row).toContainText(symbol);
      await expect(row).toContainText(/Exceptional|Strong buy|Favorable|Balanced|Caution|Avoid/);
    }
  });
});
