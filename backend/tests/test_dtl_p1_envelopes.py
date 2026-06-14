"""Offline tests for Workstream D P1/P2 freshness classes (backtest, scorecard,
prediction_market, model_forecast)."""
import unittest
from datetime import datetime, timezone, timedelta

from backend import freshness
from backend.schemas import FreshnessTier


def _now():
    return datetime.now(timezone.utc)


class TestP1FreshnessClasses(unittest.TestCase):
    def test_backtest_just_computed_is_historical_not_stale(self):
        f = freshness.assess(
            data_class="backtest", source="data_lake",
            as_of="2018-12-31", captured_at=_now(),
        )
        self.assertEqual(f.tier, FreshnessTier.HISTORICAL)
        self.assertFalse(f.is_stale)

    def test_backtest_old_computation_is_stale(self):
        old = _now() - timedelta(days=3)
        f = freshness.assess(data_class="backtest", source="data_lake", captured_at=old)
        self.assertTrue(f.is_stale)

    def test_scorecard_tier_historical(self):
        f = freshness.assess(data_class="scorecard", source="yfinance", captured_at=_now())
        self.assertEqual(f.tier, FreshnessTier.HISTORICAL)
        self.assertFalse(f.is_stale)

    def test_prediction_market_fresh_within_window(self):
        f = freshness.assess(data_class="prediction_market", source="poly", captured_at=_now())
        self.assertEqual(f.tier, FreshnessTier.DELAYED)
        self.assertFalse(f.is_stale)

    def test_prediction_market_stale_when_old(self):
        old = _now() - timedelta(hours=2)
        f = freshness.assess(data_class="prediction_market", source="poly", captured_at=old)
        self.assertTrue(f.is_stale)

    def test_model_forecast_fresh(self):
        f = freshness.assess(data_class="model_forecast", source="predictor", captured_at=_now())
        self.assertEqual(f.tier, FreshnessTier.HISTORICAL)
        self.assertFalse(f.is_stale)


if __name__ == "__main__":
    unittest.main()
