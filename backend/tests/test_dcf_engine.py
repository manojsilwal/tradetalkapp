"""Unit tests for the unified DCF engine (backend/dcf_engine.py)."""
from __future__ import annotations

import unittest

from backend import dcf_engine as eng
from backend.valuation_inputs import compute_dcf_scenarios, compute_supercycle_dcf_scenarios
from backend.brain.business_classifier import classify_business


class TestCoreMath(unittest.TestCase):
    def test_flat_path_matches_constant_growth(self) -> None:
        flat = eng.discounted_value(10.0, [0.10] * 5, 0.025, 0.09)
        const = eng.constant_growth_value(10.0, 0.10, 5, 0.025, 0.09)
        self.assertAlmostEqual(flat, const, places=9)

    def test_requires_discount_above_terminal(self) -> None:
        with self.assertRaises(ValueError):
            eng.discounted_value(10.0, [0.05] * 5, 0.05, 0.04)

    def test_monotonic_in_growth_and_discount(self) -> None:
        low = eng.constant_growth_value(10, 0.05, 5, 0.025, 0.09)
        high = eng.constant_growth_value(10, 0.15, 5, 0.025, 0.09)
        self.assertGreater(high, low)
        cheap = eng.constant_growth_value(10, 0.10, 5, 0.025, 0.08)
        dear = eng.constant_growth_value(10, 0.10, 5, 0.025, 0.12)
        self.assertGreater(cheap, dear)

    def test_multi_stage_path_shape(self) -> None:
        path = eng.multi_stage_path(0.30, 0.025, 10, high_years=3, fade_end_year=7)
        self.assertEqual(len(path), 10)
        # First three years hold the anchor.
        self.assertAlmostEqual(path[0], 0.30)
        self.assertAlmostEqual(path[2], 0.30)
        # Growth fades and converges toward terminal by the final year.
        self.assertLess(path[-1], path[0])
        self.assertAlmostEqual(path[-1], 0.025, delta=0.01)

    def test_reverse_dcf_round_trip(self) -> None:
        g0 = 0.12
        v = eng.constant_growth_value(7.0, g0, 5, 0.025, 0.09)
        implied = eng.reverse_dcf_growth(v, 7.0, years=5, terminal_growth=0.025, discount_rate=0.09)
        self.assertIsNotNone(implied)
        assert implied is not None
        self.assertAlmostEqual(implied, g0, places=3)


class TestFcffReinvestment(unittest.TestCase):
    def test_fcff_identity_first_year(self) -> None:
        # g=0.10, ROIC=0.20 -> reinvestment rate = 0.5 of NOPAT.
        fcffs, _ = eng.fcff_series(
            1000.0, [0.10], [0.30], tax_rate=0.21, roic=0.20
        )
        revenue1 = 1000.0 * 1.10
        nopat1 = revenue1 * 0.30 * (1 - 0.21)
        expected = nopat1 * (1 - 0.10 / 0.20)
        self.assertAlmostEqual(fcffs[0], expected, places=6)

    def test_growth_must_be_paid_for_no_double_count(self) -> None:
        """Reinvestment lowers value vs ignoring capital cost — proving growth
        capex is subtracted, never double-credited."""
        kwargs = dict(
            revenue0=1000.0,
            growth_path=[0.15] * 8,
            operating_margin_path=[0.30] * 8,
            tax_rate=0.21,
            roic=0.15,
            discount_rate=0.10,
            terminal_growth=0.025,
            net_cash=0.0,
            shares=100.0,
        )
        with_reinvest = eng.fcff_equity_value_per_share(**kwargs)
        ignore_reinvest = eng.fcff_equity_value_per_share(**{**kwargs, "reinvestment_cap": 0.0})
        assert with_reinvest is not None and ignore_reinvest is not None
        self.assertLess(with_reinvest, ignore_reinvest)

    def test_higher_roic_raises_value(self) -> None:
        base = dict(
            revenue0=1000.0, growth_path=[0.12] * 8, operating_margin_path=[0.25] * 8,
            tax_rate=0.21, discount_rate=0.10, terminal_growth=0.025, net_cash=0.0, shares=100.0,
        )
        low_roic = eng.fcff_equity_value_per_share(roic=0.10, **base)
        high_roic = eng.fcff_equity_value_per_share(roic=0.25, **base)
        assert low_roic is not None and high_roic is not None
        self.assertGreater(high_roic, low_roic)


class TestDiscountAndTerminal(unittest.TestCase):
    def test_execution_risk_raises_cost_of_equity(self) -> None:
        ke0 = eng.cost_of_equity(1.2, risk_free=0.045, execution_risk=0.0)
        ke1 = eng.cost_of_equity(1.2, risk_free=0.045, execution_risk=0.02)
        self.assertAlmostEqual(ke1 - ke0, 0.02, places=4)

    def test_cost_of_equity_capped(self) -> None:
        ke = eng.cost_of_equity(3.0, risk_free=0.045, execution_risk=0.05)
        self.assertLessEqual(ke, eng.KE_CAP + 1e-9)

    def test_terminal_growth_capped_below_risk_free(self) -> None:
        tg = eng.dynamic_terminal_growth("platform_reinvestment_supercycle", risk_free=0.02)
        self.assertLess(tg, 0.02)


class TestCapexSplit(unittest.TestCase):
    def test_depreciation_based_maintenance(self) -> None:
        out = eng.split_capex(capex=-100.0, depreciation=50.0)
        self.assertAlmostEqual(out["maintenance_capex"], 55.0, places=6)
        self.assertAlmostEqual(out["growth_capex"], 45.0, places=6)
        self.assertEqual(out["source"], "depreciation_x1.1")

    def test_fallback_when_no_depreciation(self) -> None:
        out = eng.split_capex(capex=-100.0, depreciation=None)
        self.assertAlmostEqual(out["maintenance_capex"], 40.0, places=6)
        self.assertEqual(out["source"], "capex_x0.4_fallback")


class TestSupercycle(unittest.TestCase):
    def test_classifier_picks_supercycle(self) -> None:
        res = classify_business({
            "market_cap": 2_000e9,
            "revenue_growth_yoy": 0.35,
            "gross_margin": 0.70,
            "operating_margin": 0.45,
            "fcf_margin": 0.30,
            "roic": 0.40,
            "capex_intensity": 0.18,
            "capex_growth": 0.45,
            "ai_exposure": 1.0,
        })
        self.assertEqual(res["business_type"], "platform_reinvestment_supercycle")

    def test_supercycle_value_positive(self) -> None:
        seed = {
            "core_revenue": 16_000e6, "ai_revenue": 115_000e6,
            "core_growth": 0.08, "ai_growth": 0.32,
            "sales_to_capital": 3.5, "capex_lag_years": 1, "horizon_years": 13,
        }
        out = eng.supercycle_value_per_share(
            revenue0=131_000e6, seed=seed, operating_margin=0.48, tax_rate=0.15,
            roic=0.45, discount_rate=0.10, terminal_growth=0.028,
            net_cash=40_000e6, shares=24_000e6,
        )
        self.assertIsNotNone(out)
        assert out is not None
        self.assertGreater(out["fair_value_per_share"], 0)
        self.assertGreaterEqual(out["years"], 10)

    def test_compute_supercycle_scenarios_via_valuation_inputs(self) -> None:
        seed = eng.ai_supercycle_seed_for("NVDA")
        self.assertIsNotNone(seed)
        snapshot = {
            "ticker": "NVDA",
            "totalRevenue": 131_000_000_000,
            "operatingMargins": 0.48,
            "returnOnEquity": 0.45,
            "sharesOutstanding": 24_000_000_000,
            "marketCap": 3_000_000_000_000,
            "beta": 1.6,
            "totalCash": 40_000_000_000,
            "totalDebt": 10_000_000_000,
        }
        res = compute_supercycle_dcf_scenarios(
            snapshot, seed=seed, classification={"business_type": "platform_reinvestment_supercycle"},
            price_usd=130.0,
        )
        self.assertTrue(res["available"])
        self.assertEqual(res["model_name"], "AI Supercycle FCFF DCF")
        self.assertIsNotNone(res["scenarios"]["base"])
        self.assertIn("market_implied", res["scenarios"])
        # Reverse DCF one-at-a-time outputs present.
        self.assertIn("implied_margin", res)
        self.assertIn("implied_roic", res)
        self.assertIn("classification", res)

    def test_routing_sends_supercycle_to_fcff(self) -> None:
        snapshot = {
            "ticker": "NVDA",
            "totalRevenue": 131_000_000_000,
            "revenueGrowth": 0.50,
            "grossMargins": 0.75,
            "operatingMargins": 0.48,
            "returnOnEquity": 0.45,
            "sharesOutstanding": 24_000_000_000,
            "marketCap": 3_000_000_000_000,
            "beta": 1.6,
            "totalCash": 40_000_000_000,
            "totalDebt": 10_000_000_000,
            "capitalExpenditures": -20_000_000_000,
            "capex_history_5y": [5e9, 7e9, 10e9, 15e9, 20e9],
            "operatingCashflow": 60_000_000_000,
        }
        res = compute_dcf_scenarios(snapshot, price_usd=130.0)
        self.assertEqual(res.get("model_name"), "AI Supercycle FCFF DCF")
        self.assertEqual(res["business_type"], "platform_reinvestment_supercycle")


class TestLedgerFeatureExtraction(unittest.TestCase):
    def test_valuation_features_extracted(self) -> None:
        from types import SimpleNamespace
        from backend.decision_terminal import _valuation_ledger_features
        from backend.schemas import (
            TerminalValuationModel,
            TerminalValuationPanel,
            TerminalFieldProvenance,
        )

        dcf = TerminalValuationModel(
            name="DCF",
            fair_value_usd=240.0,
            available=True,
            implied_growth=0.18,
            implied_margin=0.32,
            implied_roic=0.20,
            margin_of_safety_pct=-14.0,
            market_expectation="high optimism priced in",
            provenance=TerminalFieldProvenance(source="owner_earnings_dcf"),
        )
        panel = TerminalValuationPanel(
            current_price_usd=280.0,
            business_classification="platform_reinvestment_supercycle",
            market_expectation="high optimism priced in",
            models=[dcf],
        )
        payload = SimpleNamespace(valuation=panel)
        feats = _valuation_ledger_features(payload)
        names = {f.name for f in feats}
        self.assertIn("business_classification", names)
        self.assertIn("implied_growth", names)
        self.assertIn("implied_margin", names)
        self.assertIn("implied_roic", names)
        self.assertIn("dcf_base_fair_value", names)
        ig = next(f for f in feats if f.name == "implied_growth")
        self.assertAlmostEqual(ig.value_num, 0.18, places=4)


if __name__ == "__main__":
    unittest.main()
