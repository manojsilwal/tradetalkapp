import { SECTOR_COLORS } from './constants';

export function fmtUSD(n) {
  if (n == null) return '—';
  if (n >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (n >= 1e6) return `$${(n / 1e6).toFixed(0)}M`;
  return `$${Number(n).toLocaleString()}`;
}

export function sectorColor(sector) {
  return SECTOR_COLORS[sector] || '#6366f1';
}

/** Layered left→right layout: payer (customer) on the left, supplier on the right. */
export function layoutSupplyChainGraph(graph, rootHint) {
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  if (!nodes.length) return { rfNodes: [], rfEdges: [] };

  const nodeMap = Object.fromEntries(nodes.map((n) => [n.node_id, n]));
  const downstream = {};
  const indegree = {};
  nodes.forEach((n) => {
    downstream[n.node_id] = [];
    indegree[n.node_id] = 0;
  });
  edges.forEach((e) => {
    if (nodeMap[e.source_node_id] && nodeMap[e.target_node_id]) {
      downstream[e.source_node_id].push(e.target_node_id);
      indegree[e.target_node_id] += 1;
    }
  });

  let roots = nodes.filter((n) => indegree[n.node_id] === 0).map((n) => n.node_id);
  if (rootHint && nodeMap[rootHint]) roots = [rootHint];

  const layer = {};
  const queue = [...roots];
  roots.forEach((id) => {
    layer[id] = 0;
  });
  while (queue.length) {
    const id = queue.shift();
    for (const next of downstream[id] || []) {
      const nextLayer = (layer[id] ?? 0) + 1;
      if (layer[next] == null || nextLayer > layer[next]) {
        layer[next] = nextLayer;
        queue.push(next);
      }
    }
  }
  nodes.forEach((n) => {
    if (layer[n.node_id] == null) layer[n.node_id] = 0;
  });

  const byLayer = {};
  nodes.forEach((n) => {
    const L = layer[n.node_id];
    if (!byLayer[L]) byLayer[L] = [];
    byLayer[L].push(n);
  });

  const colW = 240;
  const rowH = 100;
  const rfNodes = nodes.map((n) => {
    const L = layer[n.node_id];
    const col = byLayer[L] || [];
    const idx = col.findIndex((x) => x.node_id === n.node_id);
    return {
      id: n.node_id,
      type: 'company',
      position: { x: L * colW, y: idx * rowH },
      data: {
        label: n.name,
        ticker: n.ticker || n.node_id,
        sector: n.gics_sector,
        isPublic: n.is_public,
        color: sectorColor(n.gics_sector),
        node: n,
      },
    };
  });

  const amounts = edges.map((e) => e.amount_est_usd || 0).filter((v) => v > 0);
  const maxAmt = amounts.length ? Math.max(...amounts) : 1;
  const minAmt = amounts.length ? Math.min(...amounts) : 1;

  const rfEdges = edges.map((e) => {
    const amt = e.amount_est_usd || 0;
    const t = maxAmt > minAmt ? (Math.log(amt + 1) - Math.log(minAmt + 1)) / (Math.log(maxAmt + 1) - Math.log(minAmt + 1)) : 0.5;
    const width = 1.5 + t * 5;
    return {
      id: e.edge_id,
      source: e.source_node_id,
      target: e.target_node_id,
      type: 'money',
      animated: false,
      data: {
        amount: amt,
        relationship: e.relationship_type,
        width,
        label: fmtUSD(amt),
      },
    };
  });

  return { rfNodes, rfEdges };
}

export function toNivoSankey(sectorPayload) {
  if (!sectorPayload?.links?.length) return { nodes: [], links: [] };
  const nodes = (sectorPayload.nodes || []).map((n) => ({
    id: n.id,
    nodeColor: sectorColor(n.id),
  }));
  const links = sectorPayload.links.map((l) => ({
    source: l.source,
    target: l.target,
    value: Math.max(l.value || 0, 1),
  }));
  return { nodes, links };
}

export function totalFlow(edges) {
  return (edges || []).reduce((s, e) => s + (e.amount_est_usd || 0), 0);
}
