"""
NR-9 — backtest / hit-rate surfacing for the Narrative Rotation Radar.

The radar emits ``decision_type="theme_phase"`` rows to the Decision-Outcome
Ledger; the daily ``outcome_grader`` scores each by forward **excess return vs
SPY** at multiple horizons. This module reads those graded outcomes back so the UI
can show how theme-phase calls have historically performed — no separate backtest
engine needed (Plan §10, §16).

Reuses the SQL shape from ``routers/harness.py::hit_rates`` but scoped to
``theme_phase`` and grouped by theme (symbol). Resilient: returns empty on any
backend that doesn't expose a SQLite connection (supabase/none).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def theme_phase_hit_rates(horizon: str = "21d", limit: int = 50) -> List[Dict[str, Any]]:
    """Hit rate + mean excess return by theme for graded theme-phase decisions."""
    try:
        from .. import decision_ledger as dl

        conn = getattr(dl.get_ledger(), "_conn", lambda: None)()
        if conn is None:
            return []
        rows = conn.execute(
            """SELECT d.symbol AS theme_id,
                      o.horizon,
                      COUNT(*) AS n,
                      AVG(CASE WHEN o.correct_bool = 1 THEN 1.0
                               WHEN o.correct_bool = 0 THEN 0.0 END) AS hit_rate,
                      AVG(o.excess_return) AS mean_excess_return
               FROM decision_events d
               JOIN outcome_observations o
                 ON o.decision_id = d.decision_id AND o.metric = 'excess_return'
               WHERE d.decision_type = 'theme_phase' AND o.horizon = ?
               GROUP BY d.symbol, o.horizon
               ORDER BY n DESC
               LIMIT ?""",
            (horizon, int(limit)),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug("[NarrativeRadar] backtest read failed: %s", e)
        return []


def overall_summary(horizon: str = "21d") -> Dict[str, Any]:
    rows = theme_phase_hit_rates(horizon=horizon, limit=500)
    if not rows:
        return {"horizon": horizon, "n": 0, "hit_rate": None, "mean_excess_return": None, "by_theme": []}
    total_n = sum(int(r.get("n") or 0) for r in rows)
    # n-weighted means
    hr = [(r.get("hit_rate"), r.get("n")) for r in rows if r.get("hit_rate") is not None]
    ex = [(r.get("mean_excess_return"), r.get("n")) for r in rows if r.get("mean_excess_return") is not None]
    wmean = lambda pairs: (round(sum(v * n for v, n in pairs) / sum(n for _, n in pairs), 4)
                           if pairs and sum(n for _, n in pairs) else None)
    return {
        "horizon": horizon,
        "n": total_n,
        "hit_rate": wmean(hr),
        "mean_excess_return": wmean(ex),
        "by_theme": rows,
    }
