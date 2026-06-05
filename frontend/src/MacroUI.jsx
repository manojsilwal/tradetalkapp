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


const REGIME_IMPACT_MATRIX = {
    BULL_NORMAL: {
        favor: 'Technology, Discretionary, Industrials',
        avoid: 'Utilities, Staples (relative underperformance vs cyclicals)',
    },
    BULL_EXCESS: {
        favor: 'Quality growth, Large-cap, Mega-cap tech',
        avoid: 'Speculative small-caps, unprofitable growth',
    },
    BEAR_NORMAL: {
        favor: 'Staples, Healthcare, Quality dividends',
        avoid: 'High-beta cyclicals, Discretionary',
    },
    BEAR_STRESS: {
        favor: 'Utilities, Staples, Gold / defensive',
        avoid: 'Financials, Cyclicals, High leverage',
    },
    K_SHAPE_DIVERGENCE: {
        favor: 'Luxury, Premium brands, Large-cap tech',
        avoid: 'Lower-tier Consumer, Regional banks',
    },
};

export default function MacroUI() {
    const { macroState, loadMacro, setMacroFlowPeriod } = useAnalysisHistory();
    const { data, loading, error, flowPeriod } = macroState;
    const [showExplanation, setShowExplanation] = useState(false);

    useEffect(() => {
        loadMacro();
    }, [loadMacro]);

    const setFlowPeriod = setMacroFlowPeriod;

    if (loading) {
        return (
            <div className="consumer-container fade-in" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
                <Loader2 className="spinner" size={48} color="var(--accent-blue)" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="consumer-container fade-in">
                <div className="error-banner glass-panel" style={{ borderColor: 'var(--accent-red)' }}>
                    <p style={{ color: 'var(--accent-red)', padding: '10px', margin: 0 }}>{error}</p>
                </div>
            </div>
        );
    }

    if (!data) return null;

    const isStress = data.credit_stress_index > 1.1;

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

            {/* Top Level KPIs (Scaled Down) */}
            <div className="dashboard-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '16px', marginBottom: '16px' }}>
                <div className="dash-card glass-panel fade-in" data-testid="macro-vix-card" style={{ padding: '16px', borderRadius: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                        <Globe color="var(--accent-blue)" size={18} />
                        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>CBOE ^VIX Volatility</h3>
                    </div>
                    <h1 data-testid="macro-vix-value" style={{ fontSize: '2rem', margin: 0, fontWeight: 800 }}>{data.vix_level}</h1>
                    <p style={{ color: 'var(--text-muted)', margin: '6px 0 0 0', fontSize: '0.78rem' }}>Market Expectation of near-term risk</p>
                </div>

                <div className="dash-card glass-panel fade-in" data-testid="macro-consumer-spending-chart" style={{ padding: '16px', borderRadius: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                        <Wallet color="var(--accent-green)" size={18} />
                        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>Total Cash Reserves</h3>
                    </div>
                    <h1 style={{ fontSize: '2rem', margin: 0, fontWeight: 800 }}>
                        ${data.cash_reserves && data.cash_reserves.length > 0
                            ? (data.cash_reserves[data.cash_reserves.length - 1].institutional_cash + data.cash_reserves[data.cash_reserves.length - 1].retail_cash).toFixed(2)
                            : '0.00'}T
                    </h1>
                    <p style={{ color: 'var(--text-muted)', margin: '6px 0 0 0', fontSize: '0.78rem' }}>Sitting on the sidelines</p>
                </div>

                <div className="dash-card glass-panel fade-in" data-testid="macro-cash-reserves-chart" style={{ padding: '16px', borderRadius: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
                        {isStress ? <AlertTriangle color="var(--accent-red)" size={18} /> : <TrendingUp color="var(--accent-green)" size={18} />}
                        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>Market Regime</h3>
                    </div>
                    <h1 style={{ fontSize: '1.6rem', margin: 0, fontWeight: 800, color: isStress ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                        {data.market_regime.replace('_', ' ')}
                    </h1>
                    <p style={{ color: 'var(--text-muted)', margin: '6px 0 0 0', fontSize: '0.78rem' }}>Stress Index: {data.credit_stress_index}</p>
                </div>
            </div>

            {/* Middle Grid: Charts (Scaled Down) */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '16px', marginBottom: '24px' }}>

                {/* Spending Chart */}
                <div className="dash-card glass-panel fade-in" style={{ padding: '16px', borderRadius: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
                        <DollarSign color="var(--accent-purple)" size={18} />
                        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>Historic Consumer Spending Index</h3>
                    </div>
                    <div style={{ width: '100%', height: '220px' }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={data.consumer_spending}>
                                <defs>
                                    <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="var(--accent-purple)" stopOpacity={0.8} />
                                        <stop offset="95%" stopColor="var(--accent-purple)" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                                <XAxis dataKey="month" stroke="var(--text-muted)" tickMargin={10} style={{ fontSize: '0.75rem' }} />
                                <YAxis stroke="var(--text-muted)" domain={['dataMin - 2', 'dataMax + 2']} style={{ fontSize: '0.75rem' }} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px' }}
                                    itemStyle={{ color: '#fff' }}
                                />
                                <Area type="monotone" dataKey="value" stroke="var(--accent-purple)" fillOpacity={1} fill="url(#colorValue)" strokeWidth={2.5} />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Cash Reserves Stacked Bar Chart */}
                <div className="dash-card glass-panel fade-in" style={{ padding: '16px', borderRadius: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
                        <Wallet color="var(--accent-green)" size={18} />
                        <h3 style={{ margin: 0, fontSize: '0.92rem' }}>Cash on the Sidelines (Trillions USD)</h3>
                    </div>
                    <div style={{ width: '100%', height: '220px' }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={data.cash_reserves}>
                                <defs>
                                    <linearGradient id="colorInst" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="var(--accent-blue)" stopOpacity={0.8} />
                                        <stop offset="95%" stopColor="var(--accent-blue)" stopOpacity={0} />
                                    </linearGradient>
                                    <linearGradient id="colorRet" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="var(--accent-green)" stopOpacity={0.8} />
                                        <stop offset="95%" stopColor="var(--accent-green)" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                                <XAxis dataKey="month" stroke="var(--text-muted)" tickMargin={10} style={{ fontSize: '0.75rem' }} />
                                <YAxis stroke="var(--text-muted)" domain={[0, 7]} style={{ fontSize: '0.75rem' }} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px' }}
                                    itemStyle={{ color: '#fff' }}
                                />
                                <Area type="monotone" dataKey="institutional_cash" stackId="1" stroke="var(--accent-blue)" fill="url(#colorInst)" strokeWidth={2} name="Institutional Cash" />
                                <Area type="monotone" dataKey="retail_cash" stackId="1" stroke="var(--accent-green)" fill="url(#colorRet)" strokeWidth={2} name="Retail Cash" />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </div>

            </div>

            {/* Thematic flow & regime */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '24px', marginBottom: '24px' }}>

                <MacroFlowPanel />

                {/* Regime impact matrix — maps current regime to favored / avoided sector themes */}
                <div className="dash-card glass-panel fade-in" data-testid="macro-regime-impact-matrix" style={{ padding: '24px', borderRadius: '16px', display: 'flex', flexDirection: 'column' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                        <TrendingUp color="var(--accent-green)" />
                        <div>
                            <h3 style={{ margin: 0 }}>Regime impact matrix</h3>
                            <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>
                                How the current macro label historically maps to sector winners and laggards (heuristic).
                            </p>
                        </div>
                    </div>
                    <div style={{ overflowX: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
                            <thead>
                                <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', textAlign: 'left' }}>
                                    <th style={{ padding: '10px 8px', color: 'var(--text-muted)' }}>Regime</th>
                                    <th style={{ padding: '10px 8px', color: 'var(--accent-green)' }}>Favor</th>
                                    <th style={{ padding: '10px 8px', color: 'var(--accent-red)' }}>Avoid / lag</th>
                                </tr>
                            </thead>
                            <tbody>
                                {Object.entries(REGIME_IMPACT_MATRIX).map(([regime, row]) => {
                                    const active = regime === data.market_regime;
                                    return (
                                        <tr
                                            key={regime}
                                            style={{
                                                borderBottom: '1px solid rgba(255,255,255,0.06)',
                                                background: active ? 'rgba(124, 58, 237, 0.12)' : 'transparent',
                                            }}
                                        >
                                            <td style={{ padding: '12px 8px', fontWeight: active ? 700 : 500, whiteSpace: 'nowrap' }}>
                                                {regime.replace(/_/g, ' ')}
                                                {active && (
                                                    <span style={{ marginLeft: 8, fontSize: '0.7rem', color: 'var(--accent-purple)' }}>(current)</span>
                                                )}
                                            </td>
                                            <td style={{ padding: '12px 8px', color: 'var(--text-muted)', maxWidth: 280 }}>{row.favor}</td>
                                            <td style={{ padding: '12px 8px', color: 'var(--text-muted)', maxWidth: 280 }}>{row.avoid}</td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>

            </div>

        </div>
    );
}
