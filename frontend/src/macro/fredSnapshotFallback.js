/**
 * Offline fallback when /macro/fred-snapshot is missing or returns null
 * (e.g. older backend deploy or transient FRED outage). Mirrors backend/data/macro_fred_seed.json.
 */
export const FRED_SNAPSHOT_FALLBACK = {
  fed_funds_rate: 3.63,
  cpi_yoy: 2.8,
  fred_fetched_at: '2026-05-01T00:00:00+00:00',
  source: 'fred.stlouisfed.org',
  degraded: true,
};
