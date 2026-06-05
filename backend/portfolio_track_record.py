"""
Personal track record — user-scoped decision ledger summary for Your Morning.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_WINDOW_DAYS_DEFAULT = 30


def _decisions_db_path() -> str:
    return os.environ.get("DECISIONS_DB_PATH", os.path.join("backend", "decisions.db"))


def _query_user_decisions(
    user_id: str,
    symbols: List[str],
    since_ts: float,
    *,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    path = _decisions_db_path()
    if not user_id or not os.path.isfile(path):
        return []
    sym_set = {s.upper() for s in symbols if s}
    placeholders = ",".join("?" for _ in sym_set) if sym_set else ""
    try:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        params: List[Any] = [user_id, since_ts]
        symbol_clause = ""
        if sym_set:
            symbol_clause = f" AND (d.symbol IN ({placeholders}) OR d.decision_type LIKE 'portfolio%' OR d.decision_type LIKE 'morning%')"
            params.extend(sorted(sym_set))
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT d.decision_id, d.decision_type, d.symbol, d.verdict, d.created_at,
                   o.correct_bool, o.horizon, o.metric
            FROM decision_events d
            LEFT JOIN outcome_observations o
              ON o.decision_id = d.decision_id AND o.horizon = '1d'
            WHERE d.user_id = ?
              AND d.created_at >= ?
              {symbol_clause}
            ORDER BY d.created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug("[portfolio_track_record] query failed: %s", exc)
        return []


def build_track_record(
    user_id: str,
    symbols: List[str],
    *,
    window_days: int = _WINDOW_DAYS_DEFAULT,
) -> Dict[str, Any]:
    """Summarize graded portfolio-relevant decisions for the user."""
    since = time.time() - window_days * 86400
    rows = _query_user_decisions(user_id, symbols, since)
    total = len(rows)
    graded = [r for r in rows if r.get("correct_bool") is not None]
    right = sum(1 for r in graded if int(r["correct_bool"]) == 1)
    wrong = sum(1 for r in graded if int(r["correct_bool"]) == 0)
    neutral = len(graded) - right - wrong
    ungraded = total - len(graded)

    headline = "We're still building your personal track record."
    if graded:
        headline = (
            f"In the last {window_days} days, {right} of {len(graded)} graded portfolio "
            f"observations played out directionally."
        )
    elif total:
        headline = (
            f"We've logged {total} portfolio observations in the last {window_days} days — "
            "outcomes will appear as horizons mature."
        )

    recent: List[Dict[str, Any]] = []
    for r in rows[:8]:
        outcome = "pending"
        if r.get("correct_bool") is not None:
            outcome = "right" if int(r["correct_bool"]) == 1 else "wrong"
        recent.append({
            "decision_type": r.get("decision_type"),
            "symbol": r.get("symbol"),
            "verdict": r.get("verdict"),
            "outcome": outcome,
            "created_at": r.get("created_at"),
        })

    return {
        "window_days": window_days,
        "observations_logged": total,
        "graded_count": len(graded),
        "directionally_right": right,
        "neutral": neutral,
        "wrong": wrong,
        "ungraded": ungraded,
        "headline": headline,
        "recent": recent,
    }
