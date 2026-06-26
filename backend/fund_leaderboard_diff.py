"""
Quarter-over-quarter diff engine for 13F holdings.

Given a fund's persisted holdings per period, compute position changes
(new buys, sold-out, increased, decreased, unchanged), top-10/20 concentration,
a turnover estimate, and sector flow, then persist a fund_quarterly_summary row
per period.

Pure/offline: operates on holdings dicts (no network); persistence is optional.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from . import fund_leaderboard_store as store

logger = logging.getLogger(__name__)


def _holding_key(h: Dict[str, Any]) -> str:
    """Identity for a position: prefer ticker, fall back to CUSIP, then issuer."""
    return (h.get("ticker") or h.get("cusip") or h.get("issuer_name") or "").strip().upper()


def _mv(h: Dict[str, Any]) -> float:
    return float(h.get("market_value_usd") or 0.0)


def _concentration(holdings: List[Dict[str, Any]], n: int) -> float:
    total = sum(_mv(h) for h in holdings)
    if total <= 0:
        return 0.0
    top = sorted(holdings, key=_mv, reverse=True)[:n]
    return sum(_mv(h) for h in top) / total


def compute_diff(
    current: List[Dict[str, Any]],
    previous: Optional[List[Dict[str, Any]]],
    period: str,
    prev_period: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute the quarter-over-quarter diff summary for one period."""
    cur_by_key: Dict[str, Dict[str, Any]] = {}
    for h in current:
        k = _holding_key(h)
        if not k:
            continue
        agg = cur_by_key.setdefault(k, {"key": k, "ticker": h.get("ticker"),
                                        "issuer_name": h.get("issuer_name"),
                                        "sector": h.get("sector"), "mv": 0.0, "shares": 0.0})
        agg["mv"] += _mv(h)
        agg["shares"] += float(h.get("shares") or 0.0)

    prev_by_key: Dict[str, Dict[str, Any]] = {}
    for h in (previous or []):
        k = _holding_key(h)
        if not k:
            continue
        agg = prev_by_key.setdefault(k, {"key": k, "sector": h.get("sector"), "mv": 0.0, "shares": 0.0})
        agg["mv"] += _mv(h)
        agg["shares"] += float(h.get("shares") or 0.0)

    new_positions: List[Dict[str, Any]] = []
    increased: List[Dict[str, Any]] = []
    decreased: List[Dict[str, Any]] = []
    unchanged = 0

    for k, cur in cur_by_key.items():
        prev = prev_by_key.get(k)
        if prev is None:
            new_positions.append({"ticker": cur.get("ticker"), "issuerName": cur.get("issuer_name"),
                                  "marketValueUsd": cur["mv"], "shares": cur["shares"]})
            continue
        cur_sh, prev_sh = cur["shares"], prev["shares"]
        delta = cur_sh - prev_sh
        pct = (delta / prev_sh) if prev_sh else 0.0
        rec = {"ticker": cur.get("ticker"), "issuerName": cur.get("issuer_name"),
               "marketValueUsd": cur["mv"], "sharesDelta": delta, "sharesChangePct": pct}
        if delta > 1e-9:
            increased.append(rec)
        elif delta < -1e-9:
            decreased.append(rec)
        else:
            unchanged += 1

    sold_out: List[Dict[str, Any]] = []
    for k, prev in prev_by_key.items():
        if k not in cur_by_key:
            sold_out.append({"key": k, "marketValueUsd": prev["mv"], "shares": prev["shares"]})

    # Turnover estimate: (sum |mv change| + new + sold-out value) / (2 * avg total).
    cur_total = sum(c["mv"] for c in cur_by_key.values())
    prev_total = sum(p["mv"] for p in prev_by_key.values())
    if previous:
        gross_change = 0.0
        for k, cur in cur_by_key.items():
            prev = prev_by_key.get(k)
            gross_change += abs(cur["mv"] - (prev["mv"] if prev else 0.0))
        for k, prev in prev_by_key.items():
            if k not in cur_by_key:
                gross_change += prev["mv"]
        denom = (cur_total + prev_total)
        turnover = (gross_change / denom) if denom else 0.0
    else:
        turnover = None

    # Sector flow: net MV change per sector.
    sector_now: Dict[str, float] = {}
    for c in cur_by_key.values():
        sector_now[c.get("sector") or "Unknown"] = sector_now.get(c.get("sector") or "Unknown", 0.0) + c["mv"]
    sector_prev: Dict[str, float] = {}
    for p in prev_by_key.values():
        sector_prev[p.get("sector") or "Unknown"] = sector_prev.get(p.get("sector") or "Unknown", 0.0) + p["mv"]
    sectors = set(sector_now) | set(sector_prev)
    sector_flow = sorted(
        [
            {"sector": s, "currentValueUsd": sector_now.get(s, 0.0),
             "previousValueUsd": sector_prev.get(s, 0.0),
             "netFlowUsd": sector_now.get(s, 0.0) - sector_prev.get(s, 0.0)}
            for s in sectors
        ],
        key=lambda x: abs(x["netFlowUsd"]), reverse=True,
    )

    new_positions.sort(key=lambda x: x["marketValueUsd"], reverse=True)
    increased.sort(key=lambda x: x["marketValueUsd"], reverse=True)
    decreased.sort(key=lambda x: x["marketValueUsd"], reverse=True)
    sold_out.sort(key=lambda x: x["marketValueUsd"], reverse=True)

    return {
        "period_of_report": period,
        "prev_period": prev_period,
        "total_13f_value_usd": cur_total,
        "holdings_count": len(cur_by_key),
        "top10_concentration": _concentration(current, 10),
        "top20_concentration": _concentration(current, 20),
        "turnover_estimate_pct": turnover,
        "new_count": len(new_positions),
        "soldout_count": len(sold_out),
        "increased_count": len(increased),
        "decreased_count": len(decreased),
        "unchanged_count": unchanged,
        "changes": {
            "new": new_positions[:50],
            "soldOut": sold_out[:50],
            "increased": increased[:50],
            "decreased": decreased[:50],
        },
        "sector_flow": sector_flow,
    }


def compute_and_persist_for_fund(fund_id: str, cik: str) -> int:
    """Compute diffs across all persisted periods for a fund and persist summaries.

    Returns the number of quarterly summaries written.
    """
    periods = store.list_periods(fund_id)  # most-recent first
    if not periods:
        return 0
    chronological = sorted(periods)
    written = 0
    prev_holdings: Optional[List[Dict[str, Any]]] = None
    prev_period: Optional[str] = None
    for period in chronological:
        holdings = store.get_holdings_for_period(fund_id, period)
        summary = compute_diff(holdings, prev_holdings, period, prev_period)
        try:
            store.upsert_quarterly_summary(fund_id, cik, summary)
            written += 1
        except Exception as e:
            logger.warning("[Diff] persist failed fund=%s period=%s: %s", fund_id, period, e)
        prev_holdings = holdings
        prev_period = period
    return written
