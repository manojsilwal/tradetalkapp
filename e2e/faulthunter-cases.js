/**
 * Mirrors FaultHunter `faulthunter/case_bank.py` — same ids, endpoints, methods, query/body, required fields, latency budgets.
 * Keep in sync when case_bank changes.
 *
 * @typedef {object} FaultHunterCase
 * @property {string} id
 * @property {string} feature
 * @property {'GET'|'POST'} method
 * @property {string} path
 * @property {Record<string, string>} [params]  GET query or POST JSON body
 * @property {string[]} requiredFields  dotted paths into JSON response
 * @property {number} slowLatencyMs  informational; Playwright timeout set separately
 */

/** @type {FaultHunterCase[]} */
const SMOKE_CASES = [
  {
    id: 'decision-aapl-today',
    feature: 'decision_terminal',
    method: 'GET',
    path: '/decision-terminal',
    params: { ticker: 'AAPL' },
    requiredFields: ['generated_at_utc', 'valuation.current_price_usd', 'verdict.headline_verdict'],
    slowLatencyMs: 15000,
  },
  {
    id: 'macro-allocation-week',
    feature: 'macro',
    method: 'GET',
    path: '/macro',
    params: {},
    requiredFields: ['market_regime', 'vix_level', 'dxy_level', 'treasury_10y', 'macro_narrative'],
    slowLatencyMs: 5000,
  },
  {
    id: 'gold-hedge-week',
    feature: 'gold',
    method: 'GET',
    path: '/advisor/gold',
    params: {},
    requiredFields: ['briefing.directional_bias', 'briefing.confidence_0_1', 'context.macro.dxy_spot'],
    slowLatencyMs: 7000,
  },
];

/** @type {FaultHunterCase[]} */
const DAILY_ONLY_CASES = [
  {
    id: 'trace-nvda-today',
    feature: 'trace',
    method: 'GET',
    path: '/trace',
    params: { ticker: 'NVDA' },
    requiredFields: ['global_verdict', 'confidence'],
    slowLatencyMs: 15000,
  },
  {
    id: 'debate-tsla-thesis',
    feature: 'debate',
    method: 'GET',
    path: '/debate',
    params: { ticker: 'TSLA' },
    requiredFields: ['verdict', 'consensus_confidence', 'arguments'],
    slowLatencyMs: 25000,
  },
  {
    id: 'backtest-dual-momentum-5y',
    feature: 'backtest',
    method: 'POST',
    path: '/backtest',
    params: {
      preset_id: 'dual_momentum',
      start_date: '2021-01-01',
      end_date: '2026-01-01',
    },
    requiredFields: [
      'strategy.name',
      'strategy.preset_id',
      'strategy.survivorship_note',
      'sharpe_ratio',
      'max_drawdown',
      'benchmark_cagr',
      'outperformed',
      'ai_explanation',
    ],
    slowLatencyMs: 60000,
  },
];

const ALL_DAILY_CASES = [...SMOKE_CASES, ...DAILY_ONLY_CASES];

module.exports = {
  SMOKE_CASES,
  DAILY_ONLY_CASES,
  ALL_DAILY_CASES,
};
