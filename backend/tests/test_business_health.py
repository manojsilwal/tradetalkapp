"""Tests for deterministic fundamental health assessments."""
import unittest

from backend.business_health import (
    assess_financial_metrics,
    assess_leverage,
    enrich_quality_panel,
    synthesize_fundamental_health,
)
from backend.schemas import TerminalFieldProvenance, TerminalQualityPanel, TerminalQualityRow


class TestBusinessHealth(unittest.TestCase):
    def _minimal_quality(self) -> TerminalQualityPanel:
        prov = TerminalFieldProvenance(source="test")
        return TerminalQualityPanel(
            rows=[
                TerminalQualityRow(id="roic", label="ROIC", value_label="20%", provenance=prov),
                TerminalQualityRow(id="moat", label="Moat", value_label="Wide", status_label="Strong", provenance=prov),
                TerminalQualityRow(id="fcf", label="FCF", value_label="$10B", provenance=prov),
                TerminalQualityRow(id="debt", label="Leverage", value_label="1.5x", provenance=prov),
                TerminalQualityRow(id="margin", label="Margin", value_label="45%", provenance=prov),
                TerminalQualityRow(id="current_ratio", label="CR", value_label="2.0", provenance=prov),
            ]
        )

    def test_high_quality_business_headline(self):
        panel = enrich_quality_panel(
            self._minimal_quality(),
            market_regime="BULL_NORMAL",
            roic_pct=20.0,
            moat_status="Strong",
            fcf_usd=10_000_000_000,
            market_cap=200_000_000_000,
            debt_to_ebitda=1.5,
            gross_margin_pct=45.0,
            current_ratio=2.0,
        )
        self.assertEqual(panel.fundamental_health.headline, "High-quality business")
        self.assertEqual(panel.fundamental_health.tone, "positive")
        self.assertTrue(all(r.assessment_label for r in panel.rows))

    def test_weak_fundamentals_bear_stress(self):
        panel = enrich_quality_panel(
            self._minimal_quality(),
            market_regime="BEAR_STRESS",
            roic_pct=2.0,
            moat_status="Weak",
            fcf_usd=-500_000_000,
            market_cap=50_000_000_000,
            debt_to_ebitda=5.0,
            gross_margin_pct=10.0,
            current_ratio=0.8,
        )
        self.assertEqual(panel.fundamental_health.headline, "Weak fundamentals")
        self.assertIn("stressed", panel.fundamental_health.macro_note.lower())

    def test_leverage_tighter_in_bear(self):
        healthy_bull = assess_leverage(2.2, bearish=False)
        watch_bear = assess_leverage(2.2, bearish=True)
        self.assertEqual(healthy_bull.label, "Healthy")
        self.assertEqual(watch_bear.label, "Watch")

    def test_insufficient_data_low_coverage(self):
        from backend.business_health import _Assessment

        panel = synthesize_fundamental_health(
            [_Assessment("neutral", "N/A", "")],
            market_regime="BULL_NORMAL",
        )
        self.assertEqual(panel.headline, "Insufficient data")

    def test_financial_metrics_health_block(self):
        metrics = {
            "valuation": {
                "market_cap": 1e12,
                "trailing_pe": 28.0,
                "forward_pe": 24.0,
                "price_to_sales": 6.0,
                "ev_to_ebitda": 18.0,
            },
            "cash_flow": {
                "free_cash_flow": 40e9,
                "fcf_yield": 0.04,
            },
            "margins_and_growth": {
                "profit_margins": 0.25,
                "operating_margins": 0.30,
                "earnings_growth_yoy": 0.12,
                "revenue_growth_yoy": 0.08,
            },
            "dividend": {
                "dividend_yield": 0.02,
                "payout_ratio": 0.35,
            },
        }
        panel, metric_map = assess_financial_metrics(metrics, market_regime="BULL_NORMAL")
        self.assertIn(panel.headline, ("High-quality business", "Mixed fundamentals"))
        self.assertIn("trailing_pe", metric_map)
        self.assertIn(metric_map["fcf_yield"].label, ("Healthy", "Adequate"))


if __name__ == "__main__":
    unittest.main()
