"""Phase E3 — risk assessment connector tests."""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from backend.connectors.risk_assessment import compute_risk_assessment


class _HistFrame:
    def __init__(self, highs, lows, closes):
        self._highs = highs
        self._lows = lows
        self._closes = closes
        self.empty = False

    def __getitem__(self, key):
        if key == "High":
            return _Series(self._highs)
        if key == "Low":
            return _Series(self._lows)
        if key == "Close":
            return _Series(self._closes)
        raise KeyError(key)


class _Series:
    def __init__(self, data):
        self._data = data

    def tolist(self):
        return list(self._data)


class _Ticker:
    def history(self, **kwargs):
        highs = [101 + i * 0.2 for i in range(80)]
        lows = [99 + i * 0.2 for i in range(80)]
        closes = [100 + i * 0.2 for i in range(80)]
        return _HistFrame(highs, lows, closes)


class TestRiskAssessment(unittest.IsolatedAsyncioTestCase):
    async def test_compute_risk_assessment_shape(self):
        with patch("backend.connectors.risk_assessment.yf.Ticker", return_value=_Ticker()):
            with patch(
                "backend.connectors.risk_assessment.MacroHealthConnector.fetch_data",
                new=AsyncMock(return_value={"indicators": {"vix_level": 18.5}}),
            ):
                out = await compute_risk_assessment("AAPL")

        self.assertEqual(out["ticker"], "AAPL")
        self.assertIn(out["regime"], {"ranging", "trending", "crisis"})
        self.assertIn("realized_vol_30d", out)
        self.assertIn("atr_14_pct", out)
        self.assertIn("event_risk_flags", out)
        self.assertIn("stop_distance_pct_hint", out)

    async def test_missing_ticker(self):
        out = await compute_risk_assessment("")
        self.assertEqual(out["error"], "missing_ticker")


if __name__ == "__main__":
    unittest.main()
