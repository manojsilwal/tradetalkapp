import React, { useState, useEffect } from 'react';
import { Globe, TrendingUp, TrendingDown, DollarSign, Loader2, AlertTriangle, ArrowRightLeft, Wallet } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts';
import { API_BASE_URL } from './api';

export default function MacroUI() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    useEffect(() => {
        const fetchMacro = async () => {
            try {
                const res = await fetch(`${API_BASE_URL}/macro`);
                if (!res.ok) throw new Error("Failed to fetch Global Macro data.");
                const json = await res.json();
                setData(json);
            } catch (err) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        };
        fetchMacro();
    }, []);

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
            <div className="header-section" style={{ marginBottom: '24px' }}>
                <div className="title-group">
                    <h2>Global Macroeconomic Grounding</h2>
                    <p>Live Indicators & Sector Rotation</p>
                </div>
            </div>

            {/* Top Level KPIs */}
            <div className="dashboard-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '24px', marginBottom: '24px' }}>
                <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                        <Globe color="var(--accent-blue)" />
                        <h3 style={{ margin: 0 }}>CBOE ^VIX Volatility</h3>
                    </div>
                    <h1 style={{ fontSize: '2.5rem', margin: 0 }}>{data.vix_level}</h1>
                    <p style={{ color: 'var(--text-muted)', margin: '8px 0 0 0' }}>Market Expectation of near-term risk</p>
                </div>

                <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                        <Wallet color="var(--accent-green)" />
                        <h3 style={{ margin: 0 }}>Total Cash Reserves</h3>
                    </div>
                    <h1 style={{ fontSize: '2.5rem', margin: 0 }}>
                        ${data.cash_reserves && data.cash_reserves.length > 0
                            ? (data.cash_reserves[data.cash_reserves.length - 1].institutional_cash + data.cash_reserves[data.cash_reserves.length - 1].retail_cash).toFixed(2)
                            : '0.00'}T
                    </h1>
                    <p style={{ color: 'var(--text-muted)', margin: '8px 0 0 0' }}>Sitting on the sidelines</p>
                </div>

                <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                        {isStress ? <AlertTriangle color="var(--accent-red)" /> : <TrendingUp color="var(--accent-green)" />}
                        <h3 style={{ margin: 0 }}>Market Regime</h3>
                    </div>
                    <h1 style={{ fontSize: '1.8rem', margin: 0, color: isStress ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                        {data.market_regime.replace('_', ' ')}
                    </h1>
                    <p style={{ color: 'var(--text-muted)', margin: '8px 0 0 0' }}>Stress Index: {data.credit_stress_index}</p>
                </div>
            </div>

            {/* Middle Grid: Charts */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px', marginBottom: '24px' }}>

                {/* Spending Chart */}
                <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                        <DollarSign color="var(--accent-purple)" />
                        <h3 style={{ margin: 0 }}>Historic Consumer Spending Index</h3>
                    </div>
                    <div style={{ width: '100%', height: '300px' }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <AreaChart data={data.consumer_spending}>
                                <defs>
                                    <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor="var(--accent-purple)" stopOpacity={0.8} />
                                        <stop offset="95%" stopColor="var(--accent-purple)" stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                                <XAxis dataKey="month" stroke="var(--text-muted)" tickMargin={10} />
                                <YAxis stroke="var(--text-muted)" domain={['dataMin - 2', 'dataMax + 2']} />
                                <Tooltip
                                    contentStyle={{ backgroundColor: 'rgba(15, 23, 42, 0.9)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px' }}
                                    itemStyle={{ color: '#fff' }}
                                />
                                <Area type="monotone" dataKey="value" stroke="var(--accent-purple)" fillOpacity={1} fill="url(#colorValue)" strokeWidth={3} />
                            </AreaChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Cash Reserves Stacked Bar Chart */}
                <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                        <Wallet color="var(--accent-green)" />
                        <h3 style={{ margin: 0 }}>Cash on the Sidelines (Trillions USD)</h3>
                    </div>
                    <div style={{ width: '100%', height: '300px' }}>
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
                                <XAxis dataKey="month" stroke="var(--text-muted)" tickMargin={10} />
                                <YAxis stroke="var(--text-muted)" domain={[0, 7]} />
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

            {/* Bottom Grid: Sectors */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '24px', marginBottom: '24px' }}>

                {/* Sector Rotation */}
                <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px', display: 'flex', flexDirection: 'column' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                        <TrendingUp color="var(--accent-orange)" />
                        <h3 style={{ margin: 0 }}>Live Sector Rotation</h3>
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', flex: 1, justifyContent: 'space-around' }}>
                        {data.sectors.map(sector => (
                            <div key={sector.symbol} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px', background: 'rgba(255,255,255,0.02)', borderRadius: '8px' }}>
                                <div>
                                    <strong style={{ display: 'block', fontSize: '1.1rem' }}>{sector.name}</strong>
                                    <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>{sector.symbol}</span>
                                </div>
                                <div style={{
                                    color: sector.daily_change_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                                    fontWeight: 'bold',
                                    fontSize: '1.2rem'
                                }}>
                                    {sector.daily_change_pct > 0 ? '+' : ''}{sector.daily_change_pct}%
                                </div>
                            </div>
                        ))}
                    </div>
                </div>

            </div>

            {/* Global Capital Flows (Phase 10) */}
            <div className="dash-card glass-panel fade-in" style={{ padding: '24px', borderRadius: '16px', marginTop: '24px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px' }}>
                    <ArrowRightLeft color="var(--accent-blue)" />
                    <div>
                        <h3 style={{ margin: 0 }}>Global Capital Flows</h3>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>Track money moving out of the USA and into Japan or Safe Havens.</p>
                    </div>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px' }}>
                    {data.capital_flows.map(flow => (
                        <div key={flow.asset} style={{
                            padding: '16px',
                            background: 'rgba(255,255,255,0.03)',
                            borderRadius: '12px',
                            borderLeft: `4px solid ${flow.daily_change_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)'}`
                        }}>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                                <div>
                                    <strong style={{ display: 'block', fontSize: '1.2rem' }}>{flow.asset}</strong>
                                    <span style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>{flow.category}</span>
                                </div>
                                <div style={{
                                    color: flow.daily_change_pct >= 0 ? 'var(--accent-green)' : 'var(--accent-red)',
                                    fontWeight: 'bold',
                                    fontSize: '1.3rem'
                                }}>
                                    {flow.daily_change_pct > 0 ? '+' : ''}{flow.daily_change_pct}%
                                </div>
                            </div>
                            <div style={{ fontSize: '0.9rem' }}>{flow.name}</div>
                        </div>
                    ))}
                </div>
            </div>

        </div>
    );
}
