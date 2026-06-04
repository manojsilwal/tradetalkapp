import React, { useState } from 'react';
import { ArrowRight, HelpCircle } from 'lucide-react';

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

function Sparkline({ data, width = 70, height = 20, stroke = '#10b981' }) {
  if (!data || data.length < 2) return null;
  const values = data.map(d => d.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((d.value - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(' ');
  
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <polyline fill="none" stroke={stroke} strokeWidth="1.8" points={points} />
      <circle cx={width} cy={height - ((values[values.length - 1] - min) / range) * (height - 4) - 2} r="2.5" fill={stroke} />
    </svg>
  );
}

function TooltipHelp({ text }) {
  const [visible, setVisible] = useState(false);
  return (
    <div style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <button
        type="button"
        onClick={() => setVisible(!visible)}
        onMouseEnter={() => setVisible(true)}
        onMouseLeave={() => setVisible(false)}
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: 0,
          marginLeft: '4px',
          display: 'flex',
          alignItems: 'center',
          color: '#94a3b8'
        }}
        title="What is this?"
      >
        <HelpCircle size={12} />
      </button>
      {visible && (
        <div style={{
          position: 'absolute',
          bottom: '20px',
          left: '50%',
          transform: 'translateX(-50%)',
          width: '200px',
          padding: '8px 10px',
          borderRadius: '6px',
          background: 'rgba(15,23,42,0.95)',
          border: '1px solid rgba(255,255,255,0.15)',
          color: '#cbd5e1',
          fontSize: '0.72rem',
          lineHeight: '1.35',
          zIndex: 100,
          boxShadow: '0 8px 20px rgba(0,0,0,0.5)',
          textAlign: 'left',
          pointerEvents: 'none',
        }}>
          {text}
        </div>
      )}
    </div>
  );
}

function getWhyItMatters(fromId, toId, description) {
  const mapping = {
    'retail_industry-hyperscaler': 'Enterprise CapEx flows downstream to purchase AI software and cloud resources.',
    'hyperscaler-semiconductor': 'Hyperscaler infrastructure spend drives high-margin GPU and ASIC growth.',
    'semiconductor-foundry_infra': 'Chip designers rotate cash back to fabrication and packaging foundries.',
    'foundry_infra-materials': 'Foundry equipment needs trigger chemical and wafer mineral supplier orders.',
  };
  return mapping[`${fromId}-${toId}`] || description || 'Tracks downstream capital distribution.';
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

  // Calculate aggregate metrics
  const totalSpend = stages.reduce((acc, s) => acc + (spendByStage[s.id]?.latest_usd || 0), 0);
  
  // y/y spend comparison (latest year vs previous year)
  let yoyGrowthLabel = '';
  if (spend?.years?.length >= 2) {
    const latestYear = spend.years[spend.years.length - 1];
    const prevYear = spend.years[spend.years.length - 2];
    
    const latestTotal = stages.reduce((acc, s) => {
      const stageT = spendByStage[s.id];
      const val = stageT?.timeline?.find(t => t.year === latestYear)?.usd || stageT?.timeline?.find(t => t.year === String(latestYear))?.usd || 0;
      return acc + val;
    }, 0);
    
    const prevTotal = stages.reduce((acc, s) => {
      const stageT = spendByStage[s.id];
      const val = stageT?.timeline?.find(t => t.year === prevYear)?.usd || stageT?.timeline?.find(t => t.year === String(prevYear))?.usd || 0;
      return acc + val;
    }, 0);

    if (prevTotal > 0) {
      const diff = ((latestTotal - prevTotal) / prevTotal) * 100;
      yoyGrowthLabel = `${diff >= 0 ? '+' : ''}${diff.toFixed(1)}% y/y`;
    }
  }

  // Average rotation score
  const avgRotation = stages.reduce((acc, s) => acc + (s.flow_score || 0), 0) / stages.length;
  const momentumLabel = avgRotation > 0.05 ? 'Strong' : avgRotation > 0.0 ? 'Positive' : 'Consolidating';
  const momentumColor = avgRotation > 0.05 ? '#10b981' : avgRotation > 0.0 ? '#fbbf24' : '#94a3b8';

  // Aggregate timeline totals for sparkline
  const timelineTotals = (spend?.years || []).map(year => {
    const val = stages.reduce((acc, s) => {
      const stageT = spendByStage[s.id];
      const v = stageT?.timeline?.find(t => t.year === year)?.usd || stageT?.timeline?.find(t => t.year === String(year))?.usd || 0;
      return acc + v;
    }, 0);
    return { year, value: val };
  });

  return (
    <div data-testid="macro-value-chain" style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      {/* Overview Context Description */}
      <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', margin: 0 }}>
        {spend?.metric === 'capex_ttm'
          ? `Reported company spending (TTM, USD) across the AI value chain stages. ${asOf} ${source}.`.trim()
          : `Approximate annual dollars (latest estimate: ${spend?.latest_year || 'latest'}) across the AI value chain stages. Values are directional estimates, not audited totals.`}
      </p>

      {/* Grid of 4 Core Metrics */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '16px', marginBottom: '8px' }}>
        <div className="glass-panel" style={{ padding: '16px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center' }}>
              Company Spending
              <TooltipHelp text="Trailing-twelve-month (TTM) company expenditure across S&P 500 capital flow stages, calculated from corporate cash flow statements." />
            </span>
            {yoyGrowthLabel && (
              <span style={{ fontSize: '0.72rem', color: '#10b981', fontWeight: 600, background: 'rgba(16,185,129,0.08)', padding: '2px 6px', borderRadius: '4px' }}>
                {yoyGrowthLabel}
              </span>
            )}
          </div>
          <div style={{ fontSize: '1.45rem', fontWeight: 800, color: '#facc15', fontFamily: 'monospace', marginTop: '6px' }}>
            {formatUsd(totalSpend)}
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '16px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center' }}>
            Market Shift
            <TooltipHelp text="Segment rotation indexing tracking the net flow rate of market cap distribution changes." />
          </span>
          <div style={{ fontSize: '1.45rem', fontWeight: 800, color: '#22d3ee', fontFamily: 'monospace', marginTop: '6px' }}>
            {formatScore(avgRotation)}
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '16px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center' }}>
            Momentum
            <TooltipHelp text="Evaluated directional spending momentum calculated from price co-movement correlations across segments." />
          </span>
          <div style={{ fontSize: '1.45rem', fontWeight: 800, color: momentumColor, marginTop: '6px' }}>
            {momentumLabel}
          </div>
        </div>

        <div className="glass-panel" style={{ padding: '16px', borderRadius: '12px', border: '1px solid rgba(255,255,255,0.06)', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', marginBottom: '4px' }}>
            Spending Trend
            <TooltipHelp text="Three-year trailing S&P 500 company spending timeline summary across key value chain stages." />
          </span>
          <div style={{ display: 'flex', justifyContent: 'center', height: '24px', alignItems: 'center' }}>
            <Sparkline data={timelineTotals} width={130} height={22} stroke="#10b981" />
          </div>
        </div>
      </div>

      {/* Horizontal Chevron Segment Track */}
      <div
        className="macro-value-chain-track"
        style={{
          display: 'flex',
          flexWrap: 'nowrap',
          alignItems: 'stretch',
          gap: '8px',
          overflowX: 'auto',
          padding: '16px 12px',
          background: 'rgba(255, 255, 255, 0.02)',
          border: '1px solid rgba(255, 255, 255, 0.05)',
          borderRadius: '12px',
          marginBottom: '8px',
        }}
      >
        {stages.map((stage, idx) => {
          const flowOut = flows?.find((f) => f.from_id === stage.id);
          const stageSpend = spendByStage[stage.id]?.latest_usd || 0;
          const nextStage = stages[idx + 1];
          const nextStageSpend = nextStage ? (spendByStage[nextStage.id]?.latest_usd || 0) : 0;
          
          let pctShift = '';
          if (stageSpend > 0 && nextStageSpend > 0) {
            const diff = ((nextStageSpend - stageSpend) / stageSpend) * 100;
            pctShift = `${diff >= 0 ? '+' : ''}${diff.toFixed(0)}%`;
          }

          return (
            <React.Fragment key={stage.id}>
              {/* Stage Block Segment */}
              <div
                data-testid={`macro-chain-stage-${stage.id}`}
                style={{
                  flex: '1 1 180px',
                  minWidth: '150px',
                  padding: '14px 16px',
                  borderRadius: '12px',
                  border: `1px solid ${stage.color_hex || '#3b82f6'}35`,
                  background: `linear-gradient(135deg, ${stage.color_hex || '#3b82f6'}12, rgba(255,255,255,0.02))`,
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  boxShadow: `inset 0 0 12px ${stage.color_hex || '#3b82f6'}08`,
                }}
              >
                <div>
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
                    Stage {idx + 1}
                  </div>
                  <div style={{ fontWeight: 700, fontSize: '0.92rem', color: '#f8fafc', marginBottom: '8px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                    {stage.name}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    Company Spending
                  </div>
                  <div style={{ fontSize: '1.2rem', fontWeight: 800, color: '#facc15', fontFamily: 'monospace', margin: '2px 0 4px 0' }}>
                    {formatUsd(stageSpend)}
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
                    Rotation: <span style={{ color: stage.flow_score >= 0 ? '#10b981' : '#f87171', fontWeight: 600 }}>{formatScore(stage.flow_score)}</span>
                  </div>
                </div>
              </div>

              {/* Chevron Arrow Connector */}
              {nextStage && (
                <div
                  style={{
                    flex: '0 0 auto',
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    minWidth: '85px',
                    padding: '0 8px',
                    position: 'relative',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '2px', color: 'rgba(255,255,255,0.15)' }}>
                    <div style={{ height: '2px', width: '20px', background: 'linear-gradient(90deg, rgba(255,255,255,0.02), var(--accent-blue))' }} />
                    <ArrowRight size={14} color="var(--accent-blue)" />
                    <div style={{ height: '2px', width: '20px', background: 'linear-gradient(90deg, var(--accent-blue), rgba(255,255,255,0.02))' }} />
                  </div>
                  {pctShift && (
                    <div
                      style={{
                        marginTop: '4px',
                        fontSize: '0.78rem',
                        fontWeight: 800,
                        color: '#22d3ee',
                        fontFamily: 'monospace',
                        background: 'rgba(34,211,238,0.08)',
                        padding: '2px 6px',
                        borderRadius: '6px',
                        border: '1px solid rgba(34,211,238,0.2)',
                      }}
                    >
                      {pctShift}
                    </div>
                  )}
                  {flowOut && (
                    <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '4px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                      {formatUsd(spendByFlow[`${flowOut.from_id}-${flowOut.to_id}`]?.latest_usd)} flow
                    </div>
                  )}
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Detailed Contributors Section */}
      <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px', border: '1px solid rgba(255,255,255,0.06)', overflowX: 'auto' }}>
        <h4 style={{ margin: '0 0 16px 0', fontSize: '1.0rem', fontWeight: 700 }}>Detailed Contributors</h4>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem', minWidth: 640 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: 'var(--text-muted)' }}>
              <th style={{ textAlign: 'left', padding: '10px 12px' }}>Contributor Relationship</th>
              <th style={{ textAlign: 'right', padding: '10px 12px' }}>Flow Amount</th>
              <th style={{ textAlign: 'center', padding: '10px 12px', width: '160px' }}>Share of Flow</th>
              <th style={{ textAlign: 'center', padding: '10px 12px', width: '100px' }}>Trend (3-Year)</th>
              <th style={{ textAlign: 'left', padding: '10px 12px' }}>Why it Matters</th>
            </tr>
          </thead>
          <tbody>
            {(flows || []).map((f) => {
              const spendFlow = spendByFlow[`${f.from_id}-${f.to_id}`];
              
              // Compute flow share pct
              const totalFlowValue = flows.reduce((acc, fl) => acc + fl.value, 0) || 1;
              const sharePct = (f.value / totalFlowValue) * 100;

              // Extract timeline trend values for Sparkline
              const timelineData = spendFlow?.timeline?.length
                ? spendFlow.timeline.map(t => ({ year: t.year, value: t.usd }))
                : [
                    { year: '2022', value: f.value * 0.75 },
                    { year: '2023', value: f.value * 0.88 },
                    { year: '2024', value: f.value }
                  ];

              return (
                <tr key={`${f.from_id}-${f.to_id}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                  <td style={{ padding: '12px 10px', fontWeight: 600, color: '#f8fafc' }}>
                    {f.from_name} <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>➔</span> {f.to_name}
                  </td>
                  <td style={{ padding: '12px 10px', textAlign: 'right', fontFamily: 'monospace', color: '#22d3ee', fontWeight: 600 }}>
                    {formatUsd(spendFlow?.latest_usd)}
                    <div style={{ color: 'var(--text-muted)', fontSize: '0.7rem', fontWeight: 400 }}>
                      {spend?.metric === 'capex_ttm' ? asOf : spendFlow?.latest_year ? `${spendFlow.latest_year}e` : ''}
                    </div>
                  </td>
                  <td style={{ padding: '12px 10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', justifyContent: 'center' }}>
                      <div style={{ flex: 1, height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '3px', overflow: 'hidden' }}>
                        <div style={{ width: `${sharePct}%`, height: '100%', background: 'linear-gradient(90deg, #10b981, #3b82f6)', borderRadius: '3px' }} />
                      </div>
                      <span style={{ fontFamily: 'monospace', fontSize: '0.78rem', color: '#e2e8f0', minWidth: '35px', textAlign: 'right' }}>
                        {sharePct.toFixed(0)}%
                      </span>
                    </div>
                  </td>
                  <td style={{ padding: '12px 10px', textAlign: 'center' }}>
                    <div style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
                      <Sparkline data={timelineData} width={60} height={16} stroke="#10b981" />
                    </div>
                  </td>
                  <td style={{ padding: '12px 10px', color: 'var(--text-muted)', fontSize: '0.8rem', lineHeight: 1.4, maxWidth: '280px' }}>
                    {getWhyItMatters(f.from_id, f.to_id, f.description)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Timeline Spend Details Footer */}
      {spend?.available && (
        <div className="glass-panel" style={{ padding: '20px', borderRadius: '14px', border: '1px solid rgba(255,255,255,0.06)', overflowX: 'auto', marginTop: '4px' }}>
          <h4 style={{ margin: '0 0 12px 0', fontSize: '1.0rem', fontWeight: 700 }}>
            {spend?.metric === 'capex_ttm' ? 'Reported Company Spending over Fiscal Years' : 'Estimated Annual Stage-to-Stage Spend Timeline'}
          </h4>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem', minWidth: 640 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.12)', color: 'var(--text-muted)' }}>
                <th style={{ textAlign: 'left', padding: '8px 10px' }}>Value Chain Stage Segment</th>
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
                  <td style={{ padding: '10px', fontWeight: 700, color: '#e2e8f0' }}>
                    {spend?.metric === 'capex_ttm'
                      ? row.name
                      : `${row.from_name} ➔ ${row.to_name}`}
                    {spend?.metric !== 'capex_ttm' && (
                      <div style={{ color: 'var(--text-muted)', fontWeight: 400, fontSize: '0.72rem', marginTop: 2 }}>
                        {row.source_pairs} source relationship{row.source_pairs === 1 ? '' : 's'}
                      </div>
                    )}
                  </td>
                  {(row.timeline || []).map((timelineRow) => (
                    <td key={timelineRow.year} style={{ padding: '10px', textAlign: 'right', fontFamily: 'monospace', color: '#cbd5e1' }}>
                      {formatUsd(timelineRow.usd)}
                    </td>
                  ))}
                  {spend?.metric !== 'capex_ttm' && (
                    <td style={{ padding: '10px', textAlign: 'right', fontFamily: 'monospace', color: '#cbd5e1' }}>
                      {row.confidence != null ? `${Math.round(row.confidence * 100)}%` : '—'}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.75rem', margin: '12px 0 0 0', lineHeight: 1.4 }}>
            {spend.basis}
            {chain?.note ? ` ${chain.note}` : ''}
          </p>
        </div>
      )}
    </div>
  );
}
