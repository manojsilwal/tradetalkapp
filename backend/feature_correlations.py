"""
Feature x Regime x Horizon correlation analytics (Harness Engineering Phase 2).

Reads from :mod:`backend.decision_ledger` tables and computes hit-rate plus
excess-return statistics per ``(feature_name, feature_bucket, regime, horizon)``
combination. This is how the ``decision_events`` + ``feature_snapshots`` +
``outcome_observations`` tables turn into the finance-superintelligence
"moat" — the correlations between every input datapoint and every graded
market outcome.

Two surfaces are exposed:

1. A SQLite VIEW (``v_feature_hit_rate``) installed at import time so
   operators can run ad-hoc queries on a warm ledger without spinning up the
   Python layer.
2. Python helpers :func:`compute_feature_stats` and :func:`top_features` that
   return ready-to-serialize dicts for the FastAPI resource router and for
   SEPL's :class:`DecisionLedgerReflectionSource` to drive prompt evolution.

Numeric features are bucketed by tertile within their own sample so mean
comparisons across regimes are stable (e.g. PE_LOW vs PE_HIGH). String
features are used as-is. Buckets keep the view size bounded — without them a
continuous feature like SIR would explode into millions of (feature_name,
value_str) combinations in the materialised view.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import decision_ledger as _dl

logger = logging.getLogger(__name__)


_VIEW_NAME = "v_feature_hit_rate"


_SQLITE_VIEW_DDL = f"""
CREATE VIEW IF NOT EXISTS {_VIEW_NAME} AS
SELECT
    f.feature_name                        AS feature_name,
    COALESCE(NULLIF(f.value_str, ''), '') AS feature_value,
    o.horizon                             AS horizon,
    COALESCE(NULLIF(f.regime, ''), '')    AS regime,
    COUNT(*)                              AS n,
    AVG(o.excess_return)                  AS mean_excess_return,
    AVG(CASE WHEN o.correct_bool = 1 THEN 1.0
             WHEN o.correct_bool = 0 THEN 0.0 END) AS hit_rate,
    SUM(CASE WHEN o.correct_bool IS NOT NULL THEN 1 ELSE 0 END) AS n_labelled
FROM feature_snapshots f
JOIN outcome_observations o ON o.decision_id = f.decision_id
WHERE o.metric = 'excess_return'
GROUP BY f.feature_name, feature_value, o.horizon, regime
"""


def install_sqlite_view(ledger: Any = None) -> bool:
    """Create ``v_feature_hit_rate`` on the SQLite ledger (idempotent).

    Called on demand from the API + from tests. Returns True if the view
    exists after the call, False if the ledger backend doesn't expose a raw
    connection (e.g. Supabase / Null backends).
    """
    try:
        ledger = ledger or _dl.get_ledger()
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("[FeatureCorrelations] install_sqlite_view skipped: %s", e)
        return False
    if conn is None:
        return False
    try:
        conn.executescript(_SQLITE_VIEW_DDL)
        return True
    except Exception as e:
        logger.warning("[FeatureCorrelations] view install failed: %s", e)
        return False


# ── Python analytics API ───────────────────────────────────────────────────


@dataclass
class FeatureStat:
    """One (feature, bucket, regime, horizon) summary row."""

    feature_name: str
    feature_value: str
    horizon: str
    regime: str
    n: int
    n_labelled: int
    hit_rate: Optional[float]
    mean_excess_return: Optional[float]
    stdev_excess_return: Optional[float] = None
    t_stat: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "feature_name": self.feature_name,
            "feature_value": self.feature_value,
            "horizon": self.horizon,
            "regime": self.regime,
            "n": self.n,
            "n_labelled": self.n_labelled,
            "hit_rate": self.hit_rate,
            "mean_excess_return": self.mean_excess_return,
            "stdev_excess_return": self.stdev_excess_return,
            "t_stat": self.t_stat,
        }


def compute_feature_stats(
    *,
    horizon: str = "5d",
    min_n: int = 3,
    ledger: Any = None,
    include_numeric_buckets: bool = True,
    n_buckets: int = 3,
) -> List[FeatureStat]:
    """Return per-(feature, value, regime) stats for one horizon.

    Parameters
    ----------
    horizon: ledger horizon label to filter on (``1d|5d|21d|63d``).
    min_n: drop groups with fewer than ``min_n`` graded outcomes — keeps the
        signal above the obvious-noise floor.
    include_numeric_buckets: when True, numeric features (rows where
        ``value_num IS NOT NULL``) are bucketed into quantile bins and
        included as synthetic string buckets like ``"q1"/"q2"/"q3"``. Useful
        for continuous variables like ``pe_ratio`` or ``credit_stress_index``
        that would otherwise split the sample into single-observation groups.
    n_buckets: number of quantile bins when ``include_numeric_buckets=True``.
    """
    try:
        ledger = ledger or _dl.get_ledger()
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception as e:
        logger.debug("[FeatureCorrelations] compute_feature_stats skipped: %s", e)
        return []
    if conn is None:
        return []

    try:
        rows = conn.execute(
            """
            SELECT
                f.feature_name,
                f.value_num,
                f.value_str,
                f.regime,
                o.excess_return,
                o.correct_bool
            FROM feature_snapshots f
            JOIN outcome_observations o ON o.decision_id = f.decision_id
            WHERE o.horizon = ? AND o.metric = 'excess_return'
            """,
            (horizon,),
        ).fetchall()
    except Exception as e:
        logger.warning("[FeatureCorrelations] compute fetch failed: %s", e)
        return []

    # Group by (feature_name, bucket, regime) → list of (excess, correct)
    groups: Dict[Tuple[str, str, str], List[Tuple[Optional[float], Optional[int]]]] = {}

    # First pass: collect (feature_name → [value_num values]) for quantile bucketing.
    numeric_samples: Dict[str, List[float]] = {}
    if include_numeric_buckets:
        for r in rows:
            vn = r["value_num"]
            if vn is None:
                continue
            try:
                numeric_samples.setdefault(r["feature_name"], []).append(float(vn))
            except Exception:
                continue

    quantile_edges: Dict[str, List[float]] = {}
    if include_numeric_buckets:
        for fname, vals in numeric_samples.items():
            if len(vals) < max(n_buckets, 3):
                continue
            vals_sorted = sorted(vals)
            edges: List[float] = []
            for k in range(1, n_buckets):
                q = k / n_buckets
                idx = min(len(vals_sorted) - 1, max(0, int(round(q * len(vals_sorted)) - 1)))
                edges.append(vals_sorted[idx])
            quantile_edges[fname] = edges

    for r in rows:
        fname = str(r["feature_name"] or "")
        if not fname:
            continue
        vs = str(r["value_str"] or "")
        vn = r["value_num"]
        bucket = vs
        if not bucket and include_numeric_buckets and vn is not None:
            edges = quantile_edges.get(fname) or []
            if edges:
                try:
                    vv = float(vn)
                    b_idx = 0
                    for edge in edges:
                        if vv <= edge:
                            break
                        b_idx += 1
                    bucket = f"q{b_idx + 1}"
                except Exception:
                    bucket = ""
        if not bucket:
            continue

        regime = str(r["regime"] or "")
        try:
            excess = float(r["excess_return"]) if r["excess_return"] is not None else None
        except Exception:
            excess = None
        cb = r["correct_bool"]
        try:
            cb_i = int(cb) if cb is not None else None
        except Exception:
            cb_i = None

        key = (fname, bucket, regime)
        groups.setdefault(key, []).append((excess, cb_i))

    out: List[FeatureStat] = []
    for (fname, bucket, regime), samples in groups.items():
        n = len(samples)
        if n < int(min_n):
            continue
        excess_vals = [e for e, _ in samples if e is not None]
        correct_vals = [c for _, c in samples if c is not None]

        mean_ex = sum(excess_vals) / len(excess_vals) if excess_vals else None
        stdev_ex: Optional[float] = None
        t_stat: Optional[float] = None
        if excess_vals and len(excess_vals) > 1 and mean_ex is not None:
            var = sum((x - mean_ex) ** 2 for x in excess_vals) / (len(excess_vals) - 1)
            stdev_ex = math.sqrt(max(0.0, var))
            se = stdev_ex / math.sqrt(len(excess_vals)) if len(excess_vals) else 0.0
            t_stat = (mean_ex / se) if se > 1e-9 else None

        hit_rate = (sum(correct_vals) / len(correct_vals)) if correct_vals else None

        out.append(
            FeatureStat(
                feature_name=fname,
                feature_value=bucket,
                horizon=horizon,
                regime=regime,
                n=n,
                n_labelled=len(correct_vals),
                hit_rate=hit_rate,
                mean_excess_return=mean_ex,
                stdev_excess_return=stdev_ex,
                t_stat=t_stat,
            )
        )

    return out


def top_features(
    *,
    horizon: str = "5d",
    min_n: int = 5,
    by: str = "hit_rate",
    limit: int = 10,
    ledger: Any = None,
) -> List[FeatureStat]:
    """Convenience: run :func:`compute_feature_stats` and rank the results.

    ``by`` must be one of ``"hit_rate"``, ``"mean_excess_return"``, ``"t_stat"``.
    Entries with ``None`` in the ranking field are dropped.
    """
    if by not in {"hit_rate", "mean_excess_return", "t_stat"}:
        raise ValueError(f"unknown rank field: {by}")
    stats = compute_feature_stats(horizon=horizon, min_n=min_n, ledger=ledger)
    stats = [s for s in stats if getattr(s, by) is not None]
    stats.sort(key=lambda s: float(getattr(s, by)), reverse=True)
    return stats[: max(1, int(limit))]
