/** Shared Impact Movers sort + formatting for Your Morning. */

export function fmtMoverPct(v) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  const n = Number(v);
  const sign = n > 0 ? '+' : '';
  return `${sign}${n.toFixed(1)}%`;
}

export function sortImpactMovers(movers, mode) {
  const list = [...(movers || [])];
  if (mode === 'VOL') {
    list.sort((a, b) => {
      const av = Number(a.relative_volume) || Math.abs(Number(a.portfolio_impact_pct) || 0);
      const bv = Number(b.relative_volume) || Math.abs(Number(b.portfolio_impact_pct) || 0);
      return bv - av;
    });
  } else {
    list.sort(
      (a, b) => Math.abs(Number(b.daily_return_pct) || 0) - Math.abs(Number(a.daily_return_pct) || 0),
    );
  }
  return list;
}

export function pickFooterMover(movers, { sortMode, selectedSymbol } = {}) {
  if (!movers?.length) return null;
  if (selectedSymbol) {
    const selected = movers.find((m) => m.symbol === selectedSymbol);
    if (selected) return selected;
  }
  return sortImpactMovers(movers, sortMode || 'PRICE')[0];
}

export function footerMoverLabel(mover) {
  if (!mover?.symbol) return null;
  const name = mover.company_name || mover.symbol;
  const pct = fmtMoverPct(mover.daily_return_pct);
  return `Why ${name} moved ${pct} today`;
}
