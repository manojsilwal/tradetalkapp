import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import {
    Activity,
    AlertTriangle,
    BarChart3,
    CheckCircle2,
    Loader2,
    Play,
    RefreshCw,
    Shield,
    Sparkles,
    TrendingDown,
    TrendingUp,
} from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';

const STATUS_STYLES = {
    pass: { bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.35)', color: '#4ade80', label: 'Pass' },
    fail: { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', color: '#f87171', label: 'Fail' },
    hold: { bg: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.35)', color: '#94a3b8', label: 'Hold' },
    shadow_recommended: {
        bg: 'rgba(251,191,36,0.12)',
        border: 'rgba(251,191,36,0.35)',
        color: '#fbbf24',
        label: 'Shadow recommended',
    },
};

function statusStyle(status) {
    const key = String(status || 'hold').toLowerCase();
    return STATUS_STYLES[key] || STATUS_STYLES.hold;
}

function KpiCard({ label, value, sub, accent }) {
    return (
        <div
            className="glass-panel"
            style={{
                padding: '18px 20px',
                borderRadius: '14px',
                border: '1px solid rgba(255,255,255,0.06)',
                background: 'linear-gradient(145deg, rgba(255,255,255,0.04), rgba(255,255,255,0.01))',
            }}
        >
            <div style={{ fontSize: '0.72rem', color: '#64748b', textTransform: 'uppercase', letterSpacing: 1.2, marginBottom: 8 }}>
                {label}
            </div>
            <div style={{ fontSize: '1.65rem', fontWeight: 800, color: accent || '#f8fafc', lineHeight: 1.1 }}>
                {value ?? '—'}
            </div>
            {sub && <div style={{ fontSize: '0.8rem', color: '#94a3b8', marginTop: 6 }}>{sub}</div>}
        </div>
    );
}

function VariantTable({ scores }) {
    const rows = Object.entries(scores || {});
    if (!rows.length) {
        return <p style={{ color: '#94a3b8' }}>No variant scores in this run.</p>;
    }
    return (
        <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', color: '#64748b', textAlign: 'left' }}>
                        <th style={{ padding: '10px 8px' }}>Variant</th>
                        <th style={{ padding: '10px 8px' }}>AES</th>
                        <th style={{ padding: '10px 8px' }}>Task</th>
                        <th style={{ padding: '10px 8px' }}>RAG</th>
                        <th style={{ padding: '10px 8px' }}>Orch</th>
                        <th style={{ padding: '10px 8px' }}>Safety</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map(([key, s]) => (
                        <tr key={key} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                            <td style={{ padding: '12px 8px', fontWeight: 600 }}>{key.replace(/_/g, ' ')}</td>
                            <td style={{ padding: '12px 8px', color: '#a78bfa', fontWeight: 700 }}>{s.aes ?? '—'}</td>
                            <td style={{ padding: '12px 8px' }}>{s.task_success ?? '—'}</td>
                            <td style={{ padding: '12px 8px' }}>{s.rag_quality ?? '—'}</td>
                            <td style={{ padding: '12px 8px' }}>{s.orchestration ?? '—'}</td>
                            <td style={{ padding: '12px 8px' }}>{s.safety_groundedness ?? '—'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function SwarmScoreUI() {
    const [tab, setTab] = useState('report');
    const [mode, setMode] = useState('fixture');
    const [loading, setLoading] = useState(false);
    const [bootLoading, setBootLoading] = useState(true);
    const [error, setError] = useState(null);
    const [summary, setSummary] = useState(null);
    const [results, setResults] = useState(null);
    const [reportMd, setReportMd] = useState('');

    const loadLatest = useCallback(async () => {
        setError(null);
        try {
            const [sum, res, rep] = await Promise.all([
                apiFetch(`${API_BASE_URL}/admin/swarm-score/summary`).catch(() => null),
                apiFetch(`${API_BASE_URL}/admin/swarm-score/results`).catch(() => null),
                apiFetch(`${API_BASE_URL}/admin/swarm-score/report?format=json`).catch(() => null),
            ]);
            if (sum) setSummary(sum);
            if (res) setResults(res);
            if (rep?.markdown) setReportMd(rep.markdown);
        } catch (e) {
            setError(e.message);
        } finally {
            setBootLoading(false);
        }
    }, []);

    useEffect(() => {
        loadLatest();
    }, [loadLatest]);

    const runEval = async () => {
        setLoading(true);
        setError(null);
        try {
            const out = await apiFetch(`${API_BASE_URL}/admin/swarm-score/run`, {
                method: 'POST',
                body: JSON.stringify({ mode }),
            });
            if (out.summary) setSummary(out.summary);
            await loadLatest();
            setTab('report');
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    const badge = useMemo(() => statusStyle(summary?.status), [summary]);
    const swarmAdv = summary?.swarm_advantage_score;
    const advIcon = swarmAdv == null ? null : swarmAdv >= 0 ? TrendingUp : TrendingDown;

    return (
        <div className="consumer-container fade-in" data-testid="swarm-score-page">
            <div className="header-section" style={{ marginBottom: 28 }}>
                <div className="title-group">
                    <h2 style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <Sparkles size={26} color="#a78bfa" />
                        SwarmScore Evaluator
                    </h2>
                    <p>Run the full agentic architecture evaluation and review the weekly effectiveness report.</p>
                </div>
                <div className="controls" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                    <select
                        value={mode}
                        onChange={(e) => setMode(e.target.value)}
                        style={{
                            padding: '10px 12px',
                            borderRadius: 10,
                            background: 'rgba(255,255,255,0.05)',
                            border: '1px solid rgba(255,255,255,0.1)',
                            color: '#e2e8f0',
                        }}
                        data-testid="swarm-score-mode"
                    >
                        <option value="fixture">Fixture (offline)</option>
                        <option value="dry-run">Dry-run (validate only)</option>
                    </select>
                    <button onClick={runEval} disabled={loading} data-testid="swarm-score-run">
                        {loading ? <Loader2 className="spinner" size={18} /> : <Play size={18} />}
                        <span style={{ marginLeft: 6 }}>{loading ? 'Running…' : 'Run evaluation'}</span>
                    </button>
                    <button onClick={loadLatest} disabled={loading || bootLoading} style={{ opacity: 0.9 }}>
                        <RefreshCw size={16} />
                        <span style={{ marginLeft: 6 }}>Refresh</span>
                    </button>
                </div>
            </div>

            {error && (
                <div
                    style={{
                        padding: '14px 18px',
                        borderRadius: 12,
                        marginBottom: 20,
                        background: 'rgba(239,68,68,0.1)',
                        border: '1px solid rgba(239,68,68,0.25)',
                        color: '#fca5a5',
                        fontSize: '0.88rem',
                    }}
                >
                    {error}
                </div>
            )}

            {bootLoading && (
                <div style={{ textAlign: 'center', padding: 48, color: '#94a3b8' }}>
                    <Loader2 className="spinner" size={32} />
                    <p style={{ marginTop: 12 }}>Loading latest evaluation…</p>
                </div>
            )}

            {!bootLoading && summary && (
                <>
                    <div
                        style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 12,
                            marginBottom: 20,
                            padding: '14px 18px',
                            borderRadius: 14,
                            background: badge.bg,
                            border: `1px solid ${badge.border}`,
                        }}
                    >
                        <Shield size={22} color={badge.color} />
                        <div style={{ flex: 1 }}>
                            <div style={{ fontWeight: 700, color: badge.color, fontSize: '1rem' }}>
                                {summary.dashboard_badge?.label || badge.label}
                            </div>
                            <div style={{ color: '#94a3b8', fontSize: '0.82rem', marginTop: 4 }}>
                                Run {summary.run_id || '—'} · {summary.timestamp || '—'}
                            </div>
                        </div>
                        <div style={{ textAlign: 'right', color: '#cbd5e1', fontSize: '0.85rem' }}>
                            Winner: <strong>{summary.winner || '—'}</strong>
                        </div>
                    </div>

                    <div
                        style={{
                            display: 'grid',
                            gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))',
                            gap: 16,
                            marginBottom: 24,
                        }}
                    >
                        <KpiCard label="Production AES" value={summary.production_score} accent="#60a5fa" />
                        <KpiCard label="Winning AES" value={summary.winning_score} accent="#a78bfa" />
                        <KpiCard
                            label="Score delta"
                            value={summary.score_delta != null ? `+${summary.score_delta}` : '—'}
                            sub="Candidate vs production"
                        />
                        <KpiCard
                            label="Swarm advantage"
                            value={swarmAdv ?? '—'}
                            sub={advIcon ? 'Production minus best simpler baseline' : ''}
                            accent={swarmAdv >= 0 ? '#4ade80' : '#f87171'}
                        />
                        <KpiCard label="Complexity tax" value={summary.complexity_tax} />
                        <KpiCard label="p95 latency" value={summary.p95_latency_ms != null ? `${summary.p95_latency_ms} ms` : '—'} />
                    </div>

                    {summary.recommendation && (
                        <div
                            className="glass-panel"
                            style={{
                                padding: '16px 20px',
                                borderRadius: 14,
                                marginBottom: 24,
                                borderLeft: '4px solid #a78bfa',
                            }}
                        >
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, fontWeight: 600 }}>
                                <Activity size={18} color="#a78bfa" />
                                Recommendation
                            </div>
                            <p style={{ margin: 0, color: '#cbd5e1', lineHeight: 1.65 }}>{summary.recommendation}</p>
                            {summary.top_actions?.length > 0 && (
                                <ul style={{ margin: '14px 0 0', paddingLeft: 20, color: '#94a3b8', lineHeight: 1.7 }}>
                                    {summary.top_actions.map((a) => (
                                        <li key={a}>{a}</li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
                        {['report', 'scores', 'ablation'].map((t) => (
                            <button
                                key={t}
                                onClick={() => setTab(t)}
                                style={{
                                    padding: '10px 16px',
                                    borderRadius: 10,
                                    border: tab === t ? '1px solid #a78bfa' : '1px solid rgba(255,255,255,0.08)',
                                    background: tab === t ? 'rgba(167,139,250,0.15)' : 'transparent',
                                    color: tab === t ? '#e9d5ff' : '#94a3b8',
                                    fontWeight: tab === t ? 600 : 400,
                                    cursor: 'pointer',
                                }}
                            >
                                {t === 'report' ? 'Report' : t === 'scores' ? 'Variant scores' : 'Ablations'}
                            </button>
                        ))}
                    </div>

                    <div
                        className="glass-panel"
                        style={{
                            padding: tab === 'report' ? '28px 32px' : '20px 24px',
                            borderRadius: 16,
                            minHeight: 320,
                            maxHeight: tab === 'report' ? 'none' : 520,
                            overflow: tab === 'report' ? 'visible' : 'auto',
                        }}
                    >
                        {tab === 'report' && (
                            reportMd ? (
                                <ReactMarkdown
                                    components={{
                                        h1: ({ ...props }) => (
                                            <h1 style={{ color: '#f8fafc', fontSize: '1.75rem', marginBottom: 16, fontWeight: 800 }} {...props} />
                                        ),
                                        h2: ({ ...props }) => (
                                            <h2
                                                style={{
                                                    color: '#f1f5f9',
                                                    fontSize: '1.25rem',
                                                    marginTop: 28,
                                                    marginBottom: 12,
                                                    fontWeight: 700,
                                                    borderBottom: '1px solid rgba(255,255,255,0.08)',
                                                    paddingBottom: 8,
                                                }}
                                                {...props}
                                            />
                                        ),
                                        h3: ({ ...props }) => (
                                            <h3 style={{ color: '#e2e8f0', fontSize: '1.05rem', marginTop: 20, marginBottom: 8 }} {...props} />
                                        ),
                                        p: ({ ...props }) => (
                                            <p style={{ color: '#94a3b8', lineHeight: 1.7, marginBottom: 12 }} {...props} />
                                        ),
                                        li: ({ ...props }) => (
                                            <li style={{ color: '#94a3b8', marginBottom: 6, lineHeight: 1.6 }} {...props} />
                                        ),
                                        table: ({ ...props }) => (
                                            <table
                                                style={{
                                                    width: '100%',
                                                    borderCollapse: 'collapse',
                                                    marginBottom: 20,
                                                    fontSize: '0.85rem',
                                                }}
                                                {...props}
                                            />
                                        ),
                                        th: ({ ...props }) => (
                                            <th
                                                style={{
                                                    borderBottom: '1px solid rgba(255,255,255,0.12)',
                                                    padding: '10px 8px',
                                                    textAlign: 'left',
                                                    color: '#64748b',
                                                }}
                                                {...props}
                                            />
                                        ),
                                        td: ({ ...props }) => (
                                            <td
                                                style={{
                                                    borderBottom: '1px solid rgba(255,255,255,0.06)',
                                                    padding: '10px 8px',
                                                    color: '#cbd5e1',
                                                }}
                                                {...props}
                                            />
                                        ),
                                        strong: ({ ...props }) => <strong style={{ color: '#e2e8f0' }} {...props} />,
                                    }}
                                >
                                    {reportMd}
                                </ReactMarkdown>
                            ) : (
                                <div style={{ textAlign: 'center', padding: 40, color: '#94a3b8' }}>
                                    <BarChart3 size={40} style={{ marginBottom: 12, opacity: 0.5 }} />
                                    <p>No report yet. Click <strong>Run evaluation</strong> to generate one.</p>
                                </div>
                            )
                        )}

                        {tab === 'scores' && <VariantTable scores={results?.scores} />}

                        {tab === 'ablation' && (
                            <div style={{ overflowX: 'auto' }}>
                                {(results?.ablation_results || []).length === 0 ? (
                                    <p style={{ color: '#94a3b8' }}>No ablation rows.</p>
                                ) : (
                                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                                        <thead>
                                            <tr style={{ color: '#64748b', textAlign: 'left' }}>
                                                <th style={{ padding: '10px 8px' }}>Component</th>
                                                <th style={{ padding: '10px 8px' }}>With</th>
                                                <th style={{ padding: '10px 8px' }}>Without</th>
                                                <th style={{ padding: '10px 8px' }}>Delta</th>
                                                <th style={{ padding: '10px 8px' }}>Recommendation</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {results.ablation_results.map((row) => (
                                                <tr key={row.component} style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                                                    <td style={{ padding: '12px 8px', fontWeight: 600 }}>{row.component}</td>
                                                    <td style={{ padding: '12px 8px' }}>{row.with_score ?? '—'}</td>
                                                    <td style={{ padding: '12px 8px' }}>{row.without_score ?? '—'}</td>
                                                    <td
                                                        style={{
                                                            padding: '12px 8px',
                                                            color:
                                                                row.delta == null
                                                                    ? '#94a3b8'
                                                                    : row.delta >= 2
                                                                      ? '#4ade80'
                                                                      : row.delta < 0
                                                                        ? '#f87171'
                                                                        : '#fbbf24',
                                                            fontWeight: 600,
                                                        }}
                                                    >
                                                        {row.delta ?? '—'}
                                                    </td>
                                                    <td style={{ padding: '12px 8px', color: '#94a3b8' }}>{row.recommendation}</td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        )}
                    </div>
                </>
            )}

            {!bootLoading && !summary && !error && (
                <div
                    style={{
                        textAlign: 'center',
                        padding: '64px 24px',
                        borderRadius: 16,
                        border: '1px dashed rgba(255,255,255,0.1)',
                        background: 'rgba(255,255,255,0.02)',
                    }}
                >
                    <CheckCircle2 size={48} color="#a78bfa" style={{ marginBottom: 16 }} />
                    <h3 style={{ margin: '0 0 8px' }}>No evaluation on record</h3>
                    <p style={{ color: '#94a3b8', maxWidth: 420, margin: '0 auto 20px' }}>
                        Run the SwarmScore evaluator to compare production swarm vs baselines and ablations.
                    </p>
                    <button onClick={runEval} disabled={loading}>
                        <Play size={18} />
                        <span style={{ marginLeft: 6 }}>Run first evaluation</span>
                    </button>
                </div>
            )}

            {results?.missing_inputs?.length > 0 && (
                <div
                    style={{
                        marginTop: 20,
                        padding: '14px 18px',
                        borderRadius: 12,
                        background: 'rgba(251,191,36,0.08)',
                        border: '1px solid rgba(251,191,36,0.2)',
                        fontSize: '0.82rem',
                        color: '#fcd34d',
                    }}
                >
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8, fontWeight: 600 }}>
                        <AlertTriangle size={16} />
                        Missing inputs (skipped)
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 18, color: '#fde68a' }}>
                        {results.missing_inputs.map((m) => (
                            <li key={m}>{m}</li>
                        ))}
                    </ul>
                </div>
            )}
        </div>
    );
}
