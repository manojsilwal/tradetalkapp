import { useState, useMemo } from 'react'
import {
    Search, ChevronDown, ChevronRight, ExternalLink, Copy, Check,
    Server, Filter, BookOpen, ArrowRight, Layers,
} from 'lucide-react'
import { API_BASE_URL } from './api'

const METHOD_STYLES = {
    GET:    { bg: 'rgba(16,185,129,0.15)', color: '#34d399', border: 'rgba(52,211,153,0.35)' },
    POST:   { bg: 'rgba(59,130,246,0.15)', color: '#60a5fa', border: 'rgba(96,165,250,0.35)' },
    PUT:    { bg: 'rgba(245,158,11,0.15)', color: '#fbbf24', border: 'rgba(251,191,36,0.35)' },
    DELETE: { bg: 'rgba(239,68,68,0.15)', color: '#f87171', border: 'rgba(248,113,113,0.35)' },
    SSE:    { bg: 'rgba(139,92,246,0.15)', color: '#c4b5fd', border: 'rgba(167,139,250,0.35)' },
}

/** Curated catalog of APIs whose responses are rendered in the TradeTalk UI. */
const API_CATALOG = [
    {
        tag: 'Analysis & Verdicts',
        description: 'Ticker-level swarm, debate, decision terminal, and scorecard surfaces.',
        endpoints: [
            {
                method: 'GET',
                path: '/trace',
                summary: 'Swarm consensus — 4 factor pairs in parallel',
                uiSurfaces: ['Dashboard', 'Observer'],
                uiComponent: 'UnifiedDashboardUI.jsx · AnalysisContext.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'ticker', type: 'string', required: true, example: 'AAPL', description: 'Stock symbol' },
                        { name: 'credit_stress', type: 'float', required: false, description: 'Override macro credit-stress index (0–1)' },
                    ],
                },
                response: {
                    type: 'SwarmConsensus',
                    fields: [
                        'ticker, macro_state, global_signal, global_verdict, confidence, consensus_rationale',
                        'factors.{short_interest|social_sentiment|polymarket|fundamentals} → {signal, verdict, rationale, qa_status}',
                    ],
                },
                architecture: 'React → GET /trace → analysis router → asyncio.gather(4×AgentPair) → connectors + RAG → SwarmConsensus JSON → factor cards + verdict gauge',
            },
            {
                method: 'GET',
                path: '/debate',
                summary: '5-agent LLM debate + moderator verdict',
                uiSurfaces: ['Dashboard'],
                uiComponent: 'UnifiedDashboardUI.jsx · AnalysisContext.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'ticker', type: 'string', required: true, example: 'AAPL' }],
                },
                response: {
                    type: 'DebateResult',
                    fields: [
                        'arguments[] → {agent, stance, argument, confidence}',
                        'verdict, consensus_confidence, moderator_summary, bull_score, bear_score, neutral_score',
                    ],
                },
                architecture: 'React → GET /debate → fetch_debate_data + macro_fetch → 5 role LLMs (RAG per role) → Moderator LLM → DebateResult → argument cards + verdict banner',
            },
            {
                method: 'GET',
                path: '/decision-terminal',
                summary: '4-panel valuation terminal (valuation, quality, verdict, roadmap)',
                uiSurfaces: ['Decision Terminal', 'Dashboard'],
                uiComponent: 'DecisionTerminalUI.jsx · AnalysisContext.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'ticker', type: 'string', required: true, example: 'AAPL' },
                        { name: 'credit_stress', type: 'float', required: false },
                        { name: 'provider_audit', type: 'bool', required: false, description: 'Include provider routing audit block' },
                        { name: 'audit', type: 'int', required: false, description: '1 = full audit payload' },
                    ],
                },
                response: {
                    type: 'DecisionTerminalPayload',
                    fields: [
                        'valuation, quality, verdict, roadmap (each panel object)',
                        'disclaimer, generated_at_utc, market_data_degraded?, spot_price_source?',
                    ],
                },
                architecture: 'React → GET /decision-terminal → aggregates snapshot + verdict + roadmap slices in parallel → merged DecisionTerminalPayload (used by the dashboard). The standalone Decision Terminal page calls the three slices below directly for progressive rendering.',
            },
            {
                method: 'GET',
                path: '/decision-terminal/snapshot',
                summary: 'Fast slice: valuation + quality + spot + scorecard (no swarm/debate/LLM)',
                uiSurfaces: ['Decision Terminal'],
                uiComponent: 'DecisionTerminalUI.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'ticker', type: 'string', required: true, example: 'AAPL' },
                        { name: 'force', type: 'bool', required: false, description: 'Bypass per-trading-day snapshot cache' },
                    ],
                },
                response: {
                    type: 'DecisionSnapshotPayload',
                    fields: [
                        'valuation, quality (panel objects)',
                        'spot, spot_price_source?, market_data_degraded?, scorecard_summary?',
                    ],
                },
                architecture: 'React → GET /decision-terminal/snapshot → market data + DCF/multiples/momentum + quality → renders Consensus Valuation + Business Quality panels first (seconds)',
            },
            {
                method: 'GET',
                path: '/decision-terminal/verdict',
                summary: 'Slow slice: fused swarm + debate verdict (+ embedded swarm/debate, brain block)',
                uiSurfaces: ['Decision Terminal'],
                uiComponent: 'DecisionTerminalUI.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'ticker', type: 'string', required: true, example: 'AAPL' },
                        { name: 'credit_stress', type: 'float', required: false },
                        { name: 'force', type: 'bool', required: false, description: 'Bypass per-trading-day verdict cache' },
                    ],
                },
                response: {
                    type: 'DecisionVerdictPayload',
                    fields: [
                        'verdict (panel), swarm, debate (for Trace/Debate tabs)',
                        'brain?, macro_fetched_at_utc?, verdict_captured_at_utc?',
                    ],
                },
                architecture: 'React → GET /decision-terminal/verdict → swarm factor agents + 5-agent debate + Polymarket gating + brain cutover → Verdict & Sentiment Hub (emits Decision-Outcome Ledger)',
            },
            {
                method: 'GET',
                path: '/decision-terminal/roadmap',
                summary: 'Roadmap slice: 3Y scenario prices (predictor-first, heuristic fallback)',
                uiSurfaces: ['Decision Terminal'],
                uiComponent: 'DecisionTerminalUI.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'ticker', type: 'string', required: true, example: 'AAPL' },
                        { name: 'force', type: 'bool', required: false, description: 'Bypass per-trading-day roadmap cache' },
                    ],
                },
                response: {
                    type: 'DecisionRoadmapPayload',
                    fields: [
                        'roadmap (bull/base/bear + horizon bands + provenance)',
                        'current_price_usd?',
                    ],
                },
                architecture: 'React → GET /decision-terminal/roadmap → probabilistic predictor (TimesFM path) or historical-CAGR heuristic → Future Price Roadmap chart',
            },
            {
                method: 'GET',
                path: '/metrics/{ticker}',
                summary: 'Investor metrics time-series (PE, ROE, margins, etc.)',
                uiSurfaces: ['Dashboard'],
                uiComponent: 'UnifiedDashboardUI.jsx · AnalysisContext.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    path: [{ name: 'ticker', type: 'string', required: true, example: 'AAPL' }],
                },
                response: {
                    type: 'InvestorMetricsResponse',
                    fields: [
                        'metrics.{name} → {current, historical, trend, history[]}',
                        'market_cap, cap_bucket',
                    ],
                },
                architecture: 'React → GET /metrics/{ticker} → yFinance connector → metric sparklines & trend badges on dashboard',
            },
            {
                method: 'GET',
                path: '/metrics/validate/{ticker}',
                summary: 'Fast ticker existence probe before full analysis',
                uiSurfaces: ['Dashboard'],
                uiComponent: 'AnalysisContext.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    path: [{ name: 'ticker', type: 'string', required: true }],
                },
                response: {
                    type: 'object',
                    fields: ['ticker, exists (bool), last_price?, reason?'],
                },
                architecture: 'Pre-flight check → blocks invalid tickers before expensive /trace and /debate calls',
            },
            {
                method: 'GET',
                path: '/scorecard/{ticker}',
                summary: 'Single-ticker scorecard row (9-factor weighted verdict)',
                uiSurfaces: ['Dashboard'],
                uiComponent: 'AnalysisContext.jsx',
                backendFile: 'backend/routers/scorecard.py',
                auth: 'optional',
                request: {
                    path: [{ name: 'ticker', type: 'string', required: true }],
                    query: [
                        { name: 'preset', type: 'string', required: false, example: 'balanced', description: 'growth | value | income | balanced' },
                        { name: 'skip_llm_scores', type: 'bool', required: false, description: 'Skip LLM-assisted qualitative scores' },
                    ],
                },
                response: {
                    type: 'ScorecardRowOut',
                    fields: ['return_score, risk_score, ratio, quadrant, verdict, inputs (factor breakdown)'],
                },
                architecture: 'React → GET /scorecard/{ticker} → factor engine + optional LLM → quadrant chart on dashboard',
            },
            {
                method: 'POST',
                path: '/scorecard/compare',
                summary: 'Multi-ticker scorecard comparison table',
                uiSurfaces: ['Paper Portfolio'],
                uiComponent: 'PaperPortfolioUI.jsx',
                backendFile: 'backend/routers/scorecard.py',
                auth: 'optional',
                request: {
                    body: {
                        type: 'ScorecardCompareRequest',
                        fields: [
                            'tickers: string[1..10]',
                            'preset: string',
                            'weights_override?: object',
                            'situational_flags?: string[]',
                            'skip_llm_scores?: bool',
                        ],
                    },
                },
                response: {
                    type: 'ScorecardResponse',
                    fields: ['preset, weights, denominators, rows[], notes[]'],
                },
                architecture: 'Portfolio holdings → POST /scorecard/compare → ranked comparison table in portfolio view',
            },
            {
                method: 'GET',
                path: '/prediction-markets',
                summary: 'Polymarket + Kalshi event probabilities for ticker',
                uiSurfaces: ['Dashboard'],
                uiComponent: 'AnalysisContext.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'ticker', type: 'string', required: true }],
                },
                response: {
                    type: 'object',
                    fields: ['ticker, has_relevant_data, events[], context, sources'],
                },
                architecture: 'React → GET /prediction-markets → Polymarket/Kalshi connectors → event probability cards',
            },
            {
                method: 'GET',
                path: '/predictor/forecast',
                summary: 'Multi-horizon probabilistic price forecast (TimesFM service; insufficient_data without it)',
                uiSurfaces: ['Decision Terminal'],
                uiComponent: 'DecisionTerminalUI.jsx',
                backendFile: 'backend/routers/analysis.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'ticker', type: 'string', required: true, example: 'AAPL' },
                        { name: 'horizon', type: 'string', required: false, example: '1d,5d,21d,63d', description: 'Comma-separated horizons' },
                    ],
                },
                response: {
                    type: 'PredictorForecastResponse',
                    fields: [
                        'status, ticker, cycle_id, directional_bias, model_confidence',
                        'horizon_bands_usd[] → {horizon, low_usd, high_usd, median_usd}',
                        'bull/base/bear_price_usd_3y_scenario, synthesis_summary, assumptions[]',
                    ],
                },
                architecture: 'Decision Terminal roadmap panel → GET /predictor/forecast → predictor agent + ledger emit → horizon bands chart',
            },
            {
                method: 'GET',
                path: '/small-cap-assessment/{ticker}',
                summary: 'Small/micro-cap deep assessment (cap < $2B only)',
                uiSurfaces: ['Dashboard'],
                uiComponent: 'AnalysisContext.jsx',
                backendFile: 'backend/routers/small_cap.py',
                auth: 'optional',
                request: {
                    path: [{ name: 'ticker', type: 'string', required: true }],
                },
                response: {
                    type: 'SmallCapAssessment',
                    fields: ['cap_bucket, signals[], overall_verdict, revenue_streams[], major_deals[]'],
                },
                architecture: 'Triggered when cap_bucket is Small/Micro → specialized assessment panel on dashboard',
            },
        ],
    },
    {
        tag: 'Macro & Markets',
        description: 'Global macro dashboard, flow visualizations, and market charts.',
        endpoints: [
            {
                method: 'GET',
                path: '/macro',
                summary: 'Global macro snapshot — VIX, sectors, capital flows',
                uiSurfaces: ['Dashboard', 'Macro'],
                uiComponent: 'MacroUI.jsx · AnalysisContext.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'MacroDataResponse',
                    fields: [
                        'vix_level, credit_stress_index, market_regime',
                        'sectors[], consumer_spending[], capital_flows[], cash_reserves[]',
                        'treasury_yield, cpi_yoy, macro_narrative',
                    ],
                },
                architecture: 'React → GET /macro → FRED + yFinance + sector ETFs → regime badge, sector rotation, spending charts',
            },
            {
                method: 'GET',
                path: '/macro/global-markets',
                summary: 'Indexed % change series for global indices/ETFs',
                uiSurfaces: ['Macro'],
                uiComponent: 'GlobalMarketsChart.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'period', type: 'string', required: false, example: '1M', description: '1W | 1M | 3M | 6M | YTD | 1Y' },
                        { name: 'tickers', type: 'string', required: false, description: 'Comma-separated tickers (e.g. SPY,QQQ,EEM)' },
                    ],
                },
                response: {
                    type: 'object',
                    fields: ['dates: string[], series: {TICKER: number[]}'],
                },
                architecture: 'GlobalMarketsChart → GET /macro/global-markets → multi-line Recharts chart (% from period start)',
            },
            {
                method: 'GET',
                path: '/macro/flow/sankey',
                summary: 'Macro capital-flow Sankey diagram nodes & links',
                uiSurfaces: ['Macro'],
                uiComponent: 'MacroFlowPanel.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'interval', type: 'string', required: false, example: '1w', description: '1d | 1w | 1m | 1y' }],
                },
                response: {
                    type: 'object',
                    fields: [
                        'nodes[] → {id, name, qa_verdict, flow_score, color_hex?}',
                        'links[] → {source, target, value, edge_id, description}',
                    ],
                },
                architecture: 'MacroFlowPanel → GET /macro/flow/sankey → @nivo/sankey capital flow visualization',
            },
            {
                method: 'GET',
                path: '/macro/flow/chain',
                summary: 'Value-chain stages for flow Sankey companion',
                uiSurfaces: ['Macro'],
                uiComponent: 'MacroFlowPanel.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'interval', type: 'string', required: false, example: '1w' }],
                },
                response: {
                    type: 'object',
                    fields: ['interval, has_data, stages[], flows[], spend, note'],
                },
                architecture: 'MacroFlowPanel chain view → stage-by-stage capital allocation bars',
            },
            {
                method: 'GET',
                path: '/macro/flow/stock-graph',
                summary: 'Stock-level co-flow directed graph',
                uiSurfaces: ['Macro'],
                uiComponent: 'MacroFlowPanel.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'interval', type: 'string', required: false }],
                },
                response: {
                    type: 'object',
                    fields: ['nodes[], edges[] (correlation-weighted directed edges)'],
                },
                architecture: 'MacroFlowPanel stock graph tab → @xyflow/react network diagram',
            },
            {
                method: 'POST',
                path: '/macro/flow/refresh',
                summary: 'Recompute macro flow snapshot (expensive)',
                uiSurfaces: ['Macro'],
                uiComponent: 'MacroFlowPanel.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'interval', type: 'string', required: false }],
                },
                response: {
                    type: 'object',
                    fields: ['ok, categories?, error?'],
                },
                architecture: 'Manual refresh button → POST → recomputes SQLite flow snapshot → subsequent GETs serve fresh data',
            },
            {
                method: 'GET',
                path: '/macro/supply-chain/graph',
                summary: 'Supply-chain node graph (SEC 10-K derived)',
                uiSurfaces: ['Macro (supply chain tab)'],
                uiComponent: 'supplyChain/SupplyChainTab.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'year', type: 'int', required: false },
                        { name: 'root', type: 'string', required: false },
                    ],
                },
                response: {
                    type: 'object',
                    fields: ['year, root, nodes[], edges[]'],
                },
                architecture: 'SupplyChainTab → GET graph → interactive supply-chain network visualization',
            },
            {
                method: 'GET',
                path: '/macro/supply-chain/sector-sankey',
                summary: 'Sector rollup Sankey for supply chain',
                uiSurfaces: ['Macro (supply chain tab)'],
                uiComponent: 'supplyChain/SupplyChainTab.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'year', type: 'int', required: false, example: '2025' }],
                },
                response: {
                    type: 'object',
                    fields: ['nodes[], links[] (sector-level rollup)'],
                },
                architecture: 'SupplyChainTab sector view → Sankey of sector interdependencies',
            },
            {
                method: 'GET',
                path: '/macro/supply-chain/nodes/{node_id}',
                summary: 'Supply-chain node detail panel',
                uiSurfaces: ['Macro (supply chain tab)'],
                uiComponent: 'supplyChain/SupplyChainTab.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: {
                    path: [{ name: 'node_id', type: 'string', required: true }],
                    query: [{ name: 'year', type: 'int', required: false }],
                },
                response: {
                    type: 'object',
                    fields: ['Node metadata, suppliers, customers, revenue exposure'],
                },
                architecture: 'Node click → GET detail → side panel with company/sector context',
            },
            {
                method: 'GET',
                path: '/advisor/gold',
                summary: 'Gold allocator briefing (FRED + LLM)',
                uiSurfaces: ['Macro'],
                uiComponent: 'MacroUI.jsx',
                backendFile: 'backend/routers/macro.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'GoldAdvisorResponse',
                    fields: ['context (FRED indicators), briefing (LLM narrative + allocation guidance)'],
                },
                architecture: 'Macro gold panel → GET /advisor/gold → macro context cards + LLM briefing text',
            },
        ],
    },
    {
        tag: 'Daily Brief',
        description: 'Market movers screener and deep-refresh enrichment.',
        endpoints: [
            {
                method: 'GET',
                path: '/daily-brief',
                summary: 'Daily movers table with verdict tiers',
                uiSurfaces: ['Daily Brief', 'Dashboard'],
                uiComponent: 'DailyBriefUI.jsx · AnalysisContext.jsx',
                backendFile: 'backend/routers/daily_brief.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'trade_date', type: 'string', required: false, description: 'YYYY-MM-DD' },
                        { name: 'losers', type: 'int', required: false },
                        { name: 'gainers', type: 'int', required: false },
                        { name: 'refresh', type: 'bool', required: false },
                    ],
                },
                response: {
                    type: 'object',
                    fields: [
                        'trade_date, source, verdict_tier',
                        'rows[] → {symbol, move_pct, verdict, ...}',
                        'deep_refresh, from_snapshot?',
                    ],
                },
                architecture: 'DailyBriefUI → GET /daily-brief → movers table with Buy/Sell/Hold verdict badges + realtime quote overlay',
            },
            {
                method: 'GET',
                path: '/daily-brief/screener',
                summary: 'Filtered snapshot — Buy/Sell/Strong Buy rows only',
                uiSurfaces: ['Daily Brief'],
                uiComponent: 'AnalysisContext.jsx',
                backendFile: 'backend/routers/daily_brief.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'trade_date', type: 'string', required: false }],
                },
                response: {
                    type: 'object',
                    fields: ['Filtered rows[] with actionable verdicts'],
                },
                architecture: 'Screener tab → actionable picks from daily snapshot',
            },
            {
                method: 'POST',
                path: '/daily-brief/deep-refresh',
                summary: 'LLM-enriched deep refresh for movers',
                uiSurfaces: ['Daily Brief'],
                uiComponent: 'DailyBriefUI.jsx',
                backendFile: 'backend/routers/daily_brief.py',
                auth: 'optional',
                request: {
                    query: [
                        { name: 'trade_date', type: 'string', required: false },
                        { name: 'wait', type: 'bool', required: false, description: 'Block until complete' },
                    ],
                },
                response: {
                    type: 'object',
                    fields: ['accepted, completed?, deep_refresh status'],
                },
                architecture: 'Deep refresh button → POST → async LLM enrichment → poll /deep-refresh/status',
            },
            {
                method: 'GET',
                path: '/daily-brief/deep-refresh/status',
                summary: 'Deep-refresh job status',
                uiSurfaces: ['Daily Brief'],
                uiComponent: 'DailyBriefUI.jsx',
                backendFile: 'backend/routers/daily_brief.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['Job status, progress, last_completed_at'],
                },
                architecture: 'Polling endpoint while deep-refresh runs in background',
            },
        ],
    },
    {
        tag: 'Strategy Lab',
        description: 'Backtest simulation and strategy leaderboard.',
        endpoints: [
            {
                method: 'GET',
                path: '/strategies/presets',
                summary: 'Predefined strategy templates',
                uiSurfaces: ['Strategy Lab'],
                uiComponent: 'BacktestUI.jsx',
                backendFile: 'backend/routers/backtest.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['presets[] → {id, name, description, ...}'],
                },
                architecture: 'BacktestUI preset picker → dropdown of strategy templates',
            },
            {
                method: 'GET',
                path: '/strategies/leaderboard',
                summary: 'Top-performing saved strategies',
                uiSurfaces: ['Strategy Lab'],
                uiComponent: 'BacktestUI.jsx',
                backendFile: 'backend/routers/backtest.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'n', type: 'int', required: false, example: '20' }],
                },
                response: {
                    type: 'object',
                    fields: ['strategies[], total'],
                },
                architecture: 'Leaderboard panel → ranked strategies by CAGR/Sharpe',
            },
            {
                method: 'POST',
                path: '/backtest/validate',
                summary: 'Validate strategy rules before simulation',
                uiSurfaces: ['Strategy Lab'],
                uiComponent: 'BacktestUI.jsx',
                backendFile: 'backend/routers/backtest.py',
                auth: 'optional',
                request: {
                    body: {
                        type: 'BacktestRequest',
                        fields: ['strategy?, preset_id?, start_date, end_date'],
                    },
                },
                response: {
                    type: 'object',
                    fields: ['valid (bool), errors?, warnings?'],
                },
                architecture: 'Pre-submit validation → surfaces rule errors before expensive POST /backtest',
            },
            {
                method: 'POST',
                path: '/backtest',
                summary: 'Run backtest simulation with AI explanation',
                uiSurfaces: ['Strategy Lab'],
                uiComponent: 'BacktestUI.jsx',
                backendFile: 'backend/routers/backtest.py',
                auth: 'optional',
                request: {
                    body: {
                        type: 'BacktestRequest',
                        fields: ['strategy (plain English or rules), preset_id?, start_date, end_date'],
                    },
                },
                response: {
                    type: 'BacktestResult',
                    fields: [
                        'strategy, actions[], cagr, sharpe_ratio, max_drawdown, win_rate',
                        'portfolio_value_series[], benchmark_value_series[]',
                        'ai_explanation, reflection, retrieval_telemetry',
                    ],
                },
                architecture: 'BacktestUI → POST /backtest → LLM parse rules → yFinance history → simulate → PnL chart + metrics cards + AI narrative',
            },
        ],
    },
    {
        tag: 'Portfolio & Engagement',
        description: 'Paper portfolio, Your Morning dashboard, behavioural signals, gamification, and preferences.',
        endpoints: [
            {
                method: 'GET',
                path: '/portfolio/performance',
                summary: 'Portfolio P&L vs SPY benchmark chart',
                uiSurfaces: ['Paper Portfolio'],
                uiComponent: 'PaperPortfolioUI.jsx',
                backendFile: 'backend/routers/portfolio.py',
                auth: 'required',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['Performance series, benchmark comparison, summary stats'],
                },
                architecture: 'Portfolio chart → GET /portfolio/performance → Recharts equity curve',
            },
            {
                method: 'GET',
                path: '/portfolio/morning-brief',
                summary: 'Your Morning dashboard — KPIs, impact movers, sentiment, sector swings',
                uiSurfaces: ['Your Morning', 'Dashboard'],
                uiComponent: 'YourMorningHero.jsx · ImpactMoversPanel.jsx · PortfolioSentimentCard.jsx · SectorSwingsCard.jsx',
                backendFile: 'backend/morning_brief.py · backend/routers/portfolio.py',
                auth: 'required',
                request: { query: [] },
                response: {
                    type: 'MorningBriefPayload',
                    fields: [
                        'as_of, user_id, greeting, headline, has_portfolio, disclaimer',
                        'summary → {total_value, daily_return_pct, daily_return_value, top_positive_contributor, top_negative_contributor, benchmark_context.{spy,qqq,ijr}_daily_return_pct}',
                        'impact_movers[] → {symbol, company_name, sector, industry, sector_tags, daily_return_pct, portfolio_impact_pct, impact_score, relative_volume, sparkline_5d}',
                        'portfolio_sentiment → {score, label: BULLISH|NEUTRAL|BEARISH, gauge_position_pct} (breadth + vs-SPY alpha heuristic)',
                        'sector_swings[] → {sector_name, daily_return_pct, allocation_pct} (max 3, weighted by holdings)',
                        'cards[] → {id, type, symbol, title, primary_metric, direction, chip, body, memory_context, actions[]}',
                        'market_session → {status: open|after_hours|weekend, message?}',
                        'continuity_moments[] → {type, title, body, symbol?}',
                        'watch_next[] (sector exposure chips when no sector panel)',
                        'continue_where_you_left_off: null (footer link is client-driven from selected/visible impact mover)',
                    ],
                },
                architecture: 'YourMorningHero → GET /portfolio/morning-brief → holdings perf + snapshot + daily_brief movement + Yahoo batch → ranked impact_movers + portfolio_sentiment + sector_swings → dashboard grid; footer deep-link picks selected or top sorted mover',
            },
            {
                method: 'GET',
                path: '/portfolio/track-record',
                summary: 'Decision-ledger track record window',
                uiSurfaces: ['Your Morning'],
                uiComponent: 'YourMorningHero.jsx',
                backendFile: 'backend/portfolio_track_record.py · backend/routers/portfolio.py',
                auth: 'required',
                request: {
                    query: [{ name: 'window_days', type: 'int', required: false, example: '30', description: '7–90' }],
                },
                response: {
                    type: 'object',
                    fields: [
                        'window_days, observations_logged, graded_count',
                        'directionally_right, wrong, neutral, ungraded, headline',
                        'recent[] → {decision_type, symbol, verdict, outcome, created_at}',
                    ],
                },
                architecture: 'More context panel → GET /portfolio/track-record → decision_events + outcome_observations aggregation',
            },
            {
                method: 'GET',
                path: '/portfolio/timeline',
                summary: 'Portfolio memory timeline events',
                uiSurfaces: ['Your Morning', 'Paper Portfolio'],
                uiComponent: 'PortfolioTimeline.jsx',
                backendFile: 'backend/portfolio_timeline.py · backend/routers/portfolio.py',
                auth: 'required',
                request: {
                    query: [{ name: 'limit', type: 'int', required: false, example: '20' }],
                },
                response: {
                    type: 'object',
                    fields: ['items[] → {type, timestamp, summary, metadata}'],
                },
                architecture: 'Timeline component → chronological portfolio events + reaction memory',
            },
            {
                method: 'POST',
                path: '/portfolio/user-actions/log',
                summary: 'Log implicit behavioural signals (clicks, page opens, chat)',
                uiSurfaces: ['Your Morning', 'Global'],
                uiComponent: 'YourMorningHero.jsx · AnalysisContext.jsx · App.jsx',
                backendFile: 'backend/portfolio_memory.py · backend/routers/portfolio.py',
                auth: 'required',
                request: {
                    body: {
                        type: 'UserActionLogRequest',
                        fields: [
                            'action_type: string (required) — e.g. page_open, ticker_click, brief_card_click, chat_question',
                            'symbol?: string',
                            'page?: string — e.g. your_morning',
                            'entity_type?, entity_id?, metadata?: object',
                        ],
                    },
                },
                response: {
                    type: 'object',
                    fields: ['ok: bool, action_id?'],
                },
                architecture: 'UI fire-and-forget POST → user_actions table + preference signal dual-write → ranks morning-brief cards via user_interest_score',
            },
            {
                method: 'GET',
                path: '/portfolio/news',
                summary: 'News feed for portfolio tickers',
                uiSurfaces: ['Paper Portfolio'],
                uiComponent: 'PaperPortfolioUI.jsx',
                backendFile: 'backend/routers/portfolio_news.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'tickers', type: 'string', required: true, description: 'Comma-separated' }],
                },
                response: {
                    type: 'object',
                    fields: ['items[] → {ticker, title, publisher, link, published_at, sentiment, impact}, cached'],
                },
                architecture: 'News panel → headlines with sentiment badges per holding',
            },
            {
                method: 'GET',
                path: '/preferences',
                summary: 'User preferences and signal counts',
                uiSurfaces: ['Paper Portfolio'],
                uiComponent: 'PaperPortfolioUI.jsx',
                backendFile: 'backend/routers/preferences.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['authenticated, preferences, signal_counts'],
                },
                architecture: 'Settings load → risk tolerance, notification prefs, etc.',
            },
            {
                method: 'GET',
                path: '/progress',
                summary: 'XP level and badge progress',
                uiSurfaces: ['Global XP bar'],
                uiComponent: 'components/XPBar.jsx',
                backendFile: 'backend/routers/progress.py',
                auth: 'required',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['xp, level, badges[], streak, next_level_xp'],
                },
                architecture: 'XPBar → sidebar gamification progress indicator',
            },
        ],
    },
    {
        tag: 'Chat & Assistant',
        description: 'RAG-backed chat with streaming evidence contract.',
        endpoints: [
            {
                method: 'GET',
                path: '/chat/bootstrap',
                summary: 'L1 market snapshot for chat prefetch',
                uiSurfaces: ['Chat', 'Global Assistant'],
                uiComponent: 'App.jsx · ChatUI.jsx · AppAssistantPanel.jsx',
                backendFile: 'backend/routers/chat.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['Market snapshot, pipeline status, prefetch blocks'],
                },
                architecture: 'App mount → prefetch bootstrap → warm chat context before first message',
            },
            {
                method: 'GET',
                path: '/chat/user-context',
                summary: 'Authenticated user portfolio block for chat',
                uiSurfaces: ['Chat', 'Global Assistant'],
                uiComponent: 'App.jsx · ChatUI.jsx',
                backendFile: 'backend/routers/chat.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['authenticated, user_id?, context (portfolio summary)'],
                },
                architecture: 'Parallel prefetch with bootstrap → injects holdings into chat system prompt',
            },
            {
                method: 'POST',
                path: '/chat/session',
                summary: 'Create or resume chat session',
                uiSurfaces: ['Chat', 'Global Assistant'],
                uiComponent: 'ChatUI.jsx · AppAssistantPanel.jsx',
                backendFile: 'backend/routers/chat.py',
                auth: 'optional',
                request: {
                    body: {
                        type: 'object',
                        fields: ['resume_session_id?: string'],
                    },
                },
                response: {
                    type: 'object',
                    fields: ['session_id, assembled_at, expires_at, preview, status'],
                },
                architecture: 'Session start → allocates server-side context assembly job',
            },
            {
                method: 'POST',
                path: '/chat/message',
                summary: 'Send message — returns SSE stream',
                uiSurfaces: ['Chat', 'Global Assistant'],
                uiComponent: 'ChatUI.jsx · AppAssistantPanel.jsx',
                backendFile: 'backend/routers/chat.py',
                auth: 'optional',
                request: {
                    body: {
                        type: 'ChatMessageRequest',
                        fields: ['session_id, message, history[], page_context?'],
                    },
                },
                response: {
                    type: 'SSE stream',
                    fields: [
                        'Events: meta, token (streaming text), quote_card, evidence_contract, error',
                    ],
                },
                architecture: 'POST /chat/message → SSE reader → token stream + evidence contract panel + quote cards',
            },
            {
                method: 'POST',
                path: '/chat/evidence-export',
                summary: 'Export session evidence as markdown',
                uiSurfaces: ['Chat'],
                uiComponent: 'ChatUI.jsx',
                backendFile: 'backend/routers/chat.py',
                auth: 'optional',
                request: {
                    body: { type: 'object', fields: ['session_id: string'] },
                },
                response: {
                    type: 'object',
                    fields: ['markdown, generated_at_utc, schema_version'],
                },
                architecture: 'Export button → downloadable evidence memo for audit trail',
            },
        ],
    },
    {
        tag: 'Learning & Academy',
        description: 'Curriculum, daily challenge, and lesson generation.',
        endpoints: [
            {
                method: 'GET',
                path: '/challenge/today',
                summary: 'Daily investor challenge question',
                uiSurfaces: ['Investor Academy'],
                uiComponent: 'AcademyUI.jsx',
                backendFile: 'backend/routers/challenge.py',
                auth: 'required',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['question, options[], challenge_id, xp_reward'],
                },
                architecture: 'Academy challenge tab → daily quiz card',
            },
            {
                method: 'GET',
                path: '/learning/curriculum',
                summary: 'Full learning path modules',
                uiSurfaces: ['Investor Academy'],
                uiComponent: 'AcademyUI.jsx',
                backendFile: 'backend/routers/learning.py',
                auth: 'required',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['modules[] → {id, title, progress, lessons_count}'],
                },
                architecture: 'Curriculum grid → module cards with completion state',
            },
            {
                method: 'GET',
                path: '/learning/module/{id}',
                summary: 'Single module with lessons',
                uiSurfaces: ['Investor Academy'],
                uiComponent: 'AcademyUI.jsx',
                backendFile: 'backend/routers/learning.py',
                auth: 'required',
                request: {
                    path: [{ name: 'id', type: 'string', required: true }],
                },
                response: {
                    type: 'object',
                    fields: ['module metadata, lessons[], completion status'],
                },
                architecture: 'Module detail view → lesson list',
            },
            {
                method: 'POST',
                path: '/academy/lesson/{id}/generate',
                summary: 'Generate AI lesson video/content',
                uiSurfaces: ['Investor Academy'],
                uiComponent: 'AcademyUI.jsx',
                backendFile: 'backend/routers/academy.py',
                auth: 'required',
                request: {
                    path: [{ name: 'id', type: 'string', required: true }],
                },
                response: {
                    type: 'object',
                    fields: ['lesson content, video_url?, generation status'],
                },
                architecture: 'Generate button → LLM + TTS pipeline → VideoPlayer component',
            },
        ],
    },
    {
        tag: 'Notifications',
        description: 'Real-time macro alerts via SSE.',
        endpoints: [
            {
                method: 'SSE',
                path: '/notifications/stream',
                summary: 'Server-sent events — live macro alert push',
                uiSurfaces: ['Global notification bell'],
                uiComponent: 'NotificationBell.jsx',
                backendFile: 'backend/routers/notifications.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'text/event-stream',
                    fields: ['MacroAlert events as they are scanned (60s loop)'],
                },
                architecture: 'NotificationBell → EventSource → real-time alert toasts + unread count',
            },
            {
                method: 'GET',
                path: '/notifications/history',
                summary: 'Alert history with unread count',
                uiSurfaces: ['Global notification bell'],
                uiComponent: 'NotificationBell.jsx',
                backendFile: 'backend/routers/notifications.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'AlertResponse',
                    fields: ['alerts: MacroAlert[], total, unread'],
                },
                architecture: 'Bell dropdown → historical alerts list',
            },
        ],
    },
    {
        tag: 'Developer & Eval',
        description: 'Debug, LLM audit, and benchmark dashboards.',
        endpoints: [
            {
                method: 'GET',
                path: '/llm/calls',
                summary: 'LLM call audit log',
                uiSurfaces: ['LLM Call Log'],
                uiComponent: 'LlmCallsUI.jsx',
                backendFile: 'backend/routers/debug.py',
                auth: 'optional',
                request: {
                    query: [{ name: 'limit', type: 'int', required: false, example: '100' }],
                },
                response: {
                    type: 'array',
                    fields: ['{query_brief, llm_used, cost, time_taken, tokens, timestamp, ...}[]'],
                },
                architecture: 'LlmCallsUI → latency/cost table for every LLM invocation',
            },
            {
                method: 'GET',
                path: '/learning-health',
                summary: 'Learning pipeline health metrics',
                uiSurfaces: ['Developer Trace'],
                uiComponent: 'ObserverUI.jsx',
                backendFile: 'backend/routers/debug.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['Pipeline status, ingest counts, last run timestamps'],
                },
                architecture: 'Observer dashboard → knowledge pipeline health cards',
            },
            {
                method: 'GET',
                path: '/admin/swarm-score/summary',
                summary: 'SwarmScore eval summary',
                uiSurfaces: ['SwarmScore Eval'],
                uiComponent: 'SwarmScoreUI.jsx',
                backendFile: 'backend/routers/swarm_eval.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['Latest eval run stats, pass/fail counts'],
                },
                architecture: 'SwarmScore dashboard header metrics',
            },
            {
                method: 'GET',
                path: '/admin/ubds/summary',
                summary: 'UBDS benchmark summary',
                uiSurfaces: ['UBDS Benchmark'],
                uiComponent: 'UbdsBenchmarkUI.jsx',
                backendFile: 'backend/routers/ubds_eval.py',
                auth: 'optional',
                request: { query: [] },
                response: {
                    type: 'object',
                    fields: ['Benchmark run stats, model comparison'],
                },
                architecture: 'UBDS dashboard header metrics',
            },
        ],
    },
]

function MethodBadge({ method }) {
    const style = METHOD_STYLES[method] || METHOD_STYLES.GET
    return (
        <span style={{
            display: 'inline-block',
            padding: '3px 8px',
            borderRadius: 6,
            fontSize: 11,
            fontWeight: 800,
            fontFamily: 'monospace',
            letterSpacing: 0.5,
            background: style.bg,
            color: style.color,
            border: `1px solid ${style.border}`,
            minWidth: 52,
            textAlign: 'center',
        }}>
            {method}
        </span>
    )
}

function ParamTable({ title, params }) {
    if (!params?.length) return null
    return (
        <div style={{ marginBottom: 14 }}>
            <div style={sectionLabelStyle}>{title}</div>
            <div style={{
                borderRadius: 8,
                border: '1px solid rgba(255,255,255,0.06)',
                overflow: 'hidden',
            }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                        <tr style={{ background: 'rgba(255,255,255,0.03)' }}>
                            {['Name', 'Type', 'Required', 'Description'].map(h => (
                                <th key={h} style={{
                                    textAlign: 'left', padding: '8px 12px',
                                    color: '#64748b', fontWeight: 700, fontSize: 10,
                                    letterSpacing: 0.8, textTransform: 'uppercase',
                                }}>{h}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {params.map(p => (
                            <tr key={p.name} style={{ borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                                <td style={{ padding: '8px 12px', fontFamily: 'monospace', color: '#a5b4fc' }}>{p.name}</td>
                                <td style={{ padding: '8px 12px', color: '#94a3b8' }}>{p.type}</td>
                                <td style={{ padding: '8px 12px', color: p.required ? '#f87171' : '#64748b' }}>
                                    {p.required ? 'yes' : 'no'}
                                </td>
                                <td style={{ padding: '8px 12px', color: '#cbd5e1', lineHeight: 1.5 }}>
                                    {p.description || p.example ? (
                                        <>
                                            {p.description}
                                            {p.example && (
                                                <span style={{ color: '#64748b' }}>
                                                    {p.description ? ' · ' : ''}e.g. <code style={{ color: '#a5b4fc' }}>{p.example}</code>
                                                </span>
                                            )}
                                        </>
                                    ) : '—'}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    )
}

const sectionLabelStyle = {
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: 1.2,
    color: '#64748b',
    marginBottom: 8,
    textTransform: 'uppercase',
}

function EndpointCard({ endpoint, expanded, onToggle }) {
    const [copied, setCopied] = useState(false)
    const fullUrl = `${API_BASE_URL}${endpoint.path}`

    const copyPath = () => {
        navigator.clipboard.writeText(fullUrl).then(() => {
            setCopied(true)
            setTimeout(() => setCopied(false), 1500)
        })
    }

    return (
        <div style={{
            borderRadius: 12,
            border: `1px solid ${expanded ? 'rgba(129,140,248,0.35)' : 'rgba(255,255,255,0.06)'}`,
            background: expanded ? 'rgba(99,102,241,0.06)' : 'rgba(15,23,42,0.5)',
            marginBottom: 10,
            overflow: 'hidden',
            transition: 'border-color 0.2s, background 0.2s',
        }}>
            <button
                type="button"
                onClick={onToggle}
                style={{
                    width: '100%',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 12,
                    padding: '14px 16px',
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    textAlign: 'left',
                }}
            >
                <MethodBadge method={endpoint.method} />
                <code style={{
                    flex: 1,
                    fontSize: 13,
                    fontWeight: 600,
                    color: '#e2e8f0',
                    fontFamily: 'ui-monospace, monospace',
                    wordBreak: 'break-all',
                }}>
                    {endpoint.path}
                </code>
                <span style={{ fontSize: 12, color: '#94a3b8', flexShrink: 0, maxWidth: 280, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {endpoint.summary}
                </span>
                {expanded
                    ? <ChevronDown size={16} color="#64748b" />
                    : <ChevronRight size={16} color="#64748b" />}
            </button>

            {expanded && (
                <div style={{ padding: '0 16px 18px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                    <p style={{ color: '#cbd5e1', fontSize: 13, lineHeight: 1.6, margin: '14px 0 16px' }}>
                        {endpoint.summary}
                    </p>

                    {/* Full URL */}
                    <div style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '10px 12px', borderRadius: 8,
                        background: 'rgba(0,0,0,0.25)',
                        border: '1px solid rgba(255,255,255,0.06)',
                        marginBottom: 16,
                    }}>
                        <Server size={14} color="#64748b" />
                        <code style={{ flex: 1, fontSize: 11, color: '#94a3b8', wordBreak: 'break-all' }}>{fullUrl}</code>
                        <button
                            type="button"
                            onClick={copyPath}
                            title="Copy full URL"
                            style={{
                                background: 'rgba(255,255,255,0.05)', border: '1px solid rgba(255,255,255,0.08)',
                                borderRadius: 6, padding: '4px 8px', cursor: 'pointer', color: '#94a3b8',
                                display: 'flex', alignItems: 'center', gap: 4, fontSize: 11,
                            }}
                        >
                            {copied ? <Check size={12} color="#34d399" /> : <Copy size={12} />}
                            {copied ? 'Copied' : 'Copy'}
                        </button>
                    </div>

                    {/* Meta chips */}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 16 }}>
                        <MetaChip label="Auth" value={endpoint.auth} />
                        <MetaChip label="Backend" value={endpoint.backendFile} mono />
                        {endpoint.uiSurfaces.map(s => (
                            <MetaChip key={s} label="UI" value={s} accent />
                        ))}
                    </div>

                    {/* Architecture flow */}
                    <div style={{
                        padding: '12px 14px',
                        borderRadius: 10,
                        background: 'rgba(124,58,237,0.08)',
                        border: '1px solid rgba(167,139,250,0.2)',
                        marginBottom: 16,
                    }}>
                        <div style={{ ...sectionLabelStyle, color: '#a78bfa', marginBottom: 6 }}>Request → Response flow</div>
                        <p style={{ margin: 0, fontSize: 12, color: '#cbd5e1', lineHeight: 1.65, fontFamily: 'ui-monospace, monospace' }}>
                            {endpoint.architecture}
                        </p>
                    </div>

                    {/* UI consumer */}
                    <div style={{ marginBottom: 14 }}>
                        <div style={sectionLabelStyle}>UI consumer</div>
                        <code style={{ fontSize: 12, color: '#a5b4fc' }}>{endpoint.uiComponent}</code>
                    </div>

                    {/* Request */}
                    <div style={{ marginBottom: 14 }}>
                        <div style={{ ...sectionLabelStyle, display: 'flex', alignItems: 'center', gap: 6 }}>
                            <ArrowRight size={12} /> Request
                        </div>
                        <ParamTable title="Path parameters" params={endpoint.request.path} />
                        <ParamTable title="Query parameters" params={endpoint.request.query} />
                        {endpoint.request.body && (
                            <div>
                                <div style={{ ...sectionLabelStyle, marginTop: 8 }}>Request body — {endpoint.request.body.type}</div>
                                <ul style={{ margin: 0, paddingLeft: 18, color: '#94a3b8', fontSize: 12, lineHeight: 1.8 }}>
                                    {endpoint.request.body.fields.map(f => (
                                        <li key={f}><code style={{ color: '#a5b4fc' }}>{f}</code></li>
                                    ))}
                                </ul>
                            </div>
                        )}
                        {!endpoint.request.path?.length && !endpoint.request.query?.length && !endpoint.request.body && (
                            <span style={{ fontSize: 12, color: '#64748b' }}>No parameters</span>
                        )}
                    </div>

                    {/* Response */}
                    <div>
                        <div style={{ ...sectionLabelStyle, display: 'flex', alignItems: 'center', gap: 6 }}>
                            <Layers size={12} /> Response — {endpoint.response.type}
                        </div>
                        <ul style={{ margin: 0, paddingLeft: 18, color: '#94a3b8', fontSize: 12, lineHeight: 1.8 }}>
                            {endpoint.response.fields.map(f => (
                                <li key={f}><code style={{ color: '#34d399' }}>{f}</code></li>
                            ))}
                        </ul>
                    </div>
                </div>
            )}
        </div>
    )
}

function MetaChip({ label, value, mono, accent }) {
    return (
        <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            padding: '4px 10px', borderRadius: 20,
            fontSize: 11,
            background: accent ? 'rgba(16,185,129,0.1)' : 'rgba(255,255,255,0.04)',
            border: `1px solid ${accent ? 'rgba(52,211,153,0.25)' : 'rgba(255,255,255,0.08)'}`,
            color: accent ? '#34d399' : '#94a3b8',
        }}>
            <span style={{ fontWeight: 700, opacity: 0.7 }}>{label}:</span>
            <span style={{ fontFamily: mono ? 'monospace' : 'inherit', fontSize: mono ? 10 : 11 }}>{value}</span>
        </span>
    )
}

export default function ApiCatalogUI() {
    const [search, setSearch] = useState('')
    const [activeTag, setActiveTag] = useState('all')
    const [expandedId, setExpandedId] = useState(null)
    const [methodFilter, setMethodFilter] = useState('all')

    const allEndpoints = useMemo(() =>
        API_CATALOG.flatMap(group =>
            group.endpoints.map(ep => ({ ...ep, tag: group.tag, groupDescription: group.description }))
        ),
    [])

    const filteredGroups = useMemo(() => {
        const q = search.trim().toLowerCase()
        return API_CATALOG.map(group => {
            if (activeTag !== 'all' && group.tag !== activeTag) return null
            const endpoints = group.endpoints.filter(ep => {
                if (methodFilter !== 'all' && ep.method !== methodFilter) return false
                if (!q) return true
                const hay = [
                    ep.path, ep.summary, ep.uiComponent, ep.backendFile,
                    ep.architecture, ep.response.type,
                    ...ep.uiSurfaces,
                    ...(ep.response.fields || []),
                ].join(' ').toLowerCase()
                return hay.includes(q)
            })
            if (!endpoints.length) return null
            return { ...group, endpoints }
        }).filter(Boolean)
    }, [search, activeTag, methodFilter])

    const totalShown = filteredGroups.reduce((n, g) => n + g.endpoints.length, 0)

    const toggleEndpoint = (id) => {
        setExpandedId(prev => (prev === id ? null : id))
    }

    return (
        <div style={{ padding: '24px 28px', maxWidth: 1100, margin: '0 auto' }}>
            {/* Header */}
            <div style={{ marginBottom: 24 }}>
                <h2 style={{ color: '#f1f5f9', fontSize: 22, fontWeight: 800, margin: '0 0 6px', display: 'flex', alignItems: 'center', gap: 10 }}>
                    <BookOpen size={24} color="#818cf8" />
                    API Catalog
                </h2>
                <p style={{ color: '#64748b', fontSize: 13, margin: '0 0 14px', lineHeight: 1.6 }}>
                    Swagger-style reference for every backend endpoint whose response is visualized in the TradeTalk UI.
                    Each entry documents request parameters, response shape, and the data flow from API to component.
                </p>
                <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center',
                    padding: '12px 14px', borderRadius: 10,
                    background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(129,140,248,0.25)',
                }}>
                    <span style={{ fontSize: 12, color: '#94a3b8' }}>
                        Live OpenAPI: <code style={{ color: '#a5b4fc' }}>{API_BASE_URL}/docs</code>
                    </span>
                    <a
                        href={`${API_BASE_URL}/docs`}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                            display: 'inline-flex', alignItems: 'center', gap: 6,
                            padding: '6px 12px', borderRadius: 8,
                            background: 'rgba(59,130,246,0.15)', border: '1px solid rgba(96,165,250,0.3)',
                            color: '#60a5fa', fontSize: 12, fontWeight: 600, textDecoration: 'none',
                        }}
                    >
                        Open Swagger UI <ExternalLink size={13} />
                    </a>
                    <a
                        href={`${API_BASE_URL}/openapi.json`}
                        target="_blank"
                        rel="noopener noreferrer"
                        style={{
                            display: 'inline-flex', alignItems: 'center', gap: 6,
                            padding: '6px 12px', borderRadius: 8,
                            background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
                            color: '#94a3b8', fontSize: 12, fontWeight: 600, textDecoration: 'none',
                        }}
                    >
                        openapi.json <ExternalLink size={13} />
                    </a>
                </div>
            </div>

            {/* Filters */}
            <div style={{
                display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 20, alignItems: 'center',
            }}>
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '8px 12px', borderRadius: 10,
                    background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(255,255,255,0.08)',
                    flex: '1 1 220px', minWidth: 200,
                }}>
                    <Search size={16} color="#64748b" />
                    <input
                        type="search"
                        placeholder="Search path, component, response field…"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        style={{
                            flex: 1, background: 'none', border: 'none', outline: 'none',
                            color: '#e2e8f0', fontSize: 13,
                        }}
                    />
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <Filter size={14} color="#64748b" />
                    <select
                        value={methodFilter}
                        onChange={e => setMethodFilter(e.target.value)}
                        style={{
                            padding: '8px 12px', borderRadius: 8,
                            background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(255,255,255,0.08)',
                            color: '#cbd5e1', fontSize: 12, cursor: 'pointer',
                        }}
                    >
                        <option value="all">All methods</option>
                        {['GET', 'POST', 'PUT', 'SSE'].map(m => (
                            <option key={m} value={m}>{m}</option>
                        ))}
                    </select>
                </div>

                <span style={{ fontSize: 12, color: '#64748b' }}>
                    {totalShown} of {allEndpoints.length} endpoints
                </span>
            </div>

            {/* Tag pills */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 24 }}>
                <TagPill active={activeTag === 'all'} onClick={() => setActiveTag('all')}>All</TagPill>
                {API_CATALOG.map(g => (
                    <TagPill key={g.tag} active={activeTag === g.tag} onClick={() => setActiveTag(g.tag)}>
                        {g.tag}
                    </TagPill>
                ))}
            </div>

            {/* Endpoint groups */}
            {filteredGroups.length === 0 ? (
                <div style={{
                    padding: 40, textAlign: 'center', color: '#64748b',
                    borderRadius: 12, border: '1px dashed rgba(255,255,255,0.1)',
                }}>
                    No endpoints match your filters.
                </div>
            ) : (
                filteredGroups.map(group => (
                    <section key={group.tag} style={{ marginBottom: 32 }}>
                        <div style={{ marginBottom: 14 }}>
                            <h3 style={{ color: '#e2e8f0', fontSize: 16, fontWeight: 700, margin: '0 0 4px' }}>
                                {group.tag}
                            </h3>
                            <p style={{ color: '#64748b', fontSize: 12, margin: 0 }}>{group.description}</p>
                        </div>
                        {group.endpoints.map(ep => {
                            const id = `${group.tag}::${ep.method}::${ep.path}`
                            return (
                                <EndpointCard
                                    key={id}
                                    endpoint={ep}
                                    expanded={expandedId === id}
                                    onToggle={() => toggleEndpoint(id)}
                                />
                            )
                        })}
                    </section>
                ))
            )}
        </div>
    )
}

function TagPill({ active, onClick, children }) {
    return (
        <button
            type="button"
            onClick={onClick}
            style={{
                padding: '6px 14px', borderRadius: 20, border: 'none', cursor: 'pointer',
                fontSize: 12, fontWeight: 600,
                background: active ? 'rgba(124,58,237,0.3)' : 'rgba(255,255,255,0.04)',
                color: active ? '#e9d5ff' : '#94a3b8',
                borderWidth: 1,
                borderStyle: 'solid',
                borderColor: active ? 'rgba(167,139,250,0.4)' : 'rgba(255,255,255,0.06)',
                transition: 'all 0.2s',
            }}
        >
            {children}
        </button>
    )
}
