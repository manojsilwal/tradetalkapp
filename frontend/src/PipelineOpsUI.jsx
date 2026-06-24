import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Activity, RefreshCw, Loader2, Database, Cpu, Clock, CheckCircle2, XCircle, AlertTriangle, Server, Zap, TrendingUp } from 'lucide-react'
import { API_BASE_URL, apiFetch } from './api'

const CARD = {
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 12,
    padding: 18,
    marginBottom: 16,
}
const MUTED = { color: '#94a3b8', fontSize: 13 }

function StateBadge({ state }) {
    const map = {
        succeeded: { bg: 'rgba(34,197,94,0.15)', fg: '#22c55e', Icon: CheckCircle2 },
        running: { bg: 'rgba(59,130,246,0.15)', fg: '#3b82f6', Icon: Loader2 },
        failed: { bg: 'rgba(239,68,68,0.15)', fg: '#ef4444', Icon: XCircle },
    }
    const s = map[state] || { bg: 'rgba(148,163,184,0.15)', fg: '#94a3b8', Icon: AlertTriangle }
    const Icon = s.Icon
    return (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '3px 10px', borderRadius: 999, background: s.bg, color: s.fg, fontSize: 12, fontWeight: 600 }}>
            <Icon size={13} /> {state || 'unknown'}
        </span>
    )
}

function Unavailable({ reason }) {
    return (
        <div style={{ ...MUTED, display: 'flex', alignItems: 'center', gap: 8, padding: '6px 0' }}>
            <AlertTriangle size={14} color="#f59e0b" />
            <span>Unavailable in this environment{reason ? `: ${reason}` : ''}</span>
        </div>
    )
}

function Section({ title, icon: Icon, children }) {
    return (
        <div style={CARD}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <Icon size={18} color="#a78bfa" />
                <h3 style={{ fontSize: 15, fontWeight: 700, color: '#f8fafc', margin: 0 }}>{title}</h3>
            </div>
            {children}
        </div>
    )
}

function LiveSpotTable({ activity }) {
    if (!activity?.available) return <Unavailable reason={activity?.reason} />
    const fetches = activity.recent_fetches || []
    const cache = activity.cache_entries || []
    return (
        <div>
            <div style={{ display: 'flex', gap: 20, marginBottom: 12, flexWrap: 'wrap' }}>
                <span style={{ ...MUTED, fontSize: 12 }}>
                    Cache entries: <strong style={{ color: '#e2e8f0' }}>{activity.cache_size ?? 0}</strong>
                </span>
                <span style={{ ...MUTED, fontSize: 12 }}>
                    Total fetches logged: <strong style={{ color: '#e2e8f0' }}>{fetches.length}</strong>
                </span>
                {fetches.length > 0 && (
                    <span style={{ ...MUTED, fontSize: 12 }}>
                        Last: <strong style={{ color: '#e2e8f0' }}>{fetches[0]?.ticker}</strong>
                        {' '}@ <strong style={{ color: '#e2e8f0' }}>${fetches[0]?.price?.toFixed(2) ?? '—'}</strong>
                        {' '}<span style={{ color: fetches[0]?.cache_hit ? '#94a3b8' : '#34d399' }}>
                            {fetches[0]?.cache_hit ? '(cache)' : '(live)'}
                        </span>
                    </span>
                )}
            </div>

            {/* Recent fetch log */}
            <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: '#a78bfa', marginBottom: 6 }}>
                    Recent fetch log (newest first)
                </div>
                <div style={{ maxHeight: 220, overflowY: 'auto', fontSize: 12 }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                        <thead>
                            <tr style={{ color: '#64748b', textAlign: 'left' }}>
                                <th style={{ padding: '3px 8px', fontWeight: 600 }}>Time</th>
                                <th style={{ padding: '3px 8px', fontWeight: 600 }}>Ticker</th>
                                <th style={{ padding: '3px 8px', fontWeight: 600 }}>Price</th>
                                <th style={{ padding: '3px 8px', fontWeight: 600 }}>Source</th>
                                <th style={{ padding: '3px 8px', fontWeight: 600 }}>Type</th>
                            </tr>
                        </thead>
                        <tbody>
                            {fetches.slice(0, 40).map((f, i) => (
                                <tr key={i} style={{ borderTop: '1px solid rgba(255,255,255,0.04)', color: '#e2e8f0' }}>
                                    <td style={{ padding: '3px 8px', color: '#64748b' }}>
                                        {f.ts_utc ? f.ts_utc.slice(11, 19) : '—'}
                                    </td>
                                    <td style={{ padding: '3px 8px', fontWeight: 700, color: '#a5b4fc' }}>{f.ticker}</td>
                                    <td style={{ padding: '3px 8px' }}>
                                        {f.price != null ? `$${Number(f.price).toFixed(2)}` : <span style={{ color: '#ef4444' }}>fail</span>}
                                    </td>
                                    <td style={{ padding: '3px 8px', color: f.degraded ? '#f59e0b' : '#94a3b8' }}>
                                        {f.source}{f.degraded ? ' ⚠' : ''}
                                    </td>
                                    <td style={{ padding: '3px 8px' }}>
                                        <span style={{
                                            padding: '1px 6px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                                            background: f.cache_hit ? 'rgba(148,163,184,0.12)' : 'rgba(52,211,153,0.15)',
                                            color: f.cache_hit ? '#94a3b8' : '#34d399',
                                        }}>
                                            {f.cache_hit ? 'cache' : 'live'}
                                        </span>
                                    </td>
                                </tr>
                            ))}
                            {fetches.length === 0 && (
                                <tr><td colSpan={5} style={{ padding: '12px 8px', color: '#64748b', textAlign: 'center' }}>
                                    No spot fetches yet — run an analyze or load a ticker
                                </td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Live cache state */}
            {cache.length > 0 && (
                <div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: '#a78bfa', marginBottom: 6 }}>
                        Warm cache ({cache.length} tickers)
                    </div>
                    <div style={{ maxHeight: 160, overflowY: 'auto', fontSize: 12 }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                            {cache.map((c) => (
                                <div key={c.ticker} style={{
                                    padding: '3px 8px', borderRadius: 6,
                                    background: 'rgba(99,102,241,0.1)',
                                    border: '1px solid rgba(99,102,241,0.2)',
                                    display: 'flex', gap: 6, alignItems: 'center',
                                }}>
                                    <span style={{ fontWeight: 700, color: '#a5b4fc' }}>{c.ticker}</span>
                                    <span style={{ color: '#e2e8f0' }}>${Number(c.price).toFixed(2)}</span>
                                    <span style={{ color: '#64748b' }}>{c.ttl_remaining_s}s</span>
                                    {c.degraded && <span style={{ color: '#f59e0b' }}>⚠</span>}
                                </div>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}

export default function PipelineOpsUI() {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [spotActivity, setSpotActivity] = useState(null)
    const spotIntervalRef = useRef(null)

    const fetchStatus = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const d = await apiFetch(`${API_BASE_URL}/pipeline-ops/status`)
            setData(d)
            setSpotActivity(d.live_spot_activity)
        } catch (e) {
            setError(e.message || 'Failed to load pipeline status')
        } finally {
            setLoading(false)
        }
    }, [])

    // Poll spot activity every 5 s independently (much lighter than full status)
    const pollSpotActivity = useCallback(async () => {
        try {
            const d = await apiFetch(`${API_BASE_URL}/pipeline-ops/spot-activity`)
            setSpotActivity(d)
        } catch {
            // silent — don't disturb the page on transient errors
        }
    }, [])

    useEffect(() => {
        fetchStatus()
        const statusId = setInterval(fetchStatus, 60000)
        // start spot poll after initial load settles
        const startDelay = setTimeout(() => {
            spotIntervalRef.current = setInterval(pollSpotActivity, 5000)
        }, 3000)
        return () => {
            clearInterval(statusId)
            clearTimeout(startDelay)
            if (spotIntervalRef.current) clearInterval(spotIntervalRef.current)
        }
    }, [fetchStatus, pollSpotActivity])

    const jobs = data?.cloud_run_jobs
    const sched = data?.cloud_scheduler
    const bq = data?.bigquery_freshness
    const brain = data?.brain
    const inproc = data?.in_process_pipeline
    const ledger = data?.ledger

    return (
        <div style={{ padding: '24px 28px', maxWidth: 980, margin: '0 auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Activity size={22} color="#a78bfa" />
                    <h1 style={{ fontSize: 22, fontWeight: 800, color: '#f8fafc', margin: 0 }}>Pipeline Ops</h1>
                </div>
                <button onClick={fetchStatus} disabled={loading}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.05)', color: '#e2e8f0', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
                    {loading ? <Loader2 size={14} className="spin" /> : <RefreshCw size={14} />} Refresh
                </button>
            </div>

            {data && (
                <p style={{ ...MUTED, marginTop: -8, marginBottom: 16 }}>
                    Project <code>{data.project_id}</code> · region <code>{data.region}</code>
                </p>
            )}
            {error && (
                <div style={{ ...CARD, borderColor: 'rgba(239,68,68,0.3)', color: '#fca5a5' }}>{error}</div>
            )}

            <Section title="Cloud Run Jobs" icon={Server}>
                {!jobs?.available ? <Unavailable reason={jobs?.reason} /> : (
                    <div style={{ display: 'grid', gap: 10 }}>
                        {(jobs.jobs || []).map((j) => (
                            <div key={j.job} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                                <div>
                                    <div style={{ color: '#e2e8f0', fontWeight: 600, fontSize: 14 }}>{j.job}</div>
                                    <div style={MUTED}>{j.completion_time || j.start_time || j.error || '—'}</div>
                                </div>
                                <StateBadge state={j.state} />
                            </div>
                        ))}
                    </div>
                )}
            </Section>

            <Section title="Cloud Scheduler" icon={Clock}>
                {!sched?.available ? <Unavailable reason={sched?.reason} /> : (
                    <div style={{ display: 'grid', gap: 8 }}>
                        {(sched.jobs || []).map((s) => (
                            <div key={s.name} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: '#e2e8f0', padding: '4px 0' }}>
                                <span>{s.name}</span>
                                <span style={MUTED}><code>{s.schedule}</code> · {s.state} · {s.last_attempt_time || 'never'}</span>
                            </div>
                        ))}
                    </div>
                )}
            </Section>

            <Section title="BigQuery Data Freshness" icon={Database}>
                {!bq?.available ? <Unavailable reason={bq?.reason} /> : (
                    <div style={{ display: 'grid', gap: 6 }}>
                        {(bq.tables || []).map((t) => {
                            const isStale = t.latest && new Date(t.latest) < new Date(Date.now() - 2 * 86400000)
                            return (
                                <div key={t.table} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 13, color: '#e2e8f0', padding: '5px 0', borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                                    <span style={{ fontWeight: 600 }}>{t.table}</span>
                                    {t.error
                                        ? <span style={{ color: '#fca5a5', fontSize: 12 }}>error: {t.error}</span>
                                        : <span style={MUTED}>
                                            {Number(t.rows ?? 0).toLocaleString()} rows ·{' '}
                                            <span style={{ color: isStale ? '#f59e0b' : '#34d399', fontWeight: 600 }}>
                                                {t.latest ?? '—'}
                                            </span>
                                        </span>
                                    }
                                </div>
                            )
                        })}
                    </div>
                )}
            </Section>

            <Section title="Live Stock Price Fetches" icon={Zap}>
                <LiveSpotTable activity={spotActivity} />
            </Section>

            <Section title="Finance Brain Snapshots" icon={Cpu}>
                {!brain?.available ? <Unavailable reason={brain?.reason} /> : (
                    <div style={{ fontSize: 13, color: '#e2e8f0', display: 'grid', gap: 8 }}>
                        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap', alignItems: 'center' }}>
                            <div>
                                Serving: {' '}
                                <span style={{ fontWeight: 700, color: brain.serving_enabled ? '#34d399' : '#f59e0b' }}>
                                    {brain.serving_enabled ? 'ENABLED' : 'DISABLED'}
                                </span>
                            </div>
                            <div style={MUTED}>
                                as_of <strong style={{ color: '#e2e8f0' }}>{brain.last_run?.as_of_date || '—'}</strong>
                            </div>
                            <div style={MUTED}>
                                model <strong style={{ color: '#e2e8f0' }}>{brain.last_run?.model_name}-{brain.last_run?.model_version || '—'}</strong>
                            </div>
                        </div>
                        <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
                            <div style={MUTED}>
                                Snapshots: {' '}
                                <strong style={{ color: brain.last_run?.tickers_done === brain.last_run?.tickers_total ? '#34d399' : '#f59e0b' }}>
                                    {brain.last_run?.tickers_done ?? 0}/{brain.last_run?.tickers_total ?? 0}
                                </strong>
                            </div>
                            <div style={MUTED}>
                                Storage: <strong style={{ color: '#e2e8f0' }}>{brain.last_run?.storage_backend || '—'}</strong>
                            </div>
                            <div style={MUTED}>
                                Finished: <strong style={{ color: '#e2e8f0' }}>{brain.last_run?.finished_at?.slice(0,19).replace('T',' ') || '—'} UTC</strong>
                            </div>
                        </div>
                        {(brain.last_run?.errors?.length ?? 0) > 0 && (
                            <div style={{ color: '#fca5a5', fontSize: 12 }}>{brain.last_run.errors.length} errors: {brain.last_run.errors.join(', ')}</div>
                        )}
                    </div>
                )}
            </Section>

            <Section title="In-process Knowledge Pipeline" icon={Activity}>
                {!inproc?.available ? <Unavailable reason={inproc?.reason} /> : (
                    <pre style={{ ...MUTED, whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
                        {JSON.stringify(inproc.pipeline_status || {}, null, 2)}
                    </pre>
                )}
            </Section>

            <Section title="Decision Ledger" icon={Database}>
                {!ledger?.available ? <Unavailable reason={ledger?.reason} /> : (
                    <pre style={{ ...MUTED, whiteSpace: 'pre-wrap', wordBreak: 'break-word', margin: 0 }}>
                        {JSON.stringify(ledger.stats || {}, null, 2)}
                    </pre>
                )}
            </Section>
        </div>
    )
}
