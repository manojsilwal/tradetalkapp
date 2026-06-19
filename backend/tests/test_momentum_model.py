"""Unit tests for composite momentum pricing model (offline, synthetic OHLCV)."""
from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from backend.data_errors import InsufficientDataError
from backend.momentum_model import (
    analyze_momentum,
    classify_momentum,
    compute_downside_exposure,
    compute_momentum_indicators,
    score_absolute_momentum,
)


def _make_ohlcv(
    n: int,
    *,
    start_price: float = 100.0,
    daily_drift: float = 0.002,
    noise: float = 0.005,
    volume: float = 1_000_000.0,
) -> pd.DataFrame:
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=n, freq="B")
    prices = [start_price]
    for _ in range(n - 1):
        prices.append(prices[-1] * (1.0 + daily_drift + np.random.default_rng(42).normal(0, noise)))
    close = np.array(prices)
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.998
    vol = np.full(n, volume)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=dates,
    )


class TestMomentumModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.uptrend = _make_ohlcv(280, daily_drift=0.003, noise=0.002)
        cls.decline = _make_ohlcv(280, start_price=200.0, daily_drift=-0.004, noise=0.003)
        cls.spy = _make_ohlcv(280, daily_drift=0.001, noise=0.003)
        cls.sector = _make_ohlcv(280, daily_drift=0.0015, noise=0.003)
        cls.meta = {"ticker": "TEST", "sector": "Technology", "beta": 1.2, "market_cap": 50e9}

    def test_insufficient_history_raises(self) -> None:
        short = _make_ohlcv(100)
        with self.assertRaises(InsufficientDataError):
            analyze_momentum(short, self.spy, self.sector, self.meta)

    def test_uptrend_scores_bounded(self) -> None:
        out = analyze_momentum(self.uptrend, self.spy, self.sector, self.meta)
        self.assertGreaterEqual(out["momentum_pricing_score"], 0.0)
        self.assertLessEqual(out["momentum_pricing_score"], 100.0)
        self.assertGreaterEqual(out["downside_exposure_score"], 0.0)
        self.assertLessEqual(out["downside_exposure_score"], 100.0)
        self.assertIn("classification", out)
        self.assertIn("agent_summary", out)
        self.assertIsInstance(out["risk_flags"], list)
        self.assertIn("component_breakdown", out)
        self.assertIn("technical_positioning", out)
        self.assertIn("final_agent_narrative", out)
        self.assertIn("model_read", out)
        self.assertTrue(len(out["component_breakdown"]) == 5)

    def test_uptrend_beats_decline(self) -> None:
        up = analyze_momentum(self.uptrend, self.spy, self.sector, self.meta)
        down = analyze_momentum(self.decline, self.spy, self.sector, self.meta)
        self.assertGreater(up["momentum_pricing_score"], down["momentum_pricing_score"])

    def test_indicators_deterministic(self) -> None:
        ind1 = compute_momentum_indicators(self.uptrend, self.spy, self.sector, self.meta)
        ind2 = compute_momentum_indicators(self.uptrend, self.spy, self.sector, self.meta)
        self.assertEqual(ind1["return_6m"], ind2["return_6m"])
        self.assertGreater(ind1["close"], 0)

    def test_classification_elite(self) -> None:
        label, flags = classify_momentum(92.0, 40.0, {"ema_distance_50": 0.1, "rsi_14": 65, "cmf_21d": 0.1, "obv_slope": 1, "sector_trend_score": 80, "spy_trend_score": 80})
        self.assertEqual(label, "Elite Momentum Leader")
        self.assertIsInstance(flags, list)

    def test_classification_breakdown(self) -> None:
        label, _ = classify_momentum(20.0, 80.0, {})
        self.assertEqual(label, "Momentum Breakdown")

    def test_downside_pullback_estimates_negative(self) -> None:
        ind = compute_momentum_indicators(self.uptrend, self.spy, self.sector, self.meta)
        down = compute_downside_exposure(ind)
        for key in ("mild_pullback_estimate", "trend_damage_estimate", "major_breakdown_estimate"):
            self.assertIn("%", down[key])

    def test_partial_mode_under_252_bars(self) -> None:
        partial = _make_ohlcv(180, daily_drift=0.002)
        out = analyze_momentum(partial, self.spy.tail(180), self.sector.tail(180), self.meta)
        self.assertTrue(out.get("partial_mode"))

    def test_absolute_score_in_range(self) -> None:
        ind = compute_momentum_indicators(self.uptrend, self.spy, self.sector, self.meta)
        s = score_absolute_momentum(ind)
        self.assertGreaterEqual(s, 0.0)
        self.assertLessEqual(s, 100.0)


if __name__ == "__main__":
    unittest.main()
