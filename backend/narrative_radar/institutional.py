"""
NR-5 — institutional 13F footprint aggregated to the theme level.

Rolls the existing ``thirteen_f_holdings`` table (populated by
``backend/coral_skills/sec_13f_ingestion.py`` / the fund-leaderboard pipeline) up
to a theme by its member tickers, producing:

  - ownership_breadth_pct   share of members held by ≥1 institution (latest period)
  - new_position_ratio      fraction of (fund, ticker) pairs new vs the prior period
  - net_position_change_pct avg per-member share change latest vs prior period
  - concentration_pct       top holder's share of theme market value (latest period)

13F is ~45 days lagged, so the scorer treats this as *confirmation* (0.35 weight),
not a leading signal. The pure ``aggregate_holdings`` is offline-testable; the live
``aggregate_theme`` wraps the DB read and degrades to ``{"available": False}``.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_INSTITUTIONAL", "1").strip() != "0"


def _num(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def aggregate_holdings(rows: Sequence[Dict[str, Any]], members: Sequence[str]) -> Dict[str, Any]:
    """
    Pure aggregation of 13F holding rows for a theme's members.

    Each row needs: ``report_period``, ``ticker``, ``fund_id``, ``shares``,
    ``market_value_usd``. Returns ``{"available": False}`` when there is no usable
    latest-period data.
    """
    member_set = {m.upper() for m in members}
    clean = [
        r for r in rows
        if (r.get("ticker") or "").upper() in member_set and r.get("report_period")
    ]
    if not clean:
        return {"available": False}

    periods = sorted({str(r["report_period"]) for r in clean}, reverse=True)
    latest = periods[0]
    prior = periods[1] if len(periods) > 1 else None

    latest_rows = [r for r in clean if str(r["report_period"]) == latest]
    prior_rows = [r for r in clean if prior and str(r["report_period"]) == prior]
    if not latest_rows:
        return {"available": False}

    held_latest = {(r.get("ticker") or "").upper() for r in latest_rows}
    ownership_breadth_pct = round(100.0 * len(held_latest & member_set) / max(len(member_set), 1), 2)

    pairs_latest = {((r.get("fund_id") or ""), (r.get("ticker") or "").upper()) for r in latest_rows}
    pairs_prior = {((r.get("fund_id") or ""), (r.get("ticker") or "").upper()) for r in prior_rows}
    new_position_ratio: Optional[float] = None
    if pairs_latest:
        new_position_ratio = round(len(pairs_latest - pairs_prior) / len(pairs_latest), 3) if prior else None

    # Net share change per member (latest vs prior), averaged.
    net_changes: List[float] = []
    if prior:
        def _shares_by_ticker(rs: Sequence[Dict[str, Any]]) -> Dict[str, float]:
            out: Dict[str, float] = {}
            for r in rs:
                t = (r.get("ticker") or "").upper()
                s = _num(r.get("shares")) or 0.0
                out[t] = out.get(t, 0.0) + s
            return out
        sl = _shares_by_ticker(latest_rows)
        sp = _shares_by_ticker(prior_rows)
        for t, cur in sl.items():
            base = sp.get(t)
            if base and base > 0:
                net_changes.append((cur / base - 1.0) * 100.0)
    net_position_change_pct = round(sum(net_changes) / len(net_changes), 2) if net_changes else None

    # Concentration: top fund's share of theme market value in the latest period.
    by_fund: Dict[str, float] = {}
    total_mv = 0.0
    for r in latest_rows:
        mv = _num(r.get("market_value_usd")) or 0.0
        by_fund[r.get("fund_id") or ""] = by_fund.get(r.get("fund_id") or "", 0.0) + mv
        total_mv += mv
    concentration_pct = round(100.0 * max(by_fund.values()) / total_mv, 2) if total_mv > 0 and by_fund else None

    return {
        "available": True,
        "latest_period": latest,
        "prior_period": prior,
        "holder_fund_count": len({r.get("fund_id") for r in latest_rows}),
        "ownership_breadth_pct": ownership_breadth_pct,
        "new_position_ratio": new_position_ratio,
        "net_position_change_pct": net_position_change_pct,
        "concentration_pct": concentration_pct,
    }


def _fetch_holdings(members: Sequence[str]) -> List[Dict[str, Any]]:
    """Best-effort raw read of thirteen_f_holdings for the member tickers."""
    from .. import fund_leaderboard_store as fls

    members_up = [m.upper() for m in members]
    if not members_up:
        return []
    placeholders = ",".join(["?"] * len(members_up))
    sql = (
        "SELECT report_period, ticker, fund_id, shares, market_value_usd "
        f"FROM thirteen_f_holdings WHERE UPPER(ticker) IN ({placeholders})"
    )
    with fls._cursor() as (_c, cur):  # type: ignore[attr-defined]
        cur.execute(fls._ph(sql), tuple(members_up))  # type: ignore[attr-defined]
        rows = cur.fetchall()
    return [fls._row_to_dict(r) for r in rows]  # type: ignore[attr-defined]


def aggregate_theme(members: Sequence[str]) -> Dict[str, Any]:
    """Live theme-level 13F aggregation. Resilient: any failure → unavailable."""
    if not enabled():
        return {"available": False}
    try:
        rows = _fetch_holdings(members)
        return aggregate_holdings(rows, members)
    except Exception as e:
        logger.debug("[NarrativeRadar] 13F aggregation failed: %s", e)
        return {"available": False}
