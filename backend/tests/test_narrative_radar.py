"""
Offline tests for the Narrative Rotation Radar (Plan NR-1..NR-4).

Everything here is deterministic and network-free: synthetic close series feed the
feature builder, scoring, lifecycle classifier, the pure scan pass, the SQLite
store, and the Decision-Outcome Ledger emit (backed by a per-test temp DB, per
AGENTS.md). No yfinance / RAG / LLM calls.
"""
from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
os.environ.setdefault("NARRATIVE_RADAR_RAG_ENABLE", "0")  # no RAG in unit tests
os.environ.setdefault("NARRATIVE_RADAR_SMART_MONEY", "0")  # offline unit tests
os.environ.setdefault("NARRATIVE_RADAR_OPTIONS", "0")
os.environ.setdefault("NARRATIVE_RADAR_ACTIVIST", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.narrative_radar import (  # noqa: E402
    engine as nr_engine,
    features as nr_features,
    lifecycle as nr_lifecycle,
    scoring as nr_scoring,
    store as nr_store,
    themes as nr_themes,
)


# ── Synthetic price helpers ──────────────────────────────────────────────────


def _trend(start: float, daily_pct: float, n: int = 260) -> list:
    """A clean compounding price path of n daily closes (newest last)."""
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1.0 + daily_pct / 100.0))
    return out


def _member(closes, market_cap=5e10):
    import pandas as pd
    from backend.narrative_radar import data as nr_data
    n = len(closes)
    df = pd.DataFrame({
        "Close": closes,
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.99 for c in closes],
        "Volume": [1_000_000.0 + i * 1000 for i in range(n)],
    })
    return nr_data.build_member_row("X", df, {"market_cap": market_cap})


# ── Taxonomy ─────────────────────────────────────────────────────────────────


class TestTaxonomy(unittest.TestCase):
    def test_validate(self):
        nr_themes.validate_taxonomy()

    def test_universe_and_members(self):
        self.assertGreater(len(nr_themes.theme_universe()), 30)
        self.assertTrue(nr_themes.theme_members("ai_compute"))
        self.assertIn("ai_compute", nr_themes.KEYWORDS)
        self.assertIn("sector", nr_themes.groups())
        self.assertTrue(nr_themes.theme_members("sector_technology"))
        self.assertEqual(nr_themes.theme_group("pm_gold"), "precious_metals")


# ── Features ─────────────────────────────────────────────────────────────────


class TestFeatures(unittest.TestCase):
    def test_equal_weight_basket_monotonic(self):
        up = nr_features.equal_weight_basket([_trend(100, 0.2), _trend(50, 0.2)])
        self.assertGreater(up[-1], up[0])

    def test_relative_strength_outperformer(self):
        basket = nr_features.equal_weight_basket([_trend(100, 0.30)])
        spy = _trend(100, 0.05)
        rs = nr_features.relative_strength(basket, spy)
        self.assertIsNotNone(rs["rs_ratio"])
        self.assertGreater(rs["rs_ratio"], 1.0)  # basket beat SPY
        self.assertIsNotNone(rs["rs_momentum"])

    def test_build_theme_features_shapes(self):
        members = [_member(_trend(100, 0.20)), _member(_trend(80, 0.18)), _member(_trend(60, 0.22))]
        spy = _trend(100, 0.05)
        feat = nr_features.build_theme_features("ai_compute", members, spy)
        self.assertEqual(feat["theme_id"], "ai_compute")
        self.assertEqual(feat["member_count"], 3)
        self.assertIsNotNone(feat["rs_ratio"])
        self.assertIsNotNone(feat["pct_above_50dma"])
        # volume_zscore populated when OHLCV is present on members
        self.assertIsNotNone(feat["volume_zscore"])

    def test_empty_members_degrade(self):
        feat = nr_features.build_theme_features("x", [], _trend(100, 0.05))
        self.assertIsNone(feat["rs_ratio"])
        self.assertEqual(feat["member_count"], 0)


# ── Scoring ──────────────────────────────────────────────────────────────────


def _strong_feat(theme_id):
    members = [_member(_trend(100, 0.30)), _member(_trend(80, 0.28)), _member(_trend(60, 0.32))]
    return nr_features.build_theme_features(theme_id, members, _trend(100, 0.05))


def _weak_feat(theme_id):
    members = [_member(_trend(100, -0.20)), _member(_trend(80, -0.18)), _member(_trend(60, -0.25))]
    return nr_features.build_theme_features(theme_id, members, _trend(100, 0.10))


class TestScoring(unittest.TestCase):
    def test_strong_outranks_weak_market_confirmation(self):
        rows = [_strong_feat("ai_compute"), _weak_feat("cybersecurity")]
        ctx = nr_scoring.ThemeContext.build(rows)
        strong = nr_scoring.score_theme(rows[0], ctx)
        weak = nr_scoring.score_theme(rows[1], ctx)
        self.assertGreater(
            strong["scores"]["market_confirmation_score"],
            weak["scores"]["market_confirmation_score"],
        )

    def test_deferred_families_are_none(self):
        rows = [_strong_feat("ai_compute")]
        ctx = nr_scoring.ThemeContext.build(rows)
        s = nr_scoring.score_theme(rows[0], ctx)["scores"]
        for fam in ("institutional_conviction_score", "retail_saturation_score",
                    "narrative_reality_alignment_score", "productization_score", "macro_tailwind_score"):
            self.assertIsNone(s[fam])

    def test_confidence_capped_without_all_families(self):
        rows = [_strong_feat("ai_compute")]
        ctx = nr_scoring.ThemeContext.build(rows)
        scored = nr_scoring.score_theme(rows[0], ctx)
        # Only 2 of ~7 families wired → confidence should not be "High".
        self.assertNotEqual(scored["confidence_level"], "High")
        self.assertIn(scored["confidence_level"], ("Low", "Medium"))


# ── Lifecycle classifier ─────────────────────────────────────────────────────


class TestLifecycle(unittest.TestCase):
    def test_low_confidence_fallback(self):
        phase = nr_lifecycle.classify_theme_phase({}, confidence_score=10.0)
        self.assertEqual(phase, nr_lifecycle.LOW_CONFIDENCE_WATCHLIST)

    def test_exit_beats_distribution(self):
        scores = {"theme_exit_risk_score": 90, "theme_distribution_risk_score": 90}
        self.assertEqual(nr_lifecycle.classify_theme_phase(scores, 80), nr_lifecycle.EXIT_ROTATION_AWAY)

    def test_acceleration(self):
        scores = {"theme_acceleration_score": 80, "theme_exit_risk_score": 10, "theme_distribution_risk_score": 10}
        self.assertEqual(nr_lifecycle.classify_theme_phase(scores, 70), nr_lifecycle.ACCELERATION)

    def test_recommendation_label_is_compliance_safe(self):
        label = nr_lifecycle.recommendation_label(nr_lifecycle.EXIT_ROTATION_AWAY)
        self.assertNotIn("sell", label.lower())
        self.assertEqual(label, "Exit / Avoid Chase")


# ── Pure scan pass (engine.assemble_theme_rows) ──────────────────────────────


class TestAssembleThemeRows(unittest.TestCase):
    def _rows(self):
        feats = [
            _strong_feat("ai_compute"),
            _weak_feat("cybersecurity"),
            _strong_feat("power_infra"),
        ]
        return nr_engine.assemble_theme_rows(feats)

    def test_rows_have_phase_scores_and_explanation(self):
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        for r in rows:
            self.assertIn("lifecycle_phase", r)
            self.assertIn("market_confirmation_score", r["scores"])
            self.assertIn("summary", r)
            self.assertIn("disclaimer", r["explanation"])
            self.assertTrue(r["explanation"]["top_positive_drivers"])

    def test_sorted_by_acceleration_desc(self):
        rows = self._rows()
        accels = [r["scores"].get("theme_acceleration_score") or 0 for r in rows]
        self.assertEqual(accels, sorted(accels, reverse=True))


# ── Store round-trip ─────────────────────────────────────────────────────────


class TestStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["NARRATIVE_RADAR_DB_PATH"] = os.path.join(self._tmp.name, "nr.db")

    def tearDown(self):
        os.environ.pop("NARRATIVE_RADAR_DB_PATH", None)

    def test_persist_and_load(self):
        feats = [_strong_feat("ai_compute"), _weak_feat("cybersecurity")]
        rows = nr_engine.assemble_theme_rows(feats)
        nr_store.persist_snapshot("snap1", rows, theme_count=2, skipped=0)
        meta = nr_store.latest_snapshot_meta()
        self.assertEqual(meta["snapshot_id"], "snap1")
        loaded = nr_store.load_snapshot_rows("snap1")
        self.assertEqual(len(loaded), len(rows))
        one = nr_store.load_row("snap1", "ai_compute")
        self.assertEqual(one["theme_id"], "ai_compute")


# ── Ledger emit (temp DB) ────────────────────────────────────────────────────


class TestLedgerEmit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "d.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        dl._reset_singleton_for_tests()

    def tearDown(self):
        dl._reset_singleton_for_tests()
        os.environ.pop("DECISIONS_DB_PATH", None)

    def test_emit_lands_in_ledger(self):
        from backend.narrative_radar import ledger as nr_ledger

        feats = [_strong_feat("ai_compute"), _weak_feat("cybersecurity")]
        rows = nr_engine.assemble_theme_rows(feats)
        emitted = nr_ledger.emit_decisions(rows, "snap-test")
        self.assertEqual(emitted, len(rows))

        decisions = dl.get_ledger().list_decisions_since(0.0, decision_type="theme_phase")
        self.assertEqual(len(decisions), len(rows))
        # The ledger normalizes the symbol (theme slug) to upper-case.
        symbols = {(d.symbol or "").upper() for d in decisions}
        self.assertIn("AI_COMPUTE", symbols)
        for d in decisions:
            self.assertIn(d.verdict, ("BUY", "SELL", "HOLD"))


# ── NR-5..NR-9: signal families, aggregators, alerts, backtests ──────────────


def _full_signals():
    """All eight families available → should reach High confidence."""
    return {
        "institutional": {
            "available": True, "ownership_breadth_pct": 80.0, "net_position_change_pct": 12.0,
            "new_position_ratio": 0.2, "concentration_pct": 30.0,
        },
        "etf_flow": {"available": True, "flow_score": 70.0, "flow_acceleration_pct": 65.0},
        "productization": {"available": True, "filings_count": 4, "issuer_count": 3,
                           "aum_growth_pct": 30.0, "launch_after_runup": False},
        "narrative": {"available": True, "mention_velocity_pct": 70.0, "attention_percentile": 65.0, "sentiment": 0.4},
        "retail": {"available": True, "social_velocity_pct": 60.0, "media_freq_pct": 55.0,
                   "youtube_score": 50.0, "buy_now_density": 3.0},
        "reality": {"available": True, "revenue_accel_pct": 12.0, "capex_growth_pct": 25.0,
                    "guidance_revision": 1.0, "keyword_growth_pct": 30.0, "estimate_revision_pct": 8.0},
        "macro": {"available": True, "regime_fit_pct": 80.0},
    }


class TestSignalFamilies(unittest.TestCase):
    def test_full_signals_reach_high_confidence(self):
        feats = [_strong_feat("ai_compute"), _weak_feat("cybersecurity")]
        ctx = nr_scoring.ThemeContext.build(feats)
        scored = nr_scoring.score_theme(feats[0], ctx, _full_signals())
        self.assertEqual(scored["confidence_level"], "High")
        s = scored["scores"]
        for fam in ("institutional_conviction_score", "productization_score", "narrative_score",
                    "retail_saturation_score", "narrative_reality_alignment_score", "macro_tailwind_score"):
            self.assertIsNotNone(s[fam])

    def test_institutional_none_without_real_proxy(self):
        # Pure price fast-proxy alone must not create an institutional family.
        self.assertIsNone(nr_scoring.institutional_conviction_score(None, 70.0, None))
        # 13F present → available.
        self.assertIsNotNone(nr_scoring.institutional_conviction_score(
            {"available": True, "ownership_breadth_pct": 70.0}, 60.0, None))
        # ETF flow present → available.
        self.assertIsNotNone(nr_scoring.institutional_conviction_score(
            None, 60.0, {"available": True, "flow_score": 70.0}))
        # Weeks-fresh smart-money proxy → available without 13F.
        self.assertIsNotNone(nr_scoring.institutional_conviction_score(
            None, 60.0, None, {"available": True, "accumulation_score": 72.0}))

    def test_retail_saturation_monotonic(self):
        hi = nr_scoring.retail_saturation_score({"available": True, "social_velocity_pct": 90,
                                                 "media_freq_pct": 90, "youtube_score": 90, "buy_now_density": 9})
        lo = nr_scoring.retail_saturation_score({"available": True, "social_velocity_pct": 10,
                                                 "media_freq_pct": 10, "youtube_score": 10, "buy_now_density": 0})
        self.assertGreater(hi, lo)

    def test_deferred_none_when_signals_absent(self):
        feats = [_strong_feat("ai_compute")]
        ctx = nr_scoring.ThemeContext.build(feats)
        s = nr_scoring.score_theme(feats[0], ctx)["scores"]
        self.assertIsNone(s["institutional_conviction_score"])
        self.assertIsNone(s["productization_score"])


class TestInstitutionalAggregation(unittest.TestCase):
    def test_aggregate_holdings(self):
        from backend.narrative_radar import institutional as inst
        rows = [
            {"report_period": "2026Q1", "ticker": "NVDA", "fund_id": "f1", "shares": 100, "market_value_usd": 1000},
            {"report_period": "2026Q1", "ticker": "AMD", "fund_id": "f2", "shares": 50, "market_value_usd": 400},
            {"report_period": "2025Q4", "ticker": "NVDA", "fund_id": "f1", "shares": 80, "market_value_usd": 800},
        ]
        out = inst.aggregate_holdings(rows, ["NVDA", "AMD", "AVGO"])
        self.assertTrue(out["available"])
        self.assertEqual(out["latest_period"], "2026Q1")
        self.assertAlmostEqual(out["ownership_breadth_pct"], round(100 * 2 / 3, 2))
        # NVDA shares 80→100 = +25%
        self.assertAlmostEqual(out["net_position_change_pct"], 25.0, places=1)

    def test_aggregate_holdings_empty(self):
        from backend.narrative_radar import institutional as inst
        self.assertFalse(inst.aggregate_holdings([], ["NVDA"])["available"])


class TestProductization(unittest.TestCase):
    def test_theme_productization_with_fake_counter(self):
        from backend.connectors import etf_filings
        calls = {}
        def fake(phrase):
            calls[phrase] = calls.get(phrase, 0) + 1
            return {"total": 3, "issuers": 2}
        out = etf_filings.theme_productization(["ai infrastructure", "GPU"], counter=fake)
        self.assertTrue(out["available"])
        self.assertEqual(out["filings_count"], 6)
        self.assertEqual(out["issuer_count"], 2)

    def test_productization_score_present(self):
        out = {"available": True, "filings_count": 4, "issuer_count": 3, "aum_growth_pct": 30, "launch_after_runup": False}
        self.assertIsNotNone(nr_scoring.productization_score(out, None))


class TestEtfFlows(unittest.TestCase):
    def test_flow_from_series(self):
        from backend.connectors import etf_flows
        closes = [100 + i for i in range(40)]
        volumes = [1_000_000] * 20 + [3_000_000] * 20  # rising volume
        out = etf_flows.flow_from_series(closes, volumes)
        self.assertTrue(out["available"])
        self.assertGreater(out["flow_score"], 50)

    def test_flow_insufficient(self):
        from backend.connectors import etf_flows
        self.assertFalse(etf_flows.flow_from_series([1, 2, 3], [1, 2, 3])["available"])


class TestSignalHelpers(unittest.TestCase):
    def test_keyword_hits_and_buy_now(self):
        from backend.narrative_radar import signals
        titles = ["NVDA AI infrastructure boom", "best stocks to buy now", "random sports headline"]
        self.assertEqual(signals.keyword_hits(titles, ["ai infrastructure"]), 1)
        self.assertGreater(signals.buy_now_density(titles), 0)

    def test_reality_from_members(self):
        from backend.narrative_radar import signals
        rows = [{"available": True, "qoq_revenue_accel_pct": 10.0, "qoq_revenue_growth_pct": 20.0},
                {"available": True, "qoq_revenue_accel_pct": 6.0, "qoq_revenue_growth_pct": 15.0}]
        out = signals.reality_from_members(rows)
        self.assertTrue(out["available"])
        self.assertIsNotNone(out["revenue_accel_pct"])

    def test_macro_fit(self):
        from backend.narrative_radar import signals
        hot = signals.macro_fit("ai_compute", "ai_capex_supercycle")
        self.assertTrue(hot["available"])
        self.assertGreater(hot["regime_fit_pct"], 50)
        self.assertFalse(signals.macro_fit("ai_compute", None)["available"])


class TestAlerts(unittest.TestCase):
    def test_exit_alert_fires(self):
        from backend.narrative_radar import alerts
        rows = [{"theme_id": "x", "theme_label": "X", "confidence_score": 80,
                 "scores": {"theme_exit_risk_score": 88}}]
        types = {a["alert_type"] for a in alerts.generate_alerts(rows)}
        self.assertIn(alerts.EXIT_ALERT, types)

    def test_emerging_and_none_safe(self):
        from backend.narrative_radar import alerts
        rows = [{"theme_id": "y", "theme_label": "Y", "confidence_score": 60,
                 "scores": {"theme_formation_score": 70, "retail_saturation_score": 20}}]
        a = alerts.generate_alerts(rows)
        self.assertIn(alerts.EMERGING_THEME, {x["alert_type"] for x in a})
        # Missing scores must not raise.
        self.assertIsInstance(alerts.generate_alerts([{"theme_id": "z", "scores": {}}]), list)


class TestAssembleWithSignals(unittest.TestCase):
    def test_signals_threaded_through(self):
        feats = [_strong_feat("ai_compute"), _weak_feat("cybersecurity")]
        sigs = {"ai_compute": _full_signals()}
        rows = nr_engine.assemble_theme_rows(feats, sigs)
        ai = next(r for r in rows if r["theme_id"] == "ai_compute")
        self.assertEqual(ai["confidence_level"], "High")
        self.assertIsNotNone(ai["scores"]["institutional_conviction_score"])


class TestTimeline(unittest.TestCase):
    def test_phase_timeline_emits_transitions_only(self):
        from backend.narrative_radar import timeline as tl
        history = [
            {"created_at": 100.0, "lifecycle_phase": "EARLY_ACCUMULATION", "confidence": 0.6},
            {"created_at": 200.0, "lifecycle_phase": "EARLY_ACCUMULATION", "confidence": 0.6},  # no change
            {"created_at": 300.0, "lifecycle_phase": "ACCELERATION", "confidence": 0.7},
            {"created_at": 400.0, "lifecycle_phase": "DISTRIBUTION_RISK", "confidence": 0.8},
        ]
        events = tl.phase_timeline_from_history(history)
        # 3 distinct phases → 3 events, newest first.
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["phase"], "DISTRIBUTION_RISK")
        self.assertEqual(events[-1]["phase"], "EARLY_ACCUMULATION")
        self.assertEqual(events[-1]["event_type"], "PHASE_SET")
        self.assertEqual(events[0]["event_type"], "PHASE_TRANSITION")

    def test_phase_timeline_empty(self):
        from backend.narrative_radar import timeline as tl
        self.assertEqual(tl.phase_timeline_from_history([]), [])


class TestDataFreshness(unittest.TestCase):
    def test_freshness_marks_pending_and_lag(self):
        from backend.narrative_radar import explain
        fresh = explain.data_freshness(["market_confirmation", "breadth_quality"])
        self.assertIn("pending", fresh["institutional_13f"])
        self.assertIn("pending", fresh["etf_productization"])
        full = explain.data_freshness(["institutional_conviction", "productization"])
        self.assertIn("13F", full["institutional_13f"])
        self.assertNotIn("pending", full["etf_productization"])


class TestDurableSnapshot(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DURABLE_SNAPSHOT_DB_PATH"] = os.path.join(self._tmp.name, "page.db")

    def tearDown(self):
        os.environ.pop("DURABLE_SNAPSHOT_DB_PATH", None)

    def test_put_get_roundtrip(self):
        from backend import durable_snapshot as ds
        self.assertTrue(ds.active())
        ok = ds.put("k", "snap1", 123.0, {"rows": [{"theme_id": "x"}]}, {"m": 1})
        self.assertTrue(ok)
        got = ds.get_latest("k")
        self.assertEqual(got["snapshot_id"], "snap1")
        self.assertEqual(got["payload"]["rows"][0]["theme_id"], "x")
        self.assertIsNone(ds.get_latest("other"))

    def test_inactive_without_config(self):
        from backend import durable_snapshot as ds
        os.environ.pop("DURABLE_SNAPSHOT_DB_PATH", None)
        # No Postgres, no explicit path → inactive (local stores remain source of truth).
        self.assertFalse(ds.active())
        self.assertFalse(ds.put("k", "s", 1.0, {}, {}))
        self.assertIsNone(ds.get_latest("k"))


class TestColdStartDurability(unittest.TestCase):
    """A snapshot must survive losing the local SQLite file (Cloud Run cold start)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._nr_db = os.path.join(self._tmp.name, "nr.db")
        os.environ["NARRATIVE_RADAR_DB_PATH"] = self._nr_db
        os.environ["DURABLE_SNAPSHOT_DB_PATH"] = os.path.join(self._tmp.name, "page.db")

    def tearDown(self):
        os.environ.pop("NARRATIVE_RADAR_DB_PATH", None)
        os.environ.pop("DURABLE_SNAPSHOT_DB_PATH", None)

    def test_snapshot_survives_local_db_loss(self):
        feats = [_strong_feat("ai_compute"), _weak_feat("cybersecurity")]
        rows = nr_engine.assemble_theme_rows(feats)
        nr_store.persist_snapshot("snapX", rows, theme_count=2, skipped=0)
        # Simulate a fresh instance: the local SQLite file is gone.
        if os.path.exists(self._nr_db):
            os.remove(self._nr_db)
        meta = nr_store.latest_snapshot_meta()
        self.assertIsNotNone(meta)
        self.assertEqual(meta["snapshot_id"], "snapX")
        loaded = nr_store.load_snapshot_rows("snapX")
        self.assertEqual(len(loaded), len(rows))
        self.assertIsNotNone(nr_store.load_row("snapX", "ai_compute"))

    def test_alerts_survive_local_db_loss(self):
        from backend.narrative_radar import alerts as nr_alerts
        feats = [{"theme_id": "z", "theme_label": "Z", "confidence_score": 80,
                  "scores": {"theme_exit_risk_score": 88}}]
        a = nr_alerts.generate_alerts(feats)
        nr_store.persist_alerts("snapA", a)
        if os.path.exists(self._nr_db):
            os.remove(self._nr_db)
        loaded = nr_store.load_alerts("snapA")
        self.assertEqual(len(loaded), len(a))


class TestSmartMoneyAndDivergence(unittest.TestCase):
    def test_relative_volume_zscore(self):
        from backend.narrative_radar import smart_money as sm
        base = [1_000_000.0] * 80
        spike = base + [3_000_000.0] * 21
        z = sm.relative_volume_zscore(spike)
        self.assertIsNotNone(z)
        self.assertGreater(z, 0)

    def test_etf_accumulation_signal(self):
        import pandas as pd
        from backend.narrative_radar import smart_money as sm
        n = 90
        closes = [100 + i * 0.1 for i in range(n)]
        df = pd.DataFrame({
            "Close": closes,
            "High": [c + 0.5 for c in closes],
            "Low": [c - 0.5 for c in closes],
            "Volume": [1_000_000.0] * n,
        })
        out = sm.etf_accumulation_signal(df)
        self.assertTrue(out["available"])
        self.assertIsNotNone(out["accumulation_score"])

    def test_divergence_stealth_vs_hype(self):
        from backend.narrative_radar import smart_money as sm
        stealth = sm.smart_money_divergence_score(80.0, -0.6, 30.0)
        hype = sm.smart_money_divergence_score(25.0, 0.8, 85.0)
        self.assertIsNotNone(stealth)
        self.assertIsNotNone(hype)
        self.assertGreater(stealth, hype)

    def test_retail_direction_from_titles(self):
        from backend.narrative_radar import signals as nr_signals
        buy = nr_signals.retail_direction_from_titles(["best stocks to buy now", "must buy gold"])
        sell = nr_signals.retail_direction_from_titles(["time to sell", "bubble crash incoming"])
        self.assertGreater(buy, 0)
        self.assertLess(sell, 0)

    def test_group_scoped_context(self):
        sector_feat = nr_features.build_theme_features(
            "sector_technology",
            [_member(_trend(100, 0.05))],
            _trend(100, 0.05),
        )
        ai_feat = _strong_feat("ai_compute")
        contexts = nr_scoring.ThemeContext.build_by_group([sector_feat, ai_feat])
        self.assertIn("sector", contexts)
        self.assertIn("ai_theme", contexts)
        # Single-theme group → sole member ranks at 100th percentile.
        ctx_sector = contexts["sector"]
        self.assertEqual(ctx_sector.rank("rs_ratio", sector_feat["rs_ratio"]), 100.0)


class TestActivistFilings(unittest.TestCase):
    def test_parse_atom_activist_tickers(self):
        from datetime import datetime, timezone
        from backend.narrative_radar import activist_filings as af

        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>SC 13D - NVIDIA CORP (0001045810) (Filer)</title>
    <updated>2026-06-01T12:00:00-04:00</updated>
  </entry>
</feed>"""
        cutoff = datetime(2026, 5, 1, tzinfo=timezone.utc)
        tickers = af.parse_atom_activist_tickers(
            xml,
            cik_to_ticker={"0001045810": "NVDA"},
            cutoff=cutoff,
        )
        self.assertIn("NVDA", tickers)

    def test_activist_signal_hits_members(self):
        from backend.narrative_radar import activist_filings as af

        os.environ["NARRATIVE_RADAR_ACTIVIST"] = "1"
        out = af.activist_signal_8w(["NVDA", "AMD"], recent_tickers={"NVDA", "INTC"})
        self.assertTrue(out["available"])
        self.assertEqual(out["activist_filing_count"], 1)
        self.assertIn("NVDA", out["activist_hit_tickers"])

    def test_activist_signal_no_hits(self):
        from backend.narrative_radar import activist_filings as af

        os.environ["NARRATIVE_RADAR_ACTIVIST"] = "1"
        out = af.activist_signal_8w(["XLK"], recent_tickers={"NVDA"})
        self.assertFalse(out["available"])


class TestDivergenceAlerts(unittest.TestCase):
    def test_stealth_accumulation_alert(self):
        from backend.narrative_radar import alerts as nr_alerts
        row = {
            "theme_id": "sector_energy",
            "theme_label": "Energy",
            "group": "sector",
            "confidence_score": 60,
            "scores": {
                "smart_money_divergence_score": 80.0,
                "retail_narrative_direction_score": 35.0,
            },
        }
        types = {a["alert_type"] for a in nr_alerts.generate_alerts([row])}
        self.assertIn(nr_alerts.STEALTH_ACCUMULATION, types)

    def test_distribution_into_hype_alert(self):
        from backend.narrative_radar import alerts as nr_alerts
        row = {
            "theme_id": "pm_gold",
            "theme_label": "Gold",
            "group": "precious_metals",
            "confidence_score": 60,
            "scores": {
                "smart_money_divergence_score": 20.0,
                "retail_narrative_direction_score": 75.0,
                "retail_saturation_score": 80.0,
            },
        }
        types = {a["alert_type"] for a in nr_alerts.generate_alerts([row])}
        self.assertIn(nr_alerts.DISTRIBUTION_INTO_HYPE, types)


if __name__ == "__main__":
    unittest.main()
