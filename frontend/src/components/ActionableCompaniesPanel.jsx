import React, { useState, useEffect, useCallback, useRef } from 'react';
import { Zap, Loader2, RefreshCw, Clock, ShieldAlert } from 'lucide-react';
import { API_BASE_URL, apiFetch, apiPost } from '../api';
import '../DecisionTerminalUI.css';

const POLL_INTERVAL_MS = 2500;

/**
 * State + polling for the async S&P 500 "Actionable Companies" scan.
 *
 * POST /actionable-companies/run     → 202 (job queued) | 200 (cache hit / running)
 * GET  /actionable-companies/status  → { status, progress, message, ... } poll target
 * GET  /actionable-companies/results → latest persisted snapshot, sorted by score
 */
export function useActionableCompanies() {
  const [busy, setBusy] = useState(false);
  const [jobStatus, setJobStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  const fetchResults = useCallback(async () => {
    try {
      const res = await apiFetch(`${API_BASE_URL}/actionable-companies/results?limit=25`);
      if (res && res.snapshot) setResults(res);
    } catch {
      /* no snapshot yet — panel stays hidden */
    }
  }, []);

  // Show the last cached snapshot instantly on page load
  useEffect(() => { fetchResults(); }, [fetchResults]);

  const startScan = useCallback(async (force = false) => {
    setError(null);
    setBusy(true);
    try {
      const res = await apiPost(
        `${API_BASE_URL}/actionable-companies/run${force ? '?force=true' : ''}`
      );
      if (res.accepted) {
        setJobStatus(res.job); // 202 — poller takes over
      } else if (res.cache_hit) {
        setBusy(false);
        setJobStatus(null);
        await fetchResults(); // fresh snapshot < 1h old — serve instantly
      } else if (res.reason === 'already_running') {
        setJobStatus(res.job); // attach to the in-flight job
      } else {
        setBusy(false);
      }
    } catch (e) {
      setBusy(false);
      setError(e.message || 'Failed to start scan');
    }
  }, [fetchResults]);

  useEffect(() => {
    if (!busy) {
      if (pollRef.current) clearInterval(pollRef.current);
      return undefined;
    }
    pollRef.current = setInterval(async () => {
      try {
        const st = await apiFetch(`${API_BASE_URL}/actionable-companies/status`);
        setJobStatus(st);
        if (st.status === 'done') {
          setBusy(false);
          await fetchResults();
        } else if (st.status === 'error') {
          setBusy(false);
          setError(st.error || 'Scan failed');
        }
      } catch {
        /* transient poll failure — keep polling */
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(pollRef.current);
  }, [busy, fetchResults]);

  return { busy, jobStatus, results, error, startScan };
}

/** Header toolbar trigger — purple, matching the Daily Brief styling. */
export function ActionableCompaniesButton({ busy, onClick }) {
  return (
    <button
      type="button"
      onClick={() => onClick(false)}
      disabled={busy}
      title="Run the metrics suite across all S&P 500 stocks (async background scan)"
      style={{
        padding: '10px 14px',
        borderRadius: '8px',
        border: '1px solid rgba(167,139,250,0.45)',
        background: 'rgba(124,58,237,0.15)',
        color: '#e9d5ff',
        fontWeight: 600,
        cursor: busy ? 'not-allowed' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        opacity: busy ? 0.6 : 1,
        whiteSpace: 'nowrap',
      }}
    >
      {busy ? <Loader2 size={16} className="spinner" /> : <Zap size={16} />}
      Actionable Companies
    </button>
  );
}

function verdictPill(verdict) {
  const v = (verdict || '').toLowerCase();
  if (v.includes('buy')) return 'brief-pill-green';
  if (v.includes('sell')) return 'brief-pill-red';
  return 'brief-pill-neutral';
}

function fmtPct(v) {
  if (v === null || v === undefined) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}${Number(v).toFixed(1)}%`;
}

function fmtAge(seconds) {
  if (seconds === null || seconds === undefined) return '';
  if (seconds < 90) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${(seconds / 3600).toFixed(1)} h ago`;
}

/**
 * Full-width results panel: progress bar while the batch runs, then the top
 * actionable S&P 500 candidates from the persisted snapshot.
 */
export default function ActionableCompaniesPanel({ state, onSelectTicker }) {
  const { busy, jobStatus, results, error, startScan } = state;
  const rows = results?.rows || [];
  const hasContent = busy || error || rows.length > 0;
  if (!hasContent) return null;

  return (
    <section className="dt-panel" style={{ gridColumn: '1 / -1' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
        <h2 className="dt-panel-title" style={{ margin: 0 }}>
          Actionable Companies — S&amp;P 500 Scan
        </h2>
        {results?.snapshot && !busy && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: '#94a3b8' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <Clock size={13} />
              {fmtAge(results.age_seconds)}
              {results.is_fresh ? ' · cached (fresh < 1h)' : ' · stale'}
            </span>
            <span>
              {results.snapshot.scored} scored / {results.snapshot.skipped} skipped
            </span>
            <button
              type="button"
              onClick={() => startScan(true)}
              title="Bypass the 1-hour cache and rescan with live data"
              style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '5px 10px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(255,255,255,0.05)',
                color: '#cbd5e1', cursor: 'pointer',
              }}
            >
              <RefreshCw size={12} /> Rescan
            </button>
          </div>
        )}
      </div>

      {error && (
        <p style={{ color: 'var(--accent-red)', display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, marginTop: 12 }}>
          <ShieldAlert size={16} /> {error}
        </p>
      )}

      {busy && (
        <div style={{ marginTop: 14 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: '#cbd5e1', fontSize: 13 }}>
            <Loader2 size={16} className="spinner" />
            <span data-testid="actionable-progress-message">
              {jobStatus?.message || 'Scanning S&P 500…'}
            </span>
            <span style={{ marginLeft: 'auto', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
              {jobStatus?.processed || 0}/{jobStatus?.total || 0} · {jobStatus?.progress ?? 0}%
            </span>
          </div>
          <div style={{ marginTop: 8, height: 6, borderRadius: 3, background: 'rgba(148,163,184,0.15)', overflow: 'hidden' }}>
            <div
              style={{
                height: '100%',
                width: `${jobStatus?.progress ?? 0}%`,
                borderRadius: 3,
                background: 'linear-gradient(90deg, #7c3aed, #a78bfa)',
                transition: 'width 0.6s ease',
              }}
            />
          </div>
        </div>
      )}

      {!busy && rows.length > 0 && (
        <div className="brief-table-container" style={{ marginTop: 14 }}>
          <table className="brief-table" data-testid="actionable-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Stock</th>
                <th>Sector</th>
                <th>Score</th>
                <th>Verdict</th>
                <th>3M Move</th>
                <th>RSI</th>
                <th>FCF Yield</th>
                <th>Rev Growth</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, idx) => (
                <tr
                  key={row.ticker}
                  style={{ cursor: 'pointer' }}
                  title={`Open full analysis for ${row.ticker}`}
                  onClick={() => onSelectTicker && onSelectTicker(row.ticker)}
                >
                  <td style={{ color: '#64748b' }}>{idx + 1}</td>
                  <td>
                    <div className="brief-table-ticker">
                      <span className="brief-table-ticker-name">{row.ticker}</span>
                      <span style={{ color: '#94a3b8', fontSize: 12 }}>{row.company_name}</span>
                    </div>
                  </td>
                  <td style={{ color: '#94a3b8' }}>{row.sector || '—'}</td>
                  <td style={{ fontWeight: 700, color: '#f8fafc' }}>
                    {row.score != null ? row.score.toFixed(1) : '—'}
                  </td>
                  <td><span className={verdictPill(row.verdict)}>{row.verdict}</span></td>
                  <td style={{ color: (row.momentum?.ret_3m_pct ?? 0) >= 0 ? '#34d399' : '#f87171' }}>
                    {fmtPct(row.momentum?.ret_3m_pct)}
                  </td>
                  <td style={{ color: '#cbd5e1' }}>
                    {row.momentum?.rsi_14 != null ? row.momentum.rsi_14.toFixed(0) : '—'}
                  </td>
                  <td style={{ color: '#cbd5e1' }}>{fmtPct(row.fundamentals?.fcf_yield_pct)}</td>
                  <td style={{ color: '#cbd5e1' }}>{fmtPct(row.fundamentals?.revenue_growth_pct)}</td>
                  <td style={{ color: '#64748b' }}>
                    {row.coverage != null ? `${Math.round(row.coverage * 100)}%` : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
