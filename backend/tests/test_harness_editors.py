"""Harness editors and manager tests."""

import os
import tempfile
import unittest

from backend.harness.config import HarnessConfig
from backend.harness.editors.prompt_editor import PromptEditor, REQUIRED_GUARDRAIL
from backend.harness.editors.skill_editor import SkillEditor
from backend.harness.manager import HarnessStateManager
from backend.harness.state import CRUDOperation, HarnessCRUDEdit, HarnessState


class TestPromptEditor(unittest.TestCase):
    def test_rejects_missing_guardrail_for_market_agent(self) -> None:
        st = HarnessState(session_id="s")
        edit = HarnessCRUDEdit(
            target="prompt",
            operation=CRUDOperation.UPDATE,
            target_id="gold_advisor",
            payload={"system_prompt": "Be bullish."},
        )
        with self.assertRaises(ValueError):
            PromptEditor().apply(edit, st)

    def test_accepts_guardrail(self) -> None:
        st = HarnessState(session_id="s")
        edit = HarnessCRUDEdit(
            target="prompt",
            operation=CRUDOperation.UPDATE,
            target_id="gold_advisor",
            payload={"system_prompt": REQUIRED_GUARDRAIL},
        )
        out = PromptEditor().apply(edit, st)
        self.assertIn(REQUIRED_GUARDRAIL, out.system_prompts["gold_advisor"])


class TestSkillEditor(unittest.TestCase):
    def test_sandbox_rejects_broken_code(self) -> None:
        st = HarnessState(session_id="s")
        edit = HarnessCRUDEdit(
            target="skill",
            operation=CRUDOperation.CREATE,
            target_id="sk1",
            payload={"source_code": "raise RuntimeError('x')", "skill_type": "code"},
        )
        with self.assertRaises(ValueError):
            SkillEditor(HarnessConfig()).apply(edit, st)


class TestHarnessManager(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        os.environ["HARNESS_DB_PATH"] = os.path.join(self._tmp.name, "h.db")

    def test_apply_increments_version(self) -> None:
        mgr = HarnessStateManager("sess", HarnessConfig(db_path=os.environ["HARNESS_DB_PATH"]))
        edit = HarnessCRUDEdit(
            target="memory",
            operation=CRUDOperation.UPDATE,
            target_id="hint",
            payload={"value": "use tools"},
        )
        st = mgr.apply_edit(edit)
        self.assertGreaterEqual(st.version, 1)
