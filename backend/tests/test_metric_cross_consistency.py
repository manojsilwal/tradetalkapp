"""Cross-endpoint metric consistency tests (offline / mocked)."""
import unittest
from unittest.mock import MagicMock, patch

from backend.connectors.spot import SpotQuote, clear_spot_cache, resolve_spot
from backend.metric_primitives import fcf_yield_decimal, fcf_yield_percent, roic_proxy
from backend.metric_reconciliation import build_reconciliation
from backend.schemas import TerminalScorecardSummary


class TestMetricCrossConsistency(unittest.TestCase):
    def setUp(self):
        clear_spot_cache()

    def test_roic_proxy_matches_quality_formula(self):
        roe = 22.5
        self.assertEqual(roic_proxy(roe), round(roe * 0.8, 1))

    def test_fcf_yield_decimal_vs_percent(self):
        d = fcf_yield_decimal(4_000_000_000, 100_000_000_000)
        p = fcf_yield_percent(4_000_000_000, 100_000_000_000)
        self.assertIsNotNone(d)
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p, d * 100, places=2)

    @patch("backend.connectors.spot.get_spot_with_freshness")
    def test_resolve_spot_cache_hit(self, mock_get):
        from backend.schemas import DataFreshness

        mock_get.return_value = (
            150.0,
            DataFreshness(data_class="live_quote", source="yahoo_chart", degraded=False),
        )
        q1 = resolve_spot("AAPL")
        q2 = resolve_spot("AAPL")
        self.assertIsNotNone(q1)
        self.assertIsNotNone(q2)
        assert q1 is not None and q2 is not None
        self.assertEqual(q1.price, q2.price)
        self.assertEqual(mock_get.call_count, 1)

    def test_reconciliation_conflict_when_buy_and_overvalued(self):
        sc = TerminalScorecardSummary(
            ticker="AAPL",
            ratio=1.1,
            signal="Balanced",
            action="Hold",
            verdict="Balanced",
            quadrant="balanced",
            return_score_weighted=5.0,
            risk_score_weighted=5.0,
            is_comparative=False,
        )
        panel = build_reconciliation(
            headline_verdict="BUY",
            pct_vs_average=-12.0,
            gauge_label="Moderately Overvalued",
            valuation_gap_pct=12.0,
            predicted_cagr_base_pct=8.0,
            scorecard_summary=sc,
        )
        self.assertTrue(panel.conflicting_signals)
        self.assertTrue(panel.reconciliation_note)


if __name__ == "__main__":
    unittest.main()
