import React, { useMemo } from 'react';
import SupplyChainFlowGraph from './SupplyChainFlowGraph';
import SectorSankeyViz from './SectorSankeyViz';
import { fmtUSD, totalFlow } from './utils';
import { YEARS } from './constants';
import './supplyChainViz.css';

export default function TimelineMorphView({
  year,
  onYearChange,
  graphSnapshots,
  sectorSnapshots,
  highlightId,
  onNodeClick,
  root,
}) {
  const graph = useMemo(() => {
    const snap = (graphSnapshots || []).find((s) => s.year === year);
    return snap || null;
  }, [graphSnapshots, year]);

  const sectorData = useMemo(() => {
    const snap = (sectorSnapshots || []).find((s) => s.year === year);
    return snap || null;
  }, [sectorSnapshots, year]);

  const flowTotal = totalFlow(graph?.edges);
  const prevYear = year > YEARS[0] ? year - 1 : null;
  const prevSnap = prevYear
    ? (graphSnapshots || []).find((s) => s.year === prevYear)
    : null;
  const prevTotal = prevSnap ? totalFlow(prevSnap.edges) : null;
  const yoy =
    prevTotal && prevTotal > 0
      ? (((flowTotal - prevTotal) / prevTotal) * 100).toFixed(1)
      : null;

  return (
    <div data-testid="supply-chain-timeline" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="sc-glass-panel">
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 16, justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Capital flow year</div>
            <div style={{ fontSize: '1.6rem', fontWeight: 800, color: '#22d3ee' }}>{year}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Total chain flow</div>
            <div style={{ fontSize: '1.2rem', fontWeight: 700 }}>{fmtUSD(flowTotal)}</div>
            {yoy != null && (
              <div style={{ fontSize: '0.8rem', color: Number(yoy) >= 0 ? '#22c55e' : '#f87171' }}>
                {Number(yoy) >= 0 ? '+' : ''}
                {yoy}% vs {prevYear}
              </div>
            )}
          </div>
        </div>
        <input
          type="range"
          className="sc-timeline-slider"
          min={YEARS[0]}
          max={YEARS[YEARS.length - 1]}
          step={1}
          value={year}
          onChange={(e) => onYearChange(Number(e.target.value))}
          aria-label="Timeline year"
        />
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          {YEARS.map((y) => (
            <span key={y} style={{ color: y === year ? '#22d3ee' : undefined, fontWeight: y === year ? 700 : 400 }}>
              {y}
            </span>
          ))}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 16 }}>
        <div>
          <h4 style={{ margin: '0 0 8px 0', fontSize: '0.9rem', color: 'var(--text-muted)' }}>
            Company graph {root ? `(${root})` : ''}
          </h4>
          <SupplyChainFlowGraph graph={graph} highlightId={highlightId} onNodeClick={onNodeClick} />
        </div>
        <div>
          <h4 style={{ margin: '0 0 8px 0', fontSize: '0.9rem', color: 'var(--text-muted)' }}>Sector Sankey</h4>
          <SectorSankeyViz data={sectorData} year={year} />
        </div>
      </div>
    </div>
  );
}
