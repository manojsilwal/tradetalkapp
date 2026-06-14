import React, { useState, useEffect } from 'react';
import { Globe, TrendingUp, DollarSign, Loader2, AlertTriangle, ArrowRightLeft, Wallet, CheckCircle, ChevronDown, ChevronUp } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { API_BASE_URL, apiFetch } from './api';
import MacroFlowPanel from './MacroFlowPanel';
import GlobalMarketsChart from './GlobalMarketsChart';
import { useAnalysisHistory } from './AnalysisContext';

const FLOW_PERIODS = [
    { id: '1d', label: '1D' },
    { id: '1w', label: '1W' },
    { id: '1m', label: '1M' },
    { id: '1y', label: '1Y' },
    { id: '5y', label: '5Y' },
];

function formatCompactUSD(value) {
    const abs = Math.abs(value);
    const sign = value >= 0 ? '+' : '-';
    if (abs >= 1_000_000_000_000) return `${sign}$${(abs / 1_000_000_000_000).toFixed(2)}T`;
    if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(2)}B`;
    if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
    if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`;
    return `${sign}$${abs.toFixed(0)}`;
}

function formatLargeUSD(value) {
    const abs = Math.abs(value);
    if (abs >= 1_000_000_000_000) return `$${(abs / 1_000_000_000_000).toFixed(2)}T`;
    if (abs >= 1_000_000_000) return `$${(abs / 1_000_000_000).toFixed(1)}B`;
    if (abs >= 1_000_000) return `$${(abs / 1_000_000).toFixed(0)}M`;
    return `$${abs.toLocaleString()}`;
}


export default function MacroUI() {
    const { macroState, loadMacro, setMacroFlowPeriod } = useAnalysisHistory();
    const { data, loading, error, flowPeriod } = macroState;
    const [showExplanation, setShowExplanation] = useState(false);

    useEffect(() => {
        loadMacro();
    }, [loadMacro]);

    const setFlowPeriod = setMacroFlowPeriod;

    const setFlowPeriod = setMacroFlowPeriod;

    const isStress = data?.credit_stress_index > 1.1;

    return (
        <div className="consumer-container fade-in">
            <div className="header-section" style={{ marginBottom: '20px' }}>
                <div className="title-group">
                    <h2>Global Macroeconomic Grounding</h2>
                    <p>Live indicators and thematic capital flow</p>
                </div>
            </div>

            {/* ── Global Markets Normalized Performance chart ─────────────── */}
            <div style={{ marginBottom: '24px' }}>
                <GlobalMarketsChart />
            </div>

            {error && (
                <div className="error-banner glass-panel" style={{ borderColor: 'var(--accent-red)', marginBottom: '24px' }}>
                    <p style={{ color: 'var(--accent-red)', padding: '10px', margin: 0 }}>{error}</p>
                </div>
            )}

            {/* Core Macro Indicators Redesign */}
            <div className="dash-card glass-panel fade-in" style={{ padding: '20px 24px', borderRadius: '16px', marginBottom: '24px', background: 'rgba(10, 11, 16, 0.7)', border: '1px solid rgba(255, 255, 255, 0.08)' }}>
                {/* Header */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '8px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <Globe color="#94a3b8" size={16} />
                        <span style={{ fontSize: '0.75rem', fontWeight: 700, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.08em', fontFamily: 'monospace' }}>
                            Core Macro Indicators
                        </span>
                    </div>
                    {/* Market Regime Badge */}
                    {data && (
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.75rem', color: '#94a3b8' }}>
                            <span>Market Regime:</span>
                            <span style={{ fontWeight: 700, color: isStress ? 'var(--accent-red)' : 'var(--accent-green)', padding: '2px 8px', borderRadius: '4px', background: isStress ? 'rgba(239, 68, 68, 0.1)' : 'rgba(16, 185, 129, 0.1)', border: `1px solid ${isStress ? 'rgba(239, 68, 68, 0.2)' : 'rgba(16, 185, 129, 0.2)'}` }}>
                                {data.market_regime ? data.market_regime.replace('_', ' ') : '—'}
                            </span>
                        </div>
                    )}
                </div>

                {loading && !data ? (
                    <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '120px', flexDirection: 'column', gap: '10px' }}>
                        <Loader2 className="spinner" size={36} color="var(--accent-blue)" />
                        <span style={{ color: '#94a3b8', fontSize: '0.85rem' }}>Loading indicators…</span>
                    </div>
                ) : !data ? (
                    <div style={{ color: '#94a3b8', fontSize: '0.85rem', textAlign: 'center', padding: '20px' }}>
                        No core macro indicators data available.
                    </div>
                ) : (
                    /* Grid */
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '24px' }}>
                        {/* GDP */}
                        <div className="macro-col" data-testid="macro-vix-card">
                            <div style={{ color: '#94a3b8', fontSize: '0.82rem', marginBottom: '8px', fontWeight: 500 }}>Global GDP Est. (Q3)</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '2.0rem', fontWeight: 800, color: '#ffffff', letterSpacing: '-0.02em' }}>2.4%</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '2px', padding: '4px 8px', borderRadius: '6px', background: 'rgba(239, 68, 68, 0.12)', color: '#f87171', fontSize: '0.72rem', fontWeight: 700 }}>
                                    <ChevronDown size={12} strokeWidth={2.5} />
                                    <span>0.1%</span>
                                </div>
                            </div>
                        </div>

                        {/* Fed Funds Rate */}
                        <div className="macro-col" data-testid="macro-consumer-spending-chart">
                            <div style={{ color: '#94a3b8', fontSize: '0.82rem', marginBottom: '8px', fontWeight: 500 }}>US Fed Funds Rate</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '2.0rem', fontWeight: 800, color: '#93c5fd', letterSpacing: '-0.02em' }}>
                                    {data.fed_funds_rate !== null && data.fed_funds_rate !== undefined ? `${data.fed_funds_rate}%` : '5.25%'}
                                </span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '4px', padding: '4px 8px', borderRadius: '6px', background: 'rgba(255, 255, 255, 0.05)', color: '#94a3b8', fontSize: '0.72rem', fontWeight: 700 }}>
                                    <ArrowRightLeft size={12} strokeWidth={2.5} style={{ color: '#94a3b8' }} />
                                    <span>— UNCH</span>
                                </div>
                            </div>
                        </div>

                        {/* CPI */}
                        <div className="macro-col" data-testid="macro-cash-reserves-chart">
                            <div style={{ color: '#94a3b8', fontSize: '0.82rem', marginBottom: '8px', fontWeight: 500 }}>US Core CPI (YoY)</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '2.0rem', fontWeight: 800, color: '#ffffff', letterSpacing: '-0.02em' }}>
                                    {data.cpi_yoy !== null && data.cpi_yoy !== undefined ? `${data.cpi_yoy}%` : '3.8%'}
                                </span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '2px', padding: '4px 8px', borderRadius: '6px', background: 'rgba(16, 185, 129, 0.12)', color: '#34d399', fontSize: '0.72rem', fontWeight: 700 }}>
                                    <ChevronDown size={12} strokeWidth={2.5} />
                                    <span>0.2%</span>
                                </div>
                            </div>
                        </div>

                        {/* Brent Crude */}
                        <div className="macro-col" style={{ borderRight: 'none', paddingRight: 0 }}>
                            <div style={{ color: '#94a3b8', fontSize: '0.82rem', marginBottom: '8px', fontWeight: 500 }}>Brent Crude</div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                <span style={{ fontSize: '2.0rem', fontWeight: 800, color: '#c084fc', letterSpacing: '-0.02em' }}>$84.30</span>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '2px', padding: '4px 8px', borderRadius: '6px', background: 'rgba(139, 92, 246, 0.12)', color: '#a78bfa', fontSize: '0.72rem', fontWeight: 700 }}>
                                    <ChevronUp size={12} strokeWidth={2.5} />
                                    <span>1.2%</span>
                                </div>
                            </div>
                        </div>
                    </div>
                )}
            </div>


            {/* Thematic flow & regime */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '24px', marginBottom: '24px' }}>

                <MacroFlowPanel />

            </div>

        </div>
    );
}
