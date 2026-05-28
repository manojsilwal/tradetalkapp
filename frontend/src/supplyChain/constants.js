export const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026];

export const CHAINS = [
  { id: 'LLY', label: 'Eli Lilly → ASML (AI drug discovery)' },
  { id: 'AAPL', label: 'Apple → TSMC (consumer electronics)' },
  { id: 'TSLA', label: 'Tesla → Lithium miners (EV battery)' },
  { id: null, label: 'All nodes (full graph)' },
];

export const SECTOR_COLORS = {
  Healthcare: '#ec4899',
  Software: '#a78bfa',
  Cloud: '#3b82f6',
  Semiconductors: '#22c55e',
  Industrials: '#f97316',
  'Consumer Tech': '#06b6d4',
  Materials: '#eab308',
  Financials: '#64748b',
  Energy: '#ef4444',
};

export const VIEW_MODES = [
  { id: 'graph', label: 'Node graph' },
  { id: 'sankey', label: 'Sector Sankey' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'table', label: 'Table' },
];
