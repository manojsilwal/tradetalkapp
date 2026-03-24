import React, { useState } from 'react';
import { TrendingUp, TrendingDown, Users, Globe, Activity, Loader2, DollarSign, ShieldAlert, BarChart3, Target, CheckCircle2, XCircle, Info, ChevronDown, ChevronUp } from 'lucide-react';
import { AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip } from 'recharts';
import { API_BASE_URL, apiFetch } from './api';

export default function ConsumerUI() {
    const [ticker, setTicker] = useState("GME");
    const [data, setData] = useState(null);
    const [metricsData, setMetricsData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    const analyzeTicker = async () => {
        setLoading(true);
        setError(null);
        try {
            const [traceData, metricsJson] = await Promise.all([
                apiFetch(`${API_BASE_URL}/trace?ticker=${ticker}`),
                apiFetch(`${API_BASE_URL}/metrics/${ticker}`),
            ]);

            setData(traceData);
            setMetricsData(metricsJson.metrics);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const isBullish = data?.global_signal >= 2;
    const isRejected = data?.global_verdict?.includes("REJECTED");
    const verdict = data?.global_verdict || "AWAITING ANALYSIS";

    const confidence = data ? `${Math.round(data.confidence * 100)}%` : "--";

    // Helpers to extract rationale safely based on array lengths
    const getRationale = (factorKey) => {
        if (!data || !data.factors[factorKey]) return "Awaiting Scan...";
        const factorData = data.factors[factorKey];
        const historyLen = factorData.history.length;
        if (historyLen < 2) return factorData.rationale || "No trace found.";
        // Get the last analyst explanation
        return factorData.history[historyLen - 2].content.substring(0, 110) + "...";
    }

    const shortFactor = data?.factors?.short_interest;
    const socialFactor = data?.factors?.social_sentiment;
    const fundFactor = data?.factors?.fundamentals;

    return (
        <div className="consumer-container fade-in">
            <div className="header-section">
                <div className="title-group">
                    <h2>K2-Optimus Retail Dashboard</h2>
                    <p>Real-time Swarm Analysis Summary</p>
                </div>
                <div className="controls">
                    <input
                        type="text"
                        value={ticker}
                        onChange={(e) => setTicker(e.target.value)}
                        placeholder="Ticker"
                        style={{ width: '100px', textTransform: 'uppercase' }}
                    />
                    <button onClick={analyzeTicker} disabled={loading || !ticker}>
                        {loading ? <Loader2 className="spinner" size={18} /> : <span>Analyze</span>}
                    </button>
                </div>
            </div>

            {error && (
                <div className="error-banner glass-panel" style={{ borderColor: 'var(--accent-red)', marginBottom: '20px' }}>
                    <p style={{ color: 'var(--accent-red)', padding: '10px', margin: 0 }}>{error}</p>
                </div>
            )}

            {data && (
                <>
                    {/* ── Premium Verdict Banner ── */}
                    <div className="fade-in" style={{
                        marginBottom: '32px',
                        padding: '28px 32px',
                        borderRadius: '20px',
                        background: isBullish
                            ? 'linear-gradient(135deg, rgba(16,185,129,0.12) 0%, rgba(16,185,129,0.03) 100%)'
                            : isRejected
                                ? 'linear-gradient(135deg, rgba(239,68,68,0.12) 0%, rgba(239,68,68,0.03) 100%)'
                                : 'linear-gradient(135deg, rgba(255,255,255,0.06) 0%, rgba(255,255,255,0.02) 100%)',
                        border: `1px solid ${isBullish ? 'rgba(16,185,129,0.25)' : isRejected ? 'rgba(239,68,68,0.25)' : 'rgba(255,255,255,0.1)'}`,
                        display: 'flex',
                        alignItems: 'center',
                        gap: '28px'
                    }}>
                        {/* Radial Confidence Ring */}
                        <div style={{ position: 'relative', width: '72px', height: '72px', flexShrink: 0 }}>
                            <svg viewBox="0 0 36 36" style={{ width: '100%', height: '100%', transform: 'rotate(-90deg)' }}>
                                <circle cx="18" cy="18" r="15.5" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="3" />
                                <circle cx="18" cy="18" r="15.5" fill="none"
                                    stroke={isBullish ? 'var(--accent-green)' : isRejected ? 'var(--accent-red)' : 'var(--text-muted)'}
                                    strokeWidth="3" strokeLinecap="round"
                                    strokeDasharray={`${(data.confidence || 0) * 97.4} 97.4`}
                                />
                            </svg>
                            <span style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '1rem', fontWeight: 700, color: 'var(--text-main)' }}>
                                {confidence}
                            </span>
                        </div>
                        <div style={{ flex: 1 }}>
                            <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--text-muted)', marginBottom: '4px', fontWeight: 600 }}>
                                Overall Verdict
                            </div>
                            <div style={{ fontSize: '1.4rem', fontWeight: 700, color: isBullish ? 'var(--accent-green)' : isRejected ? 'var(--accent-red)' : 'var(--text-main)' }}>
                                {isBullish ? '✅ Buy Signal Detected' : isRejected ? '⛔ Not Recommended Right Now' : '⏳ Awaiting Analysis'}
                            </div>
                            <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                                {isBullish
                                    ? `Our AI agents agree — ${ticker.toUpperCase()} shows strong upside potential across multiple signals.`
                                    : isRejected
                                        ? `Risk factors outweigh the upside for ${ticker.toUpperCase()} at this time. Consider waiting for better conditions.`
                                        : `Enter a ticker above to begin the analysis.`
                                }
                            </div>
                        </div>
                    </div>

                    {/* ── Factor Signal Cards ── */}
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '20px' }} className="dashboard-grid">

                        <FactorSignalCard
                            icon={<Activity size={20} />}
                            accentColor="var(--accent-blue)"
                            label="Short Sellers"
                            question="Are short sellers getting squeezed?"
                            answer={shortFactor?.status === 'VERIFIED' ? 'Yes — squeeze pressure detected' : 'No squeeze signal found'}
                            isPositive={shortFactor?.status === 'VERIFIED'}
                            detail={getRationale('short_interest')}
                            delay="0.1s"
                        />

                        <FactorSignalCard
                            icon={<Users size={20} />}
                            accentColor="var(--accent-purple)"
                            label="Online Buzz"
                            question="What are people saying online?"
                            answer={socialFactor?.trading_signal === 1 ? 'Very bullish — strong positive chatter' : 'Mixed or negative sentiment'}
                            isPositive={socialFactor?.trading_signal === 1}
                            detail={getRationale('social_sentiment')}
                            delay="0.2s"
                        />

                        <FactorSignalCard
                            icon={<Target size={20} />}
                            accentColor="var(--accent-orange)"
                            label="Crowd Predictions"
                            question="What does the prediction market say?"
                            answer={data?.factors?.polymarket?.trading_signal === 1 ? 'Crowd is betting on upside' : 'No prediction markets for this stock'}
                            isPositive={data?.factors?.polymarket?.trading_signal === 1}
                            detail={getRationale('polymarket')}
                            delay="0.3s"
                        />

                        <FactorSignalCard
                            icon={<Globe size={20} />}
                            accentColor="var(--accent-green)"
                            label="Financial Health"
                            question="Is the company financially sound?"
                            answer={fundFactor?.trading_signal === 1 ? 'Yes — strong balance sheet' : 'Warning — debt levels are high'}
                            isPositive={fundFactor?.trading_signal === 1}
                            detail={getRationale('fundamentals')}
                            delay="0.4s"
                        />

                    </div>

                    {/* NEW: Elite Investor Fundamental Analysis Section */}
                    {metricsData && (
                        <div className="investor-metrics-integration fade-in" style={{ marginTop: '40px', animationDelay: '0.5s' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
                                <BarChart3 color="var(--accent-blue)" size={24} />
                                <h2 style={{ margin: 0, fontSize: '1.5rem' }}>Elite Investor Valuation Profile</h2>
                            </div>

                            <div style={{ display: 'flex', flexDirection: 'column', gap: '32px' }}>

                                {/* Section 1: Valuation & Cash Flow */}
                                <TabbedSection
                                    title="1. Valuation & Cash Flow"
                                    metrics={[
                                        { title: "Free Cash Flow Yield", data: metricsData.fcf_yield, icon: <DollarSign size={16} />, isGood: (metricsData.fcf_yield?.trend || '').includes('Up'), color: 'var(--accent-green)' },
                                        { title: "EV / EBIT", data: metricsData.ev_ebit, icon: <TrendingDown size={16} />, isGood: (metricsData.ev_ebit?.trend || '').includes('Down'), color: 'var(--accent-blue)' },
                                        { title: "Price to Tangible Book", data: metricsData.price_tangible_book, icon: <Activity size={16} />, isGood: (metricsData.price_tangible_book?.trend || '').includes('Down'), color: '#00D4FF' },
                                        { title: "Margin of Safety", data: metricsData.margin_of_safety, icon: <ShieldAlert size={16} />, isGood: (metricsData.margin_of_safety?.trend || '').includes('Better'), color: 'var(--accent-purple)' }
                                    ]}
                                    renderSummary={(metrics) => (
                                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: '16px' }}>
                                            {metrics.map((m, i) => <TrendBoard key={i} {...m} />)}
                                        </div>
                                    )}
                                />

                                {/* Section 2: Profitability & Moat */}
                                <TabbedSection
                                    title="2. Profitability & Moat"
                                    metrics={[
                                        { title: "ROIC / ROE (Moat)", data: metricsData.roic_roe, color: 'var(--accent-purple)', maxVal: 40 },
                                        { title: "Gross Margins (Pricing Power)", data: metricsData.gross_margins, color: 'var(--accent-blue)', maxVal: 100 },
                                        { title: "Capacity to Reinvest", data: metricsData.reinvest_capacity, color: 'var(--accent-green)', maxVal: 30 }
                                    ]}
                                    renderSummary={(metrics) => (
                                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '24px' }}>
                                            {metrics.map((m, i) => <ProgressBarMetric key={i} {...m} />)}
                                        </div>
                                    )}
                                />

                                {/* Section 3: Downside Protection */}
                                <TabbedSection
                                    title="3. Downside Protection"
                                    metrics={[
                                        { title: "Interest Coverage Ratio", data: metricsData.interest_coverage, threshold: "5", color: 'var(--accent-red)' },
                                        { title: "Shareholder Yield", data: metricsData.shareholder_yield, threshold: "0", invert: true, color: 'var(--accent-green)' },
                                        { title: "Owner Earnings", data: metricsData.owner_earnings, threshold: "0", color: 'var(--accent-blue)' }
                                    ]}
                                    renderSummary={(metrics) => (
                                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
                                            {metrics.map((m, i) => <AlertGaugeMetric key={i} {...m} />)}
                                        </div>
                                    )}
                                />

                            </div>
                        </div>
                    )}
                </>
            )}
        </div>
    );
}

// --- Metric Insights Dictionary ---
const METRIC_INSIGHTS = {
    'Free Cash Flow Yield': {
        what: 'How much free cash a company generates relative to its market price. A higher yield means you\'re getting more cash per dollar invested.',
        how: 'Free Cash Flow ÷ Market Cap × 100. Cash left after paying operating costs and capital spending.',
        icon: '💰'
    },
    'EV / EBIT': {
        what: 'How expensive the entire business is compared to its core operating profit. Lower is cheaper — like paying less per dollar of earnings.',
        how: 'Enterprise Value (Market Cap + Debt − Cash) ÷ Operating Earnings (EBIT).',
        icon: '⚖️'
    },
    'Price to Tangible Book': {
        what: 'How much you\'re paying for the company\'s real, touchable assets (buildings, equipment, cash). Below 1x means assets alone may be worth more than the stock price.',
        how: 'Stock Price ÷ (Total Assets − Intangibles − Liabilities) per share.',
        icon: '🏗️'
    },
    'Margin of Safety': {
        what: 'The discount between the stock\'s current price and its estimated fair value. Bigger discount = more room for error in your investment thesis.',
        how: 'Estimated intrinsic value (Graham Number) vs. current market price, expressed as a percentage discount.',
        icon: '🛡️'
    },
    'ROIC / ROE (Moat)': {
        what: 'How well management turns investor money into profit. Consistently high returns signal a durable competitive advantage — a "moat" around the business.',
        how: 'Return on Equity = Net Income ÷ Shareholders\' Equity × 100.',
        icon: '🏰'
    },
    'Gross Margins (Pricing Power)': {
        what: 'How much profit the company keeps from each dollar of revenue after direct costs. Stable, high margins mean the company can charge premium prices.',
        how: '(Revenue − Cost of Goods Sold) ÷ Revenue × 100.',
        icon: '📊'
    },
    'Capacity to Reinvest': {
        what: 'How fast the company is growing its top-line revenue. Strong growth suggests the business has room and ability to reinvest profits for expansion.',
        how: 'Year-over-year revenue growth rate, expressed as a percentage.',
        icon: '🚀'
    },
    'Interest Coverage Ratio': {
        what: 'Can the company comfortably pay interest on its debt? Higher ratio = more safety cushion. Below 3x is a red flag for debt trouble.',
        how: 'EBITDA ÷ Estimated Annual Interest Expense.',
        icon: '🔐'
    },
    'Shareholder Yield': {
        what: 'The total cash return a company gives back to shareholders through both dividends and stock buybacks combined. Higher yield = more money flowing back to you as an owner.',
        how: 'Dividend Yield + Net Buyback Yield. Dividends are direct cash payments; buybacks reduce share count, boosting your ownership stake.',
        icon: '💸'
    },
    'Owner Earnings': {
        what: 'The real cash a business owner can take out. Warren Buffett\'s favorite metric — it strips away accounting tricks to show true profitability.',
        how: 'Operating Cash Flow minus Capital Expenditures (CapEx).',
        icon: '👑'
    }
};

// --- Beautiful Metric Insight Component ---
const MetricInsight = ({ title, color }) => {
    const [expanded, setExpanded] = useState(false);
    const insight = METRIC_INSIGHTS[title];
    if (!insight) return null;

    return (
        <div
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded); }}
            style={{
                marginTop: '10px',
                cursor: 'pointer',
                borderRadius: '10px',
                overflow: 'hidden',
                transition: 'all 0.3s ease',
                background: expanded ? 'rgba(255,255,255,0.03)' : 'transparent',
            }}
        >
            <div style={{
                display: 'flex', alignItems: 'center', gap: '6px',
                padding: expanded ? '8px 12px' : '4px 0',
                transition: 'all 0.3s ease',
            }}>
                <Info size={13} style={{ color: color, opacity: 0.7, flexShrink: 0 }} />
                <span style={{
                    fontSize: '0.68rem', color: 'rgba(255,255,255,0.4)',
                    fontWeight: 500, letterSpacing: '0.02em',
                }}>
                    {expanded ? 'Hide explanation' : 'What does this mean?'}
                </span>
                {expanded
                    ? <ChevronUp size={12} style={{ color: 'rgba(255,255,255,0.3)', marginLeft: 'auto' }} />
                    : <ChevronDown size={12} style={{ color: 'rgba(255,255,255,0.3)', marginLeft: 'auto' }} />
                }
            </div>

            {expanded && (
                <div style={{
                    padding: '0 12px 12px 12px',
                    animation: 'fadeIn 0.3s ease forwards',
                }}>
                    {/* Plain English explanation */}
                    <div style={{
                        display: 'flex', gap: '10px', alignItems: 'flex-start', marginBottom: '10px',
                    }}>
                        <span style={{ fontSize: '1.3rem', lineHeight: 1 }}>{insight.icon}</span>
                        <p style={{
                            margin: 0, fontSize: '0.78rem', lineHeight: 1.5,
                            color: 'rgba(255,255,255,0.65)', fontWeight: 400,
                        }}>
                            {insight.what}
                        </p>
                    </div>

                    {/* How it's calculated */}
                    <div style={{
                        background: `linear-gradient(135deg, ${color}08, ${color}15)`,
                        border: `1px solid ${color}20`,
                        borderRadius: '8px',
                        padding: '10px 12px',
                        display: 'flex', gap: '8px', alignItems: 'flex-start',
                    }}>
                        <span style={{
                            fontSize: '0.6rem', fontWeight: 700,
                            textTransform: 'uppercase', letterSpacing: '0.06em',
                            color: color, opacity: 0.8, whiteSpace: 'nowrap', paddingTop: '2px',
                        }}>Formula</span>
                        <p style={{
                            margin: 0, fontSize: '0.72rem', lineHeight: 1.5,
                            color: 'rgba(255,255,255,0.5)', fontStyle: 'italic',
                        }}>
                            {insight.how}
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
};

// --- Tabbed Section Wrapper ---
const TabbedSection = ({ title, metrics, renderSummary }) => {
    const [activeView, setActiveView] = useState('summary');

    const tabStyle = (isActive) => ({
        padding: '6px 16px',
        borderRadius: '20px',
        border: 'none',
        cursor: 'pointer',
        fontSize: '0.75rem',
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: '0.05em',
        background: isActive ? 'var(--accent-blue)' : 'rgba(255,255,255,0.08)',
        color: isActive ? '#fff' : 'var(--text-muted)',
        transition: 'all 0.2s ease'
    });

    return (
        <div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '16px' }}>
                <h4 style={{ color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', margin: 0 }}>{title}</h4>
                <div style={{ display: 'flex', gap: '6px' }}>
                    <button style={tabStyle(activeView === 'summary')} onClick={() => setActiveView('summary')}>Summary</button>
                    <button style={tabStyle(activeView === 'chart')} onClick={() => setActiveView('chart')}>Chart</button>
                </div>
            </div>

            {activeView === 'summary' && renderSummary(metrics)}

            {activeView === 'chart' && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(350px, 1fr))', gap: '20px' }}>
                    {metrics.filter(m => m.data).map((m, i) => (
                        <MetricChartCard key={i} title={m.title} data={m.data} color={m.color || 'var(--accent-blue)'} chartType={m.chartType} />
                    ))}
                </div>
            )}
        </div>
    );
};

// --- Qualtrim-Style Metric Chart Card ---
const QUARTER_LABELS = ['Q1 \'23', 'Q2 \'23', 'Q3 \'23', 'Q4 \'23', 'Q1 \'24', 'Q2 \'24', 'Q3 \'24', 'Q4 \'24'];

const MetricChartCard = ({ title, data, color, chartType }) => {
    if (!data || !data.history || data.history.length === 0) return null;

    const useBar = chartType === 'bar' ||
        title.toLowerCase().includes('margin') ||
        title.toLowerCase().includes('coverage') ||
        title.toLowerCase().includes('buyback') ||
        title.toLowerCase().includes('reinvest') ||
        title.toLowerCase().includes('roic') ||
        title.toLowerCase().includes('owner');

    const chartData = data.history.map((val, i) => ({
        label: QUARTER_LABELS[i] || `Q${i + 1}`,
        value: Math.abs(parseFloat(val)) || 0,
        raw: val
    }));

    const tooltipStyle = {
        background: 'rgba(10,12,28,0.95)',
        border: '1px solid rgba(255,255,255,0.1)',
        borderRadius: '8px',
        fontSize: '0.75rem',
        padding: '8px 12px',
        boxShadow: '0 4px 12px rgba(0,0,0,0.4)'
    };

    return (
        <div style={{
            background: 'rgba(10,14,30,0.6)',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: '12px',
            padding: '20px',
            transition: 'border-color 0.2s',
        }}>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <span style={{ fontWeight: 600, color: 'rgba(255,255,255,0.7)', fontSize: '0.8rem', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{title}</span>
                <span style={{ fontWeight: 700, color: color, fontSize: '1.2rem' }}>{data.current || 'N/A'}</span>
            </div>

            {/* Chart */}
            <div style={{ width: '100%', height: '140px' }}>
                <ResponsiveContainer width="100%" height="100%">
                    {useBar ? (
                        <BarChart data={chartData} margin={{ top: 5, right: 0, bottom: 0, left: -20 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                            <XAxis
                                dataKey="label"
                                tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 10 }}
                                axisLine={false}
                                tickLine={false}
                            />
                            <YAxis
                                tick={{ fill: 'rgba(255,255,255,0.25)', fontSize: 10 }}
                                axisLine={false}
                                tickLine={false}
                                width={40}
                            />
                            <Tooltip
                                contentStyle={tooltipStyle}
                                formatter={(val) => [val.toFixed(2), title]}
                                cursor={{ fill: 'rgba(255,255,255,0.03)' }}
                            />
                            <Bar
                                dataKey="value"
                                fill={color}
                                radius={[4, 4, 0, 0]}
                                maxBarSize={32}
                                fillOpacity={0.85}
                            />
                        </BarChart>
                    ) : (
                        <AreaChart data={chartData} margin={{ top: 5, right: 0, bottom: 0, left: -20 }}>
                            <defs>
                                <linearGradient id={`grad-${title.replace(/[\s\/()]/g, '')}`} x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="0%" stopColor={color} stopOpacity={0.35} />
                                    <stop offset="100%" stopColor={color} stopOpacity={0.02} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" vertical={false} />
                            <XAxis
                                dataKey="label"
                                tick={{ fill: 'rgba(255,255,255,0.35)', fontSize: 10 }}
                                axisLine={false}
                                tickLine={false}
                            />
                            <YAxis
                                tick={{ fill: 'rgba(255,255,255,0.25)', fontSize: 10 }}
                                axisLine={false}
                                tickLine={false}
                                width={40}
                            />
                            <Tooltip
                                contentStyle={tooltipStyle}
                                formatter={(val) => [val.toFixed(2), title]}
                            />
                            <Area
                                type="monotone"
                                dataKey="value"
                                stroke={color}
                                strokeWidth={2}
                                fill={`url(#grad-${title.replace(/[\s\/()]/g, '')})`}
                                dot={{ r: 3, fill: color, strokeWidth: 0 }}
                                activeDot={{ r: 5, fill: color, stroke: '#fff', strokeWidth: 1.5 }}
                            />
                        </AreaChart>
                    )}
                </ResponsiveContainer>
            </div>

            {/* Footer */}
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem', color: 'rgba(255,255,255,0.35)', marginTop: '8px', paddingTop: '8px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                <span>Historical: {data.historical || 'N/A'}</span>
                <span style={{ color: color, fontWeight: 600 }}>{data.trend || 'N/A'}</span>
            </div>
            <MetricInsight title={title} color={color} />
        </div>
    );
};

// --- Factor Signal Card (Premium Q&A Format) ---
const FactorSignalCard = ({ icon, accentColor, label, question, answer, isPositive, detail, delay }) => {
    const [expanded, setExpanded] = useState(false);

    return (
        <div className="fade-in" style={{
            animationDelay: delay,
            borderRadius: '16px',
            overflow: 'hidden',
            border: '1px solid rgba(255,255,255,0.06)',
            background: 'rgba(255,255,255,0.03)',
            cursor: 'pointer',
            transition: 'all 0.3s ease',
        }}
            onClick={() => setExpanded(!expanded)}
            onMouseEnter={(e) => e.currentTarget.style.border = `1px solid ${accentColor}40`}
            onMouseLeave={(e) => e.currentTarget.style.border = '1px solid rgba(255,255,255,0.06)'}
        >
            {/* Accent color top strip */}
            <div style={{ height: '3px', background: accentColor }} />

            <div style={{ padding: '20px 24px' }}>
                {/* Header row: icon + label + status dot */}
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <div style={{
                            width: '36px', height: '36px', borderRadius: '10px',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            background: `${accentColor}15`, color: accentColor
                        }}>
                            {icon}
                        </div>
                        <span style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600, color: 'var(--text-muted)' }}>{label}</span>
                    </div>
                    {isPositive
                        ? <CheckCircle2 size={18} color="var(--accent-green)" />
                        : <XCircle size={18} color="var(--accent-red)" />
                    }
                </div>

                {/* Question */}
                <p style={{ margin: '0 0 8px 0', fontSize: '0.85rem', color: 'var(--text-muted)', fontStyle: 'italic' }}>{question}</p>

                {/* Answer */}
                <p style={{
                    margin: 0, fontSize: '1.1rem', fontWeight: 600,
                    color: isPositive ? 'var(--accent-green)' : 'var(--accent-red)',
                    lineHeight: 1.4
                }}>
                    {answer}
                </p>

                {/* Expandable detail */}
                {expanded && detail && (
                    <div style={{
                        marginTop: '14px', paddingTop: '14px',
                        borderTop: '1px solid rgba(255,255,255,0.06)',
                        fontSize: '0.8rem', color: 'var(--text-muted)', lineHeight: 1.6
                    }}>
                        <span style={{ fontSize: '0.65rem', textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 600, display: 'block', marginBottom: '6px', color: accentColor }}>AI Analysis</span>
                        {detail}
                    </div>
                )}

                {/* Expand hint */}
                <div style={{ marginTop: '10px', fontSize: '0.65rem', color: 'rgba(255,255,255,0.25)', textAlign: 'right' }}>
                    {expanded ? 'Click to collapse' : 'Click for details'}
                </div>
            </div>
        </div>
    );
};

// --- Distinct Visualization Components ---

const TrendBoard = ({ title, data, icon, isGood, color }) => {
    if (!data) return null;
    const colorStr = isGood ? 'var(--accent-green)' : 'var(--accent-red)';

    return (
        <div className="glass-panel" style={{ padding: '20px', borderRadius: '12px', background: 'rgba(0,0,0,0.2)', borderLeft: `3px solid ${colorStr}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--text-muted)', fontSize: '0.8rem', textTransform: 'uppercase', fontWeight: 600, marginBottom: '12px' }}>
                {icon} <span>{title}</span>
            </div>
            <div style={{ fontSize: '1.8rem', fontWeight: 700, color: 'var(--text-main)', marginBottom: '4px' }}>
                {data.current || 'N/A'}
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                <span>Historical: {data.historical || 'N/A'}</span>
                <span style={{ color: colorStr, fontWeight: 600 }}>{data.trend || 'N/A'}</span>
            </div>
            <MetricInsight title={title} color={color || colorStr} />
        </div>
    );
};

const ProgressBarMetric = ({ title, data, color, maxVal }) => {
    if (!data) return null;

    let rawNum = parseFloat((data.current || '').replace(/[^0-9.-]/g, ''));
    if (isNaN(rawNum)) rawNum = 0;
    let fillPct = Math.min((Math.max(rawNum, 0) / maxVal) * 100, 100);

    return (
        <div className="glass-panel" style={{ padding: '20px', borderRadius: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '12px' }}>
                <span style={{ fontWeight: 600, color: 'var(--text-main)' }}>{title}</span>
                <span style={{ fontWeight: 700, color: color }}>{data.current || 'N/A'}</span>
            </div>
            <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.1)', borderRadius: '4px', overflow: 'hidden', position: 'relative' }}>
                <div style={{
                    position: 'absolute', top: 0, left: 0, height: '100%',
                    width: `${fillPct}%`,
                    background: color,
                    borderRadius: '4px',
                    transition: 'width 1s ease-out'
                }} />
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '8px', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
                <span>Trend: {data.trend || 'N/A'}</span>
                <span>(Target ~{maxVal})</span>
            </div>
            <MetricInsight title={title} color={color} />
        </div>
    );
};

const AlertGaugeMetric = ({ title, data, threshold, invert, color }) => {
    if (!data) return null;

    let rawNum = parseFloat((data.current || '').replace(/[^0-9.-]/g, ''));
    if (isNaN(rawNum)) rawNum = 0;
    const threshNum = parseFloat(threshold);
    let isWarning = invert ? rawNum <= threshNum : rawNum < threshNum;
    if (data.current === "N/A" || !data.current) isWarning = true;

    return (
        <div style={{
            padding: '16px 24px',
            borderRadius: '16px',
            background: isWarning ? 'rgba(239, 68, 68, 0.06)' : 'rgba(16, 185, 129, 0.06)',
            border: `1px solid ${isWarning ? 'rgba(239, 68, 68, 0.15)' : 'rgba(16, 185, 129, 0.15)'}`,
            flex: '1 1 280px',
        }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
                <div style={{
                    width: '40px', height: '40px', borderRadius: '50%',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: isWarning ? 'var(--accent-red)' : 'var(--accent-green)',
                    color: '#fff', flexShrink: 0,
                }}>
                    {isWarning ? <ShieldAlert size={20} /> : <TrendingUp size={20} />}
                </div>
                <div>
                    <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-muted)', textTransform: 'uppercase', fontWeight: 600 }}>{title}</p>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                        <span style={{ fontSize: '1.4rem', fontWeight: 700, color: 'var(--text-main)' }}>{data.current || 'N/A'}</span>
                        <span style={{ fontSize: '0.8rem', color: isWarning ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                            {isWarning ? "High Risk" : "Safe"}
                        </span>
                    </div>
                </div>
            </div>
            <MetricInsight title={title} color={color || (isWarning ? 'var(--accent-red)' : 'var(--accent-green)')} />
        </div>
    );
};
