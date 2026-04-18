"""
Tests for :class:`backend.sepl.DecisionLedgerReflectionSource`.

This source is the generalisation of Reflect that lets SEPL consume failures
from every decision_type — not just swarm. The tests seed a temp ledger with
debate + chat_turn + swarm_factor decisions, grade them with synthetic
outcomes, and assert that:

* Only rows with ``prompt_versions`` surface by default.
* ``effectiveness_score`` maps correctly to 1.0 / 0.0 / 0.5.
* ``decision_types`` filter works (so SEPL per-prompt reflect queries can
  narrow to a single producer).
* The output shape is still compatible with :func:`sepl._aggregate_reflections_by_prompt`
  and :func:`sepl._mean_effectiveness`.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.sepl import (  # noqa: E402
    DecisionLedgerReflectionSource,
    _aggregate_reflections_by_prompt,
    _mean_effectiveness,
)


class _LedgerHarness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "d.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        dl._reset_singleton_for_tests()

    def tearDown(self) -> None:
        dl._reset_singleton_for_tests()
        os.environ.pop("DECISIONS_DB_PATH", None)

    def _seed(
        self,
        *,
        decision_type: str,
        symbol: str,
        verdict: str,
        prompt_versions: dict | None,
        correct: bool | None,
        excess_return: float,
        age_seconds: float = 0.0,
        horizon: str = "5d",
        features: dict | None = None,
    ) -> str:
        decision_id = dl.new_decision_id()
        event = dl.DecisionEvent(
            decision_id=decision_id,
            created_at=time.time() - age_seconds,
            decision_type=decision_type,
            symbol=symbol,
            horizon_hint=horizon,
            verdict=verdict,
            confidence=0.7,
            prompt_versions=prompt_versions or {},
            source_route="tests::seed",
            model="test-model",
        )
        dl.get_ledger().emit_decision(event)
        if features:
            dl.get_ledger().record_features(
                decision_id,
                [dl.FeatureValue(name=k, value_str=str(v)) for k, v in features.items()],
            )
        # One excess_return observation per decision.
        dl.get_ledger().record_outcome(
            dl.OutcomeObservation(
                decision_id=decision_id,
                horizon=horizon,
                metric="excess_return",
                value=excess_return,
                as_of_ts=time.time(),
                benchmark="SPY",
                excess_return=excess_return,
                correct=correct,
                label_source="test",
            )
        )
        return decision_id


class TestDecisionLedgerReflectionSource(_LedgerHarness):
    def test_returns_rows_with_prompt_versions_only(self) -> None:
        # One debate decision WITH prompt versions — should surface.
        did_ok = self._seed(
            decision_type="debate",
            symbol="AAPL",
            verdict="BUY",
            prompt_versions={"moderator": "v1", "bull": "v3"},
            correct=True,
            excess_return=0.02,
            features={"market_regime": "BULL_NORMAL"},
        )
        # Another debate decision WITHOUT prompt versions — should be skipped.
        did_skip = self._seed(
            decision_type="debate",
            symbol="TSLA",
            verdict="SELL",
            prompt_versions=None,
            correct=False,
            excess_return=0.04,
        )

        src = DecisionLedgerReflectionSource()
        rows = src.fetch_recent_reflections()
        ids = [r["meta"]["decision_id"] for r in rows]
        self.assertIn(did_ok, ids)
        self.assertNotIn(did_skip, ids)

    def test_effectiveness_score_correct_incorrect_unlabelled(self) -> None:
        self._seed(decision_type="debate", symbol="AAA", verdict="BUY",
                   prompt_versions={"moderator": "v1"}, correct=True,
                   excess_return=0.03)
        self._seed(decision_type="debate", symbol="BBB", verdict="BUY",
                   prompt_versions={"moderator": "v1"}, correct=False,
                   excess_return=-0.02)
        self._seed(decision_type="debate", symbol="CCC", verdict="NEUTRAL",
                   prompt_versions={"moderator": "v1"}, correct=None,
                   excess_return=0.01)
        rows = DecisionLedgerReflectionSource().fetch_recent_reflections()
        by_symbol = {r["meta"]["symbol"]: r for r in rows}
        self.assertAlmostEqual(by_symbol["AAA"]["meta"]["effectiveness_score"], 1.0)
        self.assertAlmostEqual(by_symbol["BBB"]["meta"]["effectiveness_score"], 0.0)
        self.assertAlmostEqual(by_symbol["CCC"]["meta"]["effectiveness_score"], 0.5)

    def test_decision_types_filter_narrows_output(self) -> None:
        self._seed(decision_type="debate", symbol="AAA", verdict="BUY",
                   prompt_versions={"moderator": "v1"}, correct=True, excess_return=0.03)
        self._seed(decision_type="swarm_factor", symbol="BBB", verdict="BUY",
                   prompt_versions={"bull": "v1"}, correct=False, excess_return=-0.01)
        self._seed(decision_type="chat_turn", symbol="CCC", verdict="", 
                   prompt_versions={"moderator": "v1"}, correct=None, excess_return=0.0)
        src = DecisionLedgerReflectionSource(decision_types=("debate", "swarm_factor"))
        rows = src.fetch_recent_reflections()
        types = {r["meta"]["decision_type"] for r in rows}
        self.assertEqual(types, {"debate", "swarm_factor"})

    def test_prompt_versions_roundtrip_to_json_for_sepl_aggregator(self) -> None:
        """``_aggregate_reflections_by_prompt`` parses meta['prompt_versions'] as JSON.

        The ledger stores prompt_versions as a dict JSON-encoded inside the
        SQLite TEXT column, so our source must return it as the same JSON
        STRING the Chroma source returns (not a Python dict).
        """
        self._seed(decision_type="debate", symbol="AAA", verdict="BUY",
                   prompt_versions={"moderator": "v1", "bull": "v7"},
                   correct=True, excess_return=0.02)
        rows = DecisionLedgerReflectionSource().fetch_recent_reflections()
        self.assertEqual(len(rows), 1)
        pv_raw = rows[0]["meta"]["prompt_versions"]
        self.assertIsInstance(pv_raw, str)
        self.assertEqual(json.loads(pv_raw), {"moderator": "v1", "bull": "v7"})

        by_prompt = _aggregate_reflections_by_prompt(rows)
        self.assertEqual(set(by_prompt.keys()), {"moderator", "bull"})
        self.assertAlmostEqual(_mean_effectiveness(by_prompt["moderator"]), 1.0)

    def test_limit_bounds_result_size(self) -> None:
        for i in range(5):
            self._seed(
                decision_type="debate",
                symbol=f"S{i}",
                verdict="BUY",
                prompt_versions={"moderator": "v1"},
                correct=(i % 2 == 0),
                excess_return=0.01 * (i + 1),
                age_seconds=i,
            )
        rows = DecisionLedgerReflectionSource().fetch_recent_reflections(limit=2)
        self.assertLessEqual(len(rows), 2)

    def test_captures_market_regime_feature_snapshot(self) -> None:
        self._seed(decision_type="debate", symbol="AAA", verdict="BUY",
                   prompt_versions={"moderator": "v1"}, correct=True,
                   excess_return=0.02, features={"market_regime": "BEAR_STRESS"})
        rows = DecisionLedgerReflectionSource().fetch_recent_reflections()
        self.assertEqual(rows[0]["meta"]["market_regime"], "BEAR_STRESS")


if __name__ == "__main__":
    unittest.main()
