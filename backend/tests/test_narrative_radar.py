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
    from backend.narrative_radar import data as nr_data
    return nr_data.build_member_row("X", closes, {"market_cap": market_cap})


# ── Taxonomy ─────────────────────────────────────────────────────────────────


class TestTaxonomy(unittest.TestCase):
    def test_validate(self):
        nr_themes.validate_taxonomy()

    def test_universe_and_members(self):
        self.assertGreater(len(nr_themes.theme_universe()), 30)
        self.assertTrue(nr_themes.theme_members("ai_compute"))
        self.assertIn("ai_compute", nr_themes.KEYWORDS)


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
        self.assertIsNone(feat["volume_zscore"])  # never fabricated

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


if __name__ == "__main__":
    unittest.main()
