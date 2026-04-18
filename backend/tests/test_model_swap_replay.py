"""
Tests for :mod:`backend.model_swap_replay`.

Seeds a temp ledger with 4 debate decisions + graded outcomes, then replays
them through a couple of deterministic ``CandidateRunner`` stubs to assert:

* Incumbent + candidate hit rates are computed per-row and aggregated.
* ``agree`` tracks verdict equality, including for neutral/empty strings.
* Errors from the runner are captured on the row without poisoning the
  batch.
* Evidence + feature rows are plumbed through to the runner so a real LLM
  harness could use them as prompt inputs.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.model_swap_replay import (  # noqa: E402
    CandidateVerdict,
    run_replay,
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
        features: dict | None = None,
        evidence: list | None = None,
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
        if features:
            dl.get_ledger().record_features(
                did,
                [dl.FeatureValue(name=k, value_str=str(v)) for k, v in features.items()],
            )
        if evidence:
            dl.get_ledger().attach_evidence(
                did,
                [dl.EvidenceRef(chunk_id=x["chunk_id"],
                                collection=x["collection"],
                                rank=x.get("rank", 0))
                 for x in evidence],
            )
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


class TestRunReplay(_LedgerHarness):
    def test_candidate_that_echoes_incumbent_matches_hit_rate(self) -> None:
        for sym, excess, correct in [
            ("AAA", 0.02, True),    # incumbent BUY + excess>0 → correct
            ("BBB", -0.03, False),  # incumbent BUY + excess<0 → incorrect
            ("CCC", 0.04, True),    # incumbent BUY correct
            ("DDD", 0.01, True),    # incumbent BUY correct
        ]:
            self._seed(symbol=sym, verdict="BUY", correct=correct,
                       excess_return=excess)

        async def echo_runner(ev, evidence, features):
            return CandidateVerdict(
                decision_id=ev.decision_id,
                verdict=ev.verdict,  # same verdict as incumbent
                confidence=0.8,
                model="echo",
            )

        report = asyncio.run(run_replay(echo_runner, horizon="5d", limit=10))
        self.assertEqual(report.n_considered, 4)
        self.assertEqual(report.n_replayed, 4)
        self.assertEqual(report.n_agree, 4)
        self.assertAlmostEqual(report.incumbent_hit_rate, 0.75, places=4)
        self.assertAlmostEqual(report.candidate_hit_rate, 0.75, places=4)
        self.assertAlmostEqual(report.delta_hit_rate, 0.0, places=4)

    def test_candidate_that_inverts_is_worse_than_incumbent(self) -> None:
        for sym, excess, correct in [
            ("AAA", 0.02, True),
            ("BBB", -0.03, False),
            ("CCC", 0.04, True),
        ]:
            self._seed(symbol=sym, verdict="BUY", correct=correct,
                       excess_return=excess)

        async def inverter(ev, evidence, features):
            flipped = "SELL" if ev.verdict.upper() == "BUY" else "BUY"
            return CandidateVerdict(
                decision_id=ev.decision_id,
                verdict=flipped,
                model="inverter",
            )

        report = asyncio.run(run_replay(inverter, horizon="5d", limit=10))
        # Incumbent BUY correct ↔ excess>0 (2/3). Candidate SELL correct ↔
        # excess<0 (1/3). Delta should be negative.
        self.assertAlmostEqual(report.incumbent_hit_rate, 2 / 3, places=4)
        self.assertAlmostEqual(report.candidate_hit_rate, 1 / 3, places=4)
        self.assertLess(report.delta_hit_rate, 0.0)
        self.assertEqual(report.n_agree, 0)

    def test_runner_error_is_captured_and_does_not_break_batch(self) -> None:
        self._seed(symbol="OK", verdict="BUY", correct=True, excess_return=0.02)
        self._seed(symbol="BOOM", verdict="BUY", correct=True, excess_return=0.01)

        async def faulty(ev, evidence, features):
            if ev.symbol == "BOOM":
                raise RuntimeError("fake outage")
            return CandidateVerdict(
                decision_id=ev.decision_id, verdict="BUY",
            )

        report = asyncio.run(run_replay(faulty, horizon="5d", limit=10))
        self.assertEqual(report.n_considered, 2)
        self.assertEqual(report.n_errors, 1)
        self.assertEqual(report.n_replayed, 1)
        errored = [r for r in report.rows if r.error]
        self.assertEqual(len(errored), 1)
        self.assertIn("runner_error", errored[0].error)

    def test_evidence_and_features_flow_to_runner(self) -> None:
        did = self._seed(
            symbol="EEE", verdict="BUY", correct=True, excess_return=0.02,
            features={"market_regime": "BULL_NORMAL"},
            evidence=[{"chunk_id": "c-1", "collection": "price_movements", "rank": 0}],
        )

        captured: dict = {}

        async def capturing(ev, evidence, features):
            if ev.decision_id == did:
                captured["evidence"] = evidence
                captured["features"] = features
            return CandidateVerdict(decision_id=ev.decision_id, verdict="BUY")

        asyncio.run(run_replay(capturing, horizon="5d", limit=10))

        # Evidence plumbed through
        self.assertEqual(len(captured["evidence"]), 1)
        self.assertEqual(captured["evidence"][0]["chunk_id"], "c-1")
        self.assertEqual(captured["evidence"][0]["collection"], "price_movements")
        # Features plumbed through
        feat_names = {f["feature_name"] for f in captured["features"]}
        self.assertIn("market_regime", feat_names)

    def test_empty_ledger_returns_empty_report(self) -> None:
        async def noop(ev, evidence, features):
            return CandidateVerdict(decision_id=ev.decision_id, verdict="")

        report = asyncio.run(run_replay(noop, horizon="5d"))
        self.assertEqual(report.n_considered, 0)
        self.assertEqual(report.rows, [])
        self.assertIsNone(report.incumbent_hit_rate)


if __name__ == "__main__":
    unittest.main()
