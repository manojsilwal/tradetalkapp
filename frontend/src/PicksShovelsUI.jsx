import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Zap, Loader2, RefreshCw, Clock, ShieldAlert, X, Layers, EyeOff } from 'lucide-react';
import { API_BASE_URL, apiFetch, apiPost } from './api';
import './DecisionTerminalUI.css';

const POLL_INTERVAL_MS = 2500;
const HIDDENNESS_LEVELS = ['Big Player', 'Secondary Player', 'Hidden Player'];

/** State + polling for the async picks-and-shovels scan. */
function usePicksShovels() {
  const [busy, setBusy] = useState(false);
  const [jobStatus, setJobStatus] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [filters, setFilters] = useState({ theme: '', hiddenness: '', minScore: 0 });
  const pollRef = useRef(null);

  const buildQuery = useCallback((f) => {
    const p = new URLSearchParams();
    if (f.theme) p.set('theme', f.theme);
    if (f.hiddenness) p.set('hiddenness', f.hiddenness);
    if (f.minScore) p.set('min_score', String(f.minScore));
    p.set('limit', '100');
    return p.toString();
  }, []);

  const fetchResults = useCallback(async (f) => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/picks-shovels/stocks?${buildQuery(f)}`);
      if (res) setData(res);
    } catch {
      /* no snapshot yet */
    }
  }, [buildQuery]);

  useEffect(() => { fetchResults(filters); }, [fetchResults, filters]);

  const startScan = useCallback(async (force = false) => {
    setError(null);
    setBusy(true);
    try {
      const res = await apiPost(`${API_BASE_URL}/picks-shovels/refresh${force ? '?force=true' : ''}`);
      if (res.accepted) {
        setJobStatus(res.job);
      } else if (res.cache_hit) {
        setBusy(false);
        setJobStatus(null);
        await fetchResults(filters);
      } else if (res.reason === 'already_running') {
        setJobStatus(res.job);
      } else {
        setBusy(false);
      }
    } catch (e) {
      setBusy(false);
      setError(e.message || 'Failed to start scan');
    }
  }, [fetchResults, filters]);

  useEffect(() => {
    if (!busy) {
      if (pollRef.current) clearInterval(pollRef.current);
      return undefined;
    }
    pollRef.current = setInterval(async () => {
      try {
        const st = await apiFetch(`${API_BASE_URL}/picks-shovels/status`);
        setJobStatus(st);
        if (st.status === 'done') {
          setBusy(false);
          await fetchResults(filters);
        } else if (st.status === 'error') {
          setBusy(false);
          setError(st.error || 'Scan failed');
        }
      } catch {
        /* transient poll failure */
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [busy, fetchResults, filters]);

  // Cold-start self-heal: if there is no snapshot yet (e.g. before the first weekly cron)
  // Auto-scan if no snapshot exists, or if it is stale (older than 1 week)
  const autoWarmedRef = useRef(false);
  useEffect(() => {
    if (autoWarmedRef.current || busy || !data) return;
    if (data.snapshot === null || (data.is_fresh === false)) {
      autoWarmedRef.current = true;
      startScan(false);
    }
  }, [data, busy, startScan]);

  return { busy, jobStatus, data, error, filters, setFilters, startScan };
}

function fmtPct(v) {
  if (v === null || v === undefined) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${Number(v).toFixed(1)}%`;
}

function fmtScore(v) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(1);
}

function fmtCap(v) {
  if (v === null || v === undefined) return '—';
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v}`;
}

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return '';
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${(seconds / 3600).toFixed(1)} h ago`;
}

function hiddennessPill(level) {
  const base = { padding: '2px 8px', borderRadius: 6, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' };
  if (level === 'Big Player') return { ...base, background: 'rgba(34,197,94,0.15)', color: '#4ade80' };
  if (level === 'Hidden Player') return { ...base, background: 'rgba(168,85,247,0.18)', color: '#c084fc' };
  return { ...base, background: 'rgba(59,130,246,0.15)', color: '#60a5fa' };
}

function confidenceColor(level) {
  if (level === 'High') return '#34d399';
  if (level === 'Low') return '#f87171';
  return '#fbbf24';
}

function KpiCard({ label, value }) {
  return (
    <div className="glass-panel" style={{ padding: '14px 16px', minWidth: 130, flex: 1 }}>
      <div style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: '#f8fafc', marginTop: 4 }}>{value}</div>
    </div>
  );
}

function ScoreBar({ label, value }) {
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#cbd5e1' }}>
        <span>{label}</span>
        <span style={{ fontWeight: 700 }}>{fmtScore(value)}</span>
      </div>
      <div style={{ height: 5, borderRadius: 3, background: 'rgba(148,163,184,0.15)', overflow: 'hidden', marginTop: 3 }}>
        <div style={{ height: '100%', width: `${pct}%`, borderRadius: 3, background: 'linear-gradient(90deg, #7c3aed, #a78bfa)' }} />
      </div>
    </div>
  );
}

function StockDrawer({ ticker, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    apiFetch(`${API_BASE_URL}/picks-shovels/stocks/${ticker}`)
      .then((d) => { if (active) { setDetail(d); setLoading(false); } })
      .catch(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [ticker]);

  const bd = detail?.score_breakdown || {};
  const ex = detail?.explanation || {};

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 9990, display: 'flex', justifyContent: 'flex-end' }}
      onClick={onClose}
    >
      <div
        className="glass-panel"
        style={{ width: 'min(520px, 100%)', height: '100%', overflowY: 'auto', padding: 24, borderRadius: 0 }}
        onClick={(e) => e.stopPropagation()}
        data-testid="ps-drawer"
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0, color: '#f8fafc' }}>{ticker}</h2>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}>
            <X size={20} />
          </button>
        </div>

        {loading && <p style={{ color: '#94a3b8', marginTop: 20 }}><Loader2 size={16} className="spinner" /> Loading…</p>}

        {!loading && detail && detail.found !== false && (
          <>
            <div style={{ color: '#cbd5e1', marginTop: 4 }}>{detail.company_name}</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 10 }}>
              <span style={hiddennessPill(detail.hiddenness_level)}>{detail.hiddenness_level}</span>
              {(detail.theme_labels || []).map((t) => (
                <span key={t} style={{ padding: '2px 8px', borderRadius: 6, fontSize: 11, background: 'rgba(148,163,184,0.12)', color: '#cbd5e1' }}>{t}</span>
              ))}
            </div>

            <div style={{ marginTop: 16, fontSize: 28, fontWeight: 900, color: '#f8fafc' }}>
              {fmtScore(detail.final_score)}
              <span style={{ fontSize: 13, fontWeight: 600, color: confidenceColor(detail.confidence_level), marginLeft: 10 }}>
                {detail.confidence_level} confidence
              </span>
            </div>

            {ex.why_selected && (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ color: '#e2e8f0', margin: '0 0 6px' }}>Why Selected</h4>
                <p style={{ color: '#cbd5e1', fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-line' }}>{ex.narrative}</p>
              </div>
            )}

            <div style={{ marginTop: 16 }}>
              <h4 style={{ color: '#e2e8f0', margin: '0 0 10px' }}>Score Breakdown</h4>
              <ScoreBar label="Price Momentum" value={bd.price_momentum_score} />
              <ScoreBar label="Revenue Acceleration" value={bd.revenue_acceleration_score} />
              <ScoreBar label="Margin Expansion" value={bd.margin_expansion_score} />
              <ScoreBar label="Operating Momentum" value={bd.backlog_rpo_score} />
              <ScoreBar label="Customer Capex Exposure" value={bd.customer_capex_exposure_score} />
              <ScoreBar label="Demand Evidence" value={bd.bottleneck_evidence_score} />
              <ScoreBar label="Valuation / Risk" value={bd.valuation_risk_score} />
            </div>

            {(ex.financial_evidence || []).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ color: '#e2e8f0', margin: '0 0 6px' }}>Financial Evidence</h4>
                <ul style={{ color: '#cbd5e1', fontSize: 13, margin: 0, paddingLeft: 18 }}>
                  {ex.financial_evidence.map((x, i) => <li key={i}>{x}</li>)}
                </ul>
              </div>
            )}

            {(ex.demand_evidence || []).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ color: '#e2e8f0', margin: '0 0 6px' }}>Demand Evidence</h4>
                <ul style={{ color: '#cbd5e1', fontSize: 13, margin: 0, paddingLeft: 18 }}>
                  {ex.demand_evidence.map((x, i) => <li key={i}>{x}</li>)}
                </ul>
              </div>
            )}

            {(detail.evidence?.headlines || []).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ color: '#e2e8f0', margin: '0 0 6px' }}>Demand Headlines</h4>
                <ul style={{ color: '#cbd5e1', fontSize: 13, margin: 0, paddingLeft: 18 }}>
                  {detail.evidence.headlines.slice(0, 8).map((h, i) => (
                    <li key={i} style={{ marginBottom: 4 }}>
                      {h.link ? (
                        <a href={h.link} target="_blank" rel="noopener noreferrer" style={{ color: '#a5b4fc', textDecoration: 'none' }}>
                          {h.title}
                        </a>
                      ) : (
                        h.title
                      )}
                      {h.source && <span style={{ color: '#64748b' }}> — {h.source}</span>}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {(ex.risks || detail.risks || []).length > 0 && (
              <div style={{ marginTop: 16 }}>
                <h4 style={{ color: '#fca5a5', margin: '0 0 6px' }}>Risk Factors</h4>
                <ul style={{ color: '#fda4af', fontSize: 13, margin: 0, paddingLeft: 18 }}>
                  {(ex.risks || detail.risks).map((x, i) => <li key={i}>{x}</li>)}
                </ul>
              </div>
            )}

            <p style={{ marginTop: 20, fontSize: 11, color: '#64748b' }}>
              For research and education only. Not investment advice.
            </p>
          </>
        )}

        {!loading && detail && detail.found === false && (
          <p style={{ color: '#94a3b8', marginTop: 20 }}>{detail.message}</p>
        )}
      </div>
    </div>
  );
}

export default function PicksShovelsUI() {
  const { busy, jobStatus, data, error, filters, setFilters, startScan } = usePicksShovels();
  const [themes, setThemes] = useState([]);
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    apiFetch(`${API_BASE_URL}/picks-shovels/themes`)
      .then((d) => setThemes(d?.themes || []))
      .catch(() => {});
  }, []);

  const items = data?.items || [];
  const summary = data?.summary || {};
  const hasSnapshot = Boolean(data?.snapshot);

  const themeCounts = useMemo(() => {
    const counts = {};
    items.forEach((r) => (r.themes || []).forEach((t) => { counts[t] = (counts[t] || 0) + 1; }));
    return counts;
  }, [items]);

  return (
    <div className="consumer-container fade-in" style={{ paddingBottom: 60 }}>
      <div className="header-section" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
        <div>
          <h1 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
            <Layers size={26} /> Picks &amp; Shovels Momentum Finder
          </h1>
          <p style={{ color: '#94a3b8', marginTop: 6, maxWidth: 720 }}>
            Find big and hidden suppliers benefiting from demand shocks, bottlenecks, and infrastructure
            buildouts. For research and education only — not investment advice.
          </p>
        </div>
        <button
          type="button"
          onClick={() => startScan(false)}
          disabled={busy}
          style={{
            padding: '10px 16px', borderRadius: 8, border: '1px solid rgba(167,139,250,0.45)',
            background: 'rgba(124,58,237,0.15)', color: '#e9d5ff', fontWeight: 600,
            cursor: busy ? 'not-allowed' : 'pointer', display: 'flex', alignItems: 'center', gap: 6,
            opacity: busy ? 0.6 : 1, whiteSpace: 'nowrap',
          }}
        >
          {busy ? <Loader2 size={16} className="spinner" /> : <Zap size={16} />}
          {hasSnapshot ? 'Rescan' : 'Run Scan'}
        </button>
      </div>

      {error && (
        <p style={{ color: 'var(--accent-red)', display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
          <ShieldAlert size={16} /> {error}
        </p>
      )}

      {busy && (
        <div className="glass-panel" style={{ padding: 16, marginTop: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#cbd5e1', fontSize: 13 }}>
            <Loader2 size={16} className="spinner" />
            <span data-testid="ps-progress-message">{jobStatus?.message || 'Scanning…'}</span>
            <span style={{ marginLeft: 'auto', color: '#94a3b8' }}>
              {jobStatus?.processed || 0}/{jobStatus?.total || 0} · {jobStatus?.progress ?? 0}%
            </span>
          </div>
          <div style={{ marginTop: 8, height: 6, borderRadius: 3, background: 'rgba(148,163,184,0.15)', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${jobStatus?.progress ?? 0}%`, borderRadius: 3, background: 'linear-gradient(90deg, #7c3aed, #a78bfa)', transition: 'width 0.6s ease' }} />
          </div>
        </div>
      )}

      {!hasSnapshot && !busy && (
        <div className="glass-panel" style={{ padding: 30, marginTop: 16, textAlign: 'center', color: '#94a3b8' }}>
          No snapshot yet. Click <strong style={{ color: '#e9d5ff' }}>Run Scan</strong> to rank the picks-and-shovels universe.
        </div>
      )}

      {hasSnapshot && (
        <>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginTop: 16 }}>
            <KpiCard label="Total Scanned" value={summary.total_scanned ?? '—'} />
            <KpiCard label="High-Confidence" value={summary.high_confidence ?? '—'} />
            <KpiCard label="Hidden Players" value={summary.hidden_players ?? '—'} />
            <KpiCard label="Top Theme" value={summary.top_theme_label || '—'} />
            <KpiCard label="Avg Score" value={fmtScore(summary.avg_final_score)} />
          </div>

          {data?.snapshot && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: '#94a3b8', marginTop: 10 }}>
              <Clock size={13} /> {fmtAge(data.age_seconds)} {data.is_fresh ? '· cached (fresh < 1 week)' : '· stale'}
              <button
                type="button"
                onClick={() => startScan(true)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 10px', borderRadius: 6, fontSize: 12, border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.05)', color: '#cbd5e1', cursor: 'pointer' }}
              >
                <RefreshCw size={12} /> Rescan (live)
              </button>
            </div>
          )}

          {/* Theme heatmap */}
          <div className="glass-panel" style={{ padding: 16, marginTop: 16 }}>
            <h3 style={{ margin: '0 0 10px', color: '#e2e8f0', fontSize: 14 }}>Themes</h3>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button
                onClick={() => setFilters((f) => ({ ...f, theme: '' }))}
                style={{ padding: '6px 12px', borderRadius: 8, border: '1px solid rgba(255,255,255,0.12)', background: !filters.theme ? 'rgba(124,58,237,0.25)' : 'rgba(255,255,255,0.04)', color: '#e2e8f0', cursor: 'pointer', fontSize: 12 }}
              >
                All
              </button>
              {themes.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setFilters((f) => ({ ...f, theme: f.theme === t.id ? '' : t.id }))}
                  title={t.bottleneck}
                  style={{ padding: '6px 12px', borderRadius: 8, border: `1px solid ${t.color}55`, background: filters.theme === t.id ? `${t.color}33` : 'rgba(255,255,255,0.04)', color: '#e2e8f0', cursor: 'pointer', fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}
                >
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: t.color }} />
                  {t.label}
                  <span style={{ color: '#94a3b8' }}>{themeCounts[t.id] || 0}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Filters */}
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', alignItems: 'center', marginTop: 16 }}>
            <select
              value={filters.hiddenness}
              onChange={(e) => setFilters((f) => ({ ...f, hiddenness: e.target.value }))}
              style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.05)', color: '#e2e8f0', border: '1px solid rgba(255,255,255,0.12)' }}
            >
              <option value="">All players</option>
              {HIDDENNESS_LEVELS.map((h) => <option key={h} value={h}>{h}</option>)}
            </select>
            <button
              onClick={() => setFilters((f) => ({ ...f, hiddenness: f.hiddenness === 'Hidden Player' ? '' : 'Hidden Player' }))}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '8px 12px', borderRadius: 8, border: '1px solid rgba(168,85,247,0.4)', background: filters.hiddenness === 'Hidden Player' ? 'rgba(168,85,247,0.25)' : 'rgba(255,255,255,0.04)', color: '#c084fc', cursor: 'pointer', fontSize: 13 }}
            >
              <EyeOff size={14} /> Hidden players only
            </button>
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#94a3b8', fontSize: 13 }}>
              Min score: <strong style={{ color: '#e2e8f0' }}>{filters.minScore}</strong>
              <input
                type="range" min="0" max="100" value={filters.minScore}
                onChange={(e) => setFilters((f) => ({ ...f, minScore: Number(e.target.value) }))}
              />
            </label>
          </div>

          {/* Ranked table */}
          <div className="brief-table-container" style={{ marginTop: 14 }}>
            <table className="brief-table" data-testid="ps-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Stock</th>
                  <th>Theme</th>
                  <th>Hiddenness</th>
                  <th>Score</th>
                  <th>Momentum</th>
                  <th>Rev Accel</th>
                  <th>Margin</th>
                  <th>Demand Evidence</th>
                  <th>Valuation</th>
                  <th>Confidence</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row, idx) => {
                  const bd = row.score_breakdown || {};
                  return (
                    <tr key={row.ticker} style={{ cursor: 'pointer' }} onClick={() => setSelected(row.ticker)}>
                      <td style={{ color: '#64748b' }}>{idx + 1}</td>
                      <td>
                        <div className="brief-table-ticker">
                          <span className="brief-table-ticker-name">{row.ticker}</span>
                          <span style={{ color: '#94a3b8', fontSize: 12 }}>{row.company_name}</span>
                        </div>
                      </td>
                      <td style={{ color: '#94a3b8', fontSize: 12 }}>{(row.theme_labels || [])[0] || '—'}</td>
                      <td><span style={hiddennessPill(row.hiddenness_level)}>{row.hiddenness_level}</span></td>
                      <td style={{ fontWeight: 800, color: '#f8fafc' }}>{fmtScore(row.final_score)}</td>
                      <td style={{ color: '#cbd5e1' }}>{fmtScore(bd.price_momentum_score)}</td>
                      <td style={{ color: '#cbd5e1' }}>{fmtScore(bd.revenue_acceleration_score)}</td>
                      <td style={{ color: '#cbd5e1' }}>{fmtScore(bd.margin_expansion_score)}</td>
                      <td style={{ color: '#cbd5e1' }}>{fmtScore(bd.bottleneck_evidence_score)}</td>
                      <td style={{ color: '#cbd5e1' }}>{fmtScore(bd.valuation_risk_score)}</td>
                      <td style={{ color: confidenceColor(row.confidence_level), fontWeight: 600 }}>{row.confidence_level}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {items.length === 0 && (
              <p style={{ color: '#94a3b8', padding: 16 }}>No stocks match the current filters.</p>
            )}
          </div>
        </>
      )}

      {selected && <StockDrawer ticker={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
