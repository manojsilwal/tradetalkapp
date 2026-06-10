"""
Market-truth SEPL fixtures (Phase 3) — generated from the graded ledger.

SEPL's Evaluate stage scores candidate prompts against static fixture files
(``backend/resources/sepl_eval_fixtures/<prompt>.json``). Hand-written
fixtures drift from reality; this module regenerates them nightly from
decisions whose outcomes the grader has already ruled on, so prompt evolution
is judged against what the MARKET said — not against a frozen guess.

Fixture rows follow the format ``SEPL._load_fixtures`` expects::

    [{"input": "...", "reference_verdict": "BUY"}, ...]

``reference_verdict`` is the verdict that was CORRECT in hindsight: the
original verdict when ``correct_bool=1``, the opposite direction when
``correct_bool=0``. Unlabelled decisions are skipped.

Kill switch: ``SEPL_MARKET_FIXTURES_ENABLE=0`` (and the generator never
overwrites a fixture file with fewer than ``min_cases`` rows, so a thin
ledger cannot wipe useful hand-written fixtures).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import decision_ledger as _dl

logger = logging.getLogger(__name__)

FIXTURES_DIR = Path(__file__).resolve().parent / "resources" / "sepl_eval_fixtures"

_OPPOSITE = {
    "BUY": "SELL",
    "STRONG BUY": "SELL",
    "SELL": "BUY",
    "STRONG SELL": "BUY",
    "UP": "DOWN",
    "DOWN": "UP",
}

_TRUTHY = ("1", "true", "yes", "on")


def market_fixtures_enabled() -> bool:
    return (os.getenv("SEPL_MARKET_FIXTURES_ENABLE", "1").strip().lower() or "1") in _TRUTHY


def _graded_rows(
    *,
    horizon: str,
    lookback_days: float,
    limit: int,
    ledger=None,
) -> List[Dict[str, Any]]:
    try:
        ledger = ledger or _dl.get_ledger()
        conn = ledger._conn()  # type: ignore[attr-defined]
    except Exception:
        return []
    if conn is None:
        return []
    cutoff = time.time() - lookback_days * 86400.0
    try:
        rows = conn.execute(
            """SELECT d.decision_id, d.decision_type, d.symbol, d.verdict,
                      d.prompt_versions_json, o.excess_return, o.correct_bool,
                      (SELECT GROUP_CONCAT(f.feature_name || '=' ||
                              COALESCE(NULLIF(f.value_str, ''), CAST(f.value_num AS TEXT)), '; ')
                       FROM feature_snapshots f WHERE f.decision_id = d.decision_id) AS feats,
                      (SELECT COALESCE(NULLIF(f2.regime, ''), '')
                       FROM feature_snapshots f2
                       WHERE f2.decision_id = d.decision_id AND f2.regime != ''
                       LIMIT 1) AS regime
               FROM decision_events d
               JOIN outcome_observations o
                 ON o.decision_id = d.decision_id
                AND o.horizon = ? AND o.metric = 'excess_return'
               WHERE d.created_at >= ? AND o.correct_bool IS NOT NULL
               ORDER BY d.created_at DESC
               LIMIT ?""",
            (horizon, cutoff, int(limit)),
        ).fetchall()
    except Exception as e:
        logger.warning("[SEPLMarketFixtures] query failed: %s", e)
        return []
    return [dict(r) for r in rows]


def _hindsight_verdict(verdict: str, correct: Optional[int]) -> Optional[str]:
    v = (verdict or "").upper().strip()
    if not v or correct is None:
        return None
    if int(correct) == 1:
        return v
    return _OPPOSITE.get(v)


def build_fixture_cases(
    prompt_name: str,
    *,
    horizon: str = "5d",
    lookback_days: float = 120.0,
    max_cases: int = 40,
    ledger=None,
) -> List[Dict[str, Any]]:
    """Fixture cases for decisions produced under ``prompt_name``."""
    rows = _graded_rows(
        horizon=horizon, lookback_days=lookback_days, limit=max_cases * 10, ledger=ledger,
    )
    cases: List[Dict[str, Any]] = []
    for r in rows:
        try:
            pv = json.loads(r.get("prompt_versions_json") or "{}")
        except Exception:
            pv = {}
        if prompt_name not in (pv or {}):
            continue
        reference = _hindsight_verdict(r.get("verdict") or "", r.get("correct_bool"))
        if not reference:
            continue
        feats = (r.get("feats") or "")[:600]
        regime = r.get("regime") or "unknown"
        excess = r.get("excess_return")
        inp = (
            f"Symbol: {r.get('symbol') or 'N/A'} | regime: {regime} | "
            f"features: {feats or '(none)'}\n"
            f"Historical context: a {r.get('decision_type')} decision at this point "
            f"realized {float(excess):+.4f} excess return vs SPY over {horizon}.\n"
            "Output a JSON object with a \"verdict\" key."
        )
        cases.append({
            "input": inp,
            "reference_verdict": reference,
            "_meta": {
                "decision_id": r.get("decision_id"),
                "horizon": horizon,
                "label_source": "market_truth",
            },
        })
        if len(cases) >= max_cases:
            break
    return cases


def regenerate_fixtures(
    prompt_names: Optional[List[str]] = None,
    *,
    horizon: str = "5d",
    min_cases: int = 8,
    ledger=None,
) -> Dict[str, Any]:
    """Write market-truth fixture files for learnable prompts. Never raises."""
    if not market_fixtures_enabled():
        return {"enabled": False}
    try:
        if prompt_names is None:
            from .resource_registry import ResourceKind, get_resource_registry

            prompt_names = [
                r.name
                for r in get_resource_registry().list(ResourceKind.PROMPT)
                if r.learnable
            ]
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        written: Dict[str, int] = {}
        skipped: Dict[str, str] = {}
        for name in prompt_names:
            cases = build_fixture_cases(name, horizon=horizon, ledger=ledger)
            if len(cases) < min_cases:
                skipped[name] = f"only {len(cases)} graded cases (< {min_cases})"
                continue
            path = FIXTURES_DIR / f"{name}.json"
            path.write_text(json.dumps(cases, indent=2), encoding="utf-8")
            written[name] = len(cases)
        return {"enabled": True, "written": written, "skipped": skipped}
    except Exception as e:
        logger.warning("[SEPLMarketFixtures] regenerate failed: %s", e)
        return {"enabled": True, "error": str(e)[:300]}
