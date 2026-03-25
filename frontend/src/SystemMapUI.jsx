import { useState, useEffect, useRef } from 'react'
import {
    User, Monitor, Server, Bot, Brain, Database, Cloud,
    Clock, X, ChevronRight, Zap, RefreshCw
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
        ]
    },
    {
        id: 'api', label: 'FastAPI Backend', icon: Server, color: 'api',
        nodes: [
            { id: 'ep_trace', name: '/trace', file: 'backend/main.py', desc: 'Swarm endpoint — runs 4 AgentPairs concurrently, returns SwarmConsensus.', inputs: ['ticker', 'credit_stress?'], outputs: ['SwarmConsensus JSON'] },
            { id: 'ep_debate', name: '/debate', file: 'backend/main.py', desc: 'Debate endpoint — runs 5 LLM agents + moderator, returns DebateResult.', inputs: ['ticker'], outputs: ['DebateResult JSON'] },
            { id: 'ep_backtest', name: '/backtest', file: 'backend/main.py', desc: 'Backtest endpoint — parses strategy via LLM, simulates trades, returns results.', inputs: ['strategy text', 'start/end dates'], outputs: ['BacktestResult JSON'] },
            { id: 'ep_macro', name: '/macro', file: 'backend/main.py', desc: 'Macro endpoint — returns global macro indicators (VIX, sectors, spending).', inputs: ['(none)'], outputs: ['MacroDataResponse JSON'] },
            { id: 'ep_notif', name: '/notifications', file: 'backend/main.py', desc: 'Notification SSE stream + history + scan trigger.', inputs: ['(auto 60s loop)'], outputs: ['SSE events', 'Alert history'] },
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
            { id: 'knowledge_store', name: 'KnowledgeStore (RAG)', file: 'backend/knowledge_store.py', desc: '8 ChromaDB/pgvector collections. Semantic search for RAG context. Reflection memory with effectiveness scoring.', inputs: ['Query text + filters'], outputs: ['Relevant documents'] },
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
            { id: 'infra_render', name: 'Render.com', file: 'render.yaml', desc: 'Backend hosting. Python runtime, uvicorn. Ephemeral filesystem.', inputs: ['Git push'], outputs: ['Running FastAPI server'] },
            { id: 'infra_vercel', name: 'Vercel', file: 'frontend/vercel.json', desc: 'Frontend hosting. Static Vite build. SPA routing.', inputs: ['Git push'], outputs: ['Served React app'] },
            { id: 'infra_supabase', name: 'Supabase pgvector', file: 'backend/vector_backends.py', desc: 'Durable vector memory. match_vector_memory RPC for semantic search.', inputs: ['Embeddings + text'], outputs: ['Nearest neighbors'] },
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

export default function SystemMapUI() {
    const [selectedNode, setSelectedNode] = useState(null)
    const [selectedLayerColor, setSelectedLayerColor] = useState(null)
    const [activeTrace, setActiveTrace] = useState(null)
    const [visibleLayers, setVisibleLayers] = useState(new Set())
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

    const allNodeIds = LAYERS.flatMap(l => l.nodes.map(n => n.id))
    const findLayerColor = (nodeId) => {
        for (const layer of LAYERS) {
            if (layer.nodes.some(n => n.id === nodeId)) return layer.color
        }
        return 'api'
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
                <p style={{ color: '#64748b', fontSize: 13, margin: 0 }}>
                    Interactive view of how every layer of TradeTalk connects — from customer input to final verdict.
                </p>
            </div>

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

            {/* Detail Panel */}
            <DetailPanel
                node={selectedNode}
                layerColor={selectedLayerColor || 'api'}
                onClose={() => { setSelectedNode(null); setSelectedLayerColor(null) }}
            />
        </div>
    )
}
