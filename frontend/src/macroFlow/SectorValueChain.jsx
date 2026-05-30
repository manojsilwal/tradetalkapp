import React from 'react';
import { ArrowRight } from 'lucide-react';

function formatScore(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(3)}`;
}

function formatUsd(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const abs = Math.abs(n);
  if (abs >= 1_000_000_000_000) return `$${(n / 1_000_000_000_000).toFixed(1)}T`;
  if (abs >= 1_000_000_000) return `$${(n / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  return `$${n.toFixed(0)}`;
}

export default function SectorValueChain({ chain, loading }) {
  if (loading) return null;
  if (!chain?.stages?.length) {
    return (
      <p style={{ color: 'var(--text-muted)', margin: 0 }}>
        No value-chain data for this interval yet — run refresh after seed.
      </p>
    );
  }

  const { stages, flows, spend } = chain;
  const spendByStage = Object.fromEntries((spend?.stage_totals || []).map((s) => [s.id, s]));
  const spendByFlow = Object.fromEntries((spend?.flows || []).map((f) => [`${f.from_id}-${f.to_id}`, f]));

  const spendLabel = spend?.latest_label || (spend?.latest_year ? `Estimated spend (${spend.latest_year}e)` : 'TTM reported CapEx');
  const asOf = spend?.as_of ? `as of ${spend.as_of}` : '';
  const source = spend?.source ? `Source: ${spend.source}` : '';

  return (
    <div data-testid="macro-value-chain" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: 0 }}>
        {spend?.metric === 'capex_ttm'
          ? `Reported capital expenditure (TTM, USD) across the AI capex chain. ${asOf} ${source}.`.trim()
          : `Approximate annual dollars (latest estimate: ${spend?.latest_year || 'latest'}) across the AI capex chain. Values are directional estimates, not audited totals.`}
      </p>

      <div
        className="macro-value-chain-track"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'stretch',
          gap: 8,
          overflowX: 'auto',
          paddingBottom: 8,
        }}
      >
        {stages.map((stage, idx) => {
          const flowOut = flows?.find((f) => f.from_id === stage.id);
          const stageSpend = spendByStage[stage.id];
          return (
            <React.Fragment key={stage.id}>
              <div
                data-testid={`macro-chain-stage-${stage.id}`}
                style={{
                  flex: '1 1 140px',
                  minWidth: 130,
                  padding: '14px 12px',
                  borderRadius: 12,
                  border: `1px solid ${stage.color_hex || '#6366f1'}55`,
                  background: `${stage.color_hex || '#6366f1'}18`,
                }}
              >
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: 6 }}>
                  Stage {idx + 1}
                </div>
                <div style={{ fontWeight: 700, fontSize: '0.95rem', lineHeight: 1.25, marginBottom: 10 }}>
                  {stage.name}
                </div>
                <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  {spendLabel}
                </div>
                <div style={{ fontSize: '1.2rem', fontWeight: 800, fontFamily: 'monospace' }}>
                  {formatUsd(stageSpend?.latest_usd)}
                </div>
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: 4 }}>
                  Relative rotation: {formatScore(stage.flow_score)}
                </div>
              </div>
              {flowOut && (
                <div
                  style={{
                    flex: '0 0 auto',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minWidth: 72,
                    padding: '0 4px',
                  }}
                >
                  <ArrowRight size={20} color="var(--accent-blue)" />
                  <div
                    style={{
                      marginTop: 6,
                      fontSize: '0.85rem',
                      fontWeight: 700,
                      fontFamily: 'monospace',
                      color: '#22d3ee',
                      textAlign: 'center',
                    }}
                  >
                    {formatUsd(spendByFlow[`${flowOut.from_id}-${flowOut.to_id}`]?.latest_usd)}
                  </div>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textAlign: 'center', maxWidth: 90 }}>
                    {spend?.metric === 'capex_ttm' ? 'downstream CapEx' : spend?.latest_year ? `${spend.latest_year}e` : 'latest'}
                  </div>
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: '0.85rem',
          }}
        >
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: 'var(--text-muted)' }}>
              <th style={{ textAlign: 'left', padding: '8px 10px' }}>From</th>
              <th style={{ textAlign: 'left', padding: '8px 10px' }}>To</th>
              <th style={{ textAlign: 'right', padding: '8px 10px' }}>{spendLabel}</th>
              <th style={{ textAlign: 'right', padding: '8px 10px' }}>Rotation score</th>
              <th style={{ textAlign: 'left', padding: '8px 10px' }}>Driver</th>
            </tr>
          </thead>
          <tbody>
            {(flows || []).map((f) => {
              const spendFlow = spendByFlow[`${f.from_id}-${f.to_id}`];
              return (
                <tr key={`${f.from_id}-${f.to_id}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                  <td style={{ padding: '10px', fontWeight: 600 }}>{f.from_name}</td>
                  <td style={{ padding: '10px', fontWeight: 600 }}>{f.to_name}</td>
                  <td style={{ padding: '10px', textAlign: 'right', fontFamily: 'monospace', color: '#22d3ee' }}>
                    {formatUsd(spendFlow?.latest_usd)}
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem' }}>
                      {spend?.metric === 'capex_ttm' ? asOf : spendFlow?.latest_year ? `${spendFlow.latest_year}e` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '10px', textAlign: 'right', fontFamily: 'monospace' }}>
                    {f.value?.toFixed(3)}
                  </td>
                  <td style={{ padding: '10px', color: 'var(--text-muted)' }}>{f.description}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {spend?.available && (
        <div style={{ overflowX: 'auto' }}>
          <h4 style={{ margin: '0 0 10px 0', fontSize: '0.95rem' }}>
            {spend?.metric === 'capex_ttm' ? 'Reported CapEx over time (fiscal year)' : 'Estimated annual spend over time'}
          </h4>
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: '0.82rem',
            }}
          >
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: 'var(--text-muted)' }}>
                <th style={{ textAlign: 'left', padding: '8px 10px' }}>Stage</th>
                {(spend.years || []).map((year) => (
                  <th key={year} style={{ textAlign: 'right', padding: '8px 10px' }}>{year}</th>
                ))}
                {spend?.metric !== 'capex_ttm' && (
                  <th style={{ textAlign: 'right', padding: '8px 10px' }}>Confidence</th>
                )}
              </tr>
            </thead>
            <tbody>
              {(spend?.metric === 'capex_ttm' ? spend.stage_totals : spend.flows)?.map((row) => (
                <tr
                  key={`${row.id || row.from_id}-${row.to_id || 'stage'}-spend`}
                  style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}
                >
                  <td style={{ padding: '10px', fontWeight: 700 }}>
                    {spend?.metric === 'capex_ttm'
                      ? row.name
                      : `${row.from_name} → ${row.to_name}`}
                    {spend?.metric !== 'capex_ttm' && (
                      <div style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '0.72rem', marginTop: 2 }}>
                        {row.source_pairs} source relationship{row.source_pairs === 1 ? '' : 's'}
                      </div>
                    )}
                  </td>
                  {(row.timeline || []).map((timelineRow) => (
                    <td key={timelineRow.year} style={{ padding: '10px', textAlign: 'right', fontFamily: 'monospace' }}>
                      {formatUsd(timelineRow.usd)}
                    </td>
                  ))}
                  {spend?.metric !== 'capex_ttm' && (
                    <td style={{ padding: '10px', textAlign: 'right', fontFamily: 'monospace' }}>
                      {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : '—'}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.75rem', margin: '8px 0 0 0' }}>
            {spend.basis}
            {chain?.note ? ` ${chain.note}` : ''}
          </p>
        </div>
      )}
    </div>
  );
}
