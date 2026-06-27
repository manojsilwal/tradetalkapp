import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { Radar, Loader2, RefreshCw, Clock, X, Info } from 'lucide-react';
import { API_BASE_URL, apiFetch, apiPost } from './api';
import './DecisionTerminalUI.css';

const POLL_INTERVAL_MS = 2500;

// Lifecycle phase → accent color (matches the plan's heatmap intent).
const PHASE_COLORS = {
  DISCOVERY_SEEDING: '#38bdf8',
  EARLY_ACCUMULATION: '#22d3ee',
  ACCELERATION: '#34d399',
  MAINSTREAM_MOMENTUM: '#a3e635',
  SATURATION_CROWDING: '#fbbf24',
  DISTRIBUTION_RISK: '#fb923c',
  EXIT_ROTATION_AWAY: '#f87171',
  DORMANT_REBASE: '#94a3b8',
  LOW_CONFIDENCE_WATCHLIST: '#64748b',
};

// Columns shown in the lifecycle heatmap. `inverse` = high value is a caution/red signal.
const HEATMAP_COLUMNS = [
  { key: 'theme_formation_score', label: 'Formation' },
  { key: 'theme_accumulation_score', label: 'Accumulation' },
  { key: 'theme_acceleration_score', label: 'Acceleration' },
  { key: 'market_confirmation_score', label: 'Market Conf.' },
  { key: 'breadth_quality_score', label: 'Breadth' },
  { key: 'theme_distribution_risk_score', label: 'Distribution', inverse: true },
  { key: 'theme_exit_risk_score', label: 'Exit Risk', inverse: true },
];

function usePolledScan() {
  const [busy, setBusy] = useState(false);
  const [jobStatus, setJobStatus] = useState(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [sort, setSort] = useState('acceleration');
  const pollRef = useRef(null);

  const fetchOverview = useCallback(async (sortKey) => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/narrative-radar/overview?sort=${sortKey}&limit=50`);
      if (res) setData(res);
    } catch {
      /* no snapshot yet */
    }
  }, []);

  useEffect(() => { fetchOverview(sort); }, [fetchOverview, sort]);

  const startScan = useCallback(async (force = false) => {
    setError(null);
    setBusy(true);
    try {
      const res = await apiPost(`${API_BASE_URL}/narrative-radar/refresh${force ? '?force=true' : ''}`);
      if (res.accepted) setJobStatus(res.job);
      else if (res.cache_hit) { setBusy(false); setJobStatus(null); await fetchOverview(sort); }
      else if (res.reason === 'already_running') setJobStatus(res.job);
      else setBusy(false);
    } catch (e) {
      setBusy(false);
      setError(e.message || 'Failed to start scan');
    }
  }, [fetchOverview, sort]);

  useEffect(() => {
    if (!busy) { if (pollRef.current) clearInterval(pollRef.current); return undefined; }
    pollRef.current = setInterval(async () => {
      try {
        const st = await apiFetch(`${API_BASE_URL}/narrative-radar/status`);
        setJobStatus(st);
        if (st.status === 'done') { setBusy(false); await fetchOverview(sort); }
        else if (st.status === 'error') { setBusy(false); setError(st.error || 'Scan failed'); }
      } catch { /* transient */ }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [busy, fetchOverview, sort]);

  return { busy, jobStatus, data, error, sort, setSort, startScan };
}

function fmtScore(v) { return v === null || v === undefined ? '—' : Number(v).toFixed(0); }

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return '';
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${(seconds / 3600).toFixed(1)} h ago`;
}

/** 0-100 score → background color. inverse flips so high = red. */
function scoreCell(v, inverse) {
  if (v === null || v === undefined) return { background: 'rgba(100,116,139,0.12)', color: '#64748b' };
  const good = inverse ? 100 - v : v;
  let bg, fg;
  if (good >= 66) { bg = 'rgba(34,197,94,0.18)'; fg = '#4ade80'; }
  else if (good >= 40) { bg = 'rgba(250,204,21,0.16)'; fg = '#fbbf24'; }
  else { bg = 'rgba(248,113,113,0.16)'; fg = '#f87171'; }
  return { background: bg, color: fg };
}

function confidenceColor(level) {
  if (level === 'High') return '#34d399';
  if (level === 'Low') return '#f87171';
  return '#fbbf24';
}

function KpiCard({ label, value }) {
  return (
    <div className="glass-panel" style={{ padding: '14px 16px', minWidth: 120, flex: 1 }}>
      <div style={{ fontSize: 11, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: '#f8fafc', marginTop: 4 }}>{value}</div>
    </div>
  );
}

function PhasePill({ phase, label }) {
  const color = PHASE_COLORS[phase] || '#94a3b8';
  return (
    <span style={{
      padding: '2px 10px', borderRadius: 999, fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap',
      background: `${color}22`, color, border: `1px solid ${color}55`,
    }}>{label || phase}</span>
  );
}

function ThemeCard({ theme, onOpen }) {
  const s = theme.scores || {};
  const color = PHASE_COLORS[theme.lifecycle_phase] || '#94a3b8';
  return (
    <button
      className="glass-panel"
      onClick={() => onOpen(theme)}
      style={{
        textAlign: 'left', padding: 16, border: `1px solid ${color}44`, borderRadius: 14,
        cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 10, background: 'rgba(15,23,42,0.5)',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
        <div style={{ fontWeight: 800, color: '#f8fafc', fontSize: 15 }}>{theme.theme_label}</div>
        <PhasePill phase={theme.lifecycle_phase} label={theme.phase_label} />
      </div>
      <div style={{ fontSize: 12, color: '#cbd5e1', lineHeight: 1.4, minHeight: 34 }}>{theme.summary}</div>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
        <Metric label="Accel" v={s.theme_acceleration_score} />
        <Metric label="Breadth" v={s.breadth_quality_score} />
        <Metric label="Exit" v={s.theme_exit_risk_score} inverse />
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 11 }}>
        <span style={{ color: '#94a3b8' }}>{theme.recommendation_label}</span>
        <span style={{ color: confidenceColor(theme.confidence_level), fontWeight: 700 }}>
          {theme.confidence_level} confidence
        </span>
      </div>
    </button>
  );
}

function Metric({ label, v, inverse }) {
  const st = scoreCell(v, inverse);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
      <span style={{ fontSize: 10, color: '#94a3b8', textTransform: 'uppercase' }}>{label}</span>
      <span style={{ ...st, padding: '1px 7px', borderRadius: 6, fontWeight: 800, fontSize: 12 }}>{fmtScore(v)}</span>
    </div>
  );
}

function DetailDrawer({ theme, onClose }) {
  if (!theme) return null;
  const s = theme.scores || {};
  const exp = theme.explanation || {};
  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 60,
      display: 'flex', justifyContent: 'flex-end',
    }}>
      <div onClick={(e) => e.stopPropagation()} className="glass-panel" style={{
        width: 'min(520px, 92vw)', height: '100%', overflowY: 'auto', padding: 22, borderRadius: 0,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ margin: 0, color: '#f8fafc' }}>{theme.theme_label}</h2>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: '#94a3b8', cursor: 'pointer' }}><X size={20} /></button>
        </div>
        <div style={{ marginTop: 8 }}><PhasePill phase={theme.lifecycle_phase} label={theme.phase_label} /></div>
        <p style={{ color: '#cbd5e1', fontSize: 13, lineHeight: 1.5 }}>{theme.summary}</p>

        <h4 style={{ color: '#e2e8f0', marginBottom: 6 }}>Scores</h4>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {HEATMAP_COLUMNS.map((c) => (
            <div key={c.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '6px 10px', borderRadius: 8, ...scoreCell(s[c.key], c.inverse) }}>
              <span style={{ fontSize: 12 }}>{c.label}</span>
              <strong>{fmtScore(s[c.key])}</strong>
            </div>
          ))}
        </div>

        {exp.top_positive_drivers?.length > 0 && (
          <>
            <h4 style={{ color: '#34d399', marginBottom: 6, marginTop: 16 }}>What supports this</h4>
            <ul style={{ color: '#cbd5e1', fontSize: 13, paddingLeft: 18, lineHeight: 1.5 }}>
              {exp.top_positive_drivers.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          </>
        )}
        {exp.top_negative_drivers?.length > 0 && (
          <>
            <h4 style={{ color: '#f87171', marginBottom: 6, marginTop: 12 }}>Risks / cautions</h4>
            <ul style={{ color: '#cbd5e1', fontSize: 13, paddingLeft: 18, lineHeight: 1.5 }}>
              {exp.top_negative_drivers.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          </>
        )}
        {theme.pending_signal_families?.length > 0 && (
          <div style={{ marginTop: 12, fontSize: 12, color: '#94a3b8', display: 'flex', gap: 6 }}>
            <Info size={14} style={{ flexShrink: 0, marginTop: 2 }} />
            <span>Pending signal families (not yet wired): {theme.pending_signal_families.join(', ')}.</span>
          </div>
        )}
      </div>
    </div>
  );
}

export default function NarrativeRadarUI() {
  const { busy, jobStatus, data, error, sort, setSort, startScan } = usePolledScan();
  const [selected, setSelected] = useState(null);

  const themes = data?.themes || [];
  const snapshot = data?.snapshot || null;
  const phaseCounts = data?.phase_counts || {};

  const kpis = useMemo(() => {
    const emerging = (phaseCounts.DISCOVERY_SEEDING || 0) + (phaseCounts.EARLY_ACCUMULATION || 0);
    const accelerating = (phaseCounts.ACCELERATION || 0) + (phaseCounts.MAINSTREAM_MOMENTUM || 0);
    const exiting = (phaseCounts.DISTRIBUTION_RISK || 0) + (phaseCounts.EXIT_ROTATION_AWAY || 0);
    return { emerging, accelerating, exiting };
  }, [phaseCounts]);

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1240, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, color: '#f8fafc', display: 'flex', alignItems: 'center', gap: 10 }}>
            <Radar size={26} /> Narrative Rotation Radar
          </h1>
          <p style={{ color: '#94a3b8', marginTop: 6, maxWidth: 720, fontSize: 13, lineHeight: 1.5 }}>
            Where capital and narrative are rotating across market themes — from early seeding through
            acceleration, crowding, and distribution. Research signal only; not investment advice.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <select value={sort} onChange={(e) => setSort(e.target.value)} className="glass-panel"
            style={{ padding: '8px 10px', color: '#e2e8f0', background: 'rgba(15,23,42,0.6)', border: '1px solid #334155', borderRadius: 8 }}>
            <option value="acceleration">Sort: Acceleration</option>
            <option value="formation">Sort: Formation (early)</option>
            <option value="exit_risk">Sort: Exit risk</option>
            <option value="confidence">Sort: Confidence</option>
          </select>
          <button onClick={() => startScan(true)} disabled={busy}
            style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '9px 16px', borderRadius: 10,
              border: 'none', cursor: busy ? 'wait' : 'pointer', fontWeight: 700,
              background: busy ? '#334155' : 'linear-gradient(135deg,#6366f1,#22d3ee)', color: '#fff' }}>
            {busy ? <Loader2 size={16} className="spin" /> : <RefreshCw size={16} />}
            {busy ? 'Scanning…' : 'Refresh'}
          </button>
        </div>
      </div>

      {busy && jobStatus && (
        <div className="glass-panel" style={{ padding: 12, marginTop: 14, color: '#cbd5e1', fontSize: 13 }}>
          {jobStatus.message || 'Working…'} ({jobStatus.progress || 0}%)
        </div>
      )}
      {error && (
        <div className="glass-panel" style={{ padding: 12, marginTop: 14, color: '#f87171', fontSize: 13 }}>{error}</div>
      )}

      {snapshot && (
        <div style={{ display: 'flex', gap: 12, marginTop: 16, flexWrap: 'wrap' }}>
          <KpiCard label="Emerging" value={kpis.emerging} />
          <KpiCard label="Accelerating" value={kpis.accelerating} />
          <KpiCard label="Distribution / Exit" value={kpis.exiting} />
          <KpiCard label="Themes scored" value={snapshot.scored} />
        </div>
      )}

      {!snapshot && !busy && (
        <div className="glass-panel" style={{ padding: 24, marginTop: 18, textAlign: 'center', color: '#cbd5e1' }}>
          No radar snapshot yet. Click <strong>Refresh</strong> to scan the themes.
        </div>
      )}

      {themes.length > 0 && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 14, marginTop: 18 }}>
            {themes.map((t) => <ThemeCard key={t.theme_id} theme={t} onOpen={setSelected} />)}
          </div>

          <h3 style={{ color: '#e2e8f0', marginTop: 28 }}>Lifecycle heatmap</h3>
          <div className="glass-panel" style={{ overflowX: 'auto', marginTop: 8 }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ color: '#94a3b8', textAlign: 'right' }}>
                  <th style={{ textAlign: 'left', padding: '8px 10px' }}>Theme</th>
                  <th style={{ textAlign: 'left', padding: '8px 10px' }}>Phase</th>
                  {HEATMAP_COLUMNS.map((c) => <th key={c.key} style={{ padding: '8px 10px' }}>{c.label}</th>)}
                  <th style={{ padding: '8px 10px' }}>Conf.</th>
                </tr>
              </thead>
              <tbody>
                {themes.map((t) => (
                  <tr key={t.theme_id} style={{ borderTop: '1px solid rgba(51,65,85,0.5)', cursor: 'pointer' }}
                    onClick={() => setSelected(t)}>
                    <td style={{ padding: '7px 10px', color: '#e2e8f0', fontWeight: 600 }}>{t.theme_label}</td>
                    <td style={{ padding: '7px 10px' }}><PhasePill phase={t.lifecycle_phase} label={t.phase_label} /></td>
                    {HEATMAP_COLUMNS.map((c) => {
                      const v = (t.scores || {})[c.key];
                      const st = scoreCell(v, c.inverse);
                      return (
                        <td key={c.key} style={{ padding: '5px 6px', textAlign: 'right' }}>
                          <span style={{ ...st, padding: '2px 8px', borderRadius: 6, fontWeight: 700 }}>{fmtScore(v)}</span>
                        </td>
                      );
                    })}
                    <td style={{ padding: '7px 10px', textAlign: 'right', color: confidenceColor(t.confidence_level), fontWeight: 700 }}>
                      {t.confidence_level}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {snapshot && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#64748b', fontSize: 11, marginTop: 14 }}>
          <Clock size={12} /> Snapshot {fmtAge(data?.age_seconds)} · {data?.disclaimer}
        </div>
      )}

      <DetailDrawer theme={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
