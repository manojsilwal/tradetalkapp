import { ACADEMY_MODULES, academyModulePath } from '../academyRoutes';

const STORAGE_KEY = 'tradetalk_academy_momentum_context';
const MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes

export function momentumAcademyPath(ticker) {
  const base = academyModulePath(ACADEMY_MODULES.momentumPricing);
  const sym = (ticker || '').trim().toUpperCase();
  if (!sym) return base;
  return `${base}&ticker=${encodeURIComponent(sym)}`;
}

export function persistMomentumAcademyContext({ ticker, readout, valuation }) {
  const sym = (ticker || readout?.ticker || '').trim().toUpperCase();
  if (!sym) return;
  try {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        ticker: sym,
        savedAt: Date.now(),
        readout,
        valuation: valuation
          ? {
              current_price_usd: valuation.current_price_usd,
              valuation_gap_pct: valuation.valuation_gap_pct,
              valuation_signal: valuation.valuation_signal,
              average_fair_value_usd: valuation.average_fair_value_usd,
            }
          : null,
      }),
    );
  } catch {
    /* ignore quota / private mode */
  }
}

export function readMomentumAcademyContext(expectedTicker) {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const sym = (expectedTicker || parsed.ticker || '').trim().toUpperCase();
    if (!sym || parsed.ticker !== sym) return null;
    if (Date.now() - Number(parsed.savedAt || 0) > MAX_AGE_MS) return null;
    return parsed;
  } catch {
    return null;
  }
}
