"""Top-level continual harness orchestrator."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from .changelog.harness_changelog import HarnessChangelog
from .config import HarnessConfig, harness_config_from_env
from .failure_detector import FailureSignature, FailureSignatureDetector
from .guards.rollback_guard import RollbackGuard
from .manager import HarnessStateManager
from .refiner import RefinerAgent
from .state import HarnessCRUDEdit, RefinementCycle
from .trajectory import TrajectoryBuffer, TrajectoryEvent, TrajectoryEventType

logger = logging.getLogger(__name__)

_loops: Dict[str, "ContinualHarnessLoop"] = {}


def get_session_loop(session_id: str, *, config: Optional[HarnessConfig] = None) -> "ContinualHarnessLoop":
    cfg = config or harness_config_from_env()
    if session_id not in _loops:
        _loops[session_id] = ContinualHarnessLoop(session_id, cfg)
    return _loops[session_id]


class ContinualHarnessLoop:
    def __init__(self, session_id: str, config: HarnessConfig) -> None:
        self.session_id = session_id
        self.config = config
        self.buffer = TrajectoryBuffer(
            session_id,
            window_size=config.trajectory_window_size,
            db_path=config.db_path,
        )
        self.manager = HarnessStateManager(session_id, config)
        self.detector = FailureSignatureDetector(
            self.buffer, self.manager.get_current_state(), config
        )
        try:
            from ..deps import llm_client as _llm_client
        except Exception:
            _llm_client = None
        self.refiner = RefinerAgent(model_client=_llm_client, model_tier=config.model_tier)
        self.rollback_guard = RollbackGuard(self.manager, self.buffer, config)
        self.changelog: HarnessChangelog = self.manager.changelog
        self._step_counter = 0
        self._last_cycle_version: Optional[int] = None

    async def on_step(self, events: List[TrajectoryEvent]) -> Optional[RefinementCycle]:
        for ev in events:
            self.buffer.push(ev)
        self._step_counter += 1
        self.detector = FailureSignatureDetector(
            self.buffer, self.manager.get_current_state(), self.config
        )

        signatures = self.detector.run()
        critical = [s for s in signatures if s.severity == "critical"]
        scheduled = self._step_counter % max(1, self.config.refinement_frequency_steps) == 0

        if critical and self.config.enable_emergency_refinement:
            return await self._run_refinement_cycle(signatures)
        if scheduled and signatures:
            return await self._run_refinement_cycle(signatures)

        if self._last_cycle_version is not None:
            for pending in list(getattr(self.rollback_guard, "_pending_cycles", [])):
                if self._step_counter % self.config.rollback_eval_window_steps == 0:
                    self.rollback_guard.evaluate_and_maybe_rollback(pending)
        return None

    async def _run_refinement_cycle(
        self, failure_signatures: List[FailureSignature]
    ) -> RefinementCycle:
        state = self.manager.get_current_state()
        pre_score = self._mean_eval() or 0.5
        pre_version = state.version

        accepted, deferred = await self.refiner.propose_edits(
            failure_signatures,
            self.buffer.get_window(),
            state,
        )

        step = self.buffer.next_step()
        self.buffer.push(
            TrajectoryEvent(
                session_id=self.session_id,
                step=step,
                agent_id="refiner",
                event_type=TrajectoryEventType.REFINER_PROPOSAL,
                payload={
                    "proposed": len(accepted),
                    "deferred": len(deferred),
                    "signatures": [s.signature_id for s in failure_signatures],
                },
            )
        )

        applied: List[HarnessCRUDEdit] = []
        observe = self.config.observe_only or not self.config.mutation_enable
        if not observe and not state.refinement_frozen:
            for edit in accepted:
                try:
                    self.manager.apply_edit(edit)
                    applied.append(edit)
                    step = self.buffer.next_step()
                    self.buffer.push(
                        TrajectoryEvent(
                            session_id=self.session_id,
                            step=step,
                            agent_id="harness_manager",
                            event_type=TrajectoryEventType.HARNESS_EDIT_APPLIED,
                            payload={"edit_id": edit.edit_id, "target": edit.target},
                        )
                    )
                except Exception as e:
                    logger.warning("[Harness] apply edit failed: %s", e)

        state = self.manager.get_current_state()
        state = state.model_copy(
            update={
                "refinement_cycle_count": state.refinement_cycle_count + 1,
                "last_refined_at": state.last_refined_at,
            }
        )
        self.manager.persist_state(state)

        cycle = RefinementCycle(
            session_id=self.session_id,
            failure_signatures=[s.signature_id for s in failure_signatures],
            proposed_edits=accepted + deferred,
            applied_edits=applied,
            deferred_edits=deferred,
            pre_cycle_eval_score=pre_score,
            observe_only=observe,
            pre_cycle_version=pre_version,
        )
        self.changelog.commit_cycle(cycle, self.manager.get_current_state())
        self._last_cycle_version = pre_version
        self.rollback_guard.register_cycle(cycle)
        return cycle

    def _mean_eval(self) -> Optional[float]:
        scores = [e.eval_score for e in self.buffer.get_window() if e.eval_score is not None]
        if not scores:
            return None
        return sum(scores) / len(scores)

    async def shutdown(self) -> None:
        export = self.changelog.export_for_mutation_engine(self.session_id)
        path = Path(self.config.mutation_engine_export_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(export, indent=2, default=str), encoding="utf-8")
        self.changelog.shutdown()


def harness_enabled() -> bool:
    return os.environ.get("HARNESS_ENABLE", "1").strip().lower() not in ("0", "false", "no")
