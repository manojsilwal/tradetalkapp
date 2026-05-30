const FALLBACK_COLORS = ['#22c55e', '#3b82f6', '#eab308', '#f97316', '#ec4899', '#94a3b8', '#6366f1'];

export function toMacroSankey(sankeyPayload) {
  if (!sankeyPayload?.links?.length) return { nodes: [], links: [] };
  const colorById = Object.fromEntries(
    (sankeyPayload.nodes || []).map((n, i) => [n.id, n.color_hex || FALLBACK_COLORS[i % FALLBACK_COLORS.length]])
  );
  const nodes = (sankeyPayload.nodes || []).map((n, i) => ({
    id: n.id,
    label: n.name || n.id.replace(/_/g, ' '),
    nodeColor: colorById[n.id] || FALLBACK_COLORS[i % FALLBACK_COLORS.length],
  }));
  const links = sankeyPayload.links.map((l) => ({
    source: l.source,
    target: l.target,
    value: Math.max(Math.abs(l.value) || 0, 0.001),
    label: l.description || `${l.source} → ${l.target}`,
    rawValue: l.value,
  }));
  return { nodes, links };
}
