import React, { useState } from 'react';
import { ChevronDown, TrendingUp, BookOpen, Loader2 } from 'lucide-react';
import './InvestorMetricsUI.css';
import { API_BASE_URL } from './api';

const metricsData = [
    {
        id: "roic_roe",
        title: "ROIC & ROE",
        value: "Moat Measure",
        trend: "Compounders",
        summary: "Return on Invested Capital and Return on Equity measure how efficiently a company uses its capital to generate profits.",
        details: "This is the ultimate measure of a 'compounding machine' and a company's economic moat. Greenblatt's 'Magic Formula' uses ROIC as the core metric for business 'quality,' while Chuck Akre bases his entire philosophy around businesses with historically high ROIC.",
        investors: ["Joel Greenblatt", "Warren Buffett", "Chuck Akre", "Chris Davis"]
    },
    {
        id: "fcf_yield",
        title: "Free Cash Flow Yield",
        value: "Cash is King",
        trend: "True Yield",
        summary: "Free Cash Flow divided by Market Cap tells an investor exactly how much cash the business is throwing off relative to its price.",
        details: "Earnings can be manipulated by accounting rules, but cash cannot. This ratio allows investors to compare the cash return of buying the entire business directly against risk-free bond yields.",
        investors: ["Bill Ackman", "Seth Klarman", "Mario Gabelli"]
    },
    {
        id: "ev_ebit",
        title: "EV/EBIT",
        value: "Acquirer's Cost",
        trend: "Private Value",
        summary: "Enterprise Value to EBIT accounts for debt and cash, valuing the business as a private acquirer would.",
        details: "Unlike the standard P/E ratio, EV/EBIT prevents investors from being fooled by companies with massive debt loads. It is the valuation half of the 'Magic Formula' and foundational to Gabelli's Private Market Value approach.",
        investors: ["Joel Greenblatt", "Mario Gabelli", "David Abrams"]
    },
    {
        id: "owner_earnings",
        title: "Owner Earnings",
        value: "True Cash",
        trend: "Withdrawable",
        summary: "Net Income plus D&A, minus the average capital expenditures needed simply to maintain the current competitive position.",
        details: "Coined by Buffett in 1986, this identifies the true, withdrawable cash a business generates without hurting its future operations. It strips out growth cap-ex and focuses purely on maintenance cap-ex.",
        investors: ["Warren Buffett", "Guy Spier", "Tom Gayner"]
    },
    {
        id: "reinvest_capacity",
        title: "Capacity to Reinvest",
        value: "Growth Runway",
        trend: "Compound Loop",
        summary: "The size of the runway a company has to reinvest its profits internally at a very high rate of return.",
        details: "A high ROIC is great, but only if the company has the capacity to reinvest capital globally. Without this capacity, companies are forced to pay out dividends, which triggers taxes and stops the internal compounding loop.",
        investors: ["Tom Russo", "Chuck Akre", "Li Lu"]
    },
    {
        id: "interest_coverage",
        title: "Interest Coverage",
        value: "Survival Rate",
        trend: "Downside Risk",
        summary: "EBIT divided by Interest Expense ensures a company generates enough operating income to easily cover its debt obligations.",
        details: "Distressed and contrarian investors often buy companies going through terrible times. The absolute highest priority is ensuring the company survives long enough for the cycle to revert. If interest coverage is low, bankruptcy is a major risk.",
        investors: ["Howard Marks", "Seth Klarman", "David Tepper", "Michael Burry"]
    },
    {
        id: "price_tangible_book",
        title: "Price-to-Tangible Book",
        value: "Liquidation",
        trend: "Deep Value",
        summary: "Measures what the company would be worth if it were liquidated and physical assets sold off tomorrow.",
        details: "Used heavily in deep-value situations. It strips out intangible assets like goodwill. Buying below Tangible Book Value provides a massive 'Margin of Safety' because the physical assets alone are worth more than the stock price.",
        investors: ["Seth Klarman", "Michael Burry", "Prem Watsa"]
    },
    {
        id: "gross_margins",
        title: "Gross & Operating Margins",
        value: "Pricing Power",
        trend: "Durable Edge",
        summary: "Consistently high and stable margins are the surest mathematical proof of pricing power and a durable competitive advantage.",
        details: "Companies with high margins require very little external capital to grow. If inflation hits, they can raise prices without losing customers. Defensive value investors frequently scan for stable 50%+ gross margins.",
        investors: ["Donald Yacktman", "Warren Buffett", "Chris Davis"]
    },
    {
        id: "buyback_yield",
        title: "Net Buyback Yield",
        value: "Share Shrink",
        trend: "Ownership Up",
        summary: "The net reduction in shares outstanding, calculating how much an investor's ownership stake is growing.",
        details: "Activist investors frequently push management to initiate aggressive buybacks when a stock is undervalued. If a company reduces its share count by 5% a year, an investor's claim on the earnings grows by 5% automatically without buying a single new share.",
        investors: ["Chris Hohn", "Bill Ackman", "Jeremy Grantham"]
    },
    {
        id: "margin_of_safety",
        title: "Margin of Safety",
        value: "Discount to Fair",
        trend: "Holy Grail",
        summary: "A steep discount (usually 30% to 50%) between the stock's current price and its calculated Intrinsic Value.",
        details: "While not a standardized GAAP metric, this is the unifying principle. Whether assessing intrinsic value via sum-of-the-parts, liquidation value, or DCF, all elite investors demand this buffer to protect against unforeseen errors or market panics.",
        investors: ["All 20 Value Investors", "Benjamin Graham"]
    }
];

const MetricCard = ({ metric, index, liveData }) => {
    const [isExpanded, setIsExpanded] = useState(false);

    // Determine what to show on the front of the card
    const metricData = liveData?.metrics?.[metric.id];
    const displayValue = metricData ? metricData.current : metric.value;
    const displayTrend = metricData ? metricData.trend : metric.trend;

    return (
        <div className={`metric-card glass-panel fade-in`} style={{ animationDelay: `${index * 0.05}s` }}>
            <div
                className="metric-summary"
                onClick={() => setIsExpanded(!isExpanded)}
            >
                <div>
                    <h3 className="metric-title">{metric.title}</h3>
                    <div className="metric-value-row">
                        <span className="metric-value">
                            {displayValue}
                        </span>
                        <span className="metric-trend" style={{ color: displayTrend.includes('Down') || displayTrend.includes('Warning') || displayTrend.includes('Overvalued') ? 'var(--accent-red)' : 'var(--accent-green)' }}>
                            {metricData && <TrendingUp size={16} className="trend-icon" style={{ transform: displayTrend.includes('Up') || displayTrend.includes('Better') ? 'none' : 'scaleY(-1)' }} />}
                            {!metricData && <TrendingUp size={16} className="trend-icon" />}
                            {displayTrend}
                        </span>
                    </div>
                </div>

                <div className={`expand-btn ${isExpanded ? 'rotated' : ''}`}>
                    <ChevronDown size={20} />
                </div>
            </div>

            <div className={`metric-details ${isExpanded ? 'expanded' : ''}`}>
                <div className="details-content">
                    {metricData && (
                        <div className="live-data-box" style={{ marginBottom: '16px', padding: '12px', background: 'rgba(59, 130, 246, 0.1)', borderRadius: '8px', border: '1px solid rgba(59, 130, 246, 0.2)' }}>
                            <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-main)', display: 'flex', justifyContent: 'space-between' }}>
                                <span><strong style={{ color: 'var(--accent-blue)' }}>Current (TTM):</strong> {metricData.current}</span>
                                <span><strong style={{ color: 'var(--text-muted)' }}>Historical (Proxy):</strong> {metricData.historical}</span>
                            </p>
                        </div>
                    )}
                    <p className="details-text">
                        <strong className="details-highlight">What it is:</strong> {metric.summary}
                    </p>
                    <p className="details-text">
                        <strong className="details-highlight">Why it matters:</strong> {metric.details}
                    </p>
                    <div className="investors-box glass-panel text-sm">
                        <p className="investors-label">Used heavily by</p>
                        <p className="investors-list">{metric.investors.join(", ")}</p>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default function InvestorMetricsUI() {
    const [ticker, setTicker] = useState("");
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    const fetchMetrics = async () => {
        if (!ticker) return;
        setLoading(true);
        setError(null);
        try {
            const res = await fetch(`${API_BASE_URL}/metrics/${ticker}`);
            if (!res.ok) throw new Error("Failed to connect to Python Backend.");
            const resData = await res.json();
            if (Object.keys(resData.metrics).length === 0) {
                throw new Error("Failed to retrieve fundamental data for this ticker.");
            }
            setData(resData);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="metrics-container fade-in">
            <div className="header-section">
                <div className="title-group">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                        <BookOpen color="var(--accent-purple)" size={28} />
                        <h2>The Elite Investor Playbook</h2>
                    </div>
                    <p>The top 10 universal metrics used by legendary value, contrarian, and distressed investors.</p>
                </div>
                <div className="controls">
                    <input
                        type="text"
                        value={ticker}
                        onChange={(e) => setTicker(e.target.value)}
                        placeholder="e.g. AAPL"
                        style={{ width: '100px', textTransform: 'uppercase' }}
                        onKeyDown={(e) => e.key === 'Enter' && fetchMetrics()}
                    />
                    <button onClick={fetchMetrics} disabled={loading || !ticker}>
                        {loading ? <Loader2 className="spinner" size={18} /> : <span>Scan Ticker</span>}
                    </button>
                </div>
            </div>

            {error && (
                <div className="error-banner glass-panel" style={{ borderColor: 'var(--accent-red)', marginBottom: '20px', padding: '12px 16px', borderRadius: '12px' }}>
                    <p style={{ color: 'var(--accent-red)', margin: 0, fontSize: '0.9rem', fontWeight: 500 }}>{error}</p>
                </div>
            )}

            <div className="metrics-grid">
                {metricsData.map((metric, idx) => (
                    <MetricCard key={idx} metric={metric} index={idx} liveData={data} />
                ))}
            </div>
        </div>
    );
}
