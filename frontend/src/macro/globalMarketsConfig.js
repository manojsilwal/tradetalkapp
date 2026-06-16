/** Market definitions for Global Capital Flows — split by dashboard tab. */

export const US_SECTOR_MARKETS = [
  { id: 'SPY', label: 'S&P 500', region: 'USA', category: 'US EQUITIES', color: '#3b82f6' },
  { id: 'XLK', label: 'Technology', region: 'USA', category: 'SECTORS', color: '#39ff14' },
  { id: 'SMH', label: 'Semiconductors', region: 'USA', category: 'SECTORS', color: '#22c55e' },
  { id: 'XLF', label: 'Financials', region: 'USA', category: 'SECTORS', color: '#38bdf8' },
  { id: 'XLV', label: 'Healthcare', region: 'USA', category: 'SECTORS', color: '#a855f7' },
  { id: 'XLE', label: 'Energy', region: 'USA', category: 'SECTORS', color: '#f97316' },
  { id: 'XLC', label: 'Comm Svcs', region: 'USA', category: 'SECTORS', color: '#ec4899' },
  { id: 'XLU', label: 'Utilities', region: 'USA', category: 'SECTORS', color: '#64748b' },
  { id: 'TLT', label: 'US 20+Yr Treasury', region: 'USA', category: 'US BONDS', color: '#94a3b8' },
  { id: 'AGG', label: 'US Aggregate Bond', region: 'USA', category: 'US BONDS', color: '#cbd5e1' },
];

export const INTL_FLOW_MARKETS = [
  { id: 'SPY', label: 'S&P 500 (US)', region: 'USA', category: 'US BENCHMARK', color: '#94a3b8' },
  { id: 'EFA', label: 'Intl Developed', region: 'Global', category: 'INTL EQUITIES', color: '#3b82f6' },
  { id: 'EWJ', label: 'Nikkei 225', region: 'Japan', category: 'INTL EQUITIES', color: '#f97316' },
  { id: 'EWY', label: 'KOSPI', region: 'South Korea', category: 'INTL EQUITIES', color: '#06b6d4' },
  { id: 'MCHI', label: 'CSI 300', region: 'China', category: 'INTL EQUITIES', color: '#eab308' },
  { id: 'INDA', label: 'Nifty 50', region: 'India', category: 'INTL EQUITIES', color: '#22c55e' },
  { id: 'FEZ', label: 'Euro Stoxx 50', region: 'Europe', category: 'INTL EQUITIES', color: '#a855f7' },
  { id: 'EWU', label: 'FTSE 100', region: 'UK', category: 'INTL EQUITIES', color: '#ec4899' },
  { id: 'EWG', label: 'DAX', region: 'Germany', category: 'INTL EQUITIES', color: '#f43f5e' },
  { id: 'EWQ', label: 'CAC 40', region: 'France', category: 'INTL EQUITIES', color: '#8b5cf6' },
  { id: 'GLD', label: 'Gold', region: 'Global', category: 'SAFE HAVEN', color: '#fbbf24' },
  { id: 'BTC-USD', label: 'Crypto (BTC)', region: 'Global', category: 'CRYPTO', color: '#f59e0b' },
];

/** All markets — used by standalone GlobalMarketsChart export. */
export const ALL_GLOBAL_MARKETS = [...US_SECTOR_MARKETS, ...INTL_FLOW_MARKETS.filter(
  (m) => !US_SECTOR_MARKETS.some((u) => u.id === m.id),
)];

export function marketsForTab(tab) {
  return tab === 'intl' ? INTL_FLOW_MARKETS : US_SECTOR_MARKETS;
}

export function marketTickerList(markets) {
  return markets.map((m) => m.id).join(',');
}
