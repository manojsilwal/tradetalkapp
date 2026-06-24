import React, { useState, useEffect, useCallback } from 'react'
import { Activity, RefreshCw, Loader2, Database, Cpu, Clock, CheckCircle2, XCircle, AlertTriangle, Server } from 'lucide-react'
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

export default function PipelineOpsUI() {
    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)

    const fetchStatus = useCallback(async () => {
        setLoading(true)
        setError(null)
        try {
            const d = await apiFetch(`${API_BASE_URL}/pipeline-ops/status`)
            setData(d)
        } catch (e) {
            setError(e.message || 'Failed to load pipeline status')
        } finally {
            setLoading(false)
        }
    }, [])

    useEffect(() => {
        fetchStatus()
        const id = setInterval(fetchStatus, 60000)
        return () => clearInterval(id)
    }, [fetchStatus])

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

            <Section title="BigQuery Freshness" icon={Database}>
                {!bq?.available ? <Unavailable reason={bq?.reason} /> : (
                    <div style={{ display: 'grid', gap: 8 }}>
                        {(bq.tables || []).map((t) => (
                            <div key={t.table} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: '#e2e8f0', padding: '4px 0' }}>
                                <span>{t.table}</span>
                                <span style={MUTED}>{t.error ? `error: ${t.error}` : `${t.rows ?? '?'} rows · latest ${t.latest ?? '—'}`}</span>
                            </div>
                        ))}
                    </div>
                )}
            </Section>

            <Section title="Finance Brain" icon={Cpu}>
                {!brain?.available ? <Unavailable reason={brain?.reason} /> : (
                    <div style={{ fontSize: 13, color: '#e2e8f0', display: 'grid', gap: 6 }}>
                        <div>Serving enabled: <b>{String(brain.serving_enabled)}</b></div>
                        <div style={MUTED}>as_of {brain.last_run?.as_of_date || '—'} · model {brain.last_run?.model_name}-{brain.last_run?.model_version}</div>
                        <div style={MUTED}>snapshots {brain.last_run?.tickers_done ?? 0}/{brain.last_run?.tickers_total ?? 0} · finished {brain.last_run?.finished_at || '—'}</div>
                        {brain.last_run?.errors?.length > 0 && (
                            <div style={{ color: '#fca5a5' }}>{brain.last_run.errors.length} errors</div>
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
