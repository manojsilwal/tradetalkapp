"""Harness state serialization tests."""

import unittest

from backend.harness.state import HarnessCRUDEdit, HarnessState, RefinementCycle, CRUDOperation


class TestHarnessState(unittest.TestCase):
    def test_roundtrip_json(self) -> None:
        st = HarnessState(session_id="sess-1", system_prompts={"gold_advisor": "hello"})
        raw = st.to_json()
        back = HarnessState.from_json(raw)
        self.assertEqual(back.session_id, "sess-1")
        self.assertEqual(back.system_prompts["gold_advisor"], "hello")

    def test_refinement_cycle_immutable(self) -> None:
        cycle = RefinementCycle(session_id="s1")
        with self.assertRaises(Exception):
            cycle.rolled_back = True  # type: ignore[misc]

    def test_crud_edit_coerces_operation(self) -> None:
        e = HarnessCRUDEdit(
            target="prompt",
            operation="update",
            target_id="gold_advisor",
        )
        self.assertEqual(e.operation, CRUDOperation.UPDATE)
