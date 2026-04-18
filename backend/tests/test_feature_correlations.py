"""
Tests for :mod:`backend.feature_correlations`.

Seeds a temp ledger with a mix of numeric + string features spanning two
market regimes, records graded outcomes, then asserts:

* The SQLite VIEW installs cleanly and returns the aggregate rows.
* :func:`compute_feature_stats` computes hit-rate + mean excess return per
  (feature, bucket, regime).
* Numeric features are bucketed into quantiles when there's enough sample.
* :func:`top_features` ranks by the requested metric.
* ``min_n`` filter drops small samples as designed.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.feature_correlations import (  # noqa: E402
    compute_feature_stats,
    install_sqlite_view,
    top_features,
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
        symbol: str,
        verdict: str,
        correct: bool,
        excess_return: float,
        horizon: str = "5d",
        regime: str = "BULL_NORMAL",
        features_str: dict | None = None,
        features_num: dict | None = None,
    ) -> str:
        did = dl.new_decision_id()
        ev = dl.DecisionEvent(
            decision_id=did,
            created_at=time.time(),
            decision_type="debate",
            symbol=symbol,
            horizon_hint=horizon,
            verdict=verdict,
            confidence=0.7,
        )
        dl.get_ledger().emit_decision(ev)
        feats = []
        for k, v in (features_str or {}).items():
            feats.append(dl.FeatureValue(name=k, value_str=str(v), regime=regime))
        for k, v in (features_num or {}).items():
            feats.append(dl.FeatureValue(name=k, value_num=float(v), regime=regime))
        # Always tag market_regime as a string feature so view rows surface it.
        feats.append(dl.FeatureValue(name="market_regime", value_str=regime, regime=regime))
        if feats:
            dl.get_ledger().record_features(did, feats)
        dl.get_ledger().record_outcome(
            dl.OutcomeObservation(
                decision_id=did,
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
        return did


class TestFeatureCorrelations(_LedgerHarness):
    def test_install_view_is_idempotent_and_returns_rows(self) -> None:
        self._seed(
            symbol="AAA", verdict="BUY", correct=True, excess_return=0.02,
            features_str={"signal": "bullish"},
        )
        self.assertTrue(install_sqlite_view())
        self.assertTrue(install_sqlite_view())  # second call should not raise

        backend = dl.get_ledger()
        conn = backend._conn()  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT feature_name, feature_value, horizon, regime, n, hit_rate, "
            "mean_excess_return, n_labelled "
            "FROM v_feature_hit_rate WHERE feature_name = 'signal'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["feature_value"], "bullish")
        self.assertEqual(r["horizon"], "5d")
        self.assertEqual(r["n"], 1)
        self.assertAlmostEqual(r["mean_excess_return"], 0.02, places=4)
        self.assertAlmostEqual(r["hit_rate"], 1.0, places=4)
        self.assertEqual(r["n_labelled"], 1)

    def test_compute_feature_stats_per_regime(self) -> None:
        # 3 bullish in bull regime (all correct), 3 bullish in bear regime (2 wrong)
        for i in range(3):
            self._seed(symbol=f"A{i}", verdict="BUY", correct=True,
                       excess_return=0.02 + 0.001 * i, regime="BULL_NORMAL",
                       features_str={"signal": "bullish"})
        for i in range(3):
            correct_flag = (i == 0)
            self._seed(symbol=f"B{i}", verdict="BUY", correct=correct_flag,
                       excess_return=-0.01 if not correct_flag else 0.02,
                       regime="BEAR_STRESS",
                       features_str={"signal": "bullish"})

        stats = compute_feature_stats(horizon="5d", min_n=2)
        # Index by (feature_name, bucket, regime)
        idx = {
            (s.feature_name, s.feature_value, s.regime): s
            for s in stats
        }
        bull = idx[("signal", "bullish", "BULL_NORMAL")]
        bear = idx[("signal", "bullish", "BEAR_STRESS")]
        self.assertEqual(bull.n, 3)
        self.assertEqual(bear.n, 3)
        self.assertAlmostEqual(bull.hit_rate, 1.0, places=4)
        self.assertAlmostEqual(bear.hit_rate, 1.0 / 3.0, places=4)
        self.assertGreater(bull.mean_excess_return, bear.mean_excess_return)

    def test_numeric_features_get_quantile_bucketed(self) -> None:
        for i, pe in enumerate([10.0, 12.0, 15.0, 18.0, 22.0, 40.0, 50.0, 60.0, 80.0]):
            self._seed(
                symbol=f"N{i}",
                verdict="BUY",
                correct=(i >= 6),
                excess_return=0.01 * (i - 4),
                features_num={"pe_ratio": pe},
            )
        stats = compute_feature_stats(horizon="5d", min_n=1, n_buckets=3)
        pe_stats = [s for s in stats if s.feature_name == "pe_ratio"]
        buckets = {s.feature_value for s in pe_stats}
        # At least q1 and q3 should surface (sample had 9 points spanning 10–80).
        self.assertIn("q1", buckets)
        self.assertIn("q3", buckets)

    def test_min_n_filter_drops_small_groups(self) -> None:
        self._seed(
            symbol="Z", verdict="BUY", correct=True, excess_return=0.02,
            features_str={"rare_flag": "YES"},
        )
        stats = compute_feature_stats(horizon="5d", min_n=5)
        names = {s.feature_name for s in stats}
        self.assertNotIn("rare_flag", names)

    def test_top_features_ranks_by_hit_rate(self) -> None:
        # winner feature: always correct  (3 samples)
        for i in range(3):
            self._seed(symbol=f"W{i}", verdict="BUY", correct=True,
                       excess_return=0.03,
                       features_str={"signal": "winner"})
        # loser feature: always wrong (3 samples)
        for i in range(3):
            self._seed(symbol=f"L{i}", verdict="BUY", correct=False,
                       excess_return=-0.02,
                       features_str={"signal": "loser"})
        top = top_features(horizon="5d", min_n=2, by="hit_rate", limit=5)
        winner_entries = [s for s in top if s.feature_value == "winner"]
        self.assertTrue(winner_entries)
        self.assertAlmostEqual(winner_entries[0].hit_rate, 1.0)
        # The top-ranked entry by hit_rate should be 'winner' (1.0 > 0.0).
        self.assertEqual(top[0].feature_value, "winner")


if __name__ == "__main__":
    unittest.main()
