"""
Theme-level market-confirmation + breadth feature engineering (Plan NR-2).

Pure, offline-testable: every function takes plain Python lists/dicts (member
close series + SPY closes + per-member momentum/fundamentals) and returns raw
feature values. The 0-100 normalization happens later in ``scoring.py`` using a
cross-sectional percentile context across themes (matching the Picks & Shovels
pattern). Components with no data return ``None`` and are never fabricated.

Reuses ``backend/picks_shovels/data.py::momentum_from_closes`` for per-member
momentum and mirrors ``backend/macro_flow/macro_flow_agent.py::category_rs_metrics``
for the basket-vs-SPY relative-strength math (here on plain lists, not DataFrames).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

# Trading-day lag for RS momentum (≈1 week), matching macro_flow's default.
_RS_LAG_BARS = 5


def _clean(series: Sequence[float]) -> List[float]:
    out: List[float] = []
    for v in series or []:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f):
            continue
        out.append(f)
    return out


def _normalize_path(series: Sequence[float]) -> List[float]:
    """Index a close series to its first value (start = 1.0). None-safe."""
    clean = _clean(series)
    if not clean:
        return []
    base = clean[0]
    if base == 0:
        return [1.0 for _ in clean]
    return [c / base for c in clean]


def equal_weight_basket(member_closes: List[Sequence[float]]) -> List[float]:
    """
    Build an equal-weight normalized basket path from member close series.
    Aligns to the shortest series (by position, newest-aligned) so a single
    short-history member does not distort the basket.
    """
    paths = [_normalize_path(s) for s in member_closes]
    paths = [p for p in paths if len(p) >= 3]
    if not paths:
        return []
    n = min(len(p) for p in paths)
    # align newest bars (take the last n of each normalized path, re-normalize)
    aligned: List[List[float]] = []
    for p in paths:
        tail = p[-n:]
        b = tail[0]
        aligned.append([x / b for x in tail] if b else tail)
    out: List[float] = []
    for i in range(n):
        out.append(sum(p[i] for p in aligned) / len(aligned))
    return out


def cap_weight_basket(
    member_closes: List[Sequence[float]],
    market_caps: List[Optional[float]],
) -> List[float]:
    """Market-cap-weighted normalized basket path (falls back to equal weight)."""
    items = []
    for closes, cap in zip(member_closes, market_caps):
        p = _normalize_path(closes)
        if len(p) >= 3 and cap and cap > 0:
            items.append((p, float(cap)))
    if not items:
        return equal_weight_basket(member_closes)
    n = min(len(p) for p, _ in items)
    wsum = sum(w for _, w in items)
    out: List[float] = []
    for i in range(n):
        acc = 0.0
        for p, w in items:
            tail = p[-n:]
            b = tail[0] or 1.0
            acc += (tail[i] / b) * w
        out.append(acc / wsum)
    return out


def relative_strength(basket: Sequence[float], spy_closes: Sequence[float]) -> Dict[str, Optional[float]]:
    """
    RS ratio (basket vs SPY, both normalized) and RS momentum (change over the
    lag window). Mirrors macro_flow.category_rs_metrics. Returns None when inputs
    are insufficient.
    """
    b = _clean(basket)
    spy = _normalize_path(spy_closes)
    if len(b) < 3 or len(spy) < 3:
        return {"rs_ratio": None, "rs_momentum": None}
    n = min(len(b), len(spy))
    b_tail = b[-n:]
    spy_tail = spy[-n:]
    ratio: List[float] = []
    for bb, ss in zip(b_tail, spy_tail):
        ratio.append(bb / ss if ss else 1.0)
    rr = ratio[-1]
    prev_idx = max(0, len(ratio) - 1 - _RS_LAG_BARS)
    rr_prev = ratio[prev_idx]
    return {"rs_ratio": round(rr, 5), "rs_momentum": round(rr - rr_prev, 5)}


def _period_return_pct(closes: Sequence[float]) -> Optional[float]:
    clean = _clean(closes)
    if len(clean) < 2 or clean[0] == 0:
        return None
    return round((clean[-1] / clean[0] - 1.0) * 100.0, 2)


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return round(vals[mid], 2)
    return round((vals[mid - 1] + vals[mid]) / 2.0, 2)


def build_theme_features(
    theme_id: str,
    members: List[Dict[str, Any]],
    spy_closes: Sequence[float],
) -> Dict[str, Any]:
    """
    Build raw theme-level features from member rows + SPY closes.

    Each ``member`` dict is expected to carry:
      - ``closes``: List[float]  (raw close series, newest last)
      - ``momentum``: dict from picks_shovels.data.momentum_from_closes
      - ``fundamentals``: dict with ``market_cap``
    """
    member_closes = [m.get("closes") or [] for m in members]
    market_caps = [(m.get("fundamentals") or {}).get("market_cap") for m in members]
    momos = [m.get("momentum") or {} for m in members]

    members_with_price = sum(1 for c in member_closes if len(_clean(c)) >= 3)

    eq = equal_weight_basket(member_closes)
    cap = cap_weight_basket(member_closes, market_caps)
    rs = relative_strength(eq, spy_closes)

    # Breadth: share of members trading above their own 50/200 DMA.
    above_50 = [1.0 if (m.get("above_50dma_pct") or 0) > 0 else 0.0 for m in momos if m.get("above_50dma_pct") is not None]
    above_200 = [1.0 if (m.get("above_200dma_pct") or 0) > 0 else 0.0 for m in momos if m.get("above_200dma_pct") is not None]
    pct_above_50 = round(100.0 * sum(above_50) / len(above_50), 2) if above_50 else None
    pct_above_200 = round(100.0 * sum(above_200) / len(above_200), 2) if above_200 else None

    ret_3m = [m.get("ret_3m_pct") for m in momos]
    ret_6m = [m.get("ret_6m_pct") for m in momos]
    ret_12m = [m.get("ret_12m_pct") for m in momos]
    median_ret_3m = _median(ret_3m)

    present_3m = [r for r in ret_3m if r is not None]
    breadth_positive_pct = (
        round(100.0 * sum(1 for r in present_3m if r > 0) / len(present_3m), 2)
        if present_3m else None
    )

    eq_ret = _period_return_pct(eq)
    cap_ret = _period_return_pct(cap)
    # Positive spread = cap-weight leading equal-weight = narrowing leadership (a late-cycle flag).
    cap_vs_equal_spread = (
        round(cap_ret - eq_ret, 2) if (eq_ret is not None and cap_ret is not None) else None
    )

    return {
        "theme_id": theme_id,
        "member_count": len(members),
        "members_with_price": members_with_price,
        # Market confirmation
        "rs_ratio": rs["rs_ratio"],
        "rs_momentum": rs["rs_momentum"],
        "median_ret_3m_pct": median_ret_3m,
        "median_ret_6m_pct": _median(ret_6m),
        "median_ret_12m_pct": _median(ret_12m),
        "equal_weight_ret_pct": eq_ret,
        "cap_weight_ret_pct": cap_ret,
        # Breadth quality
        "pct_above_50dma": pct_above_50,
        "pct_above_200dma": pct_above_200,
        "breadth_positive_pct": breadth_positive_pct,
        "cap_vs_equal_spread_pct": cap_vs_equal_spread,
        # Volume z-score needs OHLCV (closes-only path) → deferred (never fabricated).
        "volume_zscore": None,
    }
