/** AI infrastructure watchlist helpers for Portfolio page. */

export const AI_INFRA_BASKET = [
  'GEV',
  'ETN',
  'SBGSY',
  'SIEGY',
  'VRT',
  'ECL',
  'ASTK.OL',
  'NVDA',
  'AVGO',
  'MRVL',
  'COHR',
  'LITE',
  'GLW',
];

export const AI_INFRA_THEMES = {
  GEV: 'Grid',
  ETN: 'Grid',
  SBGSY: 'Grid / Cooling',
  SIEGY: 'Grid',
  VRT: 'Cooling',
  ECL: 'Cooling / Water',
  'ASTK.OL': 'Cooling',
  NVDA: 'Optical / Silicon',
  AVGO: 'Optical / Networking',
  MRVL: 'Optical / Silicon',
  COHR: 'Optical',
  LITE: 'Optical',
  GLW: 'Optical / Fiber',
};

export const AI_INFRA_THESIS = {
  GEV: 'GridOS digitization leader; massive transformer backlog and Prolec GE exposure.',
  ETN: 'Transformer and switchgear capacity expansion with multi-year pricing power.',
  SBGSY: 'Schneider grid-to-chip integration plus Motivair liquid-cooling exposure.',
  SIEGY: 'European grid integration via Spectrum Power and Gridscale X platforms.',
  VRT: 'High-density thermal management; liquid cooling via CoolTera and Liebert XDU.',
  ECL: 'CoolIT acquisition ties water management to AI data center cooling.',
  'ASTK.OL': 'Niche direct-to-chip cooling specialist for ultra-dense racks.',
  NVDA: 'Vertically integrating photonics; Spectrum-X and Quantum-X CPO networking.',
  AVGO: 'Open-ecosystem CPO strategy on Tomahawk 6 scale-out optical platforms.',
  MRVL: 'Silicon photonics via Celestial AI; NVLink Fusion scale-up networking.',
  COHR: 'Core laser and transceiver supplier for silicon photonics build-out.',
  LITE: '1.6T OSFP transceivers and high-power lasers for next-gen AI bandwidth.',
  GLW: 'Fiber and optical connectivity capacity expansion backed by NVIDIA commitments.',
};

export const LOCAL_WATCHLIST_KEY = 'k2_portfolio_watchlist';
export const SCORECARD_CHUNK_SIZE = 10;
export const WATCHLIST_MAX = 20;

export function chunkTickers(tickers, chunkSize = SCORECARD_CHUNK_SIZE) {
  const list = [
    ...new Set(
      (tickers || [])
        .map((t) => String(t).trim().toUpperCase())
        .filter(Boolean),
    ),
  ];
  const chunks = [];
  for (let i = 0; i < list.length; i += chunkSize) {
    chunks.push(list.slice(i, i + chunkSize));
  }
  return chunks;
}

export function mergeWatchlistTickers(existing, additions) {
  const seen = new Set();
  const out = [];
  for (const t of [...(existing || []), ...(additions || [])]) {
    const sym = String(t).trim().toUpperCase();
    if (!sym || seen.has(sym)) continue;
    seen.add(sym);
    out.push(sym);
    if (out.length >= WATCHLIST_MAX) break;
  }
  return out;
}

/** Map scorecard verdict labels to portfolio watch wording. */
export function mapScorecardVerdict(verdict) {
  const v = String(verdict || '').trim();
  if (v === 'Strong' || v === 'Favorable') return 'Buy Watch';
  if (v === 'Balanced') return 'Hold / Wait';
  if (v === 'Stretched') return 'Overvalued';
  if (v === 'Avoid') return 'Avoid';
  return 'Hold / Wait';
}

export function watchVerdictColor(mappedVerdict) {
  const v = String(mappedVerdict || '');
  if (v === 'Buy Watch') return '#10b981';
  if (v === 'Overvalued' || v === 'Avoid') return '#ef4444';
  if (v === 'Hold / Wait') return '#eab308';
  return '#94a3b8';
}

export function watchBriefReason(row) {
  const thesis = AI_INFRA_THESIS[row?.ticker] || '';
  const oneLine = row?.one_line_reason || '';
  if (thesis && oneLine) return `${thesis} ${oneLine}`;
  return thesis || oneLine || 'Monitor valuation and backlog execution before sizing a position.';
}

export function formatWatchNum(v, digits = 1) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}
