import { expect, test, type Page, type TestInfo } from '@playwright/test';

const APP_URL = (process.env.APP_URL ?? 'https://frontend-manojsilwals-projects.vercel.app').replace(/\/$/, '');
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const OPENAI_BASE_URL = (process.env.OPENAI_BASE_URL ?? 'https://api.openai.com/v1').replace(/\/$/, '');
const LLM_QA_MODEL = process.env.LLM_QA_MODEL || 'gpt-4o-mini';
const LLM_QA_TICKER = (process.env.LLM_QA_TICKER ?? 'AAPL').trim().toUpperCase();
const DEBATE_TICKER = (process.env.LLM_QA_DEBATE_TICKER ?? 'SPY').trim().toUpperCase();
const SCORECARD_TICKERS = (process.env.LLM_QA_SCORECARD_TICKERS ?? 'SPY,QQQ')
  .split(',')
  .map((ticker) => ticker.trim().toUpperCase())
  .filter(Boolean)
  .slice(0, 4);

const YAHOO_SYMBOLS = Array.from(
  new Set([
    LLM_QA_TICKER,
    DEBATE_TICKER,
    ...SCORECARD_TICKERS,
    '^VIX',
    'XLK',
    'XLF',
    'XLV',
    'XLE',
    'XLC',
    'XLRE',
    'XME',
    'SPY',
    'EFA',
    'EWJ',
    'TLT',
    'GLD',
    'BIL',
    'GC=F',
    'DX-Y.NYB',
    '^TNX',
  ]),
);

type BrowserEvent = {
  feature?: string;
  type: 'console' | 'pageerror' | 'http';
  level?: string;
  message: string;
  url?: string;
  status?: number;
  method?: string;
};

type FeatureCapture = {
  feature: string;
  path: string;
  status: 'ok' | 'error';
  userAction: string;
  visibleText: string;
  error?: string;
  browserEvents: BrowserEvent[];
};

type YahooReference = {
  symbol: string;
  status: 'ok' | 'unavailable';
  price?: number;
  previousClose?: number | null;
  changePct?: number | null;
  marketState?: string;
  ageMinutes?: number | null;
  reason?: string;
};

function compactText(text: string, maxLength = 5_500): string {
  return text.replace(/\s+\n/g, '\n').replace(/\n{3,}/g, '\n\n').replace(/[ \t]{2,}/g, ' ').trim().slice(0, maxLength);
}

function stringifyError(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  return String(error);
}

function isAppUrl(url: string): boolean {
  return url.startsWith(APP_URL) || url.includes('tradetalkapp.onrender.com');
}

async function dismissOnboarding(page: Page): Promise<void> {
  const skip = page.getByRole('button', { name: /Skip tour/i });
  if (await skip.isVisible({ timeout: 2_000 }).catch(() => false)) {
    await skip.click();
    await expect(skip).toBeHidden({ timeout: 10_000 });
  }
}

async function visibleMainText(page: Page): Promise<string> {
  const main = page.locator('main').first();
  if (await main.isVisible({ timeout: 5_000 }).catch(() => false)) {
    return compactText(await main.innerText({ timeout: 10_000 }));
  }
  return compactText(await page.locator('body').innerText({ timeout: 10_000 }));
}

async function gotoPath(page: Page, path: string): Promise<void> {
  await page.goto(`${APP_URL}${path}`, { waitUntil: 'domcontentloaded' });
  await dismissOnboarding(page);
}

async function captureFeature(
  page: Page,
  allEvents: BrowserEvent[],
  feature: string,
  path: string,
  userAction: string,
  run: () => Promise<void>,
): Promise<FeatureCapture> {
  const startEventIndex = allEvents.length;
  try {
    await run();
    return {
      feature,
      path,
      status: 'ok',
      userAction,
      visibleText: await visibleMainText(page),
      browserEvents: allEvents.slice(startEventIndex),
    };
  } catch (error) {
    return {
      feature,
      path,
      status: 'error',
      userAction,
      visibleText: await visibleMainText(page).catch(() => ''),
      error: stringifyError(error),
      browserEvents: allEvents.slice(startEventIndex),
    };
  }
}

async function fetchYahooReference(symbol: string): Promise<YahooReference> {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`;
  let response: Response;
  try {
    response = await fetch(url);
  } catch (error) {
    return { symbol, status: 'unavailable', reason: `Yahoo network failure: ${stringifyError(error)}` };
  }

  if (response.status === 429) {
    return { symbol, status: 'unavailable', reason: 'Yahoo rate limited this run (HTTP 429)' };
  }
  if (!response.ok) {
    return { symbol, status: 'unavailable', reason: `Yahoo returned HTTP ${response.status}` };
  }

  try {
    const data = await response.json();
    const meta = data?.chart?.result?.[0]?.meta ?? {};
    const price = Number(meta.regularMarketPrice);
    const previousCloseRaw = meta.chartPreviousClose ?? meta.previousClose;
    const previousClose = previousCloseRaw == null ? null : Number(previousCloseRaw);
    const marketTime = meta.regularMarketTime == null ? null : Number(meta.regularMarketTime) * 1000;
    const ageMinutes = marketTime == null ? null : Math.round((Date.now() - marketTime) / 60_000);

    if (!Number.isFinite(price) || price <= 0) {
      return { symbol, status: 'unavailable', reason: 'Yahoo response had no usable regularMarketPrice' };
    }

    return {
      symbol,
      status: 'ok',
      price,
      previousClose: Number.isFinite(previousClose) && previousClose > 0 ? previousClose : null,
      changePct:
        Number.isFinite(previousClose) && previousClose > 0
          ? Number((((price - previousClose) / previousClose) * 100).toFixed(4))
          : null,
      marketState: String(meta.marketState ?? 'UNKNOWN'),
      ageMinutes,
    };
  } catch (error) {
    return { symbol, status: 'unavailable', reason: `Yahoo JSON parse failed: ${stringifyError(error)}` };
  }
}

function extractJsonObject(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    const start = text.indexOf('{');
    const end = text.lastIndexOf('}');
    if (start >= 0 && end > start) {
      return JSON.parse(text.slice(start, end + 1));
    }
    throw new Error(`LLM did not return JSON: ${text.slice(0, 500)}`);
  }
}

async function askQaLlm(report: unknown): Promise<any> {
  const response = await fetch(`${OPENAI_BASE_URL}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${OPENAI_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: LLM_QA_MODEL,
      temperature: 0,
      response_format: { type: 'json_object' },
      messages: [
        {
          role: 'system',
          content:
            'You are a strict production QA analyst for a finance web app. Review only the browser-visible UI evidence and the supplied Yahoo reference data. Flag high-confidence production bugs: stale or wrong market numbers, impossible finance claims, broken flows, missing results, serious browser/API errors, or UI text that contradicts the Yahoo reference. Do not fail subjective LLM opinions, educational copy, auth gates, or generated backtest strategy interpretation unless the UI is clearly broken. Treat Yahoo references marked unavailable as inconclusive, not as app bugs. Use tolerances: live prices may lag by 75 minutes, price tolerance is max(1%, $1), percent-change tolerance is 0.75 percentage points, macro/index tolerance is 5%. Return JSON only.',
        },
        {
          role: 'user',
          content: JSON.stringify({
            requiredOutput: {
              status: 'pass | warning | fail',
              summary: 'short QA summary',
              checked_features: ['feature names reviewed'],
              bugs: [
                {
                  severity: 'P0 | P1 | P2 | P3',
                  feature: 'feature name',
                  title: 'concise bug title',
                  evidence: 'visible evidence from captured UI or browser events',
                  expected: 'what should have happened',
                  actual: 'what happened',
                  suggested_fix_area: 'frontend route/component or data surface to inspect',
                },
              ],
            },
            report,
          }),
        },
      ],
    }),
  });

  const bodyText = await response.text();
  if (!response.ok) {
    throw new Error(`OpenAI QA request failed with HTTP ${response.status}: ${bodyText.slice(0, 800)}`);
  }

  const completion = JSON.parse(bodyText);
  const content = completion?.choices?.[0]?.message?.content;
  if (typeof content !== 'string' || !content.trim()) {
    throw new Error(`OpenAI QA response had no content: ${bodyText.slice(0, 800)}`);
  }
  return extractJsonObject(content);
}

test.describe.configure({ mode: 'serial' });

test.describe('LLM production UI QA', () => {
  test.beforeEach(() => {
    test.setTimeout(420_000);
  });

  test('LLM reviews production user flows against browser-visible evidence and Yahoo references', async ({ page }, testInfo: TestInfo) => {
    test.skip(!OPENAI_API_KEY, 'Set OPENAI_API_KEY to run the LLM production QA reviewer.');

    const browserEvents: BrowserEvent[] = [];
    let currentFeature = 'startup';

    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) {
        browserEvents.push({
          feature: currentFeature,
          type: 'console',
          level: message.type(),
          message: compactText(message.text(), 800),
        });
      }
    });
    page.on('pageerror', (error) => {
      browserEvents.push({
        feature: currentFeature,
        type: 'pageerror',
        message: stringifyError(error),
      });
    });
    page.on('response', (response) => {
      const url = response.url();
      if (response.status() >= 400 && isAppUrl(url)) {
        browserEvents.push({
          feature: currentFeature,
          type: 'http',
          status: response.status(),
          method: response.request().method(),
          url,
          message: `${response.status()} ${response.statusText()}`,
        });
      }
    });

    const features: FeatureCapture[] = [];
    const addFeature = async (
      feature: string,
      path: string,
      userAction: string,
      run: () => Promise<void>,
    ): Promise<void> => {
      currentFeature = feature;
      features.push(await captureFeature(page, browserEvents, feature, path, userAction, run));
    };

    await addFeature('Dashboard analysis', '/', `Analyze ${LLM_QA_TICKER}`, async () => {
      await gotoPath(page, '/');
      await page.locator('.dt-search-input').fill(LLM_QA_TICKER);
      await page.getByRole('button', { name: /^Analyze$/i }).click();
      await expect(
        page.getByTestId('dashboard-current-price').or(page.getByText(/Current price:/i)).first(),
      ).toBeVisible({ timeout: 240_000 });
    });

    await addFeature('Global Macro', '/macro', 'Open macro dashboard', async () => {
      await gotoPath(page, '/macro');
      await expect(page.getByText(/CBOE \^VIX Volatility|Global Macro/i).first()).toBeVisible({ timeout: 120_000 });
    });

    await addFeature('Gold Advisor', '/gold', 'Open gold advisor dashboard', async () => {
      await gotoPath(page, '/gold');
      await expect(page.getByText(/GC=F|DXY/i).first()).toBeVisible({ timeout: 180_000 });
    });

    await addFeature('Assistant quote', '/chat', `Ask for a ${DEBATE_TICKER} quote`, async () => {
      await gotoPath(page, '/chat');
      const input = page.getByTestId('chat-input');
      await expect(input).toBeVisible({ timeout: 30_000 });
      await input.fill(`Show the latest ${DEBATE_TICKER} quote and one sentence on whether it is up or down today.`);
      await page.getByRole('button', { name: /^Send$/i }).click();
      await expect(page.getByTestId('quote-card').first().or(page.getByTestId('evidence-contract'))).toBeVisible({
        timeout: 180_000,
      });
    });

    await addFeature('AI Debate', '/debate', `Run debate for ${DEBATE_TICKER}`, async () => {
      await gotoPath(page, '/debate');
      await page.getByPlaceholder('TICKER').fill(DEBATE_TICKER);
      await page.getByRole('button', { name: /Start Debate/i }).click();
      await expect(page.getByText('Panel Verdict')).toBeVisible({ timeout: 180_000 });
      await expect(page.getByText(/Confidence:\s*\d+%/)).toBeVisible();
    });

    await addFeature('Strategy Lab', '/backtest', 'Open strategy lab route', async () => {
      await gotoPath(page, '/backtest');
      await expect(page.locator('textarea, input').first()).toBeVisible({ timeout: 30_000 });
    });

    await addFeature('Risk-Return Scorecard', '/scorecard', `Run scorecard for ${SCORECARD_TICKERS.join(', ')}`, async () => {
      await gotoPath(page, '/scorecard');
      await page.getByPlaceholder(/Comma or space separated/i).fill(SCORECARD_TICKERS.join(', '));
      const skipLlm = page.getByLabel(/Skip LLM scoring/i);
      if (!(await skipLlm.isChecked())) {
        await skipLlm.check();
      }
      await page.getByRole('button', { name: /Run scorecard/i }).click();
      await expect(page.getByRole('heading', { name: /^Results$/ })).toBeVisible({ timeout: 120_000 });
      for (const ticker of SCORECARD_TICKERS) {
        await expect(page.getByRole('row', { name: new RegExp(ticker) })).toBeVisible();
      }
    });

    await addFeature('Daily Challenge auth gate', '/challenge', 'Open daily challenge route as signed-out user', async () => {
      await gotoPath(page, '/challenge');
      await expect(page.getByText(/Daily Challenges|Sign in/i).first()).toBeVisible({ timeout: 30_000 });
    });

    await addFeature('Paper Portfolio auth gate', '/portfolio', 'Open paper portfolio route as signed-out user', async () => {
      await gotoPath(page, '/portfolio');
      await expect(page.getByText(/Paper Portfolio|Sign in/i).first()).toBeVisible({ timeout: 30_000 });
    });

    await addFeature('Learning Path auth gate', '/learning', 'Open learning path route as signed-out user', async () => {
      await gotoPath(page, '/learning');
      await expect(page.getByText(/Learning Path|Sign in/i).first()).toBeVisible({ timeout: 30_000 });
    });

    await addFeature('Developer Trace', '/observer', `Open developer trace for ${LLM_QA_TICKER}`, async () => {
      await gotoPath(page, '/observer');
      await expect(page.getByText(/Developer Trace|Trace/i).first()).toBeVisible({ timeout: 30_000 });
    });

    await addFeature('System Map', '/systemmap', 'Open system map', async () => {
      await gotoPath(page, '/systemmap');
      await expect(page.getByText(/System Map|TradeTalk/i).first()).toBeVisible({ timeout: 30_000 });
    });

    await addFeature('System Diagrams', '/system-diagrams', 'Open system diagrams', async () => {
      await gotoPath(page, '/system-diagrams');
      await expect(page.getByText(/System Diagrams|Architecture/i).first()).toBeVisible({ timeout: 30_000 });
    });

    currentFeature = 'Yahoo references';
    const yahooReferences = await Promise.all(YAHOO_SYMBOLS.map(fetchYahooReference));
    const qaReport = {
      appUrl: APP_URL,
      capturedAt: new Date().toISOString(),
      perspective: 'Browser UI only for TradeTalk; Yahoo Finance chart endpoint is used only as the external reference.',
      tolerances: {
        maxMarketDataLagMinutes: 75,
        price: 'max(1%, $1)',
        percentChange: '0.75 percentage points',
        macroOrIndexValue: '5%',
      },
      features,
      yahooReferences,
      browserEvents,
    };

    await testInfo.attach('llm-production-qa-capture.json', {
      body: JSON.stringify(qaReport, null, 2),
      contentType: 'application/json',
    });

    currentFeature = 'LLM reviewer';
    const judgement = await askQaLlm(qaReport);
    await testInfo.attach('llm-production-qa-judgement.json', {
      body: JSON.stringify(judgement, null, 2),
      contentType: 'application/json',
    });

    const bugs = Array.isArray(judgement?.bugs) ? judgement.bugs : [];
    const blockingBugs = bugs.filter((bug: any) => ['P0', 'P1', 'P2'].includes(String(bug?.severity ?? '').toUpperCase()));
    const status = String(judgement?.status ?? '').toLowerCase();

    expect(
      { status, blockingBugs, summary: judgement?.summary },
      `LLM production QA failed:\n${JSON.stringify(judgement, null, 2)}`,
    ).toEqual(expect.objectContaining({ status: expect.not.stringMatching(/^fail$/), blockingBugs: [] }));
  });
});
