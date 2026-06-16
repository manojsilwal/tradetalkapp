import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import GlobalCapFlowDashboard from './macro/GlobalCapFlowDashboard';
import { FRED_SNAPSHOT_FALLBACK } from './macro/fredSnapshotFallback';
import { useAnalysisHistory } from './AnalysisContext';
import { FreshnessBadge } from './components/Freshness';
import { API_BASE_URL, apiFetch } from './api';
import './macro/MacroUI.css';

function mergeFredPayload(live) {
    if (live && (live.fed_funds_rate != null || live.cpi_yoy != null)) {
        return live;
    }
    return { ...FRED_SNAPSHOT_FALLBACK, ...live };
}

export default function MacroUI() {
    const { macroState, loadMacro } = useAnalysisHistory();
    const { data, loading, error } = macroState;
    const [refreshing, setRefreshing] = useState(false);
    const [fred, setFred] = useState(null);
    const [fredLoading, setFredLoading] = useState(true);

    useEffect(() => {
        loadMacro(true);
        // eslint-disable-next-line react-hooks/exhaustive-deps -- refresh macro once on page entry
    }, []);

    const loadFred = React.useCallback(async () => {
        setFredLoading(true);
        try {
            const json = await apiFetch(`${API_BASE_URL}/macro/fred-snapshot`);
            setFred(mergeFredPayload(json));
        } catch {
            setFred(FRED_SNAPSHOT_FALLBACK);
        } finally {
            setFredLoading(false);
        }
    }, []);

    useEffect(() => {
        loadFred();
    }, [loadFred]);

    const handleRefresh = async () => {
        setRefreshing(true);
        try {
            await Promise.all([loadMacro(true), loadFred()]);
        } finally {
            setRefreshing(false);
        }
    };

    const isStress = data?.credit_stress_index > 1.1;
    const fedFunds = data?.fed_funds_rate ?? fred?.fed_funds_rate;
    const cpiYoy = data?.cpi_yoy ?? fred?.cpi_yoy;
    const fredDegraded = Boolean(fred?.degraded) && data?.fed_funds_rate == null && data?.cpi_yoy == null;
    const fredFreshness = fred?.data_freshness ?? data?.data_freshness;

    return (
        <div className="macro-page macro-page-bleed fade-in">
            <div className="macro-shell">
                {error && (
                    <div className="macro-error">{error}</div>
                )}

                <GlobalCapFlowDashboard
                    macroData={data}
                    loading={loading}
                    onRefresh={handleRefresh}
                    refreshing={refreshing || loading}
                />

                <div className="macro-section">
                    <div className="macro-panel" style={{ padding: '16px 18px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
                            <span className="macro-panel-header" style={{ border: 'none', padding: 0 }}>
                                Core_macro_indicators
                            </span>
                            {fredFreshness && <FreshnessBadge freshness={fredFreshness} />}
                            {fredDegraded && !fredFreshness && (
                                <span className="macro-freshness-chip macro-freshness-stale" title="Live FRED unavailable; showing cached seed values">
                                    Cached
                                </span>
                            )}
                        </div>

                        {loading && !data ? (
                            <div className="macro-loading">
                                <Loader2 className="spinner" size={24} />
                                Loading indicators…
                            </div>
                        ) : !data ? (
                            <div className="macro-loading">No core macro indicators data available.</div>
                        ) : (
                            <div className="macro-indicators-strip">
                                <div className="macro-indicator-card" data-testid="macro-vix-card">
                                    <div className="macro-indicator-label">VIX (Volatility)</div>
                                    <div className="macro-indicator-value" style={{ color: '#fff' }}>
                                        {data.vix_level != null ? Number(data.vix_level).toFixed(2) : 'N/A'}
                                    </div>
                                </div>
                                <div className="macro-indicator-card" data-testid="macro-credit-stress-card">
                                    <div className="macro-indicator-label">Credit Stress Index</div>
                                    <div className="macro-indicator-value" style={{ color: isStress ? '#ff4d4d' : '#39ff14' }}>
                                        {data.credit_stress_index != null ? Number(data.credit_stress_index).toFixed(2) : 'N/A'}
                                    </div>
                                </div>
                                <div className="macro-indicator-card" data-testid="macro-fed-funds-card">
                                    <div className="macro-indicator-label">US Fed Funds Rate</div>
                                    {fredLoading && fedFunds == null ? (
                                        <div className="macro-loading" style={{ minHeight: 40, justifyContent: 'flex-start' }}>
                                            <Loader2 size={16} className="spinner" />
                                        </div>
                                    ) : fedFunds != null ? (
                                        <div className="macro-indicator-value" style={{ color: '#38bdf8' }}>
                                            {Number(fedFunds).toFixed(2)}%
                                        </div>
                                    ) : (
                                        <div style={{ color: '#64748b', fontSize: '0.82rem', fontStyle: 'italic' }}>
                                            Live data unavailable
                                        </div>
                                    )}
                                </div>
                                <div className="macro-indicator-card" data-testid="macro-cpi-card">
                                    <div className="macro-indicator-label">US Core CPI (YoY)</div>
                                    {fredLoading && cpiYoy == null ? (
                                        <div className="macro-loading" style={{ minHeight: 40, justifyContent: 'flex-start' }}>
                                            <Loader2 size={16} className="spinner" />
                                        </div>
                                    ) : cpiYoy != null ? (
                                        <div className="macro-indicator-value">{Number(cpiYoy).toFixed(2)}%</div>
                                    ) : (
                                        <div style={{ color: '#64748b', fontSize: '0.82rem', fontStyle: 'italic' }}>
                                            Live data unavailable
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
