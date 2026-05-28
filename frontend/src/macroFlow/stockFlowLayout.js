const SECTOR_PALETTE = [
  '#22c55e',
  '#3b82f6',
  '#eab308',
  '#f97316',
  '#ec4899',
  '#94a3b8',
  '#06b6d4',
  '#a78bfa',
  '#64748b',
  '#ef4444',
];

export function sectorPalette(sector) {
  let h = 0;
  const s = String(sector || 'Other');
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) % SECTOR_PALETTE.length;
  return SECTOR_PALETTE[h];
}

/** Grid layout: one column per sector, stacked tickers. */
export function layoutStockFlowGraph(graph) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!nodes.length) return { rfNodes: [], rfEdges: [] };

  const bySector = {};
  nodes.forEach((n) => {
    const sec = n.sector || 'Other';
    if (!bySector[sec]) bySector[sec] = [];
    bySector[sec].push(n);
  });
  Object.values(bySector).forEach((list) => list.sort((a, b) => (b.flow_score || 0) - (a.flow_score || 0)));

  const sectors = Object.keys(bySector).sort();
  const colW = 150;
  const rowH = 28;

  const rfNodes = [];
  sectors.forEach((sec, col) => {
    bySector[sec].forEach((n, row) => {
      const color = sectorPalette(sec);
      rfNodes.push({
        id: n.id,
        type: 'stock',
        position: { x: col * colW, y: row * rowH },
        data: {
          ticker: n.ticker || n.id,
          sector: sec,
          flowScore: n.flow_score,
          returnPct: n.period_return_pct,
          color,
        },
      });
    });
  });

  const values = edges.map((e) => e.value || 0).filter((v) => v > 0);
  const maxV = values.length ? Math.max(...values) : 1;
  const minV = values.length ? Math.min(...values) : 0;

  const rfEdges = edges.map((e, i) => {
    const v = e.value || 0;
    const t = maxV > minV ? (Math.log(v + 1) - Math.log(minV + 1)) / (Math.log(maxV + 1) - Math.log(minV + 1)) : 0.5;
    const width = 1 + t * 4;
    return {
      id: `${e.source}-${e.target}-${i}`,
      source: e.source,
      target: e.target,
      type: e.bidirectional ? 'stockBi' : 'stockUni',
      animated: false,
      data: {
        width,
        bidirectional: !!e.bidirectional,
        correlation: e.correlation,
        value: v,
        animOffset: (i % 5) * 0.25,
      },
    };
  });

  return { rfNodes, rfEdges };
}
