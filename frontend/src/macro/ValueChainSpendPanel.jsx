import React, { useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { API_BASE_URL, apiFetch } from '../api';
import { formatLargeUSD } from './macroUtils';
import { SPEND_CHAIN_FALLBACK } from './spendChainFallback';

function SpendTable({ title, rows, idKey = 'entity_id' }) {
  if (!rows?.length) {
    return (
      <div className="macro-spend-col">
        <div className="macro-spend-col-title">{title}</div>
        <div className="macro-loading" style={{ minHeight: 80 }}>No data</div>
      </div>
    );
  }
  return (
    <div className="macro-spend-col">
      <div className="macro-spend-col-title">{title}</div>
      <table className="macro-table">
        <thead>
          <tr>
            <th>Entity</th>
            <th>Est_spend</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row[idKey] || row.entity_id}>
              <td>
                <span className="ticker">{row.entity_id || row.spender_id || row.beneficiary_id}</span>
                <span className="macro-spend-entity-name">{row.entity_name || row.spender_name || row.beneficiary_name}</span>
              </td>
              <td className="move pos">{formatLargeUSD(row.spend_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

async function loadSpendChainGroups() {
  try {
    const json = await apiFetch(`${API_BASE_URL}/macro/spend-chain`);
    const groups = json?.spend_flow_groups || [];
    if (groups.length) {
      return { groups, latestYear: json.latest_year || groups[0]?.latest_year, fromFallback: false };
    }
  } catch {
    /* try legacy chain endpoint */
    try {
      const legacy = await apiFetch(`${API_BASE_URL}/macro/flow/chain?interval=1w`);
      const groups = legacy?.spend_flow_groups || [];
      if (groups.length) {
        return { groups, latestYear: groups[0]?.latest_year, fromFallback: false };
      }
    } catch {
      /* use bundled fallback */
    }
  }
  return {
    groups: SPEND_CHAIN_FALLBACK.spend_flow_groups,
    latestYear: SPEND_CHAIN_FALLBACK.latest_year,
    fromFallback: true,
  };
}

export default function ValueChainSpendPanel() {
  const [groups, setGroups] = useState([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [latestYear, setLatestYear] = useState(null);
  const [fromFallback, setFromFallback] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await loadSpendChainGroups();
        if (cancelled) return;
        setGroups(result.groups);
        setLatestYear(result.latestYear || null);
        setFromFallback(result.fromFallback);
        setActiveIdx(0);
      } catch (e) {
        if (!cancelled) {
          setGroups(SPEND_CHAIN_FALLBACK.spend_flow_groups);
          setLatestYear(SPEND_CHAIN_FALLBACK.latest_year);
          setFromFallback(true);
          setError(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const active = groups[activeIdx];

  if (loading) {
    return (
      <div className="macro-panel macro-spend-panel" data-testid="macro-spend-chain">
        <div className="macro-loading"><Loader2 size={20} className="spinner" /> Loading spend chain…</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="macro-panel macro-spend-panel" data-testid="macro-spend-chain">
        <div className="macro-error" style={{ margin: 12 }}>{error}</div>
      </div>
    );
  }

  if (!groups.length) {
    return (
      <div className="macro-panel macro-spend-panel" data-testid="macro-spend-chain">
        <div className="macro-loading">No spend-chain relationships available.</div>
      </div>
    );
  }

  return (
    <div className="macro-panel macro-spend-panel" data-testid="macro-spend-chain">
      <div className="macro-panel-header">
        <span>Capex_spend_chain :: top_spenders → beneficiaries</span>
        <span>{latestYear ? `Est. ${latestYear}` : 'Relationship estimates'}</span>
      </div>

      <div className="macro-spend-edge-tabs" role="tablist">
        {groups.map((g, idx) => (
          <button
            key={`${g.from_stage_id}-${g.to_stage_id}`}
            type="button"
            role="tab"
            aria-selected={activeIdx === idx}
            className={`macro-spend-edge-tab ${activeIdx === idx ? 'active' : ''}`}
            onClick={() => setActiveIdx(idx)}
          >
            {g.from_stage_name} → {g.to_stage_name}
          </button>
        ))}
      </div>

      {active && (
        <>
          <p className="macro-spend-desc">{active.description}</p>
          <div className="macro-spend-grid">
            <SpendTable title="Top_spenders" rows={active.top_spenders} />
            <SpendTable title="Top_beneficiaries" rows={active.top_beneficiaries} />
          </div>

          {active.pairs?.length > 0 && (
            <div className="macro-spend-pairs">
              <div className="macro-spend-col-title">Direct_spend_links</div>
              <table className="macro-table">
                <thead>
                  <tr>
                    <th>Spender</th>
                    <th>Beneficiary</th>
                    <th>Type</th>
                    <th>Est_spend</th>
                  </tr>
                </thead>
                <tbody>
                  {active.pairs.map((p) => (
                    <tr key={`${p.spender_id}-${p.beneficiary_id}`}>
                      <td>
                        <span className="ticker">{p.spender_id}</span>
                        <span className="macro-spend-entity-name">{p.spender_name}</span>
                      </td>
                      <td>
                        <span className="ticker">{p.beneficiary_id}</span>
                        <span className="macro-spend-entity-name">{p.beneficiary_name}</span>
                      </td>
                      <td style={{ color: 'var(--macro-muted)', textTransform: 'lowercase' }}>
                        {(p.relationship_type || 'spend').replace(/_/g, ' ')}
                      </td>
                      <td className="move pos">{formatLargeUSD(p.spend_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <p className="macro-footnote" style={{ padding: '0 12px 12px' }}>
        {fromFallback
          ? 'Showing bundled relationship estimates (API unavailable — redeploy backend for live refresh).'
          : 'Estimated vendor/customer spend from curated supply-chain relationships — not audited totals.'}
      </p>
    </div>
  );
}
