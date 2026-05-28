"""Single writer for harness state mutations."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from .changelog.harness_changelog import HarnessChangelog
from .config import HarnessConfig
from .editors.memory_editor import MemoryEditor
from .editors.prompt_editor import PromptEditor
from .editors.skill_editor import SkillEditor
from .editors.subagent_manager import SubAgentManager
from .state import HarnessCRUDEdit, HarnessState

logger = logging.getLogger(__name__)


class HarnessStateManager:
    def __init__(self, session_id: str, config: HarnessConfig) -> None:
        self._config = config
        self._changelog = HarnessChangelog(config.db_path)
        self._state = HarnessState(session_id=session_id)
        snap = self._changelog.load_snapshot(session_id, 0)
        if snap is not None:
            self._state = snap
        self._prompt_editor = PromptEditor()
        self._skill_editor = SkillEditor(config)
        self._memory_editor = MemoryEditor()
        self._subagent_manager = SubAgentManager(config)

    @property
    def changelog(self) -> HarnessChangelog:
        return self._changelog

    def get_current_state(self) -> HarnessState:
        return self._state

    def persist_state(self, state: HarnessState) -> None:
        self._state = state
        self._changelog.save_snapshot(state)

    def apply_edit(self, edit: HarnessCRUDEdit) -> HarnessState:
        pre = self._state.model_copy(deep=True)
        self._changelog.save_snapshot(pre)

        try:
            if edit.target == "prompt":
                new_state = self._prompt_editor.apply(edit, self._state)
            elif edit.target == "skill":
                new_state = self._skill_editor.apply(edit, self._state)
            elif edit.target == "memory":
                new_state = self._memory_editor.apply(edit, self._state)
            elif edit.target == "subagent":
                new_state = self._subagent_manager.apply(edit, self._state)
            else:
                raise ValueError(f"unknown edit target {edit.target}")
        except Exception:
            logger.exception("[Harness] edit failed id=%s", edit.edit_id)
            raise

        new_state = new_state.model_copy(
            update={
                "version": self._state.version + 1,
                "last_refined_at": datetime.now(timezone.utc),
            }
        )
        self._state = new_state
        self._changelog.save_snapshot(new_state)
        return new_state

    def rollback_to_version(self, version: int) -> HarnessState:
        restored = self._changelog.load_snapshot(self._state.session_id, version)
        if restored is None:
            raise ValueError(f"no snapshot for version {version}")
        self._state = restored
        return restored
