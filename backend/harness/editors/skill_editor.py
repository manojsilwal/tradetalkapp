"""CRUD on harness skills with optional sandbox validation."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Tuple

from ..config import HarnessConfig
from ..state import CRUDOperation, HarnessCRUDEdit, HarnessState, SkillRecord, new_id


class SkillEditor:
    def __init__(self, config: HarnessConfig) -> None:
        self._config = config

    def _sandbox_test(self, source_code: str) -> Tuple[bool, str]:
        if not (source_code or "").strip():
            return True, "empty"
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
                f.write(source_code)
                path = f.name
            proc = subprocess.run(
                [sys.executable, path],
                capture_output=True,
                text=True,
                timeout=self._config.skill_sandbox_timeout_seconds,
            )
            return proc.returncode == 0, (proc.stderr or proc.stdout or "")[:500]
        except subprocess.TimeoutExpired:
            return False, "sandbox timeout"
        except Exception as e:
            return False, str(e)

    def apply(self, edit: HarnessCRUDEdit, state: HarnessState) -> HarnessState:
        skills = list(state.skills)
        now = datetime.now(timezone.utc)

        if edit.operation == CRUDOperation.DELETE:
            skills = [s for s in skills if s.skill_id != edit.target_id]
            return state.model_copy(update={"skills": skills})

        if edit.operation in (CRUDOperation.CREATE, CRUDOperation.UPDATE):
            source = str(edit.payload.get("source_code") or "")
            skill_type = edit.payload.get("skill_type", "heuristic")
            if skill_type == "code":
                ok, err = self._sandbox_test(source)
                if not ok:
                    raise ValueError(f"skill sandbox failed: {err}")

            existing = next((s for s in skills if s.skill_id == edit.target_id), None)
            if existing:
                updated = existing.model_copy(
                    update={
                        "source_code": source or existing.source_code,
                        "name": edit.payload.get("name", existing.name),
                        "version": existing.version + 1,
                        "last_modified_at": now,
                        "skill_type": skill_type,
                        "domain_tags": edit.payload.get("domain_tags", existing.domain_tags),
                    }
                )
                skills = [updated if s.skill_id == edit.target_id else s for s in skills]
            else:
                skills.append(
                    SkillRecord(
                        skill_id=edit.target_id or new_id(),
                        name=str(edit.payload.get("name") or edit.target_id),
                        source_code=source,
                        skill_type=skill_type,
                        domain_tags=list(edit.payload.get("domain_tags") or []),
                    )
                )
        return state.model_copy(update={"skills": skills})
