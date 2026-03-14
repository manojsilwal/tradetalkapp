import React, { useState } from 'react';
import { Play, Loader2, Bot, ShieldCheck, AlertCircle, CheckCircle2, XCircle, Clock, AlertTriangle, Database, ArrowRight, Zap, MessageSquare, TrendingUp, TrendingDown, Info, Bell, Filter, Search, Shield } from 'lucide-react';

// ── Agent metadata ──
const AGENTS = [
    { key: 'short_interest', label: 'Short Sellers', color: '#60a5fa', dataSource: 'yFinance API', dataDetail: 'Short Interest Ratio, Days to Cover, Short % of Float' },
    { key: 'social_sentiment', label: 'Social Sentiment', color: '#a78bfa', dataSource: 'Google News RSS', dataDetail: 'Blog headlines, YouTube video titles, keyword scoring' },
    { key: 'polymarket', label: 'Prediction Markets', color: '#fb923c', dataSource: 'Polymarket Gamma API', dataDetail: 'Active prediction events, outcome probabilities, volume' },
    { key: 'fundamentals', label: 'Financial Health', color: '#34d399', dataSource: 'yFinance API', dataDetail: 'Cash reserves, total debt, cash-to-debt ratio' },
];

const NOTIFICATION_TAB_INDEX = AGENTS.length; // 5th tab

export default function ObserverUI() {
    const [ticker, setTicker] = useState("GME");
    const [traceData, setTraceData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [activeAgent, setActiveAgent] = useState(0);
    const [notifTrace, setNotifTrace] = useState(null);
    const [notifLoading, setNotifLoading] = useState(false);

    const runTrace = async () => {
        setLoading(true);
        setError(null);
        setTraceData(null);
        setActiveAgent(0);
        try {
            const res = await fetch(`http://localhost:8000/trace?ticker=${ticker}`);
            if (!res.ok) throw new Error("Backend not reachable. Is uvicorn running?");
            setTraceData(await res.json());
        } catch (err) { setError(err.message); }
        finally { setLoading(false); }
    };

    const agent = AGENTS[activeAgent];
    const factor = traceData?.factors?.[agent?.key];

    return (
        <div className="consumer-container fade-in">
            {/* ── Header ── */}
            <div className="header-section">
                <div className="title-group">
                    <h2>AI Agent Trace Log</h2>
                    <p>Debug each agent's analysis step by step</p>
                </div>
                <div className="controls">
                    <input type="text" value={ticker} onChange={e => setTicker(e.target.value)}
                        placeholder="Ticker" style={{ width: '100px', textTransform: 'uppercase' }} />
                    <button onClick={runTrace} disabled={loading || !ticker}>
                        {loading ? <Loader2 className="spinner" size={18} /> : <Play size={18} />}
                        <span style={{ marginLeft: '6px' }}>{loading ? 'Tracing…' : 'Run Trace'}</span>
                    </button>
                </div>
            </div>

            {error && (
                <div style={{ padding: '12px 18px', borderRadius: '10px', background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.2)', color: 'var(--accent-red)', marginBottom: '20px', fontSize: '0.85rem' }}>
                    {error}
                </div>
            )}

            {/* ── Empty state ── */}
            {!traceData && !loading && (
                <div style={{ padding: '60px 32px', textAlign: 'center', background: 'rgba(255,255,255,0.02)', borderRadius: '16px', border: '1px dashed rgba(255,255,255,0.08)', marginTop: '32px' }}>
                    <Bot size={44} color="#60a5fa" style={{ marginBottom: '16px' }} />
                    <h3 style={{ margin: '0 0 8px', fontWeight: 600 }}>How does this work?</h3>
                    <p style={{ color: 'var(--text-muted)', maxWidth: '480px', margin: '0 auto', lineHeight: 1.7, fontSize: '0.88rem' }}>
                        Click <strong>Run Trace</strong> to deploy 4 AI agents. Each agent investigates a different signal, writes a report, and gets reviewed by a QA agent. You can then inspect each agent's reasoning one at a time.
                    </p>
                </div>
            )}

            {/* ── Agent Tabs ── */}
            {traceData && (
                <>
                    <div style={{ display: 'flex', gap: '6px', marginBottom: '24px', borderBottom: '1px solid rgba(255,255,255,0.06)', paddingBottom: '0' }}>
                        {AGENTS.map((a, i) => {
                            const f = traceData.factors?.[a.key];
                            const isActive = i === activeAgent;
                            const passed = f?.status === 'VERIFIED';
                            return (
                                <button key={a.key} onClick={() => setActiveAgent(i)} style={{
                                    flex: 1,
                                    padding: '14px 12px 16px',
                                    background: isActive ? 'rgba(255,255,255,0.05)' : 'transparent',
                                    border: 'none',
                                    borderBottom: isActive ? `2px solid ${a.color}` : '2px solid transparent',
                                    color: isActive ? '#fff' : 'var(--text-muted)',
                                    cursor: 'pointer',
                                    borderRadius: '8px 8px 0 0',
                                    transition: 'all 0.2s',
                                    display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                                    fontSize: '0.82rem', fontWeight: isActive ? 600 : 400,
                                }}>
                                    <span style={{
                                        width: '8px', height: '8px', borderRadius: '50%',
                                        background: f ? (passed ? 'var(--accent-green)' : 'var(--accent-red)') : 'var(--text-muted)',
                                        flexShrink: 0
                                    }} />
                                    {a.label}
                                </button>
                            );
                        })}
                        {/* Notification Agent Tab */}
                        <button onClick={async () => {
                            setActiveAgent(NOTIFICATION_TAB_INDEX);
                            if (!notifTrace) {
                                setNotifLoading(true);
                                try {
                                    const res = await fetch('http://localhost:8000/notifications/trace');
                                    setNotifTrace(await res.json());
                                } catch (e) { console.error(e); }
                                finally { setNotifLoading(false); }
                            }
                        }} style={{
                            flex: 1,
                            padding: '14px 12px 16px',
                            background: activeAgent === NOTIFICATION_TAB_INDEX ? 'rgba(255,255,255,0.05)' : 'transparent',
                            border: 'none',
                            borderBottom: activeAgent === NOTIFICATION_TAB_INDEX ? '2px solid #fb923c' : '2px solid transparent',
                            color: activeAgent === NOTIFICATION_TAB_INDEX ? '#fff' : 'var(--text-muted)',
                            cursor: 'pointer',
                            borderRadius: '8px 8px 0 0',
                            transition: 'all 0.2s',
                            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px',
                            fontSize: '0.82rem', fontWeight: activeAgent === NOTIFICATION_TAB_INDEX ? 600 : 400,
                        }}>
                            <Bell size={14} />
                            Notification Agent
                        </button>
                    </div>

                    {/* ── Full Agent Detail Screen ── */}
                    {activeAgent < NOTIFICATION_TAB_INDEX && factor && <AgentDetailScreen agent={agent} factor={factor} ticker={ticker} macroState={traceData.macro_state} />}

                    {/* ── Notification Trace Screen ── */}
                    {activeAgent === NOTIFICATION_TAB_INDEX && (
                        notifLoading ? (
                            <div style={{ textAlign: 'center', padding: '60px', color: 'var(--text-muted)' }}>
                                <Loader2 className="spinner" size={28} />
                                <p style={{ marginTop: '12px' }}>Scanning live macro headlines…</p>
                            </div>
                        ) : notifTrace ? (
                            <NotificationTraceScreen trace={notifTrace} />
                        ) : null
                    )}
                </>
            )}
        </div>
    );
}

// ══════════════════════════════════════════════════
//  Full-screen detail for one agent
// ══════════════════════════════════════════════════
function AgentDetailScreen({ agent, factor, ticker, macroState }) {
    const passed = factor.status === 'VERIFIED';
    const bullish = factor.trading_signal === 1;
    const iterations = Math.ceil(factor.history.length / 2);
    const warnings = extractWarnings(factor, macroState);

    return (
        <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

            {/* ── Row 1: Status + Confidence + Signal ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px' }}>
                <StatCard
                    label="Verification Status"
                    value={passed ? 'Passed' : 'Failed'}
                    sub={`after ${iterations} round${iterations > 1 ? 's' : ''}`}
                    color={passed ? 'var(--accent-green)' : 'var(--accent-red)'}
                    icon={passed ? <CheckCircle2 size={20} /> : <XCircle size={20} />}
                />
                <StatCard
                    label="Confidence Score"
                    value={`${Math.round(factor.confidence * 100)}%`}
                    sub={factor.confidence >= 0.8 ? 'High confidence' : factor.confidence >= 0.5 ? 'Moderate' : 'Low confidence'}
                    color={agent.color}
                    icon={<TrendingUp size={20} />}
                />
                <StatCard
                    label="Trading Signal"
                    value={bullish ? 'Bullish' : 'Neutral / Bearish'}
                    sub={bullish ? `Positive for ${ticker.toUpperCase()}` : `No buy signal for ${ticker.toUpperCase()}`}
                    color={bullish ? 'var(--accent-green)' : 'var(--text-muted)'}
                    icon={bullish ? <TrendingUp size={20} /> : <TrendingDown size={20} />}
                />
            </div>

            {/* ── Row 2: Data Source + Warnings ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                {/* Data Source */}
                <div style={{ padding: '20px 24px', borderRadius: '14px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.06)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <Database size={16} color={agent.color} />
                        <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>Data Source</span>
                    </div>
                    <div style={{ fontSize: '1rem', fontWeight: 600, color: '#fff', marginBottom: '4px' }}>{agent.dataSource}</div>
                    <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>{agent.dataDetail}</div>
                </div>

                {/* Warnings / Issues */}
                <div style={{ padding: '20px 24px', borderRadius: '14px', background: warnings.length > 0 ? 'rgba(251,146,60,0.04)' : 'rgba(255,255,255,0.025)', border: `1px solid ${warnings.length > 0 ? 'rgba(251,146,60,0.15)' : 'rgba(255,255,255,0.06)'}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                        <AlertTriangle size={16} color={warnings.length > 0 ? '#fb923c' : 'var(--text-muted)'} />
                        <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>
                            {warnings.length > 0 ? `${warnings.length} Issue${warnings.length > 1 ? 's' : ''} Detected` : 'No Issues'}
                        </span>
                    </div>
                    {warnings.length > 0 ? (
                        <ul style={{ margin: 0, padding: '0 0 0 16px', fontSize: '0.82rem', color: '#fb923c', lineHeight: 1.7 }}>
                            {warnings.map((w, i) => <li key={i}>{w}</li>)}
                        </ul>
                    ) : (
                        <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)' }}>This agent completed without any issues or conflicts.</div>
                    )}
                </div>
            </div>

            {/* ── Row 3: Conversation Timeline (full width) ── */}
            <div style={{ padding: '24px', borderRadius: '14px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.06)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' }}>
                    <MessageSquare size={16} color={agent.color} />
                    <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>Agent Conversation</span>
                    <span style={{ marginLeft: 'auto', fontSize: '0.7rem', color: 'var(--text-muted)' }}>{factor.history.length} messages · {iterations} round{iterations > 1 ? 's' : ''}</span>
                </div>

                <div style={{ position: 'relative', paddingLeft: '32px' }}>
                    {/* Vertical connector */}
                    <div style={{ position: 'absolute', left: '14px', top: '8px', bottom: '8px', width: '2px', background: 'rgba(255,255,255,0.06)', borderRadius: '2px' }} />

                    {factor.history.map((msg, idx) => {
                        const isAnalyst = msg.role.includes("Analyst");
                        const isLast = idx === factor.history.length - 1;
                        return (
                            <div key={idx} className="fade-in" style={{ position: 'relative', marginBottom: isLast ? 0 : '16px', animationDelay: `${idx * 0.08}s` }}>
                                {/* Timeline dot */}
                                <div style={{
                                    position: 'absolute', left: '-24px', top: '16px',
                                    width: '12px', height: '12px', borderRadius: '50%',
                                    background: isAnalyst ? agent.color : 'var(--accent-green)',
                                    border: '3px solid rgba(15,15,30,1)',
                                    zIndex: 1
                                }} />

                                {/* Step number */}
                                <div style={{
                                    position: 'absolute', left: '-60px', top: '14px',
                                    fontSize: '0.6rem', fontWeight: 700, color: 'rgba(255,255,255,0.2)',
                                    width: '24px', textAlign: 'right'
                                }}>
                                    {String(idx + 1).padStart(2, '0')}
                                </div>

                                {/* Message card */}
                                <div style={{
                                    padding: '16px 20px',
                                    borderRadius: '12px',
                                    background: isAnalyst ? 'rgba(255,255,255,0.03)' : 'rgba(16,185,129,0.03)',
                                    border: `1px solid ${isAnalyst ? 'rgba(255,255,255,0.06)' : 'rgba(16,185,129,0.1)'}`,
                                }}>
                                    {/* Role tag */}
                                    <div style={{
                                        display: 'inline-flex', alignItems: 'center', gap: '6px',
                                        padding: '3px 10px', borderRadius: '6px', marginBottom: '10px',
                                        background: isAnalyst ? `${agent.color}15` : 'rgba(16,185,129,0.1)',
                                        color: isAnalyst ? agent.color : 'var(--accent-green)',
                                        fontSize: '0.65rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em'
                                    }}>
                                        {isAnalyst ? <Bot size={12} /> : <ShieldCheck size={12} />}
                                        {isAnalyst ? '🔍 Analyst' : '✅ QA Reviewer'}
                                    </div>

                                    <p style={{ margin: 0, fontSize: '0.88rem', lineHeight: 1.7, color: 'rgba(255,255,255,0.75)' }}>
                                        {msg.content}
                                    </p>
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>

            {/* ── Row 4: Final Rationale ── */}
            <div style={{ padding: '20px 24px', borderRadius: '14px', background: passed ? 'rgba(16,185,129,0.04)' : 'rgba(239,68,68,0.04)', border: `1px solid ${passed ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.12)'}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                    <Info size={16} color={passed ? 'var(--accent-green)' : 'var(--accent-red)'} />
                    <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>Final System Rationale</span>
                </div>
                <p style={{ margin: 0, fontSize: '0.88rem', lineHeight: 1.7, color: 'rgba(255,255,255,0.75)' }}>
                    {factor.rationale}
                </p>
            </div>
        </div>
    );
}

// ══════════════════════════════════════════════════
//  Notification Agent Trace Screen
// ══════════════════════════════════════════════════
function NotificationTraceScreen({ trace }) {
    const urgencyColor = (u) => u >= 8 ? '#ef4444' : u >= 6 ? '#fb923c' : '#60a5fa';

    return (
        <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>

            {/* ── Row 1: Pipeline Summary ── */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '16px' }}>
                <StatCard label="Headlines Scanned" value={trace.total_scanned} sub="From Google News RSS" color="#60a5fa" icon={<Search size={20} />} />
                <StatCard label="Passed Filter" value={trace.passed_filter} sub={`NotificationAgent threshold ≥ 5`} color="var(--accent-green)" icon={<CheckCircle2 size={20} />} />
                <StatCard label="Rejected" value={trace.rejected} sub="Below importance threshold" color="var(--accent-red)" icon={<XCircle size={20} />} />
                <StatCard label="Alerts Produced" value={trace.alerts_produced} sub="Verified by AnalystAgent" color="#fb923c" icon={<Bell size={20} />} />
            </div>

            {/* ── Stored Alerts (persisted) ── */}
            {trace.stored_alerts?.length > 0 && (
                <div style={{ padding: '20px 24px', borderRadius: '14px', background: 'rgba(251,146,60,0.04)', border: '1px solid rgba(251,146,60,0.12)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
                        <Database size={16} color="#fb923c" />
                        <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>
                            Active Alerts in Store ({trace.stored_alerts.length})
                        </span>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        {trace.stored_alerts.map(a => (
                            <div key={a.id} style={{
                                padding: '12px 16px', borderRadius: '10px',
                                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.06)',
                                display: 'flex', alignItems: 'center', gap: '12px',
                            }}>
                                <div style={{
                                    width: '10px', height: '10px', borderRadius: '50%',
                                    background: urgencyColor(a.urgency), flexShrink: 0,
                                    boxShadow: a.urgency >= 8 ? `0 0 8px ${urgencyColor(a.urgency)}40` : 'none'
                                }} />
                                <div style={{ flex: 1, minWidth: 0 }}>
                                    <div style={{ fontSize: '0.82rem', fontWeight: 600, color: '#fff', marginBottom: '3px' }}>{a.title}</div>
                                    <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' }}>
                                        <span style={{ fontSize: '0.6rem', padding: '2px 8px', borderRadius: '6px', background: `${urgencyColor(a.urgency)}15`, color: urgencyColor(a.urgency), fontWeight: 600, textTransform: 'uppercase' }}>
                                            Urgency {a.urgency}/10 · {a.urgency_label}
                                        </span>
                                        <span style={{
                                            fontSize: '0.6rem', padding: '2px 8px', borderRadius: '6px',
                                            background: a.source_reliability === 'high' ? 'rgba(16,185,129,0.1)' : 'rgba(255,255,255,0.05)',
                                            color: a.source_reliability === 'high' ? 'var(--accent-green)' : 'var(--text-muted)',
                                            display: 'flex', alignItems: 'center', gap: '3px'
                                        }}>
                                            <Shield size={9} /> {a.source} ({a.source_reliability})
                                        </span>
                                        {a.affected_sectors?.map(s => (
                                            <span key={s} style={{ fontSize: '0.55rem', padding: '2px 7px', borderRadius: '8px', background: 'rgba(96,165,250,0.1)', color: '#60a5fa' }}>{s}</span>
                                        ))}
                                    </div>
                                </div>
                                <span style={{ fontSize: '0.6rem', color: a.is_read ? 'var(--accent-green)' : '#fb923c', fontWeight: 600 }}>
                                    {a.is_read ? 'SEEN' : 'UNSEEN'}
                                </span>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {/* ── Pipeline Analysis: Per-headline trace ── */}
            <div style={{ padding: '24px', borderRadius: '14px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.06)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' }}>
                    <Filter size={16} color="#fb923c" />
                    <span style={{ fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>
                        Pipeline Analysis — Headline by Headline
                    </span>
                    <span style={{ marginLeft: 'auto', fontSize: '0.7rem', color: 'var(--text-muted)' }}>{trace.headlines?.length || 0} processed</span>
                </div>

                <div style={{ position: 'relative', paddingLeft: '32px' }}>
                    <div style={{ position: 'absolute', left: '14px', top: '8px', bottom: '8px', width: '2px', background: 'rgba(255,255,255,0.06)', borderRadius: '2px' }} />

                    {(trace.headlines || []).map((h, idx) => {
                        const na = h.notification_agent;
                        const aa = h.analyst_agent;
                        const passed = na?.passed;
                        return (
                            <div key={idx} className="fade-in" style={{ position: 'relative', marginBottom: '16px', animationDelay: `${idx * 0.05}s` }}>
                                {/* Timeline dot */}
                                <div style={{
                                    position: 'absolute', left: '-24px', top: '16px',
                                    width: '12px', height: '12px', borderRadius: '50%',
                                    background: passed ? 'var(--accent-green)' : 'var(--accent-red)',
                                    border: '3px solid rgba(15,15,30,1)', zIndex: 1
                                }} />
                                <div style={{
                                    position: 'absolute', left: '-60px', top: '14px',
                                    fontSize: '0.6rem', fontWeight: 700, color: 'rgba(255,255,255,0.2)', width: '24px', textAlign: 'right'
                                }}>{String(idx + 1).padStart(2, '0')}</div>

                                <div style={{
                                    padding: '16px 20px', borderRadius: '12px',
                                    background: passed ? 'rgba(16,185,129,0.03)' : 'rgba(239,68,68,0.03)',
                                    border: `1px solid ${passed ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.08)'}`,
                                }}>
                                    {/* Title + Source */}
                                    <div style={{ fontSize: '0.85rem', fontWeight: 600, color: '#fff', marginBottom: '6px', lineHeight: 1.4 }}>
                                        {h.title}
                                    </div>
                                    <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginBottom: '12px' }}>
                                        Source: {h.source}
                                    </div>

                                    {/* Stage 1: NotificationAgent */}
                                    <div style={{ marginBottom: aa ? '12px' : 0 }}>
                                        <div style={{
                                            display: 'inline-flex', alignItems: 'center', gap: '6px',
                                            padding: '3px 10px', borderRadius: '6px', marginBottom: '8px',
                                            background: '#fb923c15', color: '#fb923c',
                                            fontSize: '0.6rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em'
                                        }}>
                                            <Bot size={11} /> Stage 1: NotificationAgent
                                        </div>
                                        <div style={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.7)', lineHeight: 1.7 }}>
                                            <div style={{ marginBottom: '4px' }}>
                                                <strong style={{ color: '#fff' }}>Score: {na?.final_score}/10</strong>
                                                <span style={{ marginLeft: '8px', fontSize: '0.7rem' }}>
                                                    (threshold: ≥{na?.threshold})
                                                </span>
                                            </div>
                                            <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'monospace', marginBottom: '6px' }}>
                                                {na?.reasoning}
                                            </div>
                                            {/* Score bar */}
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
                                                <div style={{ flex: 1, height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.06)' }}>
                                                    <div style={{ width: `${(na?.final_score || 0) * 10}%`, height: '100%', borderRadius: '3px', background: passed ? 'var(--accent-green)' : 'var(--accent-red)', transition: 'width 0.5s' }} />
                                                </div>
                                                <span style={{ fontSize: '0.65rem', color: passed ? 'var(--accent-green)' : 'var(--accent-red)', fontWeight: 600 }}>
                                                    {passed ? 'PASSED' : 'REJECTED'}
                                                </span>
                                            </div>
                                            {/* Keyword matches */}
                                            <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' }}>
                                                {(na?.breaking_keywords || []).map((kw, i) => (
                                                    <span key={`b-${i}`} style={{ fontSize: '0.55rem', padding: '1px 7px', borderRadius: '8px', background: 'rgba(239,68,68,0.1)', color: '#ef4444' }}>🚨 {kw}</span>
                                                ))}
                                                {(na?.high_impact_keywords || []).map((kw, i) => (
                                                    <span key={`h-${i}`} style={{ fontSize: '0.55rem', padding: '1px 7px', borderRadius: '8px', background: 'rgba(251,146,60,0.1)', color: '#fb923c' }}>⚡ {kw}</span>
                                                ))}
                                                {(na?.moderate_impact_keywords || []).map((kw, i) => (
                                                    <span key={`m-${i}`} style={{ fontSize: '0.55rem', padding: '1px 7px', borderRadius: '8px', background: 'rgba(96,165,250,0.1)', color: '#60a5fa' }}>📊 {kw}</span>
                                                ))}
                                            </div>
                                        </div>
                                    </div>

                                    {/* Stage 2: AnalystAgent (only if passed) */}
                                    {aa && (
                                        <div style={{ paddingTop: '12px', borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                                            <div style={{
                                                display: 'inline-flex', alignItems: 'center', gap: '6px',
                                                padding: '3px 10px', borderRadius: '6px', marginBottom: '8px',
                                                background: 'rgba(16,185,129,0.1)', color: 'var(--accent-green)',
                                                fontSize: '0.6rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em'
                                            }}>
                                                <ShieldCheck size={11} /> Stage 2: AnalystAgent
                                            </div>
                                            <div style={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.7)', lineHeight: 1.7 }}>
                                                <div style={{ marginBottom: '6px' }}>
                                                    Source: <strong style={{ color: aa.reliability === 'high' ? 'var(--accent-green)' : aa.reliability === 'medium' ? '#fb923c' : 'var(--text-muted)' }}>
                                                        {aa.source_checked} ({aa.reliability} trust — {Math.round(aa.reliability_score * 100)}%)
                                                    </strong>
                                                </div>
                                                <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginBottom: '6px' }}>
                                                    {aa.affected_sectors?.map(s => (
                                                        <span key={s} style={{ fontSize: '0.55rem', padding: '2px 8px', borderRadius: '8px', background: 'rgba(96,165,250,0.1)', color: '#60a5fa', fontWeight: 500 }}>{s}</span>
                                                    ))}
                                                </div>
                                                <div style={{ fontSize: '0.72rem', fontFamily: 'monospace', color: 'var(--accent-green)' }}>
                                                    → {aa.conclusion}
                                                </div>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            </div>
        </div>
    );
}

// ── Stat card mini-component ──
function StatCard({ label, value, sub, color, icon }) {
    return (
        <div style={{ padding: '20px 24px', borderRadius: '14px', background: 'rgba(255,255,255,0.025)', border: '1px solid rgba(255,255,255,0.06)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <div style={{ color }}>{icon}</div>
                <span style={{ fontSize: '0.65rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted)' }}>{label}</span>
            </div>
            <div style={{ fontSize: '1.3rem', fontWeight: 700, color, marginBottom: '2px' }}>{value}</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>{sub}</div>
        </div>
    );
}

// ── Extract potential issues from the trace ──
function extractWarnings(factor, macroState) {
    const w = [];
    // QA rejections
    const qaRejections = factor.history.filter(m => m.role.includes('QA') && (m.content.includes('incomplete') || m.content.includes('Refusing') || m.content.includes('fails')));
    if (qaRejections.length > 0) w.push(`QA reviewer rejected ${qaRejections.length} report(s) — agent had to revise`);
    // Macro stress
    if (macroState?.credit_stress_index > 1.5) w.push(`High macro stress (${macroState.credit_stress_index}) may have influenced rejection`);
    // Low confidence
    if (factor.confidence < 0.5) w.push('Low confidence score — analysis may be unreliable');
    // Multiple iterations
    if (factor.history.length > 2) w.push(`Required ${Math.ceil(factor.history.length / 2)} rounds (typical is 1) — initial analysis was insufficient`);
    // Factor rejected
    if (factor.status === 'REJECTED') w.push('Agent could not reach VERIFIED status — result was overridden');
    return w;
}
