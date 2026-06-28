import React, { useState, useEffect } from 'react';
import { Trophy, TrendingUp, TrendingDown, Target, Building2, BarChart2, Briefcase, FileText, ShieldAlert, Award, ChevronRight, X, Loader2, AlertTriangle, PlayCircle, Info } from 'lucide-react';
import { API_BASE_URL, apiFetch, apiPost } from '../../api';
import { useRef } from 'react';

/**
 * @typedef {'reported' | '13f_economic' | '13f_investable'} LeaderboardMode
 * @typedef {'High' | 'Good' | 'Medium' | 'Low' | 'Not reliable'} DataConfidenceLabel
 *
 * @typedef {Object} FundLeaderboardRow
 * @property {number} rank
 * @property {string} fundId
 * @property {string} fundName
 * @property {string} managerType
 * @property {string[]} strategyTags
 * @property {number|null} cagr10Y
 * @property {number|null} roicProxy10Y
 * @property {number|null} alphaVsSP500
 * @property {number|null} sharpe10Y
 * @property {number|null} maxDrawdown10Y
 * @property {number|null} latest13FValueUsd
 * @property {string} latestReportPeriod
 * @property {string|null} topSector
 * @property {number|null} topSectorWeight
 * @property {number|null} top10HoldingsWeight
 * @property {number} dataConfidenceScore
 * @property {DataConfidenceLabel} dataConfidenceLabel
 * @property {string} lastFilingDate
 */

// Formatters
const formatPct = (val) => val != null ? `${(val * 100).toFixed(1)}%` : 'N/A';
const formatUsd = (val) => {
    if (val == null) return 'N/A';
    if (val >= 1e9) return `$${(val / 1e9).toFixed(1)}B`;
    if (val >= 1e6) return `$${(val / 1e6).toFixed(1)}M`;
    return `$${val.toLocaleString()}`;
};
const formatMult = (val) => val != null ? `${val.toFixed(1)}x` : 'N/A';
const formatDec = (val) => val != null ? val.toFixed(2) : 'N/A';

function ReturnsSparkline({ series }) {
    const W = 520, H = 180, pad = 8;
    const fund = series.map((p) => p.cumulativeValue).filter((v) => v != null);
    const bench = series.map((p) => p.benchmarkCumulativeValue).filter((v) => v != null);
    const all = [...fund, ...bench];
    if (all.length < 2) return null;
    const min = Math.min(...all), max = Math.max(...all);
    const range = max - min || 1;
    const x = (i, n) => pad + (i / (n - 1)) * (W - 2 * pad);
    const y = (v) => H - pad - ((v - min) / range) * (H - 2 * pad);
    const toPath = (vals) => vals.map((v, i) => `${i === 0 ? 'M' : 'L'} ${x(i, vals.length).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
    const last = series[series.length - 1];
    const totalPct = last?.cumulativeValue != null ? (last.cumulativeValue - 1) : null;

    return (
        <div>
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-48">
                <path d={toPath(fund)} fill="none" stroke="#34d399" strokeWidth="2" />
                {bench.length > 1 && <path d={toPath(bench)} fill="none" stroke="#64748b" strokeWidth="1.5" strokeDasharray="4 3" />}
            </svg>
            <div className="flex items-center gap-4 text-xs text-slate-400 mt-2">
                <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-emerald-400 inline-block" /> Fund clone</span>
                <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-slate-500 inline-block" /> SPY</span>
                {totalPct != null && (
                    <span className="ml-auto text-slate-300">Cumulative: {(totalPct * 100).toFixed(1)}%</span>
                )}
            </div>
        </div>
    );
}

const RANKING_MODES = [
    { value: 'SEC_13F_VALUE', label: '13F Value (largest book)' },
    { value: 'RETURNS', label: 'Clone Returns (risk-adjusted)' },
];

export default function FundLeaderboardUI() {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [message, setMessage] = useState(null);
    const [selectedFundId, setSelectedFundId] = useState(null);
    const [mode, setMode] = useState('13f_investable');
    const [rankingMode, setRankingMode] = useState('RETURNS');
    const [activeTab, setActiveTab] = useState('overview');
    const [tabs, setTabs] = useState({ holdings: null, changes: null, timeline: null, loading: false, error: null });

    const [scanBusy, setScanBusy] = useState(false);
    const autoWarmedRef = useRef(false);

    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(null);
        setMessage(null);

        apiFetch(`${API_BASE_URL}/api/funds/leaderboard?mode=${encodeURIComponent(mode)}&limit=50`)
            .then((data) => {
                if (cancelled) return;
                setRows(Array.isArray(data?.rows) ? data.rows : []);
                setMessage(data?.message || null);

                // Auto-scan logic if no snapshot exists
                if (!data?.rows || data.rows.length === 0) {
                    if (!autoWarmedRef.current && !scanBusy) {
                        autoWarmedRef.current = true;
                        setScanBusy(true);
                        apiPost(`${API_BASE_URL}/api/funds/ingest/run`)
                            .finally(() => setScanBusy(false));
                    }
                }
            })
            .catch((e) => {
                if (cancelled) return;
                setRows([]);
                setError(e?.message || 'Failed to load leaderboard');
            })
            .finally(() => {
                if (!cancelled) setLoading(false);
            });

        return () => { cancelled = true; };
    }, [mode, scanBusy]);

    const [detail, setDetail] = useState({ portfolio: null, returns: null, loading: false, error: null });

    useEffect(() => {
        if (!selectedFundId) {
            setDetail({ portfolio: null, returns: null, loading: false, error: null });
            return;
        }
        let cancelled = false;
        setDetail({ portfolio: null, returns: null, loading: true, error: null });

        Promise.allSettled([
            apiFetch(`${API_BASE_URL}/api/funds/${encodeURIComponent(selectedFundId)}/portfolio/latest`),
            apiFetch(`${API_BASE_URL}/api/funds/${encodeURIComponent(selectedFundId)}/returns?mode=${encodeURIComponent(mode)}`),
        ]).then(([pRes, rRes]) => {
            if (cancelled) return;
            setDetail({
                portfolio: pRes.status === 'fulfilled' ? pRes.value : null,
                returns: rRes.status === 'fulfilled' ? rRes.value : null,
                loading: false,
                error: (pRes.status === 'rejected' && rRes.status === 'rejected')
                    ? 'Detailed data not available for this fund yet.'
                    : null,
            });
        });

        return () => { cancelled = true; };
    }, [selectedFundId, mode]);

    useEffect(() => {
        setActiveTab('overview');
        setTabs({ holdings: null, changes: null, timeline: null, loading: false, error: null });
    }, [selectedFundId]);

    useEffect(() => {
        if (!selectedFundId || activeTab === 'overview') return;
        if (tabs[activeTab]) return; // already loaded
        let cancelled = false;
        setTabs((t) => ({ ...t, loading: true, error: null }));
        const endpoint = activeTab === 'holdings'
            ? `holdings`
            : activeTab === 'changes'
                ? `changes`
                : `timeline`;
        apiFetch(`${API_BASE_URL}/api/funds/${encodeURIComponent(selectedFundId)}/${endpoint}`)
            .then((data) => {
                if (cancelled) return;
                setTabs((t) => ({ ...t, [activeTab]: data, loading: false }));
            })
            .catch((e) => {
                if (cancelled) return;
                setTabs((t) => ({ ...t, loading: false, error: e?.message || 'Not available yet.' }));
            });
        return () => { cancelled = true; };
    }, [selectedFundId, activeTab, tabs]);

    const displayRows = React.useMemo(() => {
        const sorted = [...rows];
        if (rankingMode === 'SEC_13F_VALUE') {
            sorted.sort((a, b) => (b.latest13FValueUsd || 0) - (a.latest13FValueUsd || 0));
        } else {
            // Returns-ranked: emerging funds (no reconstructed returns) sink to the
            // bottom regardless of book size so the ranking stays performance-driven.
            sorted.sort((a, b) => {
                if (!!a.emerging !== !!b.emerging) return a.emerging ? 1 : -1;
                return (b.leaderboardScore || b.cagr10Y || 0) - (a.leaderboardScore || a.cagr10Y || 0);
            });
        }
        return sorted.map((r, i) => ({ ...r, displayRank: i + 1 }));
    }, [rows, rankingMode]);

    const getConfidenceColor = (label) => {
        switch (label) {
            case 'High': return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
            case 'Good': return 'text-green-400 bg-green-400/10 border-green-400/20';
            case 'Medium': return 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20';
            case 'Low': return 'text-orange-400 bg-orange-400/10 border-orange-400/20';
            case 'Emerging': return 'text-violet-300 bg-violet-400/10 border-violet-400/20';
            default: return 'text-red-400 bg-red-400/10 border-red-400/20';
        }
    };

    const selectedRow = rows.find((r) => r.fundId === selectedFundId);

    return (
        <div className="w-full max-w-7xl mx-auto space-y-6">
            <header className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        <Trophy className="text-amber-400" size={28} />
                        Fund Leaderboard
                    </h1>
                    <p className="text-slate-400 mt-1">Great investors by philosophy & 5-Year clone returns</p>
                </div>
            </header>

            {/* Methodology Banner */}
            <div className="bg-slate-800/50 border border-slate-700 p-4 rounded-lg flex items-start gap-3">
                <Info className="text-blue-400 shrink-0 mt-0.5" size={20} />
                <div className="text-sm text-slate-300">
                    <p className="font-semibold text-white mb-1">13F-Inferred Equity Clone Return</p>
                    <p>
                        This leaderboard uses public Form 13F holdings. 13F-derived returns are estimates of the reported public long-book only and are <strong>not actual fund returns</strong>. They exclude shorts, leverage, cash, and non-U.S. holdings.
                    </p>
                </div>
            </div>

            {/* Controls */}
            <div className="flex flex-wrap gap-4 items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800">
                <div className="flex items-center gap-2">
                    <span className="text-sm text-slate-400">Rank by:</span>
                    <select
                        value={rankingMode}
                        onChange={(e) => setRankingMode(e.target.value)}
                        className="bg-slate-800 text-sm text-white border border-slate-700 rounded px-3 py-1.5 focus:outline-none focus:border-blue-500"
                    >
                        {RANKING_MODES.map((m) => (
                            <option key={m.value} value={m.value}>{m.label}</option>
                        ))}
                    </select>
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-sm text-slate-400">Return mode:</span>
                    <select
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                        className="bg-slate-800 text-sm text-white border border-slate-700 rounded px-3 py-1.5 focus:outline-none focus:border-blue-500"
                    >
                        <option value="13f_investable">13F Investable (No Lookahead)</option>
                        <option value="13f_economic">13F Economic (Quarter-end)</option>
                    </select>
                </div>
            </div>

            {/* Table */}
            <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
                <div className="overflow-x-auto">
                    <table className="w-full text-left border-collapse">
                        <thead>
                            <tr className="border-b border-slate-800 bg-slate-800/30 text-xs font-semibold text-slate-400 uppercase tracking-wider">
                                <th className="px-4 py-3">Rank</th>
                                <th className="px-4 py-3">Fund / Manager</th>
                                <th className="px-4 py-3 text-right">5Y CAGR</th>
                                <th className="px-4 py-3 text-right">5Y Alpha</th>
                                <th className="px-4 py-3 text-right">Sharpe</th>
                                <th className="px-4 py-3 text-right">Drawdown</th>
                                <th className="px-4 py-3 text-right">Latest 13F Value</th>
                                <th className="px-4 py-3">Top Sector</th>
                                <th className="px-4 py-3 text-center">Data Confidence</th>
                                <th className="px-4 py-3"></th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-slate-800/50">
                            {loading ? (
                                <tr>
                                    <td colSpan="10" className="px-4 py-8 text-center text-slate-500">
                                        <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2 text-blue-500" />
                                        Loading leaderboard data...
                                    </td>
                                </tr>
                            ) : error ? (
                                <tr>
                                    <td colSpan="10" className="px-4 py-8 text-center text-rose-400">
                                        <AlertTriangle className="w-6 h-6 mx-auto mb-2" />
                                        {error}
                                    </td>
                                </tr>
                            ) : displayRows.length === 0 ? (
                                <tr>
                                    <td colSpan="10" className="px-4 py-8 text-center text-slate-500">
                                        {scanBusy ? 'Preparing leaderboard data... this may take a moment.' : (message || 'No funds match the current filters.')}
                                    </td>
                                </tr>
                            ) : displayRows.map((row) => (
                                <tr key={row.fundId} className="hover:bg-slate-800/40 transition-colors group">
                                    <td className="px-4 py-3 font-medium text-white">#{row.displayRank}</td>
                                    <td className="px-4 py-3 max-w-[320px]">
                                        <div className="flex items-center gap-2">
                                            <span className="font-medium text-blue-400 group-hover:text-blue-300 transition-colors cursor-pointer" onClick={() => setSelectedFundId(row.fundId)}>
                                                {row.fundName}
                                            </span>
                                            {row.emerging && (
                                                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide text-violet-300 bg-violet-400/10 border border-violet-400/20">
                                                    Emerging
                                                </span>
                                            )}
                                        </div>
                                        <div className="text-xs text-slate-500 flex items-center gap-2 mt-0.5">
                                            <span className="capitalize">{(row.managerType || '').replace(/_/g, ' ')}</span>
                                            {row.strategyTags && row.strategyTags.length > 0 && (
                                                <>
                                                    <span className="w-1 h-1 rounded-full bg-slate-600"></span>
                                                    <span>{row.strategyTags.slice(0, 2).join(' · ').replace(/_/g, ' ')}</span>
                                                </>
                                            )}
                                        </div>
                                        {row.philosophy && (
                                            <div className="text-xs text-slate-400 mt-1 line-clamp-2 max-w-[300px]" title={row.philosophy}>
                                                {row.philosophy}
                                            </div>
                                        )}
                                    </td>
                                    <td className={`px-4 py-3 text-right font-medium ${row.cagr10Y == null ? 'text-slate-500' : row.cagr10Y > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                        {row.cagr10Y == null ? '—' : formatPct(row.cagr10Y)}
                                    </td>
                                    <td className={`px-4 py-3 text-right text-sm ${row.alphaVsSP500 == null ? 'text-slate-500' : row.alphaVsSP500 > 0 ? 'text-emerald-400/80' : 'text-rose-400/80'}`}>
                                        {row.alphaVsSP500 == null ? '—' : `${row.alphaVsSP500 > 0 ? '+' : ''}${formatPct(row.alphaVsSP500)}`}
                                    </td>
                                    <td className="px-4 py-3 text-right text-sm text-slate-300">{row.sharpe10Y == null ? '—' : formatDec(row.sharpe10Y)}</td>
                                    <td className="px-4 py-3 text-right text-sm text-rose-400">{row.maxDrawdown10Y == null ? '—' : formatPct(row.maxDrawdown10Y)}</td>
                                    <td className="px-4 py-3 text-right text-sm text-slate-300">{formatUsd(row.latest13FValueUsd)}</td>
                                    <td className="px-4 py-3">
                                        <div className="text-sm text-slate-300 truncate max-w-[120px]" title={row.topSector}>
                                            {row.topSector || 'N/A'}
                                        </div>
                                        <div className="text-xs text-slate-500">{formatPct(row.topSectorWeight)}</div>
                                    </td>
                                    <td className="px-4 py-3 text-center">
                                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium border ${getConfidenceColor(row.dataConfidenceLabel)}`}>
                                            {row.dataConfidenceLabel}
                                        </span>
                                    </td>
                                    <td className="px-4 py-3 text-right">
                                        <button
                                            onClick={() => setSelectedFundId(row.fundId)}
                                            className="p-1.5 text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded transition-colors"
                                            title="View Portfolio & Analytics"
                                        >
                                            <ChevronRight size={18} />
                                        </button>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Detailed Fund Drawer (Placeholder for selection) */}
            {selectedFundId && (
                <div className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm" onClick={() => setSelectedFundId(null)}>
                    <div
                        className="w-full max-w-2xl bg-slate-900 border-l border-slate-800 h-full shadow-2xl flex flex-col overflow-hidden animate-slide-in-right"
                        onClick={(e) => e.stopPropagation()}
                    >
                        {/* Drawer Header */}
                        <div className="flex items-center justify-between p-5 border-b border-slate-800 bg-slate-900">
                            <div>
                                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                                    {selectedRow?.fundName}
                                    {selectedRow?.emerging && (
                                        <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide text-violet-300 bg-violet-400/10 border border-violet-400/20">
                                            Emerging
                                        </span>
                                    )}
                                </h2>
                                <p className="text-sm text-slate-400 mt-1">13F Portfolio Analysis & 5Y Return Metrics</p>
                            </div>
                            <button
                                onClick={() => setSelectedFundId(null)}
                                className="p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        {/* Drawer Tab Bar */}
                        <div className="flex gap-1 px-5 pt-3 border-b border-slate-800 bg-slate-900">
                            {[
                                { id: 'overview', label: 'Overview' },
                                { id: 'holdings', label: 'Holdings' },
                                { id: 'changes', label: 'Position Changes' },
                                { id: 'timeline', label: 'Timeline' },
                            ].map((t) => (
                                <button
                                    key={t.id}
                                    onClick={() => setActiveTab(t.id)}
                                    className={`px-3 py-2 text-sm font-medium rounded-t-lg transition-colors ${activeTab === t.id ? 'text-white border-b-2 border-blue-500' : 'text-slate-400 hover:text-slate-200'}`}
                                >
                                    {t.label}
                                </button>
                            ))}
                        </div>

                        {/* Drawer Content Area */}
                        <div className="flex-1 overflow-y-auto p-5 space-y-6">

                          {activeTab === 'overview' && (<>
                            {/* Investing Philosophy */}
                            {selectedRow?.philosophy && (
                                <div className="bg-slate-800/40 p-4 rounded-xl border border-slate-700/50">
                                    <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                                        <Award size={13} className="text-amber-400" /> Investing Philosophy
                                    </p>
                                    <p className="text-sm text-slate-200 leading-relaxed">{selectedRow.philosophy}</p>
                                    {selectedRow.strategyTags && selectedRow.strategyTags.length > 0 && (
                                        <div className="flex flex-wrap gap-1.5 mt-3">
                                            {selectedRow.strategyTags.map((t) => (
                                                <span key={t} className="text-[11px] px-2 py-0.5 rounded-full bg-slate-700/60 text-slate-300 capitalize">
                                                    {String(t).replace(/_/g, ' ')}
                                                </span>
                                            ))}
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Summary Card */}
                            <div className="grid grid-cols-2 gap-4">
                                <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700/50">
                                    <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-1">Latest 13F Market Value</p>
                                    <p className="text-2xl font-semibold text-white">
                                        {formatUsd(selectedRow?.latest13FValueUsd)}
                                    </p>
                                    <p className="text-xs text-slate-500 mt-1">As of {selectedRow?.latestReportPeriod}</p>
                                </div>
                                <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700/50">
                                    <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-1">5Y CAGR (Clone)</p>
                                    <p className={`text-2xl font-semibold ${selectedRow?.cagr10Y == null ? 'text-slate-500' : 'text-emerald-400'}`}>
                                        {selectedRow?.cagr10Y == null ? 'N/A' : formatPct(selectedRow?.cagr10Y)}
                                    </p>
                                    <p className="text-xs text-slate-500 mt-1">
                                        {selectedRow?.emerging ? 'Insufficient history for a reliable clone' : '5Y Investable Clone Return'}
                                    </p>
                                </div>
                            </div>

                            {/* Emerging notice */}
                            {selectedRow?.emerging && (
                                <div className="bg-violet-500/10 border border-violet-500/20 p-4 rounded-lg flex gap-3 text-violet-100 text-sm">
                                    <Info className="shrink-0 mt-0.5 text-violet-300" size={18} />
                                    <div>
                                        <p className="font-semibold text-violet-300">Emerging / high-conviction manager</p>
                                        <p className="mt-1 opacity-80">
                                            This fund has a short 13F history and/or an options- or short-driven book that the long-equity clone can't represent. We show its reported holdings and philosophy, but withhold a clone return rather than report a misleading number.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* Trust Indicator */}
                            {selectedRow?.dataConfidenceLabel === 'Low' && (
                                <div className="bg-orange-500/10 border border-orange-500/20 p-4 rounded-lg flex gap-3 text-orange-200 text-sm">
                                    <ShieldAlert className="shrink-0 mt-0.5 text-orange-400" size={18} />
                                    <div>
                                        <p className="font-semibold text-orange-400">Low Data Confidence</p>
                                        <p className="mt-1 opacity-80">
                                            This fund may have low mapping coverage, missing history, or strategy mismatch. Returns reconstructed from 13F public filings may poorly proxy their actual fund performance.
                                        </p>
                                    </div>
                                </div>
                            )}

                            {/* Portfolio Allocation */}
                            <div className="bg-slate-950 rounded-xl border border-slate-800 p-5">
                                <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-4">
                                    <Briefcase size={16} className="text-blue-400" />
                                    Top Holdings & Sectors
                                </h3>
                                {detail.loading ? (
                                    <div className="flex items-center justify-center h-40 text-slate-500">
                                        <Loader2 className="w-5 h-5 animate-spin" />
                                    </div>
                                ) : detail.portfolio ? (
                                    <div className="space-y-4">
                                        <div className="space-y-1.5">
                                            {(detail.portfolio.sectorAllocation || []).slice(0, 5).map((s) => (
                                                <div key={s.sector} className="flex items-center gap-3">
                                                    <span className="text-xs text-slate-400 w-40 truncate" title={s.sector}>{s.sector}</span>
                                                    <div className="flex-1 h-2 bg-slate-800 rounded overflow-hidden">
                                                        <div className="h-full bg-blue-500/70" style={{ width: `${Math.min(100, (s.weight || 0) * 100)}%` }} />
                                                    </div>
                                                    <span className="text-xs text-slate-300 w-12 text-right">{formatPct(s.weight)}</span>
                                                </div>
                                            ))}
                                        </div>
                                        <div className="border-t border-slate-800 pt-3 space-y-1">
                                            {[...(detail.portfolio.holdings || [])]
                                                .sort((a, b) => (b.weight || 0) - (a.weight || 0))
                                                .slice(0, 8)
                                                .map((h, i) => (
                                                    <div key={`${h.ticker || h.companyName}-${i}`} className="flex justify-between text-xs">
                                                        <span className="text-slate-300 truncate max-w-[60%]">{h.ticker || h.companyName || 'N/A'}</span>
                                                        <span className="text-slate-400">{formatPct(h.weight)}</span>
                                                    </div>
                                                ))}
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex items-center justify-center h-40 text-slate-500 border border-dashed border-slate-800 rounded-lg text-sm">
                                        {detail.error || 'Holdings data unavailable.'}
                                    </div>
                                )}
                            </div>

                            {/* Cumulative Returns */}
                            <div className="bg-slate-950 rounded-xl border border-slate-800 p-5">
                                <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-4">
                                    <TrendingUp size={16} className="text-emerald-400" />
                                    5-Year Return Growth
                                </h3>
                                {detail.loading ? (
                                    <div className="flex items-center justify-center h-48 text-slate-500">
                                        <Loader2 className="w-5 h-5 animate-spin" />
                                    </div>
                                ) : (detail.returns?.series?.length > 1) ? (
                                    <ReturnsSparkline series={detail.returns.series} />
                                ) : (
                                    <div className="flex items-center justify-center h-48 text-slate-500 border border-dashed border-slate-800 rounded-lg text-sm text-center px-6">
                                        {selectedRow?.emerging
                                            ? 'Clone return withheld — too few quarters of 13F history (and options/short exposure is not captured by the long-book clone).'
                                            : (detail.error || 'Return series unavailable.')}
                                    </div>
                                )}
                            </div>
                          </>)}

                          {activeTab === 'holdings' && (
                            <div className="bg-slate-950 rounded-xl border border-slate-800 p-5">
                                <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-4">
                                    <Briefcase size={16} className="text-blue-400" />
                                    Reported Holdings {tabs.holdings?.reportPeriod ? `(${tabs.holdings.reportPeriod})` : ''}
                                </h3>
                                {tabs.loading ? (
                                    <div className="flex items-center justify-center h-40 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>
                                ) : (tabs.holdings?.holdings?.length > 0) ? (
                                    <div className="space-y-1">
                                        <div className="flex justify-between text-[11px] uppercase tracking-wider text-slate-500 pb-2 border-b border-slate-800">
                                            <span>Holding</span><span>Value / Weight</span>
                                        </div>
                                        {tabs.holdings.holdings.slice(0, 50).map((h, i) => (
                                            <div key={`${h.ticker || h.cusip}-${i}`} className="flex justify-between text-xs py-1">
                                                <span className="text-slate-300 truncate max-w-[55%]" title={h.companyName}>
                                                    <span className="text-blue-400 font-medium">{h.ticker || '—'}</span> {h.companyName}
                                                </span>
                                                <span className="text-slate-400">{formatUsd(h.marketValueUsd)} · {formatPct(h.weight)}</span>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="flex items-center justify-center h-40 text-slate-500 border border-dashed border-slate-800 rounded-lg text-sm">
                                        {tabs.error || 'Holdings unavailable.'}
                                    </div>
                                )}
                            </div>
                          )}

                          {activeTab === 'changes' && (
                            <div className="space-y-4">
                                {tabs.loading ? (
                                    <div className="flex items-center justify-center h-40 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>
                                ) : tabs.changes?.changes ? (
                                    <>
                                        <div className="grid grid-cols-4 gap-2 text-center">
                                            {[
                                                { k: 'new', label: 'New', color: 'text-emerald-400' },
                                                { k: 'increased', label: 'Added', color: 'text-green-400' },
                                                { k: 'decreased', label: 'Trimmed', color: 'text-orange-400' },
                                                { k: 'soldOut', label: 'Exited', color: 'text-rose-400' },
                                            ].map((c) => (
                                                <div key={c.k} className="bg-slate-800/50 rounded-lg p-3 border border-slate-700/50">
                                                    <p className={`text-xl font-semibold ${c.color}`}>{tabs.changes.counts?.[c.k] ?? 0}</p>
                                                    <p className="text-[11px] text-slate-400 uppercase tracking-wider">{c.label}</p>
                                                </div>
                                            ))}
                                        </div>
                                        <div className="text-xs text-slate-500">
                                            Turnover est: {formatPct(tabs.changes.turnoverEstimatePct)} · Top-10 concentration: {formatPct(tabs.changes.top10Concentration)}
                                        </div>
                                        {[
                                            { k: 'new', label: 'New Buys', color: 'text-emerald-400' },
                                            { k: 'increased', label: 'Increased', color: 'text-green-400' },
                                            { k: 'decreased', label: 'Decreased', color: 'text-orange-400' },
                                            { k: 'soldOut', label: 'Sold Out', color: 'text-rose-400' },
                                        ].map((sec) => (
                                            (tabs.changes.changes[sec.k]?.length > 0) && (
                                                <div key={sec.k} className="bg-slate-950 rounded-xl border border-slate-800 p-4">
                                                    <h4 className={`text-xs font-semibold mb-2 ${sec.color}`}>{sec.label}</h4>
                                                    <div className="space-y-1">
                                                        {tabs.changes.changes[sec.k].slice(0, 10).map((c, i) => (
                                                            <div key={i} className="flex justify-between text-xs">
                                                                <span className="text-slate-300 truncate max-w-[60%]">{c.ticker || c.issuerName || c.key || 'N/A'}</span>
                                                                <span className="text-slate-400">
                                                                    {c.sharesChangePct != null ? `${(c.sharesChangePct * 100).toFixed(0)}%` : formatUsd(c.marketValueUsd)}
                                                                </span>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )
                                        ))}
                                    </>
                                ) : (
                                    <div className="flex items-center justify-center h-40 text-slate-500 border border-dashed border-slate-800 rounded-lg text-sm">
                                        {tabs.error || 'No position-change data for this fund yet.'}
                                    </div>
                                )}
                            </div>
                          )}

                          {activeTab === 'timeline' && (
                            <div className="bg-slate-950 rounded-xl border border-slate-800 p-5">
                                <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-4">
                                    <BarChart2 size={16} className="text-blue-400" /> Quarterly Timeline
                                </h3>
                                {tabs.loading ? (
                                    <div className="flex items-center justify-center h-40 text-slate-500"><Loader2 className="w-5 h-5 animate-spin" /></div>
                                ) : (tabs.timeline?.timeline?.length > 0) ? (
                                    <div className="space-y-1">
                                        <div className="grid grid-cols-4 gap-2 text-[11px] uppercase tracking-wider text-slate-500 pb-2 border-b border-slate-800">
                                            <span>Period</span><span className="text-right">13F Value</span><span className="text-right">Holdings</span><span className="text-right">Turnover</span>
                                        </div>
                                        {tabs.timeline.timeline.map((t) => (
                                            <div key={t.periodOfReport} className="grid grid-cols-4 gap-2 text-xs py-1">
                                                <span className="text-slate-300">{t.periodOfReport}</span>
                                                <span className="text-right text-slate-400">{formatUsd(t.total13FValueUsd)}</span>
                                                <span className="text-right text-slate-400">{t.holdingsCount ?? '—'}</span>
                                                <span className="text-right text-slate-400">{formatPct(t.turnoverEstimatePct)}</span>
                                            </div>
                                        ))}
                                    </div>
                                ) : (
                                    <div className="flex items-center justify-center h-40 text-slate-500 border border-dashed border-slate-800 rounded-lg text-sm">
                                        {tabs.error || 'No timeline data for this fund yet.'}
                                    </div>
                                )}
                            </div>
                          )}

                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
