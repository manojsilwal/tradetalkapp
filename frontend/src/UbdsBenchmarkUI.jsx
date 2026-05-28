import React, { useCallback, useEffect, useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import {
    CheckCircle2,
    Gauge,
    Layout,
    Loader2,
    Play,
    RefreshCw,
    AlertTriangle,
} from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';

const STATUS_STYLES = {
    pass: { bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.35)', color: '#4ade80', label: 'Pass' },
    fail: { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', color: '#f87171', label: 'Fail' },
    hold: { bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.35)', color: '#fbbf24', label: 'Hold' },
};

const CATEGORY_LABELS = {
    task_success_completion: 'Task Success',
    efficiency_flow_friction: 'Efficiency',
    error_rate_recovery: 'Errors & Recovery',
    navigation_information_architecture: 'Navigation',
    visual_design_consistency: 'Visual Design',
    accessibility_responsiveness: 'Accessibility',
    user_satisfaction_trust: 'Satisfaction',
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

function CategoryGrid({ scores }) {
    const entries = Object.entries(scores || {});
    if (!entries.length) return <p style={{ color: '#94a3b8' }}>No category scores yet.</p>;
    return (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 12 }}>
            {entries.map(([key, cat]) => (
                <div
                    key={key}
                    style={{
                        padding: 14,
                        borderRadius: 12,
                        border: '1px solid rgba(255,255,255,0.06)',
                        background: 'rgba(255,255,255,0.02)',
                    }}
                >
                    <div style={{ fontSize: '0.75rem', color: '#64748b', marginBottom: 6 }}>
                        {CATEGORY_LABELS[key] || key}
                    </div>
                    <div style={{ fontSize: '1.4rem', fontWeight: 800, color: '#38bdf8' }}>{cat.score ?? '—'}</div>
                    <div style={{ fontSize: '0.7rem', color: '#475569', marginTop: 4 }}>
                        weight {(cat.weight * 100).toFixed(0)}%
                    </div>
                </div>
            ))}
        </div>
    );
}

function TaskTable({ tasks }) {
    if (!tasks?.length) return <p style={{ color: '#94a3b8' }}>No task rows in this run.</p>;
    return (
        <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', color: '#64748b', textAlign: 'left' }}>
                        <th style={{ padding: '10px 8px' }}>Task</th>
                        <th style={{ padding: '10px 8px' }}>Done</th>
                        <th style={{ padding: '10px 8px' }}>Time (ms)</th>
                        <th style={{ padding: '10px 8px' }}>Errors</th>
                        <th style={{ padding: '10px 8px' }}>SEQ</th>
                    </tr>
                </thead>
                <tbody>
                    {tasks.map((t) => (
                        <tr key={t.task_id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                            <td style={{ padding: '12px 8px' }}>{t.task_name || t.task_id}</td>
                            <td style={{ padding: '12px 8px' }}>{t.completed ? '✓' : '—'}</td>
                            <td style={{ padding: '12px 8px' }}>{t.time_on_task_ms ?? '—'}</td>
                            <td style={{ padding: '12px 8px' }}>{t.error_count ?? 0}</td>
                            <td style={{ padding: '12px 8px' }}>{t.seq_score ?? '—'}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function UbdsBenchmarkUI() {
    const [tab, setTab] = useState('report');
    const [mode, setMode] = useState('fixture');
    const [loading, setLoading] = useState(false);
    const [bootLoading, setBootLoading] = useState(true);
    const [error, setError] = useState(null);
    const [summary, setSummary] = useState(null);
    const [results, setResults] = useState(null);
    const [reportMd, setReportMd] = useState('');
    const [history, setHistory] = useState([]);

    const loadLatest = useCallback(async () => {
        setBootLoading(true);
        setError(null);
        try {
            const [s, r, rep, hist] = await Promise.all([
                apiFetch(`${API_BASE_URL}/admin/ubds/summary`).catch(() => null),
                apiFetch(`${API_BASE_URL}/admin/ubds/results`).catch(() => null),
                apiFetch(`${API_BASE_URL}/admin/ubds/report?format=json`).catch(() => null),
                apiFetch(`${API_BASE_URL}/admin/ubds/history`).catch(() => []),
            ]);
            if (s) setSummary(s);
            if (r) setResults(r);
            if (rep?.markdown) setReportMd(rep.markdown);
            if (Array.isArray(hist)) setHistory(hist);
        } finally {
            setBootLoading(false);
        }
    }, []);

    const exportReport = useCallback(async () => {
        const text = reportMd || '# UBDS Report\n\nRun a benchmark first.';
        try {
            await navigator.clipboard.writeText(text);
        } catch {
            const blob = new Blob([text], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `ubds-report-${summary?.run_id || 'latest'}.md`;
            a.click();
            URL.revokeObjectURL(url);
        }
    }, [reportMd, summary]);

    useEffect(() => {
        loadLatest();
    }, [loadLatest]);

    const runEval = async () => {
        setLoading(true);
        setError(null);
        try {
            const out = await apiFetch(`${API_BASE_URL}/admin/ubds/run`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ mode }),
            });
            setSummary(out.summary || null);
            await loadLatest();
        } catch (e) {
            setError(e?.message || 'UBDS run failed');
        } finally {
            setLoading(false);
        }
    };

    const st = useMemo(() => statusStyle(summary?.status || results?.status), [summary, results]);

    return (
        <div className="consumer-container fade-in" data-testid="ubds-page">
            <header style={{ marginBottom: 28 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                    <Gauge size={28} color="#38bdf8" />
                    <h1 style={{ margin: 0, fontSize: '1.75rem', fontWeight: 800 }}>
                        UBDS — UI Behavior & Design
                    </h1>
                </div>
                <p style={{ color: '#94a3b8', maxWidth: 720, lineHeight: 1.6, margin: 0 }}>
                    App UI Behavior & Design Benchmark Standard v1.0. Scores task success, efficiency, errors,
                    navigation, visual design, accessibility, and satisfaction for release gates.
                </p>
            </header>

            <div
                style={{
                    display: 'flex',
                    flexWrap: 'wrap',
                    gap: 12,
                    alignItems: 'center',
                    marginBottom: 24,
                    padding: 16,
                    borderRadius: 14,
                    border: '1px solid rgba(255,255,255,0.08)',
                    background: 'rgba(255,255,255,0.02)',
                }}
            >
                <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                    data-testid="ubds-mode"
                    style={{ padding: '8px 12px', borderRadius: 8, background: '#0f172a', color: '#e2e8f0', border: '1px solid #334155' }}
                >
                    <option value="fixture">fixture (offline datasets)</option>
                    <option value="playwright">playwright (last E2E JSON)</option>
                </select>
                <button onClick={runEval} disabled={loading} data-testid="ubds-run" className="primary-btn">
                    {loading ? <Loader2 size={16} className="spin" /> : <Play size={16} />}
                    Run UBDS benchmark
                </button>
                <button type="button" onClick={loadLatest} disabled={bootLoading} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <RefreshCw size={14} />
                    Refresh
                </button>
                <button
                    type="button"
                    onClick={exportReport}
                    disabled={!reportMd}
                    data-testid="ubds-export-report"
                    style={{ display: 'flex', alignItems: 'center', gap: 6 }}
                >
                    Export report
                </button>
            </div>

            {error && (
                <div style={{ padding: 14, marginBottom: 20, borderRadius: 10, background: 'rgba(239,68,68,0.1)', color: '#fca5a5' }}>
                    <AlertTriangle size={16} style={{ verticalAlign: 'middle', marginRight: 8 }} />
                    {error}
                </div>
            )}

            {bootLoading && !summary ? (
                <div style={{ padding: 40, textAlign: 'center', color: '#94a3b8' }}>
                    <Loader2 size={32} className="spin" />
                </div>
            ) : summary ? (
                <>
                    <div
                        style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 8,
                            padding: '6px 14px',
                            borderRadius: 999,
                            marginBottom: 20,
                            background: st.bg,
                            border: `1px solid ${st.border}`,
                            color: st.color,
                            fontWeight: 700,
                            fontSize: '0.8rem',
                        }}
                    >
                        <CheckCircle2 size={14} />
                        {st.label} · Grade {summary.grade}
                    </div>

                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 14, marginBottom: 28 }}>
                        <KpiCard label="UBDS Score" value={summary.overall_score} accent="#38bdf8" />
                        <KpiCard label="Grade" value={summary.grade} />
                        <KpiCard label="Release gate" value={String(summary.status || '').toUpperCase()} />
                        <KpiCard label="Run ID" value={summary.run_id?.slice(-12)} sub={summary.timestamp?.slice(0, 10)} />
                        {summary.overall_score_delta != null && (
                            <KpiCard
                                label="vs previous"
                                value={`${summary.overall_score_delta >= 0 ? '+' : ''}${summary.overall_score_delta}`}
                                sub={summary.previous_overall_score != null ? `was ${summary.previous_overall_score}` : ''}
                                accent={summary.overall_score_delta >= 0 ? '#4ade80' : '#f87171'}
                            />
                        )}
                    </div>

                    {summary.recommendation && (
                        <p style={{ color: '#cbd5e1', marginBottom: 24, lineHeight: 1.6 }}>{summary.recommendation}</p>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
                        {['report', 'categories', 'tasks', 'issues', 'history'].map((t) => (
                            <button
                                key={t}
                                type="button"
                                onClick={() => setTab(t)}
                                style={{
                                    padding: '8px 16px',
                                    borderRadius: 8,
                                    border: tab === t ? '1px solid #38bdf8' : '1px solid rgba(255,255,255,0.1)',
                                    background: tab === t ? 'rgba(56,189,248,0.12)' : 'transparent',
                                    color: tab === t ? '#7dd3fc' : '#94a3b8',
                                    cursor: 'pointer',
                                }}
                            >
                                {t.charAt(0).toUpperCase() + t.slice(1)}
                            </button>
                        ))}
                    </div>

                    {tab === 'report' && reportMd && (
                        <div className="glass-panel" style={{ padding: 24, borderRadius: 14, maxWidth: 900 }}>
                            <ReactMarkdown>{reportMd}</ReactMarkdown>
                        </div>
                    )}
                    {tab === 'categories' && <CategoryGrid scores={results?.scores} />}
                    {tab === 'tasks' && <TaskTable tasks={results?.task_results} />}
                    {tab === 'history' && (
                        <div style={{ overflowX: 'auto' }}>
                            {history.length === 0 ? (
                                <p style={{ color: '#94a3b8' }}>No prior runs recorded.</p>
                            ) : (
                                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                                    <thead>
                                        <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)', color: '#64748b', textAlign: 'left' }}>
                                            <th style={{ padding: '10px 8px' }}>Run</th>
                                            <th style={{ padding: '10px 8px' }}>Score</th>
                                            <th style={{ padding: '10px 8px' }}>Grade</th>
                                            <th style={{ padding: '10px 8px' }}>Status</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {[...history].reverse().map((h) => (
                                            <tr key={h.run_id} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                                                <td style={{ padding: '10px 8px' }}>{h.run_id}</td>
                                                <td style={{ padding: '10px 8px' }}>{h.overall_score}</td>
                                                <td style={{ padding: '10px 8px' }}>{h.grade}</td>
                                                <td style={{ padding: '10px 8px' }}>{h.status}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            )}
                        </div>
                    )}
                    {tab === 'issues' && (
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
                            <div>
                                <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#4ade80' }}>
                                    <CheckCircle2 size={18} /> Strengths
                                </h3>
                                <ul style={{ color: '#94a3b8', lineHeight: 1.8 }}>
                                    {(summary.top_strengths || []).map((s) => (
                                        <li key={s}>{s}</li>
                                    ))}
                                </ul>
                            </div>
                            <div>
                                <h3 style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#fbbf24' }}>
                                    <AlertTriangle size={18} /> Issues
                                </h3>
                                <ul style={{ color: '#94a3b8', lineHeight: 1.8 }}>
                                    {(summary.top_issues || []).map((i) => (
                                        <li key={i}>{i}</li>
                                    ))}
                                </ul>
                            </div>
                        </div>
                    )}
                </>
            ) : (
                <div style={{ padding: 48, textAlign: 'center', color: '#94a3b8' }}>
                    <Layout size={40} style={{ marginBottom: 12, opacity: 0.5 }} />
                    Run the UBDS benchmark to score UI behavior and design quality.
                </div>
            )}
        </div>
    );
}
