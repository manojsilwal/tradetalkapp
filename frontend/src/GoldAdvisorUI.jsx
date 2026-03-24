import React, { useState, useCallback } from 'react';
import { Coins, Loader2, RefreshCw, AlertTriangle, TrendingUp, Shield } from 'lucide-react';
import { API_BASE_URL, apiFetch } from './api';

function fmt(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number' && Number.isFinite(v)) return String(v);
  return String(v);
}

export default function GoldAdvisorUI() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const json = await apiFetch(`${API_BASE_URL}/advisor/gold`);
      setData(json);
    } catch (e) {
      setError(e.message || 'Failed to load Gold Advisor');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="consumer-container fade-in">
      <div className="header-section" style={{ marginBottom: 24 }}>
        <div className="title-group">
          <h2>Gold Advisor</h2>
          <p>Investor snapshot — not for intraday trading. Refresh occasionally for allocation context.</p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="glass-panel"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 8,
            padding: '10px 16px',
            borderRadius: 12,
            border: '1px solid rgba(148,163,184,0.35)',
            background: 'rgba(15,23,42,0.5)',
            color: '#e2e8f0',
            cursor: loading ? 'wait' : 'pointer',
          }}
        >
          {loading ? <Loader2 className="spinner" size={18} /> : <RefreshCw size={18} />}
          Refresh snapshot
        </button>
      </div>

      {error && (
        <div className="error-banner glass-panel" style={{ borderColor: 'var(--accent-red)', marginBottom: 20 }}>
          <p style={{ color: 'var(--accent-red)', padding: 10, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            <AlertTriangle size={18} /> {error}
          </p>
        </div>
      )}

      {loading && !data && (
        <div style={{ display: 'flex', justifyContent: 'center', padding: 48 }}>
          <Loader2 className="spinner" size={48} color="var(--accent-blue)" />
        </div>
      )}

      {data && (
        <>
          <div
            className="glass-panel"
            style={{ padding: 20, marginBottom: 20, borderRadius: 16, border: '1px solid rgba(234,179,8,0.25)' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
              <Coins color="#eab308" size={22} />
              <h3 style={{ margin: 0 }}>AI briefing</h3>
            </div>
            <div
              style={{
                display: 'inline-block',
                padding: '4px 10px',
                borderRadius: 8,
                background: 'rgba(234,179,8,0.12)',
                color: '#fcd34d',
                fontSize: 12,
                fontWeight: 700,
                marginBottom: 12,
                textTransform: 'uppercase',
              }}
            >
              {data.briefing?.directional_bias || 'neutral'}
            </div>
            <p style={{ color: '#cbd5e1', lineHeight: 1.6, margin: '0 0 16px' }}>{data.briefing?.summary}</p>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 16 }}>
              <div>
                <h4 style={{ color: '#94a3b8', fontSize: 12, margin: '0 0 8px' }}>Key drivers</h4>
                <ul style={{ margin: 0, paddingLeft: 18, color: '#e2e8f0' }}>
                  {(data.briefing?.key_drivers || []).map((x, i) => (
                    <li key={i} style={{ marginBottom: 6 }}>{x}</li>
                  ))}
                </ul>
              </div>
              <div>
                <h4 style={{ color: '#94a3b8', fontSize: 12, margin: '0 0 8px' }}>Risks</h4>
                <ul style={{ margin: 0, paddingLeft: 18, color: '#e2e8f0' }}>
                  {(data.briefing?.risk_factors || []).map((x, i) => (
                    <li key={i} style={{ marginBottom: 6 }}>{x}</li>
                  ))}
                </ul>
              </div>
            </div>
            {data.briefing?.levels_to_watch && (
              <p style={{ color: '#94a3b8', fontSize: 14, marginTop: 16, marginBottom: 0 }}>
                <TrendingUp size={14} style={{ verticalAlign: 'middle', marginRight: 6 }} />
                {data.briefing.levels_to_watch}
              </p>
            )}
            <p style={{ color: '#64748b', fontSize: 12, marginTop: 16, marginBottom: 0 }}>
              Model confidence (heuristic): {fmt(data.briefing?.confidence_0_1)} · {data.context?.investor_note}
            </p>
          </div>

          <div className="dashboard-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 20 }}>
            {[
              ['Gold (GC=F last)', fmt(data.context?.macro?.gold_futures_last_usd)],
              ['DXY', fmt(data.context?.macro?.dxy_spot)],
              ['10Y TIPS real yield %', fmt(data.context?.macro?.ten_year_tips_real_yield_pct)],
              ['10Y nominal %', fmt(data.context?.macro?.ten_year_nominal_treasury_pct)],
              ['VIX', fmt(data.context?.macro?.vix)],
            ].map(([label, val]) => (
              <div key={label} className="dash-card glass-panel fade-in" style={{ padding: 18, borderRadius: 14 }}>
                <div style={{ color: '#94a3b8', fontSize: 12, marginBottom: 6 }}>{label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: '#f8fafc' }}>{val}</div>
              </div>
            ))}
          </div>

          <div className="glass-panel" style={{ padding: 18, borderRadius: 16, marginBottom: 20 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <Shield size={18} color="#60a5fa" />
              <h3 style={{ margin: 0 }}>Daily technicals (deterministic)</h3>
            </div>
            <pre
              style={{
                margin: 0,
                padding: 12,
                background: 'rgba(15,23,42,0.6)',
                borderRadius: 10,
                fontSize: 12,
                color: '#cbd5e1',
                overflow: 'auto',
                maxHeight: 320,
              }}
            >
              {JSON.stringify(data.context?.technicals_daily, null, 2)}
            </pre>
          </div>

          <div className="glass-panel" style={{ padding: 18, borderRadius: 16, marginBottom: 20 }}>
            <h3 style={{ margin: '0 0 12px' }}>Headline sentiment (MVP)</h3>
            <p style={{ color: '#94a3b8', fontSize: 13, marginBottom: 8 }}>
              Score −1…+1 (keyword heuristic). {data.context?.sentiment?.source}
            </p>
            <div style={{ fontSize: 20, fontWeight: 700, color: '#e2e8f0', marginBottom: 12 }}>
              {fmt(data.context?.sentiment?.score_neg1_to_pos1)}
            </div>
            <ul style={{ margin: 0, paddingLeft: 18, color: '#cbd5e1' }}>
              {(data.context?.sentiment?.headlines || []).map((h, i) => (
                <li key={i} style={{ marginBottom: 8 }}>{h}</li>
              ))}
            </ul>
          </div>

          <div className="glass-panel" style={{ padding: 18, borderRadius: 16 }}>
            <h3 style={{ margin: '0 0 12px' }}>Calendar hints (long-term holder)</h3>
            <ul style={{ margin: 0, paddingLeft: 18, color: '#94a3b8' }}>
              {(data.context?.calendar_hints || []).map((h, i) => (
                <li key={i} style={{ marginBottom: 8 }}>{h}</li>
              ))}
            </ul>
            <p style={{ color: '#64748b', fontSize: 12, marginTop: 12, marginBottom: 0 }}>
              As of UTC: {data.context?.as_of_utc}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
