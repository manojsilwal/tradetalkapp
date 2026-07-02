"""Rollback guard tests."""

import os
import tempfile
import unittest

from backend.harness.config import HarnessConfig
from backend.harness.guards.rollback_guard import RollbackGuard
from backend.harness.loop import ContinualHarnessLoop
from backend.harness.manager import HarnessStateManager
from backend.harness.state import RefinementCycle
from backend.harness.trajectory import TrajectoryBuffer, TrajectoryEvent, TrajectoryEventType


class TestRollbackGuard(unittest.TestCase):
    def test_triggers_on_degradation(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "h.db")
        cfg = HarnessConfig(db_path=db, rollback_degradation_threshold=0.05)
        mgr = HarnessStateManager("s", cfg)
        buf = TrajectoryBuffer("s", db_path=db)
        guard = RollbackGuard(mgr, buf, cfg)

        st = mgr.get_current_state()
        st = st.model_copy(update={"version": 1})
        mgr.persist_state(st)
        mgr.changelog.flush()

        cycle = RefinementCycle(
            session_id="s",
            pre_cycle_eval_score=0.9,
            pre_cycle_version=1,
        )
        for i in range(5):
            buf.push(
                TrajectoryEvent(
                    session_id="s",
                    step=i + 1,
                    agent_id="a",
                    event_type=TrajectoryEventType.AGENT_ACTION,
                    payload={},
                    eval_score=0.1,
                )
            )
        self.assertTrue(guard.evaluate_and_maybe_rollback(cycle))
        tmp.cleanup()


class TestHarnessLoopObserveOnly(unittest.IsolatedAsyncioTestCase):
    async def test_observe_only_does_not_mutate(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "h.db")
        cfg = HarnessConfig(
            db_path=db,
            mutation_enable=False,
            observe_only=True,
            refinement_frequency_steps=1,
        )
        loop = ContinualHarnessLoop("sess-loop", cfg)
        loop.buffer.push(
            TrajectoryEvent(
                session_id="sess-loop",
                step=1,
                agent_id="gold_advisor",
                event_type=TrajectoryEventType.HALLUCINATION_FLAG,
                payload={},
            )
        )
        before = loop.manager.get_current_state().version
        await loop.on_step([])
        after = loop.manager.get_current_state().version
        self.assertEqual(before, after)
        tmp.cleanup()
