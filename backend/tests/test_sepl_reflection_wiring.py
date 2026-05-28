"""SEPL reflection source factory and composite merge behavior."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend.sepl import (  # noqa: E402
    CompositeReflectionSource,
    DecisionLedgerReflectionSource,
    KnowledgeStoreReflectionSource,
    build_sepl_reflection_source,
    sepl_reflection_source_mode,
)


class TestCompositeReflectionSource(unittest.TestCase):
    def test_dedupes_by_decision_id_prefers_first_source(self) -> None:
        a = MagicMock()
        b = MagicMock()
        a.fetch_recent_reflections.return_value = [
            {
                "doc": "ledger row",
                "meta": {
                    "decision_id": "abc",
                    "date": "2026-05-01",
                    "prompt_versions": '{"bull":"v1"}',
                    "effectiveness_score": 1.0,
                },
            }
        ]
        b.fetch_recent_reflections.return_value = [
            {
                "doc": "chroma row",
                "meta": {
                    "decision_id": "abc",
                    "date": "2026-05-02",
                    "prompt_versions": '{"bull":"v0"}',
                    "effectiveness_score": 0.0,
                },
            },
            {
                "doc": "chroma only",
                "meta": {"date": "2026-04-30", "prompt_versions": '{"bear":"v1"}'},
            },
        ]
        merged = CompositeReflectionSource([a, b]).fetch_recent_reflections(limit=10)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["doc"], "ledger row")
        self.assertEqual(merged[1]["doc"], "chroma only")

    def test_build_factory_respects_env(self) -> None:
        ks = MagicMock()
        os.environ["SEPL_REFLECTION_SOURCE"] = "ledger"
        self.assertIsInstance(build_sepl_reflection_source(ks), DecisionLedgerReflectionSource)
        os.environ["SEPL_REFLECTION_SOURCE"] = "chroma"
        self.assertIsInstance(build_sepl_reflection_source(ks), KnowledgeStoreReflectionSource)
        os.environ["SEPL_REFLECTION_SOURCE"] = "composite"
        src = build_sepl_reflection_source(ks)
        self.assertIsInstance(src, CompositeReflectionSource)
        os.environ.pop("SEPL_REFLECTION_SOURCE", None)
        self.assertEqual(sepl_reflection_source_mode(), "composite")


class TestLearningHealthEndpoint(unittest.TestCase):
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

    def test_learning_health_counts(self) -> None:
        import asyncio

        from backend.routers.debug import learning_health_endpoint

        dl.emit_decision(
            decision_type="swarm_factor",
            symbol="AAPL",
            horizon_hint="1d",
            verdict="BUY",
            output={"ok": True},
            prompt_versions={"bull": "v1"},
        )
        body = asyncio.run(learning_health_endpoint())
        self.assertGreaterEqual(body["ledger"]["table_counts"]["decision_events"], 1)
        self.assertIn("reflection_source", body["sepl"])
