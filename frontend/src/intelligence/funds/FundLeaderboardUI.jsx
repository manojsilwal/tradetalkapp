import React, { useState, useEffect } from 'react';
import { Trophy, TrendingUp, TrendingDown, Target, Building2, BarChart2, Briefcase, FileText, ShieldAlert, Award, ChevronRight, X, Loader2, AlertTriangle, PlayCircle, Info } from 'lucide-react';

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

export default function FundLeaderboardUI() {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedFundId, setSelectedFundId] = useState(null);
    const [mode, setMode] = useState('13f_investable');

    useEffect(() => {
        // Fetch or use mock data
        setLoading(true);
        // Mock data to unblock UI while Phase 2 connects backend
        const mockRows = [
            {
                rank: 1,
                fundId: 'f1',
                fundName: 'Pershing Square Capital',
                managerType: 'Hedge Fund',
                strategyTags: ['Concentrated', 'Activist'],
                cagr10Y: 0.185,
                roicProxy10Y: 4.4,
                alphaVsSP500: 0.052,
                sharpe10Y: 1.3,
                maxDrawdown10Y: -0.21,
                latest13FValueUsd: 10400000000,
                latestReportPeriod: '2023-12-31',
                topSector: 'Consumer Discretionary',
                topSectorWeight: 0.35,
                top10HoldingsWeight: 0.95,
                dataConfidenceScore: 85,
                dataConfidenceLabel: 'Good',
                lastFilingDate: '2024-02-14'
            },
            {
                rank: 2,
                fundId: 'f2',
                fundName: 'Renaissance Technologies',
                managerType: 'Quant',
                strategyTags: ['Quant', 'High Frequency'],
                cagr10Y: 0.125,
                roicProxy10Y: 2.1,
                alphaVsSP500: -0.01,
                sharpe10Y: 0.8,
                maxDrawdown10Y: -0.15,
                latest13FValueUsd: 65000000000,
                latestReportPeriod: '2023-12-31',
                topSector: 'Information Technology',
                topSectorWeight: 0.22,
                top10HoldingsWeight: 0.12,
                dataConfidenceScore: 45,
                dataConfidenceLabel: 'Low',
                lastFilingDate: '2024-02-14'
            }
        ];

        setTimeout(() => {
            setRows(mockRows);
            setLoading(false);
        }, 500);
    }, [mode]);

    const getConfidenceColor = (label) => {
        switch (label) {
            case 'High': return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
            case 'Good': return 'text-green-400 bg-green-400/10 border-green-400/20';
            case 'Medium': return 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20';
            case 'Low': return 'text-orange-400 bg-orange-400/10 border-orange-400/20';
            default: return 'text-red-400 bg-red-400/10 border-red-400/20';
        }
    };

    return (
        <div className="w-full max-w-7xl mx-auto space-y-6">
            <header className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-white flex items-center gap-3">
                        <Trophy className="text-amber-400" size={28} />
                        Fund Leaderboard
                    </h1>
                    <p className="text-slate-400 mt-1">Institutional Intelligence & 10-Year Clone Returns</p>
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
            <div className="flex gap-4 items-center bg-slate-900/50 p-3 rounded-lg border border-slate-800">
                <div className="flex items-center gap-2">
                    <span className="text-sm text-slate-400">Mode:</span>
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
                                <th className="px-4 py-3 text-right">10Y CAGR</th>
                                <th className="px-4 py-3 text-right">10Y Alpha</th>
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
                            ) : rows.length === 0 ? (
                                <tr>
                                    <td colSpan="10" className="px-4 py-8 text-center text-slate-500">
                                        No funds match the current filters.
                                    </td>
                                </tr>
                            ) : rows.map((row) => (
                                <tr key={row.fundId} className="hover:bg-slate-800/40 transition-colors group">
                                    <td className="px-4 py-3 font-medium text-white">#{row.rank}</td>
                                    <td className="px-4 py-3">
                                        <div className="font-medium text-blue-400 group-hover:text-blue-300 transition-colors cursor-pointer" onClick={() => setSelectedFundId(row.fundId)}>
                                            {row.fundName}
                                        </div>
                                        <div className="text-xs text-slate-500 flex items-center gap-2 mt-0.5">
                                            <span>{row.managerType}</span>
                                            {row.strategyTags && row.strategyTags.length > 0 && (
                                                <>
                                                    <span className="w-1 h-1 rounded-full bg-slate-600"></span>
                                                    <span>{row.strategyTags[0]}</span>
                                                </>
                                            )}
                                        </div>
                                    </td>
                                    <td className={`px-4 py-3 text-right font-medium ${row.cagr10Y > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                                        {formatPct(row.cagr10Y)}
                                    </td>
                                    <td className={`px-4 py-3 text-right text-sm ${row.alphaVsSP500 > 0 ? 'text-emerald-400/80' : 'text-rose-400/80'}`}>
                                        {row.alphaVsSP500 > 0 ? '+' : ''}{formatPct(row.alphaVsSP500)}
                                    </td>
                                    <td className="px-4 py-3 text-right text-sm text-slate-300">{formatDec(row.sharpe10Y)}</td>
                                    <td className="px-4 py-3 text-right text-sm text-rose-400">{formatPct(row.maxDrawdown10Y)}</td>
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
                                <h2 className="text-xl font-bold text-white">
                                    {rows.find(r => r.fundId === selectedFundId)?.fundName}
                                </h2>
                                <p className="text-sm text-slate-400 mt-1">13F Portfolio Analysis & 10Y Return Metrics</p>
                            </div>
                            <button
                                onClick={() => setSelectedFundId(null)}
                                className="p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition-colors"
                            >
                                <X size={20} />
                            </button>
                        </div>

                        {/* Drawer Content Area */}
                        <div className="flex-1 overflow-y-auto p-5 space-y-6">

                            {/* Summary Card */}
                            <div className="grid grid-cols-2 gap-4">
                                <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700/50">
                                    <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-1">Latest 13F Market Value</p>
                                    <p className="text-2xl font-semibold text-white">
                                        {formatUsd(rows.find(r => r.fundId === selectedFundId)?.latest13FValueUsd)}
                                    </p>
                                    <p className="text-xs text-slate-500 mt-1">As of {rows.find(r => r.fundId === selectedFundId)?.latestReportPeriod}</p>
                                </div>
                                <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700/50">
                                    <p className="text-slate-400 text-xs font-medium uppercase tracking-wider mb-1">10Y CAGR</p>
                                    <p className="text-2xl font-semibold text-emerald-400">
                                        {formatPct(rows.find(r => r.fundId === selectedFundId)?.cagr10Y)}
                                    </p>
                                    <p className="text-xs text-slate-500 mt-1">Investable Clone Return</p>
                                </div>
                            </div>

                            {/* Trust Indicator */}
                            {rows.find(r => r.fundId === selectedFundId)?.dataConfidenceLabel === 'Low' && (
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

                            {/* Portfolio Preview */}
                            <div className="bg-slate-950 rounded-xl border border-slate-800 p-5">
                                <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-4">
                                    <Briefcase size={16} className="text-blue-400" />
                                    Portfolio Allocation
                                </h3>
                                <div className="flex items-center justify-center h-40 text-slate-500 border border-dashed border-slate-800 rounded-lg">
                                    Sector & Holdings Charts (Backend Required)
                                </div>
                            </div>

                            {/* Performance Chart Preview */}
                            <div className="bg-slate-950 rounded-xl border border-slate-800 p-5">
                                <h3 className="text-sm font-semibold text-white flex items-center gap-2 mb-4">
                                    <TrendingUp size={16} className="text-emerald-400" />
                                    10-Year Return Growth
                                </h3>
                                <div className="flex items-center justify-center h-48 text-slate-500 border border-dashed border-slate-800 rounded-lg">
                                    Cumulative Returns Chart (Backend Required)
                                </div>
                            </div>

                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
