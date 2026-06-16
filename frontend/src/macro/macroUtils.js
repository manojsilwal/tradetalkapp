/** Shared formatters and derived metrics for the Global Macro dashboard. */

export const SECTOR_AUM_USD = {
  XLK: 70_000_000_000,
  SMH: 28_000_000_000,
  XLF: 45_000_000_000,
  XLV: 40_000_000_000,
  XLE: 35_000_000_000,
  XLC: 20_000_000_000,
  XLRE: 8_000_000_000,
  XME: 2_000_000_000,
  XLU: 18_000_000_000,
};

export const SECTOR_LABELS = {
  XLK: 'Technology',
  SMH: 'Semiconductors',
  XLF: 'Financials',
  XLV: 'Healthcare',
  XLE: 'Energy',
  XLC: 'Comm Svcs',
  XLRE: 'Real Estate',
  XME: 'Metals & Mining',
  XLU: 'Utilities',
};

export const PERIOD_OPTIONS = [
  { id: '1M', label: '1M', flowInterval: '1m', marketsPeriod: '1M' },
  { id: 'YTD', label: 'YTD', flowInterval: '1m', marketsPeriod: 'YTD' },
  { id: '1Y', label: '1Y', flowInterval: '1y', marketsPeriod: '1Y' },
  { id: '5Y', label: '5Y', flowInterval: '1y', marketsPeriod: '1Y' },
];

export const STOCK_NAMES = {
  NVDA: 'NVIDIA Corp',
  TSM: 'Taiwan Semi',
  VRT: 'Vertiv Hldgs',
  CEG: 'Constellation',
  AAPL: 'Apple Inc',
  MSFT: 'Microsoft',
  AVGO: 'Broadcom',
  AMD: 'AMD',
  META: 'Meta Platforms',
  GOOGL: 'Alphabet',
  AMZN: 'Amazon',
};

export function formatCompactUSD(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const abs = Math.abs(n);
  const sign = n >= 0 ? '+' : '-';
  if (abs >= 1_000_000_000_000) return `${sign}$${(abs / 1_000_000_000_000).toFixed(2)}T`;
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function formatLargeUSD(value) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const abs = Math.abs(Number(value));
  if (abs >= 1_000_000_000_000) return `$${(abs / 1_000_000_000_000).toFixed(2)}T`;
  if (abs >= 1_000_000_000) return `$${(abs / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `$${(abs / 1_000_000).toFixed(0)}M`;
  return `$${abs.toLocaleString()}`;
}

export function formatPct(value, digits = 1) {
  if (value == null || Number.isNaN(Number(value))) return '—';
  const n = Number(value);
  const sign = n >= 0 ? '+' : '';
  return `${sign}${n.toFixed(digits)}%`;
}

export function estimateFlowUsd(pctChange, aumUsd) {
  if (pctChange == null || aumUsd == null) return null;
  return (Number(pctChange) / 100) * Number(aumUsd);
}

export function computeRotationVelocity(sectors) {
  if (!sectors?.length) return null;
  const vals = sectors.map((s) => Number(s.pctChange ?? s.daily_change_pct ?? 0));
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const variance = vals.reduce((a, b) => a + (b - mean) ** 2, 0) / vals.length;
  return Math.sqrt(variance);
}

export function computeFlowConcentration(sectors) {
  if (!sectors?.length) return null;
  const flows = sectors.map((s) => Math.abs(Number(s.flowUsd ?? 0)));
  const total = flows.reduce((a, b) => a + b, 0);
  if (total === 0) return 0;
  const shares = flows.map((f) => f / total);
  const hhi = shares.reduce((a, s) => a + s * s, 0);
  return hhi * 100;
}

export function regimeLabel(marketRegime, creditStress) {
  if (marketRegime?.includes('BEAR') || (creditStress != null && creditStress > 1.1)) {
    return { label: 'RISK-OFF', tone: 'bear' };
  }
  if (marketRegime?.includes('BULL') || (creditStress != null && creditStress <= 1.1)) {
    return { label: 'RISK-ON', tone: 'bull' };
  }
  return { label: 'NEUTRAL', tone: 'neutral' };
}

export function forecastConfidence(vix, creditStress) {
  if (vix == null && creditStress == null) return { label: 'LOW CONF', tone: 'low' };
  const stress = creditStress ?? (vix != null ? vix / 15 : 1);
  if (stress <= 1.0 && (vix == null || vix < 18)) return { label: 'HIGH CONF', tone: 'high' };
  if (stress <= 1.2) return { label: 'MED CONF', tone: 'med' };
  return { label: 'LOW CONF', tone: 'low' };
}

export function periodReturnFromSeries(series) {
  if (!series?.length) return null;
  return series[series.length - 1];
}

export function buildSectorRows(sectors, periodReturns = {}) {
  const base = sectors || [];
  const symbols = new Set(base.map((s) => s.symbol));
  const extras = ['SMH'].filter((sym) => !symbols.has(sym));
  const rows = [
    ...base.map((s) => ({
      symbol: s.symbol,
      name: s.name || SECTOR_LABELS[s.symbol] || s.symbol,
      pctChange: periodReturns[s.symbol] ?? s.daily_change_pct ?? 0,
    })),
    ...extras.map((sym) => ({
      symbol: sym,
      name: SECTOR_LABELS[sym] || sym,
      pctChange: periodReturns[sym] ?? 0,
    })),
  ];
  return rows
    .map((row) => {
      const aum = SECTOR_AUM_USD[row.symbol];
      const flowUsd = aum != null ? estimateFlowUsd(row.pctChange, aum) : null;
      return { ...row, aum, flowUsd };
    })
    .sort((a, b) => (b.flowUsd ?? 0) - (a.flowUsd ?? 0));
}

export function buildIntlBucketRows(buckets, periodKey) {
  if (!buckets?.length) return [];

  const intlRows = buckets
    .filter((b) => b.region === 'INTL_COUNTERPARTY' || !b.is_us_destination)
    .map((b) => {
      const pct = b.historical_returns?.[periodKey] ?? b.price_change_pct ?? 0;
      const flowUsd = estimateFlowUsd(pct, b.notional_base_usd);
      return {
        symbol: b.proxy_symbol,
        name: b.display_name,
        pctChange: pct,
        flowUsd,
        stance: b.stance,
      };
    })
    .sort((a, b) => (b.flowUsd ?? 0) - (a.flowUsd ?? 0));

  const spyBucket = buckets.find((b) => b.proxy_symbol === 'SPY');
  if (!spyBucket) return intlRows;

  const spyPct = spyBucket.historical_returns?.[periodKey] ?? spyBucket.price_change_pct ?? 0;
  const spyRow = {
    symbol: 'SPY',
    name: 'S&P 500 (US)',
    pctChange: spyPct,
    flowUsd: estimateFlowUsd(spyPct, spyBucket.notional_base_usd),
    stance: spyBucket.stance,
    isBenchmark: true,
  };

  return [spyRow, ...intlRows.filter((r) => r.symbol !== 'SPY')];
}

export function mapFlowInterval(periodId) {
  return PERIOD_OPTIONS.find((p) => p.id === periodId)?.flowInterval || '1w';
}

export function mapMarketsPeriod(periodId) {
  return PERIOD_OPTIONS.find((p) => p.id === periodId)?.marketsPeriod || '1M';
}

export function bucketPeriodKey(periodId) {
  if (periodId === '1M') return '1m';
  if (periodId === 'YTD') return '1m';
  if (periodId === '1Y') return '1y';
  if (periodId === '5Y') return '5y';
  return '1w';
}
