"""
Unit + integration tests for :mod:`backend.decision_ledger`.

Covers the three supported backends (sqlite, null, supabase-with-fake-client),
the public convenience API, CORAL dual-write, and contract-validator sink
wiring.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any, Dict, List

os.environ.setdefault("RATE_LIMIT_ENABLED", "0")

from backend import decision_ledger as dl  # noqa: E402
from backend import contract_validator as cv  # noqa: E402


class _LedgerBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["DECISIONS_DB_PATH"] = os.path.join(self._tmp.name, "d.db")
        os.environ["DECISION_LEDGER_ENABLE"] = "1"
        os.environ["DECISION_BACKEND"] = "sqlite"
        dl._reset_singleton_for_tests()
        cv._reset_singleton_for_tests()

    def tearDown(self) -> None:
        dl._reset_singleton_for_tests()
        cv._reset_singleton_for_tests()
        os.environ.pop("DECISIONS_DB_PATH", None)


# ── SQLite backend ───────────────────────────────────────────────────────────


class TestSQLiteLedgerBackendHappyPath(_LedgerBase):
    def test_emit_and_round_trip(self) -> None:
        did = dl.emit_decision(
            decision_type="swarm",
            output={"verdict": "BUY", "confidence": 0.7, "rationale": "uptrend"},
            symbol="MSFT",
            horizon_hint="1d",
            model="gemma-4",
            prompt_versions={"swarm_synthesizer": "1.0.0"},
            registry_snapshot_id="abc123",
            verdict="BUY",
            confidence=0.7,
            source_route="agents.py::AgentPair.run",
        )
        self.assertEqual(len(did), 32)  # uuid4 hex

        ev = dl.get_ledger().get_decision(did)
        self.assertIsNotNone(ev)
        self.assertEqual(ev.decision_type, "swarm")
        self.assertEqual(ev.symbol, "MSFT")  # uppercased
        self.assertEqual(ev.verdict, "BUY")
        self.assertEqual(ev.output["rationale"], "uptrend")
        self.assertEqual(ev.prompt_versions, {"swarm_synthesizer": "1.0.0"})

    def test_attach_evidence_and_features_inline(self) -> None:
        did = dl.emit_decision(
            decision_type="swarm",
            output={"verdict": "SELL"},
            symbol="tsla",
            verdict="SELL",
            evidence=[
                dl.EvidenceRef(chunk_id="c1", collection="swarm_history", relevance=0.9, rank=0),
                dl.EvidenceRef(chunk_id="c2", collection="stock_profiles", relevance=0.7, rank=1),
            ],
            features=[
                dl.FeatureValue(name="pe", value_num=25.4, regime="BULL_NORMAL"),
                dl.FeatureValue(name="regime", value_str="BULL_NORMAL", regime="BULL_NORMAL"),
            ],
        )
        stats = dl.get_ledger().stats()
        self.assertEqual(stats["decision_events"], 1)
        self.assertEqual(stats["decision_evidence"], 2)
        self.assertEqual(stats["feature_snapshots"], 2)

        # Empty refs lists are a safe no-op
        self.assertEqual(dl.attach_evidence(did, []), 0)
        self.assertEqual(dl.record_features(did, []), 0)

        # chunks with blank ids are skipped without raising
        self.assertEqual(
            dl.attach_evidence(did, [dl.EvidenceRef(chunk_id="")]), 0
        )

    def test_record_outcome_unique_per_decision_horizon_metric(self) -> None:
        did = dl.emit_decision(
            decision_type="swarm",
            output={"verdict": "BUY"},
            symbol="NVDA",
            verdict="BUY",
        )
        first = dl.record_outcome(
            dl.OutcomeObservation(
                decision_id=did,
                horizon="1d",
                metric="price_return_pct",
                value=2.5,
                as_of_ts=0.0,
                benchmark="SPY",
                excess_return=1.0,
                correct=True,
                label_source="yfinance",
            )
        )
        self.assertTrue(first)
        # Re-record same (did,horizon,metric) — MUST NOT crash; UPSERT overwrites.
        second = dl.record_outcome(
            dl.OutcomeObservation(
                decision_id=did,
                horizon="1d",
                metric="price_return_pct",
                value=3.3,
                as_of_ts=0.0,
                correct=True,
                label_source="yfinance",
            )
        )
        self.assertTrue(second)
        self.assertEqual(dl.get_ledger().stats()["outcome_observations"], 1)

    def test_list_decisions_since_filters_by_type(self) -> None:
        dl.emit_decision(decision_type="swarm", output={"verdict": "BUY"}, verdict="BUY")
        dl.emit_decision(decision_type="debate", output={"verdict": "SELL"}, verdict="SELL")
        dl.emit_decision(decision_type="chat_tool", output={"answer": "ok"})

        swarm = dl.get_ledger().list_decisions_since(0.0, decision_type="swarm")
        self.assertEqual(len(swarm), 1)
        self.assertEqual(swarm[0].decision_type, "swarm")

        everything = dl.get_ledger().list_decisions_since(0.0)
        self.assertEqual(len(everything), 3)

    def test_ungraded_decisions_for_horizon(self) -> None:
        a = dl.emit_decision(
            decision_type="swarm", output={"verdict": "BUY"}, verdict="BUY",
            created_at=1000.0,
        )
        b = dl.emit_decision(
            decision_type="swarm", output={"verdict": "SELL"}, verdict="SELL",
            created_at=1500.0,
        )
        c = dl.emit_decision(
            decision_type="swarm", output={"verdict": "NEUTRAL"}, verdict="NEUTRAL",
            created_at=3000.0,  # too young for horizon
        )
        # Grade a only
        dl.record_outcome(
            dl.OutcomeObservation(
                decision_id=a, horizon="1d", metric="price_return_pct",
                value=0.5, as_of_ts=1100.0,
            )
        )
        ungraded = dl.get_ledger().ungraded_decisions_for_horizon(
            "1d", older_than_ts=2000.0
        )
        ids = {d.decision_id for d in ungraded}
        # b is elderly AND ungraded -> picked up; a is graded; c is too young.
        self.assertEqual(ids, {b})

    def test_write_path_never_raises_on_bad_decision_id_fk(self) -> None:
        # Attaching evidence to an unknown decision_id *shouldn't* raise in
        # SQLite even with FK=ON (the parent row just doesn't exist) — but if
        # it ever did, the wrapper must still return 0 instead of crashing.
        n = dl.attach_evidence("no-such-id", [dl.EvidenceRef(chunk_id="x")])
        # 0 if FK blocks, 1 if the insert goes through — either is acceptable;
        # the guarantee is "never raise."
        self.assertIn(n, (0, 1))


class TestLedgerCoralDualWrite(_LedgerBase):
    def test_emit_decision_writes_coral_handoff_event(self) -> None:
        captured: List[Any] = []

        # Patch coral_hub.log_handoff_event to record calls without touching the
        # real progress.db from this test's temp dir.
        from backend import coral_hub
        original = coral_hub.log_handoff_event
        coral_hub.log_handoff_event = lambda event_type, payload: captured.append(  # type: ignore[assignment]
            (event_type, dict(payload))
        ) or 1

        try:
            did = dl.emit_decision(
                decision_type="debate",
                output={"verdict": "STRONG BUY"},
                symbol="AAPL",
                verdict="STRONG BUY",
                confidence=0.82,
                model="gemma-4",
                source_route="debate_agents.run_moderator",
                horizon_hint="5d",
            )
        finally:
            coral_hub.log_handoff_event = original  # type: ignore[assignment]

        self.assertEqual(len(captured), 1)
        event_type, payload = captured[0]
        self.assertEqual(event_type, "decision_emitted")
        self.assertEqual(payload["decision_id"], did)
        self.assertEqual(payload["decision_type"], "debate")
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertEqual(payload["verdict"], "STRONG BUY")
        self.assertEqual(payload["horizon_hint"], "5d")


# ── Null backend ────────────────────────────────────────────────────────────


class TestNullLedger(_LedgerBase):
    def test_flag_off_returns_null_backend(self) -> None:
        os.environ["DECISION_LEDGER_ENABLE"] = "0"
        dl._reset_singleton_for_tests()
        backend = dl.get_ledger()
        self.assertIsInstance(backend, dl.NullLedgerBackend)
        self.assertEqual(backend.name, "null")

    def test_null_backend_swallows_every_write_returns_still_work(self) -> None:
        os.environ["DECISION_LEDGER_ENABLE"] = "0"
        dl._reset_singleton_for_tests()
        did = dl.emit_decision(
            decision_type="swarm", output={"verdict": "BUY"}, verdict="BUY"
        )
        # Ledger is a no-op but we still get an id back, so calling code can
        # proceed and stamp it on return payloads without branching.
        self.assertEqual(len(did), 32)
        self.assertEqual(dl.attach_evidence(did, [dl.EvidenceRef(chunk_id="c1")]), 0)
        self.assertIsNone(dl.get_ledger().get_decision(did))
        self.assertEqual(dl.get_ledger().list_decisions_since(0.0), [])


# ── Supabase backend with fake client ───────────────────────────────────────


class _FakeSupabaseResult:
    def __init__(self, data: List[Dict[str, Any]] | None = None, count: int = 0):
        self.data = data or []
        self.count = count


class _FakeSupabaseQuery:
    """Chainable, records every call so tests can assert payload shapes."""

    def __init__(self, tbl: str, journal: Dict[str, List[Any]], data_store: Dict[str, List[Dict[str, Any]]]):
        self._tbl = tbl
        self._journal = journal
        self._data_store = data_store
        self._filters: List[tuple] = []
        self._last_payload: Any = None
        self._op: str = ""

    # write ops
    def upsert(self, payload, on_conflict: str = ""):  # type: ignore[no-untyped-def]
        self._op = "upsert"
        self._last_payload = payload
        self._journal.setdefault(self._tbl, []).append(("upsert", payload, on_conflict))
        if isinstance(payload, dict):
            payload = [payload]
        existing = self._data_store.setdefault(self._tbl, [])
        for row in payload:
            existing.append(dict(row))
        return self

    def insert(self, payload):  # type: ignore[no-untyped-def]
        self._op = "insert"
        self._last_payload = payload
        self._journal.setdefault(self._tbl, []).append(("insert", payload))
        if isinstance(payload, dict):
            payload = [payload]
        self._data_store.setdefault(self._tbl, []).extend(dict(r) for r in payload)
        return self

    # read ops
    def select(self, *_cols, count: str = "") -> "_FakeSupabaseQuery":
        self._op = "select"
        self._count_mode = count
        return self

    def eq(self, col, val) -> "_FakeSupabaseQuery":  # type: ignore[no-untyped-def]
        self._filters.append(("eq", col, val))
        return self

    def gte(self, col, val) -> "_FakeSupabaseQuery":  # type: ignore[no-untyped-def]
        self._filters.append(("gte", col, val))
        return self

    def lte(self, col, val) -> "_FakeSupabaseQuery":  # type: ignore[no-untyped-def]
        self._filters.append(("lte", col, val))
        return self

    def in_(self, col, vals) -> "_FakeSupabaseQuery":  # type: ignore[no-untyped-def]
        self._filters.append(("in", col, list(vals)))
        return self

    def order(self, col, desc: bool = False) -> "_FakeSupabaseQuery":  # type: ignore[no-untyped-def]
        self._filters.append(("order", col, desc))
        return self

    def limit(self, n) -> "_FakeSupabaseQuery":  # type: ignore[no-untyped-def]
        self._filters.append(("limit", n))
        return self

    def execute(self) -> _FakeSupabaseResult:
        if self._op in ("insert", "upsert"):
            return _FakeSupabaseResult(data=[], count=0)

        rows = list(self._data_store.get(self._tbl, []))
        for f in self._filters:
            if f[0] == "eq":
                rows = [r for r in rows if r.get(f[1]) == f[2]]
            elif f[0] == "gte":
                rows = [r for r in rows if r.get(f[1], 0.0) >= f[2]]
            elif f[0] == "lte":
                rows = [r for r in rows if r.get(f[1], 0.0) <= f[2]]
            elif f[0] == "in":
                rows = [r for r in rows if r.get(f[1]) in f[2]]
            elif f[0] == "order":
                rows.sort(key=lambda r, col=f[1]: r.get(col, 0), reverse=bool(f[2]))
            elif f[0] == "limit":
                rows = rows[: int(f[1])]
        if getattr(self, "_count_mode", "") == "exact":
            return _FakeSupabaseResult(data=rows[:1], count=len(self._data_store.get(self._tbl, [])))
        return _FakeSupabaseResult(data=rows)


class _FakeSupabaseClient:
    def __init__(self) -> None:
        self.journal: Dict[str, List[Any]] = {}
        self._data_store: Dict[str, List[Dict[str, Any]]] = {}

    def table(self, tbl: str) -> _FakeSupabaseQuery:
        return _FakeSupabaseQuery(tbl, self.journal, self._data_store)


class TestSupabaseLedgerBackendWithFakeClient(_LedgerBase):
    def setUp(self) -> None:
        super().setUp()
        self.fake = _FakeSupabaseClient()
        dl.set_ledger_for_tests(dl.SupabaseLedgerBackend(self.fake))

    def test_emit_decision_upserts_to_decision_events(self) -> None:
        did = dl.emit_decision(
            decision_type="swarm",
            output={"verdict": "BUY", "confidence": 0.5},
            symbol="AAPL",
            verdict="BUY",
            confidence=0.5,
        )
        writes = self.fake.journal.get("decision_events", [])
        self.assertEqual(len(writes), 1)
        op, payload, on_conflict = writes[0]
        self.assertEqual(op, "upsert")
        self.assertEqual(payload["decision_id"], did)
        self.assertEqual(payload["symbol"], "AAPL")
        # Supabase upsert passes through without on_conflict for events (PK).
        self.assertEqual(on_conflict, "")

    def test_record_outcome_sets_on_conflict_triplet(self) -> None:
        did = dl.emit_decision(decision_type="swarm", output={"v": 1}, verdict="BUY")
        dl.record_outcome(
            dl.OutcomeObservation(
                decision_id=did,
                horizon="1d",
                metric="price_return_pct",
                value=0.7,
                as_of_ts=1.0,
            )
        )
        writes = self.fake.journal.get("outcome_observations", [])
        self.assertEqual(len(writes), 1)
        op, payload, on_conflict = writes[0]
        self.assertEqual(op, "upsert")
        self.assertEqual(on_conflict, "decision_id,horizon,metric")

    def test_violation_insert_maps_fields(self) -> None:
        dl.record_violation(
            resource_name="bull",
            resource_version="1.0.0",
            model="gemma-4",
            path="$",
            code="missing_required",
            message="key_points is missing",
            decision_id="did",
        )
        writes = self.fake.journal.get("contract_violations", [])
        self.assertEqual(len(writes), 1)
        _op, payload = writes[0]
        self.assertEqual(payload["resource_name"], "bull")
        self.assertEqual(payload["code"], "missing_required")
        self.assertEqual(payload["decision_id"], "did")


# ── Contract-validator sink wiring ──────────────────────────────────────────


class TestContractValidatorSinkIntegration(_LedgerBase):
    def test_install_contract_validator_sink_writes_to_ledger(self) -> None:
        dl.install_contract_validator_sink()
        validator = cv.get_contract_validator()
        schema = {
            "type": "object",
            "required": ["a", "b"],
            "properties": {"a": {"type": "string"}, "b": {"type": "number"}},
        }
        payload, viols, coerced = validator.validate_result(
            {"a": "hi"},  # missing 'b' -> missing_required violation
            role="bull",
            schema=schema,
            fallback={"a": "fallback", "b": 0.0},
            version="1.0.0",
            model="test-model",
        )
        self.assertTrue(coerced)
        self.assertTrue(viols)
        # Ledger should now contain at least one row in contract_violations.
        stats = dl.get_ledger().stats()
        self.assertGreaterEqual(stats["contract_violations"], 1)


if __name__ == "__main__":
    unittest.main()
