// @ts-check
/**
 * Live API accuracy checks against Yahoo Finance reference data.
 *
 * This suite intentionally calls the API surface directly. Use it for finance
 * number parity, not as a UI smoke test:
 *
 *   E2E_API_BASE_URL=http://127.0.0.1:8000 npm run e2e:api-accuracy
 *
 * Expensive mutating refreshes are skipped unless RUN_EXPENSIVE_ACCURACY=1.
 * Chat streaming and notification SSE are skipped unless their explicit env
 * flags are set, because they can hold open connections and call LLMs.
 */
const { test, expect } = require('@playwright/test');
const {
  fetchYahooCloseSeries,
  fetchYahooQuote,
  fetchYahooSummary,
  isUnavailable,
  priceTolerance,
} = require('./helpers/yahooFinance');

const API_BASE = (
  process.env.E2E_API_BASE_URL ||
  process.env.VITE_API_BASE_URL ||
  'http://127.0.0.1:8000'
).replace(/\/$/, '');

const TICKER = (process.env.ACCURACY_TICKER || 'AAPL').toUpperCase();
const PEER_TICKER = (process.env.ACCURACY_PEER_TICKER || 'MSFT').toUpperCase();
const SMALL_CAP_TICKER = (process.env.ACCURACY_SMALL_CAP_TICKER || 'HUMA').toUpperCase();

test.describe.configure({ mode: 'serial' });

/** @param {number | null | undefined} v */
function usableNumber(v) {
  return Number.isFinite(Number(v)) ? Number(v) : null;
}

/** @param {unknown} v */
function parseNumeric(v) {
  if (typeof v === 'number') return usableNumber(v);
  if (typeof v !== 'string') return null;
  const s = v.trim().replace(/,/g, '');
  const m = s.match(/-?\$?([0-9]+(?:\.[0-9]+)?)([KMBT])?/i);
  if (!m) return null;
  const mult = { K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[String(m[2] || '').toUpperCase()] || 1;
  return Number(m[1]) * mult;
}

function pctTolerance() {
  return Number(process.env.ACCURACY_PCT_TOLERANCE || 0.85);
}

function fundamentalTolerance(expected) {
  return Math.max(Math.abs(expected) * 0.08, 0.5);
}

/**
 * @param {import('@playwright/test').APIRequestContext} request
 * @param {'GET'|'POST'} method
 * @param {string} path
 * @param {{params?: Record<string, string | number | boolean>, data?: unknown, timeout?: number, headers?: Record<string, string>}} [opts]
 */
async function apiJson(request, method, path, opts = {}) {
  const url = `${API_BASE}${path}`;
  const timeout = opts.timeout || 180000;
  const headers = opts.headers || {};
  let res = method === 'GET'
    ? await request.get(url, { params: opts.params || {}, timeout, headers })
    : await request.post(url, {
      data: opts.data || {},
      headers: { 'Content-Type': 'application/json', ...headers },
      timeout,
    });
  if ([502, 503, 504].includes(res.status())) {
    await new Promise((r) => setTimeout(r, 5000));
    res = method === 'GET'
      ? await request.get(url, { params: opts.params || {}, timeout, headers })
      : await request.post(url, {
        data: opts.data || {},
        headers: { 'Content-Type': 'application/json', ...headers },
        timeout,
      });
  }
  expect(res.ok(), `${method} ${path}: HTTP ${res.status()} ${await res.text().catch(() => '')}`).toBeTruthy();
  return res.json();
}

/** @param {import('@playwright/test').APIRequestContext} request */
async function devAuthHeaders(request) {
  const res = await request.post(`${API_BASE}/auth/google`, {
    data: { token: 'dev' },
    headers: { 'Content-Type': 'application/json' },
    timeout: 60000,
  });
  test.skip(!res.ok(), `dev auth unavailable: HTTP ${res.status()}`);
  const json = await res.json();
  test.skip(!json.token, 'dev auth did not return a token');
  return { Authorization: `Bearer ${json.token}` };
}

function assertClose(label, actual, expected, tolerance) {
  const a = usableNumber(actual);
  const e = usableNumber(expected);
  expect(a, `${label}: app value must be numeric`).not.toBeNull();
  expect(e, `${label}: Yahoo value must be numeric`).not.toBeNull();
  expect(Math.abs(a - e), `${label}: app=${a} yahoo=${e} tolerance=${tolerance}`).toBeLessThanOrEqual(tolerance);
}

function assertOptionalClose(label, actual, expected, tolerance) {
  const a = usableNumber(actual);
  const e = usableNumber(expected);
  test.skip(a == null || e == null, `${label}: numeric value missing from app or Yahoo`);
  assertClose(label, a, e, tolerance);
}

test.describe('API accuracy vs live Yahoo Finance', () => {
  test('ticker quote-bearing APIs match Yahoo price and core fundamentals', async ({ request }) => {
    test.setTimeout(420000);
    const quote = await fetchYahooQuote(TICKER);
    test.skip(isUnavailable(quote), `Yahoo quote unavailable: ${isUnavailable(quote) ? quote.reason : ''}`);
    const summary = await fetchYahooSummary(TICKER);
    const hasSummary = !isUnavailable(summary);

    const validate = await apiJson(request, 'GET', `/metrics/validate/${TICKER}`, { timeout: 30000 });
    expect(validate.exists, '/metrics/validate should confirm ticker exists').toBe(true);
    assertClose('/metrics/validate last_price', validate.last_price, quote.price, priceTolerance(quote.price));

    const decision = await apiJson(request, 'GET', '/decision-terminal', {
      params: { ticker: TICKER },
      timeout: 300000,
    });
    assertClose(
      '/decision-terminal valuation.current_price_usd',
      decision.valuation?.current_price_usd,
      quote.price,
      priceTolerance(quote.price),
    );

    const metrics = await apiJson(request, 'GET', `/metrics/${TICKER}`, { timeout: 180000 });
    if (hasSummary) {
      assertOptionalClose(
        '/metrics market_cap',
        metrics.market_cap,
        summary.marketCap,
        Math.max(Number(summary.marketCap || 0) * 0.02, 5_000_000_000),
      );
    }
    expect(metrics.cap_bucket, '/metrics cap_bucket should be populated').toBeTruthy();

    const scorecard = await apiJson(request, 'GET', `/scorecard/${TICKER}`, {
      params: { preset: 'balanced', skip_llm_scores: true },
      timeout: 240000,
    });
    assertClose('/scorecard inputs.current_price', scorecard.inputs?.current_price, quote.price, priceTolerance(quote.price));
    if (hasSummary) {
      assertOptionalClose('/scorecard inputs.forward_pe', scorecard.inputs?.forward_pe, summary.forwardPE, fundamentalTolerance(Number(summary.forwardPE || 0)));
      assertOptionalClose('/scorecard inputs.beta', scorecard.inputs?.beta, summary.beta, 0.12);
      assertOptionalClose('/scorecard inputs.revenue_growth_pct', scorecard.inputs?.revenue_growth_pct, summary.revenueGrowthPct, 2.5);
    }

    const comparison = await apiJson(request, 'POST', '/scorecard/compare', {
      data: { tickers: [TICKER, PEER_TICKER], preset: 'balanced', skip_llm_scores: true },
      timeout: 240000,
    });
    const row = (comparison.rows || []).find((r) => r.ticker === TICKER);
    expect(row, '/scorecard/compare should include requested ticker').toBeTruthy();
    assertClose('/scorecard/compare row inputs.current_price', row.inputs?.current_price, quote.price, priceTolerance(quote.price));

    const predictor = await apiJson(request, 'GET', '/predictor/forecast', {
      params: { ticker: TICKER, horizon: '1d,5d' },
      timeout: 240000,
    });
    expect(predictor.ticker).toBe(TICKER);
    expect(predictor.horizon_bands_usd?.length, '/predictor/forecast should emit horizon bands').toBeGreaterThan(0);
    for (const band of predictor.horizon_bands_usd || []) {
      for (const key of ['q10_usd', 'q50_usd', 'q90_usd', 'point_usd']) {
        const value = usableNumber(band[key]);
        expect(value, `/predictor/forecast ${band.horizon}.${key} numeric`).not.toBeNull();
        expect(value, `/predictor/forecast ${band.horizon}.${key} positive`).toBeGreaterThan(0);
      }
    }
  });

  test('small-cap assessment only accepts genuinely small-cap Yahoo tickers', async ({ request }) => {
    test.setTimeout(180000);
    const summary = await fetchYahooSummary(SMALL_CAP_TICKER);
    test.skip(isUnavailable(summary), `Yahoo summary unavailable: ${isUnavailable(summary) ? summary.reason : ''}`);
    test.skip(
      !summary.marketCap || summary.marketCap >= 2_000_000_000,
      `${SMALL_CAP_TICKER} Yahoo market cap is not below $2B; set ACCURACY_SMALL_CAP_TICKER`,
    );

    const assessment = await apiJson(request, 'GET', `/small-cap-assessment/${SMALL_CAP_TICKER}`, {
      timeout: 240000,
    });
    expect(assessment.ticker).toBe(SMALL_CAP_TICKER);
    expect(String(assessment.cap_bucket || '').toLowerCase()).toMatch(/small|micro/);
    expect(assessment.signals?.length, '/small-cap-assessment should include signals').toBeGreaterThan(0);
  });

  test('macro market snapshots match Yahoo index and ETF moves', async ({ request }) => {
    test.setTimeout(300000);
    const macro = await apiJson(request, 'GET', '/macro', { timeout: 180000 });

    const vix = await fetchYahooQuote('^VIX');
    test.skip(isUnavailable(vix), `Yahoo VIX unavailable: ${isUnavailable(vix) ? vix.reason : ''}`);
    assertClose('/macro vix_level', macro.vix_level, vix.price, Math.max(priceTolerance(vix.price), 0.75));

    for (const sector of (macro.sectors || []).slice(0, 4)) {
      const ref = await fetchYahooQuote(sector.symbol);
      test.skip(isUnavailable(ref), `Yahoo sector quote unavailable for ${sector.symbol}`);
      assertOptionalClose(`/macro sectors.${sector.symbol}.daily_change_pct`, sector.daily_change_pct, ref.changePct, pctTolerance());
    }

    for (const flow of (macro.capital_flows || []).slice(0, 4)) {
      const ref = await fetchYahooQuote(flow.asset);
      test.skip(isUnavailable(ref), `Yahoo capital flow quote unavailable for ${flow.asset}`);
      assertOptionalClose(`/macro capital_flows.${flow.asset}.daily_change_pct`, flow.daily_change_pct, ref.changePct, pctTolerance());
    }

    const globalMarkets = await apiJson(request, 'GET', '/macro/global-markets', {
      params: { period: '1M', tickers: 'SPY,TLT' },
      timeout: 180000,
    });
    for (const symbol of ['SPY', 'TLT']) {
      const series = await fetchYahooCloseSeries(symbol, { range: '1mo', interval: '1d' });
      test.skip(isUnavailable(series), `Yahoo close series unavailable for ${symbol}`);
      const closes = series.closes;
      const expected = ((closes[closes.length - 1] - closes[0]) / closes[0]) * 100;
      const appSeries = globalMarkets.series?.[symbol] || [];
      assertClose(`/macro/global-markets ${symbol} last normalized pct`, appSeries[appSeries.length - 1], expected, 1.0);
    }
  });

  test('daily brief realtime overlay rows match Yahoo quotes', async ({ request }) => {
    test.setTimeout(240000);
    const brief = await apiJson(request, 'GET', '/daily-brief', {
      params: { losers: 3, gainers: 3 },
      timeout: 180000,
    });
    const rows = (brief.rows || []).filter((r) => r.symbol && usableNumber(r.close) != null).slice(0, 4);
    test.skip(rows.length === 0, '/daily-brief returned no quote-bearing rows');

    for (const row of rows) {
      const ref = await fetchYahooQuote(row.symbol);
      test.skip(isUnavailable(ref), `Yahoo quote unavailable for ${row.symbol}`);
      assertClose(`/daily-brief ${row.symbol}.close`, row.close, ref.price, priceTolerance(ref.price));
      assertOptionalClose(`/daily-brief ${row.symbol}.daily_return_pct`, row.daily_return_pct, ref.changePct, pctTolerance());
    }

    const screener = await apiJson(request, 'GET', '/daily-brief/screener', { timeout: 120000 });
    expect(Array.isArray(screener.rows), '/daily-brief/screener rows should be an array').toBe(true);
  });

  test('portfolio quote-bearing rows match Yahoo when a dev portfolio exists', async ({ request }) => {
    test.setTimeout(240000);
    const perf = await apiJson(request, 'GET', '/portfolio/performance', { timeout: 180000 });
    const positions = (perf.positions || []).filter((p) => p.ticker && usableNumber(p.current_price) != null).slice(0, 5);
    if (positions.length) {
      for (const p of positions) {
        const ref = await fetchYahooQuote(p.ticker);
        test.skip(isUnavailable(ref), `Yahoo quote unavailable for ${p.ticker}`);
        assertClose(`/portfolio/performance ${p.ticker}.current_price`, p.current_price, ref.price, priceTolerance(ref.price));
      }
    }

    const morning = await apiJson(request, 'GET', '/portfolio/morning-brief', { timeout: 180000 });
    expect(morning.as_of || morning.generated_at_utc || morning.summary, '/portfolio/morning-brief should return a dashboard payload').toBeTruthy();
    const movers = (morning.impact_movers || []).filter((m) => m.symbol && usableNumber(m.daily_return_pct) != null).slice(0, 3);
    for (const m of movers) {
      const ref = await fetchYahooQuote(m.symbol);
      test.skip(isUnavailable(ref), `Yahoo quote unavailable for ${m.symbol}`);
      assertOptionalClose(`/portfolio/morning-brief ${m.symbol}.daily_return_pct`, m.daily_return_pct, ref.changePct, pctTolerance());
    }
  });

  test('non-price catalog APIs remain live and structurally sane', async ({ request }) => {
    test.setTimeout(300000);
    const authHeaders = await devAuthHeaders(request);
    const predictionMarkets = await apiJson(request, 'GET', '/prediction-markets', {
      params: { ticker: TICKER },
      timeout: 120000,
    });
    expect(Array.isArray(predictionMarkets.events), '/prediction-markets events should be an array').toBe(true);

    const sankey = await apiJson(request, 'GET', '/macro/flow/sankey', { timeout: 120000 });
    expect(Array.isArray(sankey.nodes), '/macro/flow/sankey nodes should be an array').toBe(true);
    expect(Array.isArray(sankey.links), '/macro/flow/sankey links should be an array').toBe(true);

    const chain = await apiJson(request, 'GET', '/macro/flow/chain', { timeout: 120000 });
    expect(chain.interval || chain.stages || chain.nodes || chain.links, '/macro/flow/chain should return a payload').toBeTruthy();

    const stockGraph = await apiJson(request, 'GET', '/macro/flow/stock-graph', { timeout: 240000 });
    expect(Array.isArray(stockGraph.nodes), '/macro/flow/stock-graph nodes should be an array').toBe(true);
    expect(Array.isArray(stockGraph.links || stockGraph.edges), '/macro/flow/stock-graph links/edges should be an array').toBe(true);

    const supplyGraph = await apiJson(request, 'GET', '/macro/supply-chain/graph', { timeout: 120000 });
    expect(Array.isArray(supplyGraph.nodes), '/macro/supply-chain/graph nodes should be an array').toBe(true);
    expect(Array.isArray(supplyGraph.links || supplyGraph.edges), '/macro/supply-chain/graph links/edges should be an array').toBe(true);
    const firstNode = (supplyGraph.nodes || []).find((n) => n.id || n.node_id);
    if (firstNode) {
      const detail = await apiJson(request, 'GET', `/macro/supply-chain/nodes/${encodeURIComponent(firstNode.id || firstNode.node_id)}`, {
        timeout: 120000,
      });
      const detailNode = detail.node || detail;
      expect(
        detailNode.id || detailNode.node_id || detailNode.name,
        '/macro/supply-chain/nodes/{node_id} should return node detail',
      ).toBeTruthy();
      expect(Array.isArray(detail.upstream || []), '/macro/supply-chain/nodes/{node_id} upstream should be an array').toBe(true);
      expect(Array.isArray(detail.downstream || []), '/macro/supply-chain/nodes/{node_id} downstream should be an array').toBe(true);
    }

    const sectorSankey = await apiJson(request, 'GET', '/macro/supply-chain/sector-sankey', { timeout: 120000 });
    expect(Array.isArray(sectorSankey.nodes), '/macro/supply-chain/sector-sankey nodes should be an array').toBe(true);
    expect(Array.isArray(sectorSankey.links), '/macro/supply-chain/sector-sankey links should be an array').toBe(true);

    const trackRecord = await apiJson(request, 'GET', '/portfolio/track-record', { timeout: 120000 });
    expect(trackRecord, '/portfolio/track-record should return JSON').toBeTruthy();

    const timeline = await apiJson(request, 'GET', '/portfolio/timeline', { timeout: 120000 });
    expect(Array.isArray(timeline.items), '/portfolio/timeline items should be an array').toBe(true);

    const action = await apiJson(request, 'POST', '/portfolio/user-actions/log', {
      data: { action_type: 'accuracy_e2e_probe', symbol: TICKER, page: 'api-yahoo-accuracy' },
      timeout: 60000,
    });
    expect(action.ok, '/portfolio/user-actions/log should accept event').toBe(true);

    const news = await apiJson(request, 'GET', '/portfolio/news', {
      params: { tickers: TICKER },
      timeout: 120000,
    });
    expect(Array.isArray(news.items || news.news || news), '/portfolio/news should return news array data').toBe(true);

    const prefs = await apiJson(request, 'GET', '/preferences', { timeout: 60000 });
    expect(prefs.preferences, '/preferences should return preferences').toBeTruthy();

    const progress = await apiJson(request, 'GET', '/progress', { timeout: 60000, headers: authHeaders });
    expect(progress, '/progress should return JSON').toBeTruthy();

    const bootstrap = await apiJson(request, 'GET', '/chat/bootstrap', { timeout: 120000 });
    expect(bootstrap, '/chat/bootstrap should return JSON').toBeTruthy();

    const userContext = await apiJson(request, 'GET', '/chat/user-context', { timeout: 120000 });
    expect(userContext.context || userContext.authenticated === false, '/chat/user-context should return context or unauthenticated state').toBeTruthy();

    const session = await apiJson(request, 'POST', '/chat/session', { data: {}, timeout: 120000 });
    expect(session.session_id, '/chat/session should create a session').toBeTruthy();

    const status = await apiJson(request, 'GET', '/daily-brief/deep-refresh/status', { timeout: 60000 });
    expect(status.status || status.started_at || status.last_completed_at || typeof status === 'object', '/daily-brief/deep-refresh/status should return status').toBeTruthy();
  });

  test('expensive refresh APIs are opt-in and return accepted status', async ({ request }) => {
    test.skip(process.env.RUN_EXPENSIVE_ACCURACY !== '1', 'Set RUN_EXPENSIVE_ACCURACY=1 to run expensive refresh endpoints');
    test.setTimeout(600000);

    const macroRefresh = await apiJson(request, 'POST', '/macro/flow/refresh', {
      params: { interval: '1w' },
      data: {},
      timeout: 420000,
    });
    expect(typeof macroRefresh.ok).toBe('boolean');

    const deepRefresh = await apiJson(request, 'POST', '/daily-brief/deep-refresh', {
      params: { losers: 3, gainers: 3, wait: false },
      data: {},
      timeout: 120000,
    });
    expect(typeof deepRefresh.accepted).toBe('boolean');
  });

  test('chat message SSE is opt-in and starts a stream', async ({ request }) => {
    test.skip(process.env.RUN_CHAT_STREAM_ACCURACY !== '1', 'Set RUN_CHAT_STREAM_ACCURACY=1 to run chat/message SSE');
    test.setTimeout(240000);
    const session = await apiJson(request, 'POST', '/chat/session', { data: {}, timeout: 120000 });
    const res = await request.post(`${API_BASE}/chat/message`, {
      data: { session_id: session.session_id, message: `Give me the live price for ${TICKER}.` },
      headers: { 'Content-Type': 'application/json' },
      timeout: 180000,
    });
    expect(res.ok(), `POST /chat/message HTTP ${res.status()}`).toBeTruthy();
    expect(String(res.headers()['content-type'] || '')).toContain('text/event-stream');
  });

  test('notifications SSE is opt-in and exposes an event-stream endpoint', async () => {
    test.skip(process.env.RUN_SSE_ACCURACY !== '1', 'Set RUN_SSE_ACCURACY=1 to probe notifications SSE');
    test.setTimeout(30000);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5000);
    try {
      const res = await fetch(`${API_BASE}/notifications/stream`, { signal: controller.signal });
      expect(res.ok).toBe(true);
      expect(res.headers.get('content-type') || '').toContain('text/event-stream');
    } finally {
      clearTimeout(timer);
      controller.abort();
    }
  });
});
