"""
Continuity moments — rhyme today's portfolio context with past user history.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import portfolio_memory as pm


def _list_reactions(user_id: str, *, limit: int = 40) -> List[Dict[str, Any]]:
    try:
        if pm._use_postgres():
            conn = pm._pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM portfolio_reaction_memory
                    WHERE user_id = %s
                    ORDER BY event_date DESC, created_at DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
            conn.close()
            return [pm._row_dict(r) for r in rows]

        conn = pm._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM portfolio_reaction_memory
            WHERE user_id = ?
            ORDER BY event_date DESC, created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [pm._row_dict(r) for r in rows]
    except Exception:
        return []


def _list_snapshots(user_id: str, *, limit: int = 30) -> List[Dict[str, Any]]:
    try:
        if pm._use_postgres():
            conn = pm._pg_connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT * FROM portfolio_snapshots
                    WHERE user_id = %s
                    ORDER BY snapshot_date DESC
                    LIMIT %s
                    """,
                    (user_id, limit),
                )
                rows = cur.fetchall()
            conn.close()
            return [pm._row_dict(r) for r in rows]

        conn = pm._get_conn()
        rows = conn.execute(
            """
            SELECT * FROM portfolio_snapshots
            WHERE user_id = ?
            ORDER BY snapshot_date DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [pm._row_dict(r) for r in rows]
    except Exception:
        return []


def find_continuity_moments(
    user_id: str,
    *,
    symbols: List[str],
    today_daily_return_pct: Optional[float] = None,
    top_movers: Optional[List[Dict[str, Any]]] = None,
    max_moments: int = 2,
) -> List[Dict[str, Any]]:
    """
    Return human-readable continuity lines when history rhymes with today.
    """
    if not user_id:
        return []

    sym_set = {s.upper() for s in symbols if s}
    moments: List[Dict[str, Any]] = []
    top_movers = top_movers or []

    # Symbol big-move recall
    for mover in top_movers[:3]:
        sym = (mover.get("symbol") or "").upper()
        if not sym or sym not in sym_set:
            continue
        today_move = float(mover.get("daily_return_pct") or 0)
        if abs(today_move) < 2.0:
            continue
        reactions = [r for r in _list_reactions(user_id) if (r.get("symbol") or "").upper() == sym]
        for past in reactions[1:4]:
            past_move = float(past.get("move_pct") or 0)
            if past_move == 0:
                continue
            if (today_move > 0) != (past_move > 0):
                continue
            evt_date = past.get("event_date") or ""
            impact = past.get("portfolio_impact_pct")
            impact_txt = f" ({impact:+.1f}% portfolio impact)" if impact is not None else ""
            direction = "rose" if today_move > 0 else "fell"
            moments.append({
                "type": "symbol_move_rhyme",
                "symbol": sym,
                "title": f"Last time {sym} {direction} sharply",
                "body": (
                    f"On {evt_date}, {sym} moved {past_move:+.1f}%{impact_txt}. "
                    f"Today it's moving in a similar direction ({today_move:+.1f}%)."
                ),
            })
            break

    # Portfolio-level drawdown/recovery rhyme
    if today_daily_return_pct is not None and len(moments) < max_moments:
        snaps = _list_snapshots(user_id, limit=60)
        prior_stress = [
            s for s in snaps[1:]
            if s.get("daily_return_pct") is not None
            and float(s["daily_return_pct"]) <= -1.5
        ]
        if today_daily_return_pct <= -1.0 and prior_stress:
            p = prior_stress[0]
            recovery = next(
                (
                    s for s in snaps
                    if s.get("snapshot_date", "") > p.get("snapshot_date", "")
                    and float(s.get("daily_return_pct") or 0) > 0.5
                ),
                None,
            )
            if recovery:
                moments.append({
                    "type": "portfolio_recovery_rhyme",
                    "symbol": None,
                    "title": "You were here before",
                    "body": (
                        f"After a {float(p['daily_return_pct']):.1f}% portfolio day on "
                        f"{p.get('snapshot_date')}, you recovered within a few sessions."
                    ),
                })

    # Longest-held position memory
    if len(moments) < max_moments and symbols:
        events = pm.list_portfolio_events(user_id, limit=30)
        adds = [e for e in events if e.get("event_type") == "position_added" and e.get("symbol")]
        if adds:
            sym = adds[-1].get("symbol")
            evt_date = adds[-1].get("event_date") or ""
            if sym and sym.upper() in sym_set:
                moments.append({
                    "type": "holding_tenure",
                    "symbol": sym.upper(),
                    "title": f"{sym.upper()} is part of your story",
                    "body": f"You added {sym.upper()} on {evt_date} — it's still in your open portfolio.",
                })

    deduped: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for m in moments:
        key = m.get("type", "") + ":" + str(m.get("symbol") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
        if len(deduped) >= max_moments:
            break
    return deduped
