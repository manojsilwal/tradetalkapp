"""
Phase A lineage tests: every reflection / swarm-history write must carry
``prompt_versions`` + ``registry_snapshot_id`` on its metadata so that a
future SEPL optimizer can tie outcomes back to the exact prompt versions
that produced them (AGP §3.1.2 "auditable lineage").

We bypass the real Chroma/Supabase backends with an in-memory fake that
captures ``add(...)`` calls verbatim.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, Dict, List

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")


class FakeCollection:
    """Captures col.add(...) calls so we can assert on metadata shape."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []

    def add(self, documents, metadatas, ids):
        for doc, meta, _id in zip(documents, metadatas, ids):
            self.rows.append({"doc": doc, "meta": meta, "id": _id})

    def count(self) -> int:
        return len(self.rows)


class _FakeCollectionMixin:
    """Create a KnowledgeStore-like object with _safe_col returning fakes."""

    def setUp(self):  # noqa: D401
        from backend.knowledge_store import KnowledgeStore

        self.collections: Dict[str, FakeCollection] = {}
        ks = KnowledgeStore.__new__(KnowledgeStore)  # skip heavy __init__

        def fake_safe_col(name: str):
            return self.collections.setdefault(name, FakeCollection())

        # KnowledgeStore uses self._safe_col internally; monkey-patch the bound method
        ks._safe_col = fake_safe_col  # type: ignore[method-assign]
        self.ks = ks


def _make_consensus(ticker: str = "AAPL"):
    regime = SimpleNamespace(value="BULL_NORMAL")
    macro_state = SimpleNamespace(market_regime=regime, credit_stress_index=1.02)
    return SimpleNamespace(
        ticker=ticker,
        global_verdict="BUY",
        confidence=0.72,
        global_signal=1,
        macro_state=macro_state,
    )


class TestSwarmReflectionLineage(_FakeCollectionMixin, unittest.TestCase):
    def test_default_call_stamps_unversioned_placeholders(self):
        self.ks.add_swarm_reflection(
            ticker="AAPL", signal=1, verdict="BUY", confidence=0.8,
            price_change_pct=1.2, lesson="test", regime="BULL_NORMAL", correct=True,
        )
        meta = self.collections["swarm_reflections"].rows[0]["meta"]
        self.assertEqual(meta["prompt_versions"], "{}")
        self.assertEqual(meta["agent_version"], "unversioned")
        self.assertEqual(meta["registry_snapshot_id"], "")

    def test_with_kwargs_stamps_json_encoded_versions(self):
        versions = {"bull": "1.0.0", "swarm_reflection_writer": "1.2.3"}
        self.ks.add_swarm_reflection(
            ticker="AAPL", signal=1, verdict="BUY", confidence=0.8,
            price_change_pct=-0.4, lesson="test", regime="BEAR_NORMAL", correct=False,
            prompt_versions=versions,
            agent_version="swarm.v0",
            registry_snapshot_id="deadbeefcafe0000",
        )
        meta = self.collections["swarm_reflections"].rows[0]["meta"]
        self.assertEqual(json.loads(meta["prompt_versions"]), versions)
        self.assertEqual(meta["agent_version"], "swarm.v0")
        self.assertEqual(meta["registry_snapshot_id"], "deadbeefcafe0000")

    def test_legacy_positional_signature_still_works(self):
        try:
            self.ks.add_swarm_reflection(
                "T", 0, "NEUTRAL", 0.5, 0.0, "lesson", "BULL_NORMAL", False,
            )
        except TypeError as e:
            self.fail(f"legacy positional signature broke: {e}")
        self.assertEqual(len(self.collections["swarm_reflections"].rows), 1)


class TestSwarmAnalysisLineage(_FakeCollectionMixin, unittest.TestCase):
    def test_default_call_stamps_placeholders(self):
        self.ks.add_swarm_analysis(_make_consensus())
        meta = self.collections["swarm_history"].rows[0]["meta"]
        self.assertEqual(meta["prompt_versions"], "{}")
        self.assertEqual(meta["agent_version"], "unversioned")
        self.assertEqual(meta["registry_snapshot_id"], "")

    def test_kwargs_stamp_versions_and_snapshot(self):
        versions = {"bull": "1.0.0", "bear": "1.0.0", "moderator": "1.0.0"}
        self.ks.add_swarm_analysis(
            _make_consensus(),
            prompt_versions=versions,
            registry_snapshot_id="abc1234567890def",
        )
        meta = self.collections["swarm_history"].rows[0]["meta"]
        self.assertEqual(json.loads(meta["prompt_versions"]), versions)
        self.assertEqual(meta["registry_snapshot_id"], "abc1234567890def")

    def test_kwargs_never_omit_fields(self):
        # Whether prompt_versions is empty dict or None, meta fields must exist.
        self.ks.add_swarm_analysis(
            _make_consensus(),
            prompt_versions={},
            agent_version=None,
            registry_snapshot_id=None,
        )
        meta = self.collections["swarm_history"].rows[0]["meta"]
        for key in ("prompt_versions", "agent_version", "registry_snapshot_id"):
            self.assertIn(key, meta)


class TestRegistrySnapshotContents(unittest.TestCase):
    """
    Verify the call-site logic in analysis router / daily_pipeline builds a
    non-empty prompt_versions dict from a freshly-seeded registry.
    """

    def setUp(self):
        from backend import resource_registry as rr
        self._tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp_db.close()
        self._orig_db = os.environ.get("RESOURCES_DB_PATH", "")
        os.environ["RESOURCES_DB_PATH"] = self._tmp_db.name
        rr._reset_singleton_for_tests()

        from backend.resource_seeder import seed_resources_if_empty
        self.reg = rr.get_resource_registry()
        seed_resources_if_empty(self.reg)

    def tearDown(self):
        from backend import resource_registry as rr
        rr._reset_singleton_for_tests()
        if self._orig_db:
            os.environ["RESOURCES_DB_PATH"] = self._orig_db
        else:
            os.environ.pop("RESOURCES_DB_PATH", None)
        try:
            os.unlink(self._tmp_db.name)
        except OSError:
            pass

    def test_snapshot_and_versions_are_nonempty(self):
        from backend.resource_registry import ResourceKind
        versions = {r.name: r.version for r in self.reg.list(ResourceKind.PROMPT)}
        self.assertGreaterEqual(len(versions), 15)
        for expected in ("bull", "bear", "moderator", "swarm_analyst"):
            self.assertEqual(versions.get(expected), "1.0.0")
        snapshot = self.reg.snapshot_id()
        self.assertEqual(len(snapshot), 16)


if __name__ == "__main__":
    unittest.main()
