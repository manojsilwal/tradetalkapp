import unittest
from backend.harness.refiner import RefinerAgent
from backend.harness.failure_detector import FailureSignature
from backend.harness.state import HarnessState

class TestHarnessRefinerHeuristics(unittest.TestCase):
    def setUp(self) -> None:
        self.refiner = RefinerAgent(model_client=None)
        self.state = HarnessState(
            session_id="test_session",
            system_prompts={"planner": "Existing planner prompt", "router": "Existing router prompt"}
        )

    def test_heuristic_propose_agent_loop(self) -> None:
        sig = FailureSignature(
            signature_id="AGENT_LOOP",
            severity="high",
            affected_agent_ids=["planner"],
            description="Agent loop detected"
        )
        edits = self.refiner._heuristic_propose([sig], self.state)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].target, "prompt")
        self.assertEqual(edits[0].target_id, "planner")
        self.assertIn("If you find yourself repeating the same action", edits[0].payload["system_prompt"])

    def test_heuristic_propose_routing_schema_mismatch(self) -> None:
        sig = FailureSignature(
            signature_id="ROUTING_SCHEMA_MISMATCH",
            severity="critical",
            affected_agent_ids=["router"],
            description="Schema mismatch detected"
        )
        edits = self.refiner._heuristic_propose([sig], self.state)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0].target, "prompt")
        self.assertEqual(edits[0].target_id, "router")
        self.assertIn("You must strictly adhere to the defined routing handoff schema", edits[0].payload["system_prompt"])

if __name__ == "__main__":
    unittest.main()
