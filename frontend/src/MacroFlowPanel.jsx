import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw, GitBranch, Activity, Share2 } from 'lucide-react';
import {
    ResponsiveContainer,
    ScatterChart,
    Scatter,
    XAxis,
    YAxis,
    CartesianGrid,
    Tooltip,
    ZAxis,
    Cell,
} from 'recharts';
import { API_BASE_URL, apiFetch } from './api';

const INTERVALS = [
    { id: '1d', label: '1D' },
    { id: '1w', label: '1W' },
    { id: '1m', label: '1M' },
    { id: '1y', label: '1Y' },
];

export default function MacroFlowPanel() {
    const [interval, setInterval] = useState('1w');
    const [view, setView] = useState('rrg');
    const [points, setPoints] = useState([]);
    const [sankey, setSankey] = useState({ nodes: [], links: [] });
    const [chain, setChain] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState(null);
    const [selected, setSelected] = useState(null);

    const loadRrg = useCallback(async () => {
        const json = await apiFetch(`${API_BASE_URL}/macro/flow/rrg?interval=${encodeURIComponent(interval)}`);
        const pts = Array.isArray(json.points) ? json.points : [];
        setPoints(pts);
        setSelected((prev) => {
            if (prev && pts.some((p) => p.category_id === prev)) return prev;
            return pts[0]?.category_id ?? null;
        });
    }, [interval]);

    const loadSankey = useCallback(async () => {
        const json = await apiFetch(`${API_BASE_URL}/macro/flow/sankey?interval=${encodeURIComponent(interval)}`);
        setSankey({ nodes: json.nodes || [], links: json.links || [] });
    }, [interval]);

    const loadChain = useCallback(async () => {
        const json = await apiFetch(
            `${API_BASE_URL}/macro/flow/value-chain?theme=ai-infra&interval=${encodeURIComponent(interval)}`
        );
        setChain(json);
    }, [interval]);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            setLoading(true);
            setError(null);
            try {
                await loadRrg();
                if (view === 'sankey') await loadSankey();
                if (view === 'chain') await loadChain();
            } catch (e) {
                if (!cancelled) setError(e.message || String(e));
            } finally {
                if (!cancelled) setLoading(false);
            }
        })();
        return () => { cancelled = true; };
    }, [interval, view, loadRrg, loadSankey, loadChain]);

    const onRefresh = async () => {
        setRefreshing(true);
        setError(null);
        try {
            await apiFetch(`${API_BASE_URL}/macro/flow/refresh?interval=${encodeURIComponent(interval)}`, {
                method: 'POST',
            });
            await loadRrg();
            if (view === 'sankey') await loadSankey();
            if (view === 'chain') await loadChain();
        } catch (e) {
            setError(e.message || String(e));
        } finally {
            setRefreshing(false);
        }
    };

    const chartData = points.map((p) => ({
        ...p,
        x: Number(p.rs_ratio) || 0,
        y: Number(p.rs_momentum) || 0,
        z: Math.max(4, 14 + 40 * Math.abs(Number(p.flow_score) || 0)),
    }));

    const sel = points.find((p) => p.category_id === selected) || null;

    return (
        <div
            className="dash-card glass-panel fade-in"
            data-testid="macro-flow-section"
            style={{ padding: '24px', borderRadius: '16px', display: 'flex', flexDirection: 'column', gap: '16px' }}
        >
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                    <Activity color="var(--accent-orange)" />
                    <div>
                        <h3 style={{ margin: 0 }}>Thematic capital flow</h3>
                        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: '4px 0 0 0' }}>
                            Relative strength vs SPY, CMF blend, and QA verdicts by theme (not raw sector ETF %).
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    data-testid="macro-flow-refresh"
                    onClick={onRefresh}
                    disabled={refreshing}
                    className="glass-panel"
                    style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 8,
                        padding: '8px 14px',
                        borderRadius: 10,
                        border: '1px solid rgba(255,255,255,0.12)',
                        background: 'rgba(255,255,255,0.04)',
                        color: 'var(--text-primary)',
                        cursor: refreshing ? 'wait' : 'pointer',
                    }}
                >
                    {refreshing ? <Loader2 className="spinner" size={18} /> : <RefreshCw size={18} />}
                    Refresh data
                </button>
            </div>

            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center' }}>
                <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Interval</span>
                {INTERVALS.map((iv) => (
                    <button
                        type="button"
                        key={iv.id}
                        data-testid={`macro-flow-interval-${iv.id}`}
                        onClick={() => setInterval(iv.id)}
                        style={{
                            padding: '6px 12px',
                            borderRadius: 8,
                            border: interval === iv.id ? '1px solid var(--accent-purple)' : '1px solid rgba(255,255,255,0.1)',
                            background: interval === iv.id ? 'rgba(124,58,237,0.2)' : 'transparent',
                            color: 'var(--text-primary)',
                            cursor: 'pointer',
                        }}
                    >
                        {iv.label}
                    </button>
                ))}
            </div>

            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                {[
                    { id: 'rrg', label: 'RRG', icon: Activity },
                    { id: 'sankey', label: 'Flow links', icon: Share2 },
                    { id: 'chain', label: 'Value chain', icon: GitBranch },
                ].map((v) => {
                    const Icon = v.icon;
                    return (
                        <button
                            type="button"
                            key={v.id}
                            data-testid={`macro-flow-view-${v.id}`}
                            onClick={() => setView(v.id)}
                            style={{
                                display: 'inline-flex',
                                alignItems: 'center',
                                gap: 6,
                                padding: '8px 12px',
                                borderRadius: 8,
                                border: view === v.id ? '1px solid var(--accent-blue)' : '1px solid rgba(255,255,255,0.1)',
                                background: view === v.id ? 'rgba(59,130,246,0.15)' : 'transparent',
                                color: 'var(--text-primary)',
                                cursor: 'pointer',
                            }}
                        >
                            <Icon size={16} />
                            {v.label}
                        </button>
                    );
                })}
            </div>

            {error && (
                <div style={{ color: 'var(--accent-red)', fontSize: '0.9rem' }}>{error}</div>
            )}

            {loading ? (
                <div style={{ display: 'flex', justifyContent: 'center', padding: 40 }}>
                    <Loader2 className="spinner" size={36} color="var(--accent-blue)" />
                </div>
            ) : view === 'rrg' ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
                    <div data-testid="macro-rrg-chart" style={{ width: '100%', height: 360 }}>
                        <ResponsiveContainer width="100%" height="100%">
                            <ScatterChart margin={{ top: 16, right: 16, bottom: 8, left: 8 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                                <XAxis type="number" dataKey="x" name="RS ratio" stroke="var(--text-muted)" />
                                <YAxis type="number" dataKey="y" name="RS momentum" stroke="var(--text-muted)" />
                                <ZAxis type="number" dataKey="z" range={[60, 400]} />
                                <Tooltip
                                    cursor={{ strokeDasharray: '3 3' }}
                                    contentStyle={{ backgroundColor: 'rgba(15,23,42,0.95)', border: '1px solid rgba(255,255,255,0.1)' }}
                                    formatter={(value, name) => {
                                        if (name === 'x') return [value, 'RS ratio'];
                                        if (name === 'y') return [value, 'RS momentum'];
                                        return [value, name];
                                    }}
                                    labelFormatter={() => ''}
                                />
                                <Scatter name="Themes" data={chartData}>
                                    {chartData.map((entry) => (
                                        <Cell
                                            key={entry.category_id}
                                            fill={entry.color_hex || '#6366f1'}
                                            stroke={selected === entry.category_id ? '#fff' : 'rgba(0,0,0,0.3)'}
                                            strokeWidth={selected === entry.category_id ? 2 : 1}
                                            onClick={() => setSelected(entry.category_id)}
                                            style={{ cursor: 'pointer' }}
                                        />
                                    ))}
                                </Scatter>
                            </ScatterChart>
                        </ResponsiveContainer>
                    </div>
                    <div
                        data-testid="macro-flow-agent-panel"
                        style={{
                            padding: '16px',
                            borderRadius: '12px',
                            background: 'rgba(255,255,255,0.03)',
                            border: '1px solid rgba(255,255,255,0.08)',
                        }}
                    >
                        <h4 style={{ margin: '0 0 8px 0' }}>Agent intel</h4>
                        {!sel ? (
                            <p style={{ color: 'var(--text-muted)', margin: 0 }}>Select a bubble on the chart.</p>
                        ) : (
                            <>
                                <div style={{ fontWeight: 700, fontSize: '1.1rem' }}>{sel.name}</div>
                                <div style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: 8 }}>
                                    Verdict: <strong>{sel.qa_verdict}</strong>
                                    {sel.conflict_flag ? ' · conflict' : ''}
                                </div>
                                <div style={{ fontSize: '0.85rem', lineHeight: 1.5 }}>
                                    Flow {Number(sel.flow_score).toFixed(2)} · CMF {Number(sel.cmf).toFixed(2)}
                                    <br />
                                    Fundamentals: {sel.fundamental_band}
                                    <br />
                                    {sel.notes}
                                </div>
                                {Array.isArray(sel.top_movers) && sel.top_movers.length > 0 && (
                                    <div style={{ marginTop: 10, fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                        <strong>Top movers</strong>
                                        <ul style={{ margin: '6px 0 0 0', paddingLeft: 18 }}>
                                            {sel.top_movers.map((m) => (
                                                <li key={m.symbol}>
                                                    {m.symbol}: {m.period_change_pct > 0 ? '+' : ''}
                                                    {m.period_change_pct}%
                                                </li>
                                            ))}
                                        </ul>
                                    </div>
                                )}
                            </>
                        )}
                    </div>
                </div>
            ) : view === 'sankey' ? (
                <div data-testid="macro-sankey-panel" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: 0 }}>
                        Propagated theme links (magnitude × edge strength). Full Sankey layout is planned; list view is the stable MVP.
                    </p>
                    {(sankey.links || []).length === 0 ? (
                        <p style={{ color: 'var(--text-muted)' }}>No links for this interval yet — run refresh after seed.</p>
                    ) : (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                            {sankey.links.map((l) => (
                                <li key={`${l.source}-${l.target}-${l.edge_id}`} style={{ marginBottom: 6 }}>
                                    <strong>{l.source}</strong> → <strong>{l.target}</strong>
                                    {' '}
                                    <span style={{ color: 'var(--text-muted)' }}>
                                        ({(l.value || 0).toFixed(3)})
                                    </span>
                                    {l.description ? <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{l.description}</div> : null}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            ) : (
                <div data-testid="macro-value-chain-panel" style={{ fontSize: '0.9rem' }}>
                    {!chain?.links?.length ? (
                        <p style={{ color: 'var(--text-muted)' }}>No edges for this theme.</p>
                    ) : (
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                            {chain.links.map((l) => (
                                <li key={`${l.source}-${l.target}`}>
                                    {l.source} → {l.target}
                                    {l.flow_magnitude != null && (
                                        <span style={{ color: 'var(--text-muted)' }}> ({Number(l.flow_magnitude).toFixed(3)})</span>
                                    )}
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            )}
        </div>
    );
}
