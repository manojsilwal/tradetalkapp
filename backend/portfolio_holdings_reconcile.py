"""
Pure reconciliation helpers for vision / manual holdings import (paper portfolio).

Compares extracted rows to aggregated open LONG positions per ticker.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class ExtractedHolding(TypedDict, total=False):
    ticker: str
    shares: Optional[float]
    avg_cost: Optional[float]


class CurrentAgg(TypedDict):
    shares: float
    avg_cost: float
    allocated: float
    position_ids: List[str]


def normalize_ticker(raw: str) -> str:
    t = (raw or "").strip().upper()
    # Strip common suffix noise from screenshots
    for suf in (".US", "-USD"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def _to_opt_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def normalize_extracted_holdings(rows: List[Dict[str, Any]]) -> List[ExtractedHolding]:
    out: List[ExtractedHolding] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        t = normalize_ticker(str(r.get("ticker") or ""))
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(
            {
                "ticker": t,
                "shares": _to_opt_float(r.get("shares")),
                "avg_cost": _to_opt_float(r.get("avg_cost")),
            }
        )
    return out


def aggregate_open_long_positions(positions: List[Dict[str, Any]]) -> Dict[str, CurrentAgg]:
    """Aggregate open LONG rows by ticker (VWAP cost)."""
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for p in positions or []:
        if p.get("closed"):
            continue
        if str(p.get("direction", "")).upper() != "LONG":
            continue
        t = normalize_ticker(str(p.get("ticker") or ""))
        if not t:
            continue
        by_ticker.setdefault(t, []).append(p)

    out: Dict[str, CurrentAgg] = {}
    for t, rows in by_ticker.items():
        total_shares = sum(float(r["shares"]) for r in rows)
        allocated = sum(float(r["allocated"]) for r in rows)
        vw = allocated / total_shares if total_shares else 0.0
        out[t] = {
            "shares": total_shares,
            "avg_cost": vw,
            "allocated": allocated,
            "position_ids": [str(r["id"]) for r in rows],
        }
    return out


def _close_enough(a: Optional[float], b: float, *, rel_tol: float, abs_tol: float) -> bool:
    if a is None:
        return True
    diff = abs(float(a) - float(b))
    if diff <= abs_tol:
        return True
    scale = max(abs(float(a)), abs(float(b)), 1e-9)
    return diff / scale <= rel_tol


def reconcile_holdings(
    extracted: List[ExtractedHolding],
    current_by_ticker: Dict[str, CurrentAgg],
    *,
    full_snapshot: bool = False,
    shares_abs_tol: float = 1e-3,
    shares_rel_tol: float = 1e-4,
    cost_abs_tol: float = 0.02,
    cost_rel_tol: float = 1e-4,
) -> Dict[str, Any]:
    """
    Classify extracted vs current aggregated LONG positions.

    Returns:
      new, updated, unchanged, removed (only when full_snapshot), skipped_invalid
    """
    new: List[Dict[str, Any]] = []
    updated: List[Dict[str, Any]] = []
    unchanged: List[Dict[str, Any]] = []
    skipped_invalid: List[Dict[str, Any]] = []
    removed: List[Dict[str, Any]] = []

    ext_map = {normalize_ticker(e["ticker"]): e for e in extracted if e.get("ticker")}

    for t, ex in ext_map.items():
        sh, ac = ex.get("shares"), ex.get("avg_cost")
        if sh is not None and sh <= 0:
            skipped_invalid.append({**ex, "reason": "shares must be positive"})
            continue
        if ac is not None and ac < 0:
            skipped_invalid.append({**ex, "reason": "avg_cost cannot be negative"})
            continue
        cur = current_by_ticker.get(t)
        if cur is None:
            new.append(dict(ex))
            continue

        shares_match = _close_enough(sh, cur["shares"], rel_tol=shares_rel_tol, abs_tol=shares_abs_tol)
        cost_match = _close_enough(ac, cur["avg_cost"], rel_tol=cost_rel_tol, abs_tol=cost_abs_tol)

        if shares_match and cost_match:
            unchanged.append(
                {
                    "ticker": t,
                    "shares": sh if sh is not None else cur["shares"],
                    "avg_cost": ac if ac is not None else cur["avg_cost"],
                    "current": dict(cur),
                }
            )
        else:
            updated.append(
                {
                    "ticker": t,
                    "proposed": {"shares": sh, "avg_cost": ac},
                    "current": dict(cur),
                }
            )

    if full_snapshot:
        for t, cur in current_by_ticker.items():
            if t not in ext_map:
                removed.append({"ticker": t, "current": dict(cur)})

    return {
        "new": new,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "skipped_invalid": skipped_invalid,
    }
