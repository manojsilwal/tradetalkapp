"""
Tests for :mod:`backend.outcome_grader`.

We never touch yfinance — every test injects a deterministic
``PriceProvider`` so the grading math, horizon gating, and ledger wiring can
be asserted on SQLite in milliseconds.
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("GEMINI_PRIMARY", "0")
os.environ.setdefault("GEMINI_LLM_FALLBACK", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.outcome_grader import (  # noqa: E402
    HORIZONS,
    OutcomeGrader,
    PriceProvider,
    _grade_correctness,
)


class _FixedPriceProvider(PriceProvider):
    """Deterministic price provider driven by a ``{(symbol, iso_date): px}`` map.

    Any miss returns the previous known price for that symbol (carry forward)
    to mirror yfinance's weekend / holiday gap-filling. Trailing vol is a
    constant so the math is exact.
    """

    def __init__(
        self,
        prices: Dict[Tuple[str, str], float],
        *,
        daily_vol: Optional[float] = 0.01,
    ) -> None:
        self._prices = dict(prices)
        self._daily_vol = daily_vol

    def close_price(self, symbol: str, as_of: datetime) -> Optional[float]:
        sym = symbol.upper()
        day = as_of
        for _ in range(20):
            key = (sym, day.date().isoformat())
            if key in self._prices:
                p = self._prices[key]
                return float(p) if p > 0 else None
            day = day - timedelta(days=1)
        return None

    def trailing_vol(self, symbol: str, end: datetime, window_days: int) -> Optional[float]:
        return self._daily_vol


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


# ── Pure helpers ────────────────────────────────────────────────────────────


class TestGradeCorrectness(unittest.TestCase):
    def test_buy_verdict_with_positive_excess_is_correct(self) -> None:
        self.assertTrue(_grade_correctness("BUY", 0.02))
        self.assertTrue(_grade_correctness("STRONG BUY", 0.0001))

    def test_buy_verdict_with_negative_excess_is_incorrect(self) -> None:
        self.assertFalse(_grade_correctness("BUY", -0.02))

    def test_sell_verdict_flipped(self) -> None:
        self.assertTrue(_grade_correctness("STRONG SELL", -0.05))
        self.assertFalse(_grade_correctness("SELL", 0.03))

    def test_neutral_and_empty_are_none(self) -> None:
        self.assertIsNone(_grade_correctness("NEUTRAL", 0.02))
        self.assertIsNone(_grade_correctness("", 0.02))
        self.assertIsNone(_grade_correctness("BUY", None))

    def test_price_forecast_direction_verdicts(self) -> None:
        self.assertTrue(_grade_correctness("UP", 0.01))
        self.assertFalse(_grade_correctness("UP", -0.01))
        self.assertTrue(_grade_correctness("DOWN", -0.02))
        self.assertFalse(_grade_correctness("DOWN", 0.05))
        self.assertIsNone(_grade_correctness("FLAT", 0.02))
        self.assertIsNone(_grade_correctness("MIXED", -0.02))


# ── End-to-end grader on SQLite ─────────────────────────────────────────────


class TestOutcomeGraderEndToEnd(_LedgerHarness):
    def _emit(self, *, symbol: str, verdict: str, horizon_hint: str, age_days: int) -> str:
        # Bypass the emit wrapper so we can backdate created_at.
        decision_id = dl.new_decision_id()
        event = dl.DecisionEvent(
            decision_id=decision_id,
            created_at=time.time() - age_days * 86400,
            decision_type="debate",
            symbol=symbol,
            horizon_hint=horizon_hint,
            verdict=verdict,
            confidence=0.7,
        )
        dl.get_ledger().emit_decision(event)
        return decision_id

    def test_grades_buy_decision_with_positive_excess_return(self) -> None:
        decision_id = self._emit(
            symbol="AAPL", verdict="BUY", horizon_hint="5d", age_days=10,
        )

        # Build a provider where AAPL rose 4% and SPY rose 1% over the window.
        entry_dt = datetime.fromtimestamp(
            dl.get_ledger().get_decision(decision_id).created_at, tz=timezone.utc,
        )
        exit_dt = entry_dt + timedelta(days=int(round(5 * 1.45)))
        prices = {
            ("AAPL", entry_dt.date().isoformat()): 100.0,
            ("AAPL", exit_dt.date().isoformat()): 104.0,
            ("SPY", entry_dt.date().isoformat()): 400.0,
            ("SPY", exit_dt.date().isoformat()): 404.0,  # +1%
        }
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices, daily_vol=0.01))
        report = grader.grade_due("5d")
        self.assertEqual(report.graded, 1)
        self.assertEqual(report.considered, 1)

        backend = dl.get_ledger()
        conn = backend._conn()  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT metric, value, benchmark, excess_return, correct_bool, label_source "
            "FROM outcome_observations WHERE decision_id = ? ORDER BY metric",
            (decision_id,),
        ).fetchall()
        metrics = {r["metric"]: r for r in rows}
        self.assertIn("abs_return", metrics)
        self.assertIn("excess_return", metrics)
        self.assertIn("risk_adjusted", metrics)
        self.assertAlmostEqual(metrics["abs_return"]["value"], 0.04, places=4)
        self.assertAlmostEqual(metrics["excess_return"]["value"], 0.03, places=4)
        self.assertEqual(metrics["excess_return"]["benchmark"], "SPY")
        # correct_bool is stored as 1/0 on the excess_return row
        self.assertEqual(metrics["excess_return"]["correct_bool"], 1)
        # risk_adjusted = 0.03 / 0.01 = 3.0
        self.assertAlmostEqual(metrics["risk_adjusted"]["value"], 3.0, places=4)
        self.assertEqual(metrics["abs_return"]["label_source"], "market_truth_v1")

    def test_skips_decisions_when_horizon_hint_is_none(self) -> None:
        self._emit(symbol="TSLA", verdict="BUY", horizon_hint="none", age_days=10)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider({}))
        report = grader.grade_due("5d")
        # Considered (still ungraded) but skipped: horizon_hint filter fires.
        self.assertEqual(report.considered, 1)
        self.assertEqual(report.graded, 0)
        self.assertEqual(report.skipped_no_horizon_hint, 1)

    def test_skips_decisions_with_mismatched_horizon_hint(self) -> None:
        # horizon_hint=5d but grading 21d — should not write any row.
        self._emit(symbol="NVDA", verdict="BUY", horizon_hint="5d", age_days=35)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider({}))
        report = grader.grade_due("21d")
        self.assertEqual(report.graded, 0)

    def test_skips_decisions_without_symbol(self) -> None:
        self._emit(symbol="", verdict="BUY", horizon_hint="5d", age_days=10)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider({}))
        report = grader.grade_due("5d")
        self.assertEqual(report.skipped_no_symbol, 1)
        self.assertEqual(report.graded, 0)

    def test_skips_when_no_price_data(self) -> None:
        self._emit(symbol="UNKNOWNXYZ", verdict="BUY", horizon_hint="5d", age_days=10)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider({}))
        report = grader.grade_due("5d")
        self.assertEqual(report.skipped_no_price, 1)
        self.assertEqual(report.graded, 0)

    def test_idempotent_regrading_does_not_duplicate_rows(self) -> None:
        decision_id = self._emit(
            symbol="AAPL", verdict="BUY", horizon_hint="5d", age_days=10,
        )
        entry_dt = datetime.fromtimestamp(
            dl.get_ledger().get_decision(decision_id).created_at, tz=timezone.utc,
        )
        exit_dt = entry_dt + timedelta(days=int(round(5 * 1.45)))
        prices = {
            ("AAPL", entry_dt.date().isoformat()): 100.0,
            ("AAPL", exit_dt.date().isoformat()): 102.0,
            ("SPY", entry_dt.date().isoformat()): 400.0,
            ("SPY", exit_dt.date().isoformat()): 404.0,
        }
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        grader.grade_due("5d")
        grader.grade_due("5d")  # second pass

        conn = dl.get_ledger()._conn()  # type: ignore[attr-defined]
        n = conn.execute(
            "SELECT COUNT(*) FROM outcome_observations WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
        # Same three metrics regardless of how many times we grade.
        self.assertEqual(n, 3)

    def test_grade_all_covers_every_horizon(self) -> None:
        grader = OutcomeGrader(price_provider=_FixedPriceProvider({}))
        reports = grader.grade_all()
        self.assertEqual(set(reports.keys()), set(HORIZONS.keys()))

    def test_respect_horizon_hint_false_ignores_hint_filter(self) -> None:
        decision_id = self._emit(
            symbol="AAPL", verdict="BUY", horizon_hint="none", age_days=10,
        )
        entry_dt = datetime.fromtimestamp(
            dl.get_ledger().get_decision(decision_id).created_at, tz=timezone.utc,
        )
        exit_dt = entry_dt + timedelta(days=int(round(5 * 1.45)))
        prices = {
            ("AAPL", entry_dt.date().isoformat()): 100.0,
            ("AAPL", exit_dt.date().isoformat()): 105.0,
            ("SPY", entry_dt.date().isoformat()): 400.0,
            ("SPY", exit_dt.date().isoformat()): 400.0,
        }
        grader = OutcomeGrader(
            price_provider=_FixedPriceProvider(prices),
            respect_horizon_hint=False,
        )
        report = grader.grade_due("5d")
        self.assertEqual(report.graded, 1)


# ── Quantile-forecast grading ───────────────────────────────────────────────


class TestForecastGrading(_LedgerHarness):
    """price_forecast decisions with q10/q50/q90 bands get accuracy rows."""

    def _emit_forecast(
        self,
        *,
        symbol: str = "AAPL",
        horizon_hint: str = "5d",
        age_days: int = 10,
        output: Optional[dict] = None,
    ) -> str:
        decision_id = dl.new_decision_id()
        event = dl.DecisionEvent(
            decision_id=decision_id,
            created_at=time.time() - age_days * 86400,
            decision_type="price_forecast",
            symbol=symbol,
            horizon_hint=horizon_hint,
            verdict="UP",
            confidence=None,
            output=output or {},
        )
        dl.get_ledger().emit_decision(event)
        return decision_id

    def _prices_for(self, decision_id: str, *, entry: float, exit_px: float) -> dict:
        entry_dt = datetime.fromtimestamp(
            dl.get_ledger().get_decision(decision_id).created_at, tz=timezone.utc,
        )
        exit_dt = entry_dt + timedelta(days=int(round(5 * 1.45)))
        return {
            ("AAPL", entry_dt.date().isoformat()): entry,
            ("AAPL", exit_dt.date().isoformat()): exit_px,
            ("SPY", entry_dt.date().isoformat()): 400.0,
            ("SPY", exit_dt.date().isoformat()): 404.0,
        }

    def _graded_metrics(self, decision_id: str) -> dict:
        conn = dl.get_ledger()._conn()  # type: ignore[attr-defined]
        rows = conn.execute(
            "SELECT metric, value, correct_bool FROM outcome_observations "
            "WHERE decision_id = ?",
            (decision_id,),
        ).fetchall()
        return {r["metric"]: r for r in rows}

    def test_band_hit_pinball_and_point_error(self) -> None:
        # Realized exit 104 inside [q10=98, q90=110]; q50/point = 105.
        decision_id = self._emit_forecast(
            output={"q10_usd": 98.0, "q50_usd": 105.0, "q90_usd": 110.0,
                    "point_forecast_usd": 105.0},
        )
        prices = self._prices_for(decision_id, entry=100.0, exit_px=104.0)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        report = grader.grade_due("5d")
        self.assertEqual(report.graded, 1)

        metrics = self._graded_metrics(decision_id)
        # Classic verdict rows still present for forecast decisions.
        self.assertIn("abs_return", metrics)
        self.assertIn("excess_return", metrics)

        self.assertEqual(metrics["forecast_band_hit"]["value"], 1.0)
        self.assertEqual(metrics["forecast_band_hit"]["correct_bool"], 1)
        # pinball = (PL(104,98,.1) + PL(104,105,.5) + PL(104,110,.9)) / 3
        #         = (0.6 + 0.5 + 0.6) / 3 = 0.566667 → ÷ entry 100 = 0.0056667
        self.assertAlmostEqual(
            metrics["forecast_pinball"]["value"], 0.0056667, places=6,
        )
        # |105 − 104| / 100 = 0.01
        self.assertAlmostEqual(
            metrics["forecast_point_err"]["value"], 0.01, places=6,
        )

    def test_band_miss_is_labelled_incorrect(self) -> None:
        # Realized exit 120 above q90=110 → coverage miss.
        decision_id = self._emit_forecast(
            output={"q10_usd": 98.0, "q50_usd": 105.0, "q90_usd": 110.0},
        )
        prices = self._prices_for(decision_id, entry=100.0, exit_px=120.0)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        grader.grade_due("5d")

        metrics = self._graded_metrics(decision_id)
        self.assertEqual(metrics["forecast_band_hit"]["value"], 0.0)
        self.assertEqual(metrics["forecast_band_hit"]["correct_bool"], 0)

    def test_point_only_forecast_gets_point_error_only(self) -> None:
        decision_id = self._emit_forecast(output={"point_forecast_usd": 102.0})
        prices = self._prices_for(decision_id, entry=100.0, exit_px=104.0)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        grader.grade_due("5d")

        metrics = self._graded_metrics(decision_id)
        self.assertNotIn("forecast_band_hit", metrics)
        self.assertNotIn("forecast_pinball", metrics)
        self.assertAlmostEqual(
            metrics["forecast_point_err"]["value"], 0.02, places=6,
        )

    def test_non_forecast_decision_gets_no_forecast_rows(self) -> None:
        decision_id = dl.new_decision_id()
        dl.get_ledger().emit_decision(dl.DecisionEvent(
            decision_id=decision_id,
            created_at=time.time() - 10 * 86400,
            decision_type="debate",
            symbol="AAPL",
            horizon_hint="5d",
            verdict="BUY",
            confidence=0.7,
        ))
        prices = self._prices_for(decision_id, entry=100.0, exit_px=104.0)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        grader.grade_due("5d")

        metrics = self._graded_metrics(decision_id)
        self.assertNotIn("forecast_band_hit", metrics)
        self.assertNotIn("forecast_pinball", metrics)
        self.assertNotIn("forecast_point_err", metrics)
        self.assertEqual(len(metrics), 3)  # abs / excess / risk_adjusted only

    def test_malformed_band_values_are_ignored(self) -> None:
        decision_id = self._emit_forecast(
            output={"q10_usd": "not-a-number", "q50_usd": None, "q90_usd": -5.0},
        )
        prices = self._prices_for(decision_id, entry=100.0, exit_px=104.0)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        report = grader.grade_due("5d")
        self.assertEqual(report.graded, 1)  # classic rows still written

        metrics = self._graded_metrics(decision_id)
        self.assertNotIn("forecast_band_hit", metrics)
        self.assertNotIn("forecast_pinball", metrics)
        self.assertNotIn("forecast_point_err", metrics)

    def test_forecast_regrading_stays_idempotent(self) -> None:
        decision_id = self._emit_forecast(
            output={"q10_usd": 98.0, "q50_usd": 105.0, "q90_usd": 110.0},
        )
        prices = self._prices_for(decision_id, entry=100.0, exit_px=104.0)
        grader = OutcomeGrader(price_provider=_FixedPriceProvider(prices))
        grader.grade_due("5d")
        grader.grade_due("5d")

        conn = dl.get_ledger()._conn()  # type: ignore[attr-defined]
        n = conn.execute(
            "SELECT COUNT(*) FROM outcome_observations WHERE decision_id = ?",
            (decision_id,),
        ).fetchone()[0]
        # abs/excess/risk_adjusted + band_hit/pinball/point_err = 6, once each.
        self.assertEqual(n, 6)


# ── Scheduler entry point smoke test ────────────────────────────────────────


class TestRunGraderPass(_LedgerHarness):
    def test_kill_switch_disables_grader(self) -> None:
        import asyncio

        os.environ["DECISION_LEDGER_ENABLE"] = "0"
        try:
            result = asyncio.run(__import__(
                "backend.outcome_grader", fromlist=["run_grader_pass"],
            ).run_grader_pass())
        finally:
            os.environ["DECISION_LEDGER_ENABLE"] = "1"
        self.assertEqual(result.get("grader_enabled"), False)


if __name__ == "__main__":
    unittest.main()
