"""Revert harness state when post-cycle eval degrades."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, List, Optional

from ..config import HarnessConfig
from ..state import RefinementCycle
from ..trajectory import TrajectoryBuffer, TrajectoryEvent, TrajectoryEventType

if TYPE_CHECKING:
    from ..manager import HarnessStateManager

logger = logging.getLogger(__name__)


class RollbackGuard:
    def __init__(
        self,
        manager: "HarnessStateManager",
        buffer: TrajectoryBuffer,
        config: Optional[HarnessConfig] = None,
    ) -> None:
        self._manager = manager
        self._buffer = buffer
        self._config = config or HarnessConfig()
        self._pending_cycles: List[RefinementCycle] = []

    def register_cycle(self, cycle: RefinementCycle) -> None:
        self._pending_cycles.append(cycle)

    def _mean_eval_since(self, since_step: int) -> Optional[float]:
        scores = [
            e.eval_score
            for e in self._buffer.get_window(since_step=since_step)
            if e.eval_score is not None
        ]
        if not scores:
            return None
        return sum(scores) / len(scores)

    def evaluate_and_maybe_rollback(self, cycle: RefinementCycle) -> bool:
        state = self._manager.get_current_state()
        if state.refinement_frozen:
            return False

        post = self._mean_eval_since(
            max(0, self._buffer.get_window()[-1].step - self._config.rollback_eval_window_steps)
            if self._buffer.get_window()
            else 0
        )
        if post is None:
            return False

        pre = cycle.pre_cycle_eval_score
        if post >= pre - self._config.rollback_degradation_threshold:
            return False

        restored = self._manager.rollback_to_version(cycle.pre_cycle_version)
        state = self._manager.get_current_state()
        state.rollback_count += 1
        if state.rollback_count >= self._config.max_rollbacks_per_session:
            state.refinement_frozen = True
        self._manager.persist_state(state)

        step = self._buffer.next_step()
        self._buffer.push(
            TrajectoryEvent(
                session_id=state.session_id,
                step=step,
                agent_id="rollback_guard",
                event_type=TrajectoryEventType.ROLLBACK_TRIGGERED,
                payload={
                    "cycle_id": cycle.cycle_id,
                    "pre_score": pre,
                    "post_score": post,
                    "restored_version": restored.version,
                },
            )
        )
        logger.warning(
            "[Harness] rollback triggered cycle=%s pre=%.3f post=%.3f version=%s",
            cycle.cycle_id,
            pre,
            post,
            restored.version,
        )
        return True
