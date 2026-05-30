/**
 * Normalize 3Y bull/base/bear vs spot for roadmap display.
 * Bull = upside path (>= spot); bear = downside (<= spot); ordered bull >= base >= bear.
 */
export function sanitizeRoadmapScenarios(spot, bull, base, bear) {
  if (!spot || spot <= 0) {
    return { bull: bull ?? null, base: base ?? null, bear: bear ?? null };
  }

  let b = Number(bull);
  let m = Number(base);
  let e = Number(bear);
  if (![b, m, e].every((x) => Number.isFinite(x) && x > 0)) {
    return { bull: spot * 1.36, base: spot * 1.12, bear: spot * 0.82 };
  }

  const misscaled =
    Math.max(b, m, e) < spot * 0.55 ||
    Math.max(b, m, e) > spot * 25 ||
    Math.min(b, m, e) < spot * 0.02;

  const lo = spot * 0.35;
  const hi = spot * 2.75;

  if (misscaled) {
    b = spot * 1.36;
    m = spot * 1.12;
    e = spot * 0.82;
  } else {
    b = Math.max(lo, Math.min(hi, b));
    m = Math.max(lo, Math.min(hi, m));
    e = Math.max(lo, Math.min(hi, e));
    [b, m, e] = [b, m, e].sort((a, c) => c - a);
  }

  b = Math.max(b, spot * 1.08);
  e = Math.min(e, spot * 0.92);
  m = Math.max(e, Math.min(b, m));
  if (m < spot * 0.98) {
    m = Math.max(e, Math.min(b, spot * 1.04));
  }

  b = Math.max(b, m, e);
  e = Math.min(b, m, e);
  m = Math.max(e, Math.min(b, m));

  return { bull: b, base: m, bear: e };
}

/**
 * Build Recharts rows for the 3Y bull/base/bear roadmap panel.
 * Uses spot -> 3Y linear paths only (predictor PI bands are not scenario trajectories).
 */
export function buildRoadmapChartData(roadmap, currentPriceUsd) {
  if (!roadmap || currentPriceUsd == null || Number(currentPriceUsd) <= 0) return [];
  if (roadmap.bull_price_usd == null) return [];

  const spot = Number(currentPriceUsd);
  const { bull, base, bear } = sanitizeRoadmapScenarios(
    spot,
    roadmap.bull_price_usd,
    roadmap.base_price_usd,
    roadmap.bear_price_usd,
  );

  const sy = new Date().getFullYear();
  const rows = [{ t: `Now ($${spot.toFixed(2)})`, bull: spot, base: spot, bear: spot }];
  for (let i = 1; i <= 3; i++) {
    const frac = i / 3;
    rows.push({
      t: `${sy + i}`,
      bull: spot + (bull - spot) * frac,
      base: spot + (base - spot) * frac,
      bear: spot + (bear - spot) * frac,
    });
  }
  return rows;
}

/** Sanitized scenario prices for legend labels / slider. */
export function roadmapScenarioPrices(roadmap, currentPriceUsd) {
  if (!roadmap || currentPriceUsd == null || Number(currentPriceUsd) <= 0) return null;
  return sanitizeRoadmapScenarios(
    Number(currentPriceUsd),
    roadmap.bull_price_usd,
    roadmap.base_price_usd,
    roadmap.bear_price_usd,
  );
}
