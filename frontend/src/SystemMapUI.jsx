import { useState, useEffect, useRef } from 'react'
import {
    User, Monitor, Server, Bot, Brain, Database, Cloud,
    Clock, X, ChevronRight, Zap, RefreshCw, Github, BookOpen, Layers,
} from 'lucide-react'

const LAYER_COLORS = {
    customer: { bg: 'rgba(16,185,129,0.12)', border: '#10b981', text: '#34d399', glow: 'rgba(16,185,129,0.35)' },
    frontend: { bg: 'rgba(59,130,246,0.12)', border: '#3b82f6', text: '#60a5fa', glow: 'rgba(59,130,246,0.35)' },
    api:      { bg: 'rgba(99,102,241,0.12)', border: '#6366f1', text: '#818cf8', glow: 'rgba(99,102,241,0.35)' },
    agents:   { bg: 'rgba(124,58,237,0.12)',  border: '#7c3aed', text: '#a78bfa', glow: 'rgba(124,58,237,0.35)' },
    intel:    { bg: 'rgba(139,92,246,0.12)',  border: '#8b5cf6', text: '#c4b5fd', glow: 'rgba(139,92,246,0.35)' },
    data:     { bg: 'rgba(245,158,11,0.12)',  border: '#f59e0b', text: '#fbbf24', glow: 'rgba(245,158,11,0.35)' },
    infra:    { bg: 'rgba(100,116,139,0.12)', border: '#64748b', text: '#94a3b8', glow: 'rgba(100,116,139,0.35)' },
}

const LAYERS = [
    {
        id: 'customer', label: 'Customer Input', icon: User, color: 'customer',
        nodes: [
            { id: 'user_input', name: 'User enters ticker or strategy', file: null, desc: 'The customer types a stock ticker (e.g. AAPL) or a plain-English strategy into the UI.', inputs: ['Keyboard input'], outputs: ['Ticker string', 'Strategy text'] },
        ]
    },
    {
        id: 'frontend', label: 'Frontend (React + Vite)', icon: Monitor, color: 'frontend',
        nodes: [
            { id: 'consumer_ui', name: 'ConsumerUI', file: 'frontend/src/ConsumerUI.jsx', desc: 'Valuation Dashboard — runs Swarm analysis and shows factor verdicts + investor metrics.', inputs: ['Ticker'], outputs: ['GET /trace', 'GET /metrics/{ticker}'] },
            { id: 'debate_ui', name: 'DebateUI', file: 'frontend/src/DebateUI.jsx', desc: 'AI Debate view — 5 agent argument cards + moderator verdict banner.', inputs: ['Ticker'], outputs: ['GET /debate'] },
            { id: 'backtest_ui', name: 'BacktestUI', file: 'frontend/src/BacktestUI.jsx', desc: 'Strategy Lab — takes plain-English strategy, returns backtest chart + AI explanation.', inputs: ['Strategy text'], outputs: ['POST /backtest'] },
            { id: 'macro_ui', name: 'MacroUI', file: 'frontend/src/MacroUI.jsx', desc: 'Global Macro dashboard — VIX, credit stress, sector rotation, capital flows.', inputs: ['(auto)'], outputs: ['GET /macro'] },
            { id: 'notif_bell', name: 'NotificationBell', file: 'frontend/src/NotificationBell.jsx', desc: 'Real-time SSE alert bell — receives macro news alerts as they scan.', inputs: ['SSE stream'], outputs: ['GET /notifications/stream'] },
            { id: 'chat_ui', name: 'ChatUI', file: 'frontend/src/ChatUI.jsx', desc: 'Chat with context — bootstrap + user context; RAG-backed recall.', inputs: ['User messages'], outputs: ['GET /chat/bootstrap', 'POST /chat/*'] },
        ]
    },
    {
        id: 'api', label: 'FastAPI Backend', icon: Server, color: 'api',
        nodes: [
            { id: 'ep_trace', name: '/trace (swarm)', file: 'backend/routers/analysis.py', desc: 'Swarm endpoint — runs 4 AgentPairs concurrently, returns SwarmConsensus. Not the same as GET /notifications/trace.', inputs: ['ticker', 'credit_stress?'], outputs: ['SwarmConsensus JSON'] },
            { id: 'ep_debate', name: '/debate', file: 'backend/routers/analysis.py', desc: 'Debate endpoint — runs 5 LLM agents + moderator, returns DebateResult.', inputs: ['ticker'], outputs: ['DebateResult JSON'] },
            { id: 'ep_backtest', name: '/backtest', file: 'backend/routers/backtest.py', desc: 'Backtest endpoint — parses strategy via LLM, simulates trades, returns results.', inputs: ['strategy text', 'start/end dates'], outputs: ['BacktestResult JSON'] },
            { id: 'ep_macro', name: '/macro', file: 'backend/routers/macro.py', desc: 'Macro endpoint — returns global macro indicators (VIX, sectors, spending).', inputs: ['(none)'], outputs: ['MacroDataResponse JSON'] },
            { id: 'ep_notif', name: '/notifications/*', file: 'backend/routers/notifications.py', desc: 'SSE stream, history, scan. GET /notifications/trace returns the last background news-scan trace (cached), not swarm output.', inputs: ['(auto 60s loop)'], outputs: ['SSE events', 'Alert history', 'GET /notifications/trace'] },
            { id: 'ep_knowledge', name: '/knowledge/*', file: 'backend/routers/knowledge.py', desc: 'Stats, export, pipeline triggers. POST /knowledge/pipeline-run and sp500-ingest may require PIPELINE_CRON_SECRET.', inputs: ['Cron or admin'], outputs: ['Pipeline status', 'Ingest triggers'] },
        ]
    },
    {
        id: 'agents', label: 'Agent Layer', icon: Bot, color: 'agents',
        nodes: [
            { id: 'swarm_short', name: 'Short Interest Pair', file: 'backend/agents.py', desc: 'Analyst checks SIR + days-to-cover from yFinance. QA rejects bullish signals in bearish macro.', inputs: ['ShortsConnector data', 'MarketState'], outputs: ['FactorResult'], tag: 'swarm' },
            { id: 'swarm_social', name: 'Social Sentiment Pair', file: 'backend/agents.py', desc: 'Counts bullish/bearish keywords in Google News RSS. QA rejects in severe stress.', inputs: ['SocialConnector data', 'MarketState'], outputs: ['FactorResult'], tag: 'swarm' },
            { id: 'swarm_poly', name: 'Polymarket Pair', file: 'backend/agents.py', desc: 'Reads prediction market probabilities from Polymarket Gamma API.', inputs: ['PolymarketConnector data'], outputs: ['FactorResult'], tag: 'swarm' },
            { id: 'swarm_fund', name: 'Fundamentals Pair', file: 'backend/agents.py', desc: 'Checks cash-to-debt ratio. QA requires explicit figures; rejects low ratios in stress.', inputs: ['FundamentalsConnector data', 'MarketState'], outputs: ['FactorResult'], tag: 'swarm' },
            { id: 'debate_bull', name: 'Bull Agent', file: 'backend/debate_agents.py', desc: 'LLM-powered bullish specialist. Queries RAG for price movements + YouTube insights.', inputs: ['Live data', 'RAG context'], outputs: ['DebateArgument'], tag: 'debate' },
            { id: 'debate_bear', name: 'Bear Agent', file: 'backend/debate_agents.py', desc: 'LLM-powered bearish specialist. Queries macro alerts + swarm history.', inputs: ['Live data', 'RAG context'], outputs: ['DebateArgument'], tag: 'debate' },
            { id: 'debate_macro', name: 'Macro Agent', file: 'backend/debate_agents.py', desc: 'LLM-powered macro specialist. Queries macro snapshots + alerts.', inputs: ['Macro state', 'RAG context'], outputs: ['DebateArgument'], tag: 'debate' },
            { id: 'debate_value', name: 'Value Agent', file: 'backend/debate_agents.py', desc: 'LLM-powered value specialist. Queries swarm + debate history.', inputs: ['Live data', 'RAG context'], outputs: ['DebateArgument'], tag: 'debate' },
            { id: 'debate_momentum', name: 'Momentum Agent', file: 'backend/debate_agents.py', desc: 'LLM-powered momentum specialist. Queries price movements + YouTube.', inputs: ['Live data', 'RAG context'], outputs: ['DebateArgument'], tag: 'debate' },
            { id: 'moderator', name: 'Moderator', file: 'backend/debate_agents.py', desc: 'Synthesises all 5 agent arguments into a final verdict via LLM.', inputs: ['5 DebateArguments', 'RAG context'], outputs: ['Verdict', 'Summary'], tag: 'debate' },
            { id: 'notif_pipeline', name: 'Notification Pipeline', file: 'backend/notification_agents.py', desc: 'Two-stage pipeline: NotificationAgent (scorer) + AnalystAgent (trust + sector).', inputs: ['Google News headlines'], outputs: ['MacroAlerts'] },
        ]
    },
    {
        id: 'intel', label: 'Intelligence Layer', icon: Brain, color: 'intel',
        nodes: [
            { id: 'llm_client', name: 'LLMClient', file: 'backend/llm_client.py', desc: 'OpenRouter API (Nemotron Super). Role-based model tiers (heavy/light). Fallback templates.', inputs: ['Role + prompt + context'], outputs: ['LLM JSON response'] },
            { id: 'knowledge_store', name: 'KnowledgeStore (RAG)', file: 'backend/knowledge_store.py', desc: 'VECTOR_BACKEND selects storage: Supabase pgvector (default on Render in render.yaml) with OpenRouter embeddings when configured; local Chroma on disk; or VECTOR_BACKEND=hf snapshot from Hugging Face. Multiple named collections — see COLLECTIONS in code. Reflection memory with effectiveness scoring.', inputs: ['Query text + filters'], outputs: ['Relevant documents'] },
            { id: 'guardrails', name: 'Policy Guardrails', file: 'backend/agent_policy_guardrails.py', desc: 'Per-workload capability sandboxing. Blocks cross-workload abuse, validates outbound hosts, redacts secrets.', inputs: ['Workload + capability'], outputs: ['Allow / Deny'] },
        ]
    },
    {
        id: 'data', label: 'Data Sources', icon: Database, color: 'data',
        nodes: [
            { id: 'ds_yfinance', name: 'yFinance', file: 'backend/connectors/', desc: 'Stock data: SIR, days-to-cover, VIX, sector ETFs, cash/debt, historical prices, PE, ROE.', inputs: ['Ticker symbol'], outputs: ['Market data'] },
            { id: 'ds_gnews', name: 'Google News RSS', file: 'backend/connectors/news_scanner.py', desc: '11 macro keyword queries. Deduplicates with SHA256. Feeds notification pipeline.', inputs: ['Search queries'], outputs: ['Headlines'] },
            { id: 'ds_poly', name: 'Polymarket API', file: 'backend/connectors/polymarket.py', desc: 'Gamma API for prediction market events + outcome probabilities.', inputs: ['Ticker keyword'], outputs: ['Events + probabilities'] },
            { id: 'ds_fred', name: 'FRED', file: 'backend/connectors/fred.py', desc: 'Federal Reserve data: Fed Funds Rate, CPI, 10Y Treasury, Unemployment, M2.', inputs: ['(public CSV)'], outputs: ['Macro indicators'] },
            { id: 'ds_youtube', name: 'YouTube API', file: 'backend/connectors/youtube.py', desc: 'Finance channel videos: CNBC, Bloomberg, Graham Stephan, etc.', inputs: ['Channel IDs'], outputs: ['Video titles + metadata'] },
        ]
    },
    {
        id: 'infra', label: 'Infrastructure', icon: Cloud, color: 'infra',
        nodes: [
            { id: 'infra_render', name: 'Render.com', file: 'render.yaml', desc: 'Backend hosting. Python runtime, uvicorn. Ephemeral disk — use Supabase for durable vectors in production.', inputs: ['Git push', 'Env vars'], outputs: ['Running FastAPI server'] },
            { id: 'infra_vercel', name: 'Vercel', file: 'frontend/vercel.json', desc: 'Frontend hosting. Static Vite build. SPA routing. Browser calls API via VITE_API_BASE_URL.', inputs: ['Git push'], outputs: ['Served React app'] },
            { id: 'infra_supabase', name: 'Supabase pgvector', file: 'backend/vector_backends.py', desc: 'Default VECTOR_BACKEND in render.yaml. Table vector_memory + match_vector_memory RPC. Embeddings via OpenRouter when OPENROUTER_EMBEDDING_MODEL is set — not Hugging Face.', inputs: ['Embeddings + text'], outputs: ['Nearest neighbors'] },
            { id: 'infra_chroma', name: 'ChromaDB (local)', file: 'backend/knowledge_store.py', desc: 'VECTOR_BACKEND=chroma with CHROMA_PATH (e.g. ./chroma_db). On Render, optional HF_TOKEN uses Hugging Face Inference API for embeddings via InferenceClient.', inputs: ['Documents'], outputs: ['Embeddings + search'] },
            { id: 'infra_hf', name: 'Hugging Face Hub', file: 'backend/vector_backends.py', desc: 'Optional: remote embeddings for Chroma on Render; VECTOR_BACKEND=hf dataset snapshot (rag_summaries); backtest/data-lake Parquet datasets; ETL uploads from CI.', inputs: ['HF_TOKEN', 'HF_DATASET_*'], outputs: ['Embeddings', 'Parquet', 'JSON snapshots'] },
            { id: 'infra_gha', name: 'GitHub Actions / cron', file: '.github/workflows/', desc: 'External schedules: wake GET /docs (Render free tier), POST /knowledge/pipeline-run with PIPELINE_CRON_SECRET. See docs/CRON.md.', inputs: ['Repository secrets'], outputs: ['HTTP triggers to Render'] },
            { id: 'infra_sqlite', name: 'SQLite', file: 'backend/alert_store.py', desc: 'alerts.db (macro alerts) + progress.db (users, XP, badges, portfolio, challenges).', inputs: ['Insert/query'], outputs: ['Rows'] },
        ]
    },
]

const FLOW_TRACES = {
    swarm:   ['user_input', 'consumer_ui', 'ep_trace', 'swarm_short', 'swarm_social', 'swarm_poly', 'swarm_fund', 'llm_client', 'knowledge_store', 'guardrails', 'ds_yfinance', 'ds_gnews', 'ds_poly'],
    debate:  ['user_input', 'debate_ui', 'ep_debate', 'debate_bull', 'debate_bear', 'debate_macro', 'debate_value', 'debate_momentum', 'moderator', 'llm_client', 'knowledge_store', 'guardrails', 'ds_yfinance'],
    backtest:['user_input', 'backtest_ui', 'ep_backtest', 'llm_client', 'knowledge_store', 'guardrails', 'ds_yfinance'],
}

const BACKGROUND_TASKS = [
    { id: 'news_loop', name: 'News Scan Loop', interval: '60s', icon: RefreshCw, desc: 'Scans Google News RSS every 60 seconds for macro alerts. Feeds NotificationPipeline + SSE.' },
    { id: 'daily_pipeline', name: 'Daily Pipeline', interval: '00:00 UTC', icon: Clock, desc: 'APScheduler job: ingests top movers, FRED macro, YouTube videos into KnowledgeStore.' },
    { id: 'external_cron', name: 'External cron → Render', interval: 'scheduled', icon: Github, desc: 'Outside the process: e.g. GitHub Actions hits GET /docs to wake the service, POST /knowledge/pipeline-run for daily ingest (Bearer PIPELINE_CRON_SECRET). Render sleeps without traffic — see docs/CRON.md.' },
]

function NodeCard({ node, layerColor, isActive, isDimmed, onClick }) {
    const colors = LAYER_COLORS[layerColor]
    return (
        <button
            onClick={() => onClick(node)}
            style={{
                position: 'relative',
                background: colors.bg,
                border: `1px solid ${isActive ? colors.border : 'rgba(255,255,255,0.06)'}`,
                borderRadius: 10,
                padding: '10px 14px',
                color: colors.text,
                fontSize: 12,
                fontWeight: 600,
                cursor: 'pointer',
                backdropFilter: 'blur(8px)',
                transition: 'all 0.3s ease',
                opacity: isDimmed ? 0.25 : 1,
                boxShadow: isActive ? `0 0 20px ${colors.glow}` : 'none',
                textAlign: 'left',
                minWidth: 0,
                flex: '0 1 auto',
            }}
        >
            <span style={{ display: 'block', lineHeight: 1.3 }}>{node.name}</span>
            {node.tag && (
                <span style={{
                    display: 'inline-block', marginTop: 4, fontSize: 9, fontWeight: 700,
                    letterSpacing: 1, textTransform: 'uppercase',
                    color: node.tag === 'swarm' ? '#a78bfa' : '#c4b5fd',
                    opacity: 0.7,
                }}>{node.tag === 'swarm' ? 'Analyst-QA Loop' : 'LLM Agent'}</span>
            )}
        </button>
    )
}

function DetailPanel({ node, layerColor, onClose }) {
    if (!node) return null
    const colors = LAYER_COLORS[layerColor]
    return (
        <div style={{
            position: 'fixed', top: 0, right: 0, bottom: 0, width: 380, zIndex: 100,
            background: 'rgba(15,23,42,0.97)',
            borderLeft: `1px solid ${colors.border}`,
            backdropFilter: 'blur(24px)',
            padding: '28px 24px',
            overflowY: 'auto',
            animation: 'slideInRight 0.3s ease',
        }}>
            <button onClick={onClose} style={{
                position: 'absolute', top: 16, right: 16, background: 'none',
                border: 'none', color: '#94a3b8', cursor: 'pointer',
            }}><X size={18} /></button>

            <div style={{
                fontSize: 10, fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
                color: colors.text, marginBottom: 8,
            }}>Component Detail</div>

            <h3 style={{ color: '#f1f5f9', fontSize: 20, fontWeight: 700, margin: '0 0 16px' }}>{node.name}</h3>

            {node.file && (
                <div style={{
                    background: 'rgba(255,255,255,0.04)', borderRadius: 8,
                    padding: '8px 12px', marginBottom: 16, fontSize: 12,
                    fontFamily: 'monospace', color: '#94a3b8',
                }}>{node.file}</div>
            )}

            <p style={{ color: '#cbd5e1', fontSize: 13, lineHeight: 1.6, marginBottom: 20 }}>{node.desc}</p>

            {node.inputs && (
                <div style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1.2, color: '#64748b', marginBottom: 6, textTransform: 'uppercase' }}>Inputs</div>
                    {node.inputs.map((inp, i) => (
                        <div key={i} style={{
                            display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4,
                            fontSize: 12, color: '#94a3b8',
                        }}>
                            <ChevronRight size={12} color={colors.border} />
                            {inp}
                        </div>
                    ))}
                </div>
            )}

            {node.outputs && (
                <div>
                    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1.2, color: '#64748b', marginBottom: 6, textTransform: 'uppercase' }}>Outputs</div>
                    {node.outputs.map((out, i) => (
                        <div key={i} style={{
                            display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4,
                            fontSize: 12, color: '#94a3b8',
                        }}>
                            <ChevronRight size={12} color={colors.border} />
                            {out}
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

function LayerConnector({ delay }) {
    return (
        <div style={{ display: 'flex', justifyContent: 'center', padding: '2px 0', position: 'relative' }}>
            <svg width="2" height="32" style={{ overflow: 'visible' }}>
                <line x1="1" y1="0" x2="1" y2="32" stroke="rgba(148,163,184,0.18)" strokeWidth="2" strokeDasharray="4 4" />
                <circle r="3" fill="#7c3aed" opacity="0.9">
                    <animate attributeName="cy" from="-3" to="35" dur="1.8s" begin={`${delay}s`} repeatCount="indefinite" />
                    <animate attributeName="opacity" values="0;0.9;0.9;0" dur="1.8s" begin={`${delay}s`} repeatCount="indefinite" />
                </circle>
            </svg>
        </div>
    )
}

function ParallelBracket({ label, color }) {
    return (
        <div style={{
            display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6,
        }}>
            <div style={{
                width: 3, height: 20, borderRadius: 2,
                background: `linear-gradient(180deg, ${LAYER_COLORS[color].border}, transparent)`,
            }} />
            <span style={{
                fontSize: 9, fontWeight: 700, letterSpacing: 1.5, textTransform: 'uppercase',
                color: LAYER_COLORS[color].text, opacity: 0.7,
            }}>{label}</span>
        </div>
    )
}

function DiagramFrame({ children, ariaLabel }) {
    return (
        <figure style={{ margin: 0 }}>
            <div
                role="img"
                aria-label={ariaLabel}
                style={{
                    borderRadius: 10,
                    background: 'linear-gradient(155deg, rgba(30,41,59,0.55) 0%, rgba(15,23,42,0.92) 55%, rgba(15,23,42,0.98) 100%)',
                    border: '1px solid rgba(148,163,184,0.14)',
                    padding: '14px 10px',
                    overflowX: 'auto',
                    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.04)',
                }}
            >
                {children}
            </div>
        </figure>
    )
}

/** Rounded rect for SVG diagrams */
function svgBox(x, y, w, h, fill, stroke, label, sub) {
    return (
        <g>
            <rect x={x} y={y} width={w} height={h} rx={8} fill={fill} stroke={stroke} strokeWidth={1.2} />
            <text x={x + w / 2} y={y + h / 2 - (sub ? 5 : 0)} textAnchor="middle" fill="#f1f5f9" fontSize={11} fontWeight={700} fontFamily="system-ui, sans-serif">{label}</text>
            {sub && (
                <text x={x + w / 2} y={y + h / 2 + 9} textAnchor="middle" fill="#94a3b8" fontSize={9} fontFamily="system-ui, sans-serif">{sub}</text>
            )}
        </g>
    )
}

function DiagramSharedRequestPipeline() {
    const w = 720
    const h = 200
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <defs>
                <marker id="guideArrowHeadReq" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                    <path d="M0,0 L8,4 L0,8 Z" fill="rgba(148,163,184,0.65)" />
                </marker>
                <linearGradient id="gBlue" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stopColor="rgba(59,130,246,0.35)" />
                    <stop offset="100%" stopColor="rgba(59,130,246,0.08)" />
                </linearGradient>
                <linearGradient id="gViolet" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" stopColor="rgba(124,58,237,0.4)" />
                    <stop offset="100%" stopColor="rgba(124,58,237,0.1)" />
                </linearGradient>
            </defs>
            {svgBox(8, 72, 88, 44, 'url(#gBlue)', 'rgba(59,130,246,0.5)', 'React', 'Vite SPA')}
            <line x1={96} y1={94} x2={116} y2={94} stroke="rgba(148,163,184,0.55)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadReq)" />
            {svgBox(118, 72, 72, 44, 'rgba(99,102,241,0.2)', 'rgba(129,140,248,0.6)', 'HTTPS', 'fetch')}
            <line x1={190} y1={94} x2={210} y2={94} stroke="rgba(148,163,184,0.55)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadReq)" />
            {svgBox(212, 64, 100, 60, 'rgba(99,102,241,0.25)', 'rgba(129,140,248,0.55)', 'FastAPI', 'Uvicorn')}
            <line x1={312} y1={94} x2={332} y2={94} stroke="rgba(148,163,184,0.55)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadReq)" />
            {svgBox(334, 72, 92, 44, 'rgba(139,92,246,0.2)', 'rgba(167,139,250,0.55)', 'Router', 'routers/')}
            <line x1={426} y1={94} x2={446} y2={94} stroke="rgba(148,163,184,0.55)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadReq)" />
            {svgBox(448, 64, 104, 60, 'url(#gViolet)', 'rgba(167,139,250,0.5)', 'Handler', 'deps + logic')}
            <text x={w / 2} y={22} textAnchor="middle" fill="#64748b" fontSize={10} fontWeight={600} letterSpacing={1.2} fontFamily="system-ui">REQUEST PATH</text>
            <line x1={500} y1={124} x2={500} y2={136} stroke="rgba(148,163,184,0.55)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadReq)" />
            {svgBox(388, 138, 224, 52, 'rgba(15,23,42,0.9)', 'rgba(245,158,11,0.35)', 'Side effects', 'Connectors · LLMClient · KnowledgeStore (RAG) · rate limits · guardrails')}
        </svg>
    )
}

function DiagramSwarmFlow() {
    const w = 720
    const h = 352
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <defs>
                <marker id="guideArrowHeadSwarm" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                    <path d="M0,0 L8,4 L0,8 Z" fill="rgba(167,139,250,0.7)" />
                </marker>
            </defs>
            <text x={w / 2} y={20} textAnchor="middle" fill="#64748b" fontSize={10} fontWeight={600} letterSpacing={1.2} fontFamily="system-ui">SWARM /trace · asyncio.gather (parallel)</text>
            {svgBox(300, 32, 120, 36, 'rgba(16,185,129,0.2)', 'rgba(52,211,153,0.5)', 'Ticker in', 'optional stress')}
            <line x1={360} y1={68} x2={360} y2={88} stroke="rgba(167,139,250,0.5)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadSwarm)" />
            {svgBox(270, 88, 180, 40, 'rgba(245,158,11,0.15)', 'rgba(251,191,36,0.45)', 'macro_fetch → MarketState', 'regime from credit stress')}
            <text x={360} y={152} textAnchor="middle" fill="#a78bfa" fontSize={9.5} fontWeight={700} fontFamily="system-ui">4 × AgentPair (each: Analyst ⇄ QA loop → VERIFIED / REJECTED)</text>
            {['Shorts', 'Social', 'Polymarket', 'Fund'].map((label, i) => {
                const x = 48 + i * 168
                return (
                    <g key={label}>
                        {svgBox(x, 162, 150, 72, 'rgba(124,58,237,0.18)', 'rgba(167,139,250,0.45)', label, 'connector.fetch_data')}
                        <text x={x + 75} y={248} textAnchor="middle" fill="#c4b5fd" fontSize={8.5} fontFamily="system-ui">RAG · swarm_reflections</text>
                    </g>
                )
            })}
            <line x1={360} y1={234} x2={360} y2={258} stroke="rgba(167,139,250,0.5)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadSwarm)" />
            {svgBox(230, 258, 260, 44, 'rgba(124,58,237,0.28)', 'rgba(167,139,250,0.55)', 'Consensus + optional LLM synthesis', 'global_verdict · confidence')}
            <line x1={360} y1={302} x2={360} y2={318} stroke="rgba(52,211,153,0.45)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadSwarm)" />
            {svgBox(200, 318, 200, 22, 'rgba(16,185,129,0.12)', 'rgba(52,211,153,0.35)', 'KnowledgeStore.write', 'swarm_history + factor snapshots')}
            {svgBox(420, 318, 200, 22, 'rgba(59,130,246,0.12)', 'rgba(96,165,250,0.4)', 'JSON → browser', 'SwarmConsensus')}
        </svg>
    )
}

function DiagramDebateFlow() {
    const w = 700
    const h = 308
    const agents = ['Bull', 'Bear', 'Macro', 'Value', 'Momentum']
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <defs>
                <marker id="guideArrowHeadDebate" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
                    <path d="M0,0 L8,4 L0,8 Z" fill="rgba(196,181,253,0.75)" />
                </marker>
            </defs>
            <text x={w / 2} y={18} textAnchor="middle" fill="#64748b" fontSize={10} fontWeight={600} letterSpacing={1.2} fontFamily="system-ui">DEBATE /debate · run_full_debate</text>
            {svgBox(80, 28, 130, 38, 'rgba(59,130,246,0.2)', 'rgba(96,165,250,0.5)', 'fetch_debate_data', 'live context')}
            {svgBox(490, 28, 130, 38, 'rgba(245,158,11,0.15)', 'rgba(251,191,36,0.45)', 'macro_fetch', 'macro_state')}
            <line x1={210} y1={47} x2={300} y2={100} stroke="rgba(196,181,253,0.45)" strokeWidth={1.2} />
            <line x1={490} y1={47} x2={400} y2={100} stroke="rgba(196,181,253,0.45)" strokeWidth={1.2} />
            {agents.map((a, i) => {
                const x = 70 + i * 112
                return (
                    <g key={a}>
                        {svgBox(x, 100, 100, 52, 'rgba(139,92,246,0.22)', 'rgba(196,181,253,0.5)', a, 'LLM + RAG')}
                    </g>
                )
            })}
            <line x1={350} y1={168} x2={350} y2={192} stroke="rgba(196,181,253,0.55)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadDebate)" />
            {svgBox(250, 192, 200, 48, 'rgba(124,58,237,0.35)', 'rgba(196,181,253,0.65)', 'Moderator LLM', 'verdict + summary')}
            <line x1={350} y1={240} x2={350} y2={258} stroke="rgba(52,211,153,0.45)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadDebate)" />
            {svgBox(230, 258, 240, 36, 'rgba(16,185,129,0.12)', 'rgba(52,211,153,0.4)', 'add_debate → DebateResult JSON', '')}
        </svg>
    )
}

function DiagramBacktestFlow() {
    const w = 680
    const h = 120
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <defs>
                <marker id="guideArrowHeadBt" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                    <path d="M0,0 L7,3.5 L0,7 Z" fill="rgba(251,191,36,0.65)" />
                </marker>
            </defs>
            <text x={w / 2} y={16} textAnchor="middle" fill="#64748b" fontSize={10} fontWeight={600} letterSpacing={1.2} fontFamily="system-ui">BACKTEST · linear pipeline</text>
            {['Strategy text', 'Parse rules', 'Fetch history', 'Simulate', 'PnL + JSON'].map((label, i) => (
                <g key={label}>
                    {svgBox(12 + i * 128, 32, 118, 44, 'rgba(245,158,11,0.14)', 'rgba(251,191,36,0.4)', label, i === 1 ? 'LLM assist' : i === 2 ? 'yFinance' : '')}
                    {i < 4 && <line x1={130 + i * 128} y1={54} x2={140 + i * 128} y2={54} stroke="rgba(251,191,36,0.45)" strokeWidth={1.5} markerEnd="url(#guideArrowHeadBt)" />}
                </g>
            ))}
        </svg>
    )
}

function DiagramDataSources() {
    const w = 720
    const h = 200
    const cols = [
        { title: 'Live connectors', sub: 'Per-request HTTP', color: 'rgba(59,130,246,0.25)', stroke: 'rgba(96,165,250,0.55)', items: ['yFinance', 'RSS / News', 'Polymarket', 'FRED', 'YouTube'] },
        { title: 'Vector RAG', sub: 'KnowledgeStore', color: 'rgba(124,58,237,0.22)', stroke: 'rgba(167,139,250,0.5)', items: ['Past traces & debates', 'Macro alerts & pipeline text', 'Embeddings + search'] },
        { title: 'Background', sub: 'While process runs', color: 'rgba(16,185,129,0.15)', stroke: 'rgba(52,211,153,0.45)', items: ['60s news → SSE', 'Daily pipeline', 'Market intel jobs'] },
    ]
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <text x={w / 2} y={18} textAnchor="middle" fill="#64748b" fontSize={10} fontWeight={600} letterSpacing={1.2} fontFamily="system-ui">THREE DATA PATHS</text>
            {cols.map((c, i) => {
                const x = 24 + i * 232
                return (
                    <g key={c.title}>
                        <rect x={x} y={36} width={208} height={158} rx={12} fill={c.color} stroke={c.stroke} strokeWidth={1.2} />
                        <text x={x + 104} y={58} textAnchor="middle" fill="#f1f5f9" fontSize={12} fontWeight={700} fontFamily="system-ui">{c.title}</text>
                        <text x={x + 104} y={74} textAnchor="middle" fill="#94a3b8" fontSize={9} fontFamily="system-ui">{c.sub}</text>
                        {c.items.map((line, j) => (
                            <text key={line} x={x + 104} y={96 + j * 18} textAnchor="middle" fill="#cbd5e1" fontSize={9.5} fontFamily="system-ui">{line}</text>
                        ))}
                    </g>
                )
            })}
        </svg>
    )
}

/** How embeddings + vector DB + agents connect (mechanics). */
function DiagramVectorRAGMechanics() {
    const w = 720
    const h = 380
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <defs>
                <marker id="ragArrA" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                    <path d="M0,0 L7,3.5 L0,7 Z" fill="rgba(52,211,153,0.7)" />
                </marker>
                <marker id="ragArrB" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                    <path d="M0,0 L7,3.5 L0,7 Z" fill="rgba(167,139,250,0.75)" />
                </marker>
            </defs>
            <text x={w / 2} y={22} textAnchor="middle" fill="#e9d5ff" fontSize={13} fontWeight={800} fontFamily="system-ui">Vector RAG — inside KnowledgeStore</text>
            <text x={w / 2} y={40} textAnchor="middle" fill="#64748b" fontSize={9.5} fontFamily="system-ui">Same embedding model for storage and search · collections partition memory</text>

            <text x={24} y={68} fill="#34d399" fontSize={10} fontWeight={700} fontFamily="system-ui" letterSpacing={1}>WRITE (ingest)</text>
            {svgBox(24, 78, 100, 44, 'rgba(16,185,129,0.18)', 'rgba(52,211,153,0.45)', 'Text + meta', 'from API / pipeline')}
            <line x1={124} y1={100} x2={148} y2={100} stroke="rgba(52,211,153,0.5)" strokeWidth={1.5} markerEnd="url(#ragArrA)" />
            {svgBox(148, 78, 118, 44, 'rgba(245,158,11,0.12)', 'rgba(251,191,36,0.4)', 'Embed model', 'OpenRouter / HF / Chroma')}
            <line x1={266} y1={100} x2={290} y2={100} stroke="rgba(52,211,153,0.5)" strokeWidth={1.5} markerEnd="url(#ragArrA)" />
            {svgBox(290, 72, 200, 56, 'rgba(15,23,42,0.95)', 'rgba(52,211,153,0.35)', 'vector_memory row', 'collection · embedding[] · document · metadata')}
            <text x={390} y={148} textAnchor="middle" fill="#64748b" fontSize={8.5} fontFamily="system-ui">Supabase pgvector RPC match_vector_memory · or Chroma query</text>

            <text x={24} y={188} fill="#a78bfa" fontSize={10} fontWeight={700} fontFamily="system-ui" letterSpacing={1}>READ (retrieval)</text>
            {svgBox(24, 198, 112, 44, 'rgba(124,58,237,0.2)', 'rgba(167,139,250,0.45)', 'Query string', 'from agent / chat')}
            <line x1={136} y1={220} x2={158} y2={220} stroke="rgba(167,139,250,0.5)" strokeWidth={1.5} markerEnd="url(#ragArrB)" />
            {svgBox(158, 198, 118, 44, 'rgba(245,158,11,0.1)', 'rgba(251,191,36,0.38)', 'Embed query', 'same model')}
            <line x1={276} y1={220} x2={300} y2={220} stroke="rgba(167,139,250,0.5)" strokeWidth={1.5} markerEnd="url(#ragArrB)" />
            {svgBox(300, 198, 150, 44, 'rgba(124,58,237,0.22)', 'rgba(167,139,250,0.5)', 'ANN similarity', 'top-K nearest vectors')}
            <line x1={450} y1={220} x2={472} y2={220} stroke="rgba(167,139,250,0.5)" strokeWidth={1.5} markerEnd="url(#ragArrB)" />
            {svgBox(472, 198, 120, 44, 'rgba(139,92,246,0.2)', 'rgba(196,181,253,0.45)', 'Top-K chunks', 'document text')}
            <line x1={592} y1={220} x2={612} y2={220} stroke="rgba(167,139,250,0.5)" strokeWidth={1.5} markerEnd="url(#ragArrB)" />
            {svgBox(612, 198, 96, 44, 'rgba(124,58,237,0.28)', 'rgba(196,181,253,0.55)', 'format_context', 'prompt block')}

            <text x={w / 2} y={288} textAnchor="middle" fill="#94a3b8" fontSize={9.5} fontFamily="system-ui">Code: <tspan fill="#a78bfa">knowledge_store.query(collection, query_text, n_results)</tspan> · falls back to lexical match if embeddings fail</text>

            <rect x={24} y={308} width={672} height={62} rx={10} fill="rgba(99,102,241,0.06)" stroke="rgba(129,140,248,0.25)" />
            <text x={36} y={328} fill="#cbd5e1" fontSize={9.5} fontFamily="system-ui" fontWeight={600}>Named collections (examples)</text>
            <text x={36} y={346} fill="#94a3b8" fontSize={8.5} fontFamily="system-ui">
                swarm_history · debate_history · macro_alerts · price_movements · youtube_insights · strategy_backtests · swarm_reflections · stock_profiles · chat_memories …
            </text>
            <text x={36} y={362} fill="#64748b" fontSize={8} fontFamily="system-ui">Each query searches <tspan fill="#e2e8f0">one</tspan> collection unless code runs multiple queries (debate, chat).</text>
        </svg>
    )
}

/** Which agents pull from RAG and how. */
function DiagramVectorRAGAgents() {
    const w = 720
    const h = 280
    return (
        <svg width="100%" height={h} viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" style={{ display: 'block' }} aria-hidden>
            <text x={w / 2} y={20} textAnchor="middle" fill="#e9d5ff" fontSize={12} fontWeight={800} fontFamily="system-ui">How agents benefit from Vector RAG</text>
            <text x={w / 2} y={38} textAnchor="middle" fill="#64748b" fontSize={9} fontFamily="system-ui">Retrieval runs before or alongside LLM calls — not a separate service</text>

            {svgBox(16, 52, 220, 88, 'rgba(124,58,237,0.15)', 'rgba(167,139,250,0.4)', 'Swarm · AgentPair', 'before analyst loop')}
            <text x={126} y={118} textAnchor="middle" fill="#cbd5e1" fontSize={8.5} fontFamily="system-ui">swarm_reflections, stock_profiles,</text>
            <text x={126} y={132} textAnchor="middle" fill="#cbd5e1" fontSize={8.5} fontFamily="system-ui">earnings_memory → “Memory” in history</text>

            {svgBox(250, 52, 220, 88, 'rgba(139,92,246,0.16)', 'rgba(196,181,253,0.42)', 'Debate · 5 roles', 'per-role collection map')}
            <text x={360} y={114} textAnchor="middle" fill="#cbd5e1" fontSize={8.5} fontFamily="system-ui">bull: prices, youtube, debates…</text>
            <text x={360} y={128} textAnchor="middle" fill="#cbd5e1" fontSize={8.5} fontFamily="system-ui">bear: macro, swarm_history… · + reflections</text>

            {svgBox(484, 52, 220, 88, 'rgba(59,130,246,0.14)', 'rgba(96,165,250,0.4)', 'Chat', 'multi-collection + rerank')}
            <text x={594} y={118} textAnchor="middle" fill="#cbd5e1" fontSize={8.5} fontFamily="system-ui">Several collections in parallel,</text>
            <text x={594} y={132} textAnchor="middle" fill="#cbd5e1" fontSize={8.5} fontFamily="system-ui">merge hits → ranked context block</text>

            {svgBox(120, 168, 480, 92, 'rgba(16,185,129,0.08)', 'rgba(52,211,153,0.3)', 'Outcome', '')}
            <text x={360} y={200} textAnchor="middle" fill="#e2e8f0" fontSize={10} fontWeight={600} fontFamily="system-ui">Retrieved text is concatenated / formatted and passed into the LLM prompt</text>
            <text x={360} y={220} textAnchor="middle" fill="#94a3b8" fontSize={9} fontFamily="system-ui">So agents “remember” past runs, debates, macro, and pipeline content without hard-coding rules</text>
            <text x={360} y={242} textAnchor="middle" fill="#64748b" fontSize={8.5} fontFamily="system-ui">New writes after each successful run keep the knowledge base growing for the next retrieval</text>
        </svg>
    )
}

function VectorRAGSection() {
    const p = { color: '#94a3b8', fontSize: 13, lineHeight: 1.7, margin: '0 0 14px' }
    const h4 = { color: '#c4b5fd', fontSize: 12, fontWeight: 700, margin: '18px 0 8px', letterSpacing: 0.3 }
    const li = { marginBottom: 8, paddingLeft: 4 }
    return (
        <div style={{
            borderRadius: 14,
            border: '1px solid rgba(167,139,250,0.35)',
            background: 'linear-gradient(180deg, rgba(124,58,237,0.12) 0%, rgba(15,23,42,0.5) 100%)',
            padding: '22px 20px 24px',
            marginBottom: 8,
        }}>
            <h3 style={{
                color: '#f1f5f9', fontSize: 17, fontWeight: 800, margin: '0 0 8px', letterSpacing: -0.3,
            }}>Vector RAG (KnowledgeStore)</h3>
            <p style={{ ...p, margin: '0 0 16px', fontSize: 13.5, color: '#cbd5e1' }}>
                <strong style={{ color: '#e2e8f0' }}>RAG</strong> = retrieval-augmented generation. TradeTalk does not stuff the whole database into the model.
                It <strong style={{ color: '#e2e8f0' }}>searches</strong> for a small set of relevant text chunks, then passes those into the LLM as context.
            </p>

            <DiagramFrame ariaLabel="Vector RAG write and read mechanics">
                <DiagramVectorRAGMechanics />
            </DiagramFrame>

            <h4 style={h4}>What “vector” means (for new developers)</h4>
            <p style={p}>
                Each piece of text is converted to a <strong style={{ color: '#e2e8f0' }}>vector</strong> (a long list of numbers) using an <strong style={{ color: '#e2e8f0' }}>embedding model</strong>.
                Texts with similar meaning get similar vectors. To find relevant past notes, we embed your <strong style={{ color: '#e2e8f0' }}>query string</strong> the same way and ask the database for the <strong style={{ color: '#e2e8f0' }}>nearest</strong> stored vectors — that is semantic search, not keyword grep.
            </p>

            <h4 style={h4}>Where rows live</h4>
            <p style={p}>
                <code style={{ fontSize: 11, color: '#a5b4fc' }}>VECTOR_BACKEND</code> selects storage: production often uses{' '}
                <strong style={{ color: '#e2e8f0' }}>Supabase</strong> (<code style={{ fontSize: 11 }}>vector_memory</code> + <code style={{ fontSize: 11 }}>match_vector_memory</code>),
                local dev may use <strong style={{ color: '#e2e8f0' }}>Chroma</strong> on disk. Documents are grouped into <strong style={{ color: '#e2e8f0' }}>collections</strong> (e.g. <code style={{ fontSize: 11 }}>debate_history</code>, <code style={{ fontSize: 11 }}>swarm_reflections</code>) so search stays scoped.
            </p>

            <h4 style={h4}>How agents benefit</h4>
            <ul style={{ color: '#94a3b8', fontSize: 13, lineHeight: 1.65, margin: '0 0 16px', paddingLeft: 20 }}>
                <li style={li}><strong style={{ color: '#e2e8f0' }}>Swarm (AgentPair):</strong> before the analyst/QA loop, the code loads prior lessons from <code style={{ fontSize: 11 }}>swarm_reflections</code>, optional stock/earnings RAG — injected as a <strong style={{ color: '#e2e8f0' }}>Memory</strong> turn so factors learn from history.</li>
                <li style={li}><strong style={{ color: '#e2e8f0' }}>Debate:</strong> each role (bull, bear, …) queries a <strong style={{ color: '#e2e8f0' }}>different set of collections</strong> with a ticker-themed query string, then merges snippets into one context block for that agent’s LLM call. Moderator does the same with debate history.</li>
                <li style={li}><strong style={{ color: '#e2e8f0' }}>Chat:</strong> hits multiple collections, merges and <strong style={{ color: '#e2e8f0' }}>reranks</strong> hits so the assistant sees the best cross-cutting context.</li>
                <li style={li}><strong style={{ color: '#e2e8f0' }}>After each run:</strong> successful analyses/debates are often <strong style={{ color: '#e2e8f0' }}>written back</strong> into the store, so future retrieval gets richer.</li>
            </ul>

            <DiagramFrame ariaLabel="Agent benefits from vector RAG by feature">
                <DiagramVectorRAGAgents />
            </DiagramFrame>

            <p style={{ ...p, margin: '14px 0 0', fontSize: 12, color: '#64748b' }}>
                Deeper reference: <code style={{ fontSize: 11 }}>backend/knowledge_store.py</code>, <code style={{ fontSize: 11 }}>backend/vector_backends.py</code>, <code style={{ fontSize: 11 }}>backend/debate_agents.py</code>, <code style={{ fontSize: 11 }}>backend/chat_service.py</code> · policy: <code style={{ fontSize: 11 }}>docs/RAG_POLICY.md</code>
            </p>
        </div>
    )
}

/** Visual-only guide: stacked flow diagrams (labels live inside SVG). */
function RequestWorkflowsGuide() {
    return (
        <div style={{
            marginBottom: 28,
            display: 'flex',
            flexDirection: 'column',
            gap: 14,
        }}>
            <DiagramFrame ariaLabel="Request path from React through FastAPI router and handler to side effects">
                <DiagramSharedRequestPipeline />
            </DiagramFrame>

            <VectorRAGSection />

            <DiagramFrame ariaLabel="Swarm trace with four parallel factor agent pairs, consensus, and persistence">
                <DiagramSwarmFlow />
            </DiagramFrame>
            <DiagramFrame ariaLabel="Debate flow from debate data and macro inputs through five agents to moderator and result">
                <DiagramDebateFlow />
            </DiagramFrame>
            <DiagramFrame ariaLabel="Backtest linear pipeline from strategy to simulation output">
                <DiagramBacktestFlow />
            </DiagramFrame>
            <DiagramFrame ariaLabel="Three data paths: live connectors, vector RAG, and background jobs">
                <DiagramDataSources />
            </DiagramFrame>
        </div>
    )
}

export default function SystemMapUI() {
    const [selectedNode, setSelectedNode] = useState(null)
    const [selectedLayerColor, setSelectedLayerColor] = useState(null)
    const [activeTrace, setActiveTrace] = useState(null)
    const [visibleLayers, setVisibleLayers] = useState(new Set())
    const [mainTab, setMainTab] = useState('guide')
    const containerRef = useRef(null)

    useEffect(() => {
        const timers = LAYERS.map((layer, i) =>
            setTimeout(() => setVisibleLayers(prev => new Set([...prev, layer.id])), i * 120)
        )
        return () => timers.forEach(clearTimeout)
    }, [])

    const traceNodes = activeTrace ? new Set(FLOW_TRACES[activeTrace]) : null

    const handleNodeClick = (node, layerColor) => {
        if (selectedNode?.id === node.id) {
            setSelectedNode(null)
            setSelectedLayerColor(null)
        } else {
            setSelectedNode(node)
            setSelectedLayerColor(layerColor)
        }
    }

    return (
        <div ref={containerRef} style={{ padding: '24px 28px', maxWidth: 1100, margin: '0 auto' }}>
            <style>{`
                @keyframes slideInRight {
                    from { transform: translateX(100%); opacity: 0; }
                    to   { transform: translateX(0);    opacity: 1; }
                }
                @keyframes layerAppear {
                    from { transform: translateY(18px); opacity: 0; }
                    to   { transform: translateY(0);    opacity: 1; }
                }
                @keyframes pulseGlow {
                    0%, 100% { box-shadow: 0 0 0 0 rgba(124,58,237,0); }
                    50%      { box-shadow: 0 0 16px 4px rgba(124,58,237,0.25); }
                }
            `}</style>

            {/* Header */}
            <div style={{ marginBottom: 28 }}>
                <h2 style={{ color: '#f1f5f9', fontSize: 22, fontWeight: 800, margin: '0 0 6px' }}>
                    System Architecture Map
                </h2>
                <p style={{ color: '#64748b', fontSize: 13, margin: '0 0 14px' }}>
                    <strong style={{ color: '#94a3b8' }}>How requests &amp; agents work</strong> includes detailed <strong style={{ color: '#94a3b8' }}>Vector RAG</strong> explanation plus flow diagrams; <strong style={{ color: '#94a3b8' }}>Interactive component map</strong> lists components and trace highlights.
                </p>
                <div style={{
                    padding: '14px 16px',
                    borderRadius: 12,
                    border: '1px solid rgba(99,102,241,0.25)',
                    background: 'rgba(99,102,241,0.06)',
                    marginBottom: 4,
                }}>
                    <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: 1.2, color: '#818cf8', marginBottom: 8, textTransform: 'uppercase' }}>
                        Production at a glance
                    </div>
                    <p style={{ color: '#94a3b8', fontSize: 12, lineHeight: 1.65, margin: 0 }}>
                        The browser loads the static app from <strong style={{ color: '#cbd5e1' }}>Vercel</strong>; API calls go to{' '}
                        <strong style={{ color: '#cbd5e1' }}>Render</strong> using <code style={{ fontSize: 11, color: '#a5b4fc' }}>VITE_API_BASE_URL</code>.
                        Default production vectors use <strong style={{ color: '#cbd5e1' }}>Supabase pgvector</strong> (see <code style={{ fontSize: 11 }}>render.yaml</code>) with{' '}
                        <strong style={{ color: '#cbd5e1' }}>OpenRouter</strong> embeddings when configured — not Hugging Face.
                        HF is used for optional Chroma-on-Render embeddings, <code style={{ fontSize: 11 }}>VECTOR_BACKEND=hf</code> snapshot RAG, and optional backtest/data-lake Parquet from Hub datasets.
                        Scheduled ingest and wake pings are documented in <code style={{ fontSize: 11 }}>docs/CRON.md</code>; full narrative in <code style={{ fontSize: 11 }}>docs/ARCHITECTURE.md</code>.
                    </p>
                </div>
            </div>

            {/* Primary tabs: guide vs interactive map */}
            <div style={{
                display: 'flex', gap: 8, marginBottom: 20, padding: 4,
                background: 'rgba(15,23,42,0.85)',
                borderRadius: 12,
                border: '1px solid rgba(255,255,255,0.06)',
                width: 'fit-content',
                flexWrap: 'wrap',
            }}>
                {[
                    { id: 'guide', label: 'How requests & agents work', Icon: BookOpen },
                    { id: 'map', label: 'Interactive component map', Icon: Layers },
                ].map(({ id, label, Icon }) => (
                    <button
                        key={id}
                        type="button"
                        onClick={() => setMainTab(id)}
                        style={{
                            display: 'flex', alignItems: 'center', gap: 8,
                            padding: '10px 16px', borderRadius: 10, border: 'none', cursor: 'pointer',
                            fontSize: 13, fontWeight: 700,
                            background: mainTab === id ? 'rgba(124,58,237,0.35)' : 'transparent',
                            color: mainTab === id ? '#e9d5ff' : '#94a3b8',
                            transition: 'background 0.2s, color 0.2s',
                        }}
                    >
                        <Icon size={17} />
                        {label}
                    </button>
                ))}
            </div>

            {mainTab === 'guide' && <RequestWorkflowsGuide />}

            {mainTab === 'map' && (
            <>
            {/* Trace selector */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 24, flexWrap: 'wrap' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: '#475569', lineHeight: '30px', letterSpacing: 1 }}>
                    TRACE A REQUEST:
                </span>
                {['swarm', 'debate', 'backtest'].map(t => (
                    <button
                        key={t}
                        onClick={() => setActiveTrace(activeTrace === t ? null : t)}
                        style={{
                            padding: '5px 14px', borderRadius: 8, fontSize: 12, fontWeight: 700,
                            textTransform: 'capitalize', cursor: 'pointer',
                            border: activeTrace === t ? '1px solid #7c3aed' : '1px solid rgba(255,255,255,0.08)',
                            background: activeTrace === t ? 'rgba(124,58,237,0.2)' : 'rgba(255,255,255,0.03)',
                            color: activeTrace === t ? '#a78bfa' : '#94a3b8',
                            transition: 'all 0.2s ease',
                        }}
                    >{t}</button>
                ))}
                {activeTrace && (
                    <button
                        onClick={() => setActiveTrace(null)}
                        style={{
                            padding: '5px 10px', borderRadius: 8, fontSize: 11, cursor: 'pointer',
                            border: '1px solid rgba(239,68,68,0.3)', background: 'rgba(239,68,68,0.08)',
                            color: '#f87171', fontWeight: 600,
                        }}
                    >Clear</button>
                )}
            </div>

            {/* Layers */}
            {LAYERS.map((layer, li) => {
                const Icon = layer.icon
                const colors = LAYER_COLORS[layer.color]
                const isVisible = visibleLayers.has(layer.id)

                const swarmNodes = layer.nodes.filter(n => n.tag === 'swarm')
                const debateNodes = layer.nodes.filter(n => n.tag === 'debate')
                const otherNodes = layer.nodes.filter(n => !n.tag)
                const hasSubGroups = swarmNodes.length > 0 || debateNodes.length > 0

                return (
                    <div key={layer.id}>
                        {li > 0 && <LayerConnector delay={li * 0.3} />}
                        <div style={{
                            background: 'rgba(15,23,42,0.6)',
                            border: `1px solid rgba(255,255,255,0.06)`,
                            borderRadius: 14,
                            padding: '16px 20px',
                            opacity: isVisible ? 1 : 0,
                            transform: isVisible ? 'translateY(0)' : 'translateY(18px)',
                            transition: 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
                        }}>
                            <div style={{
                                display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12,
                            }}>
                                <div style={{
                                    width: 30, height: 30, borderRadius: 8,
                                    background: colors.bg, border: `1px solid ${colors.border}`,
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                }}>
                                    <Icon size={16} color={colors.text} />
                                </div>
                                <span style={{
                                    fontSize: 13, fontWeight: 700, color: colors.text, letterSpacing: 0.5,
                                }}>{layer.label}</span>
                                {layer.id === 'agents' && (
                                    <span style={{
                                        fontSize: 10, color: '#475569', fontWeight: 600,
                                        background: 'rgba(255,255,255,0.04)', padding: '2px 8px',
                                        borderRadius: 6, marginLeft: 4,
                                    }}>Concurrent execution</span>
                                )}
                            </div>

                            {hasSubGroups ? (
                                <div>
                                    {swarmNodes.length > 0 && (
                                        <div style={{ marginBottom: 12 }}>
                                            <ParallelBracket label="Swarm — 4 AgentPairs (parallel)" color={layer.color} />
                                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                                {swarmNodes.map(node => (
                                                    <NodeCard
                                                        key={node.id}
                                                        node={node}
                                                        layerColor={layer.color}
                                                        isActive={selectedNode?.id === node.id}
                                                        isDimmed={traceNodes && !traceNodes.has(node.id)}
                                                        onClick={(n) => handleNodeClick(n, layer.color)}
                                                    />
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                    {debateNodes.length > 0 && (
                                        <div style={{ marginBottom: otherNodes.length > 0 ? 12 : 0 }}>
                                            <ParallelBracket label="Debate — 5 LLM Specialists + Moderator" color={layer.color} />
                                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                                {debateNodes.map(node => (
                                                    <NodeCard
                                                        key={node.id}
                                                        node={node}
                                                        layerColor={layer.color}
                                                        isActive={selectedNode?.id === node.id}
                                                        isDimmed={traceNodes && !traceNodes.has(node.id)}
                                                        onClick={(n) => handleNodeClick(n, layer.color)}
                                                    />
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                    {otherNodes.length > 0 && (
                                        <div>
                                            <ParallelBracket label="Background" color={layer.color} />
                                            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                                {otherNodes.map(node => (
                                                    <NodeCard
                                                        key={node.id}
                                                        node={node}
                                                        layerColor={layer.color}
                                                        isActive={selectedNode?.id === node.id}
                                                        isDimmed={traceNodes && !traceNodes.has(node.id)}
                                                        onClick={(n) => handleNodeClick(n, layer.color)}
                                                    />
                                                ))}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                                    {layer.nodes.map(node => (
                                        <NodeCard
                                            key={node.id}
                                            node={node}
                                            layerColor={layer.color}
                                            isActive={selectedNode?.id === node.id}
                                            isDimmed={traceNodes && !traceNodes.has(node.id)}
                                            onClick={(n) => handleNodeClick(n, layer.color)}
                                        />
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )
            })}

            {/* Background tasks row */}
            <LayerConnector delay={LAYERS.length * 0.3} />
            <div style={{
                background: 'rgba(15,23,42,0.4)',
                border: '1px solid rgba(255,255,255,0.04)',
                borderRadius: 14, padding: '14px 20px',
                opacity: visibleLayers.has('infra') ? 1 : 0,
                transform: visibleLayers.has('infra') ? 'translateY(0)' : 'translateY(18px)',
                transition: 'all 0.5s cubic-bezier(0.16, 1, 0.3, 1) 0.15s',
            }}>
                <div style={{
                    display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10,
                }}>
                    <div style={{
                        width: 30, height: 30, borderRadius: 8,
                        background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)',
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                        <Zap size={16} color="#fbbf24" />
                    </div>
                    <span style={{ fontSize: 13, fontWeight: 700, color: '#fbbf24', letterSpacing: 0.5 }}>
                        Background Pipelines
                    </span>
                </div>
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                    {BACKGROUND_TASKS.map(task => {
                        const TaskIcon = task.icon
                        return (
                            <button
                                key={task.id}
                                onClick={() => handleNodeClick({
                                    id: task.id, name: task.name, file: null,
                                    desc: task.desc, inputs: ['(automated)'], outputs: ['KnowledgeStore', 'SSE stream'],
                                }, 'data')}
                                style={{
                                    display: 'flex', alignItems: 'center', gap: 8,
                                    background: 'rgba(245,158,11,0.06)',
                                    border: '1px solid rgba(245,158,11,0.15)',
                                    borderRadius: 10, padding: '8px 14px',
                                    color: '#fbbf24', fontSize: 12, fontWeight: 600,
                                    cursor: 'pointer', transition: 'all 0.2s',
                                }}
                            >
                                <TaskIcon size={14} style={{
                                    animation: task.id === 'news_loop' ? 'spin 3s linear infinite' : 'none',
                                }} />
                                <span>{task.name}</span>
                                <span style={{
                                    fontSize: 10, color: '#92400e', background: 'rgba(245,158,11,0.15)',
                                    padding: '1px 6px', borderRadius: 4, fontWeight: 700,
                                }}>{task.interval}</span>
                            </button>
                        )
                    })}
                </div>
            </div>

            {/* Legend */}
            <div style={{
                marginTop: 24, display: 'flex', gap: 12, flexWrap: 'wrap',
                padding: '12px 16px', background: 'rgba(15,23,42,0.4)',
                borderRadius: 10, border: '1px solid rgba(255,255,255,0.04)',
            }}>
                <span style={{ fontSize: 10, fontWeight: 700, color: '#475569', letterSpacing: 1, lineHeight: '20px' }}>LEGEND:</span>
                {Object.entries(LAYER_COLORS).map(([key, c]) => (
                    <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <div style={{
                            width: 10, height: 10, borderRadius: 3,
                            background: c.border,
                        }} />
                        <span style={{ fontSize: 10, color: '#94a3b8', textTransform: 'capitalize' }}>{key}</span>
                    </div>
                ))}
            </div>

            </>
            )}

            {/* Detail Panel (component map interactions) */}
            <DetailPanel
                node={selectedNode}
                layerColor={selectedLayerColor || 'api'}
                onClose={() => { setSelectedNode(null); setSelectedLayerColor(null) }}
            />
        </div>
    )
}
