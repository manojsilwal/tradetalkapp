import json
import tempfile
import unittest
from pathlib import Path

from backend.eval.swarm_score.runner import run_swarm_score


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestSwarmScoreRunner(unittest.TestCase):
    def test_missing_inputs_reported_in_dry_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = run_swarm_score(repo_root=root, mode="dry-run", write_outputs=False)
            result = output["result"]
            self.assertGreater(len(result["missing_inputs"]), 0)
            skipped = [row for row in result["variant_scores"] if row["status"] == "skipped"]
            self.assertGreater(len(skipped), 0)

    def test_fixture_run_writes_outputs_and_skips_unscored_variants(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            datasets = root / "evals" / "datasets"
            configs = root / "evals" / "configs"
            (root / "frontend" / "public" / "dashboard").mkdir(parents=True, exist_ok=True)
            datasets.mkdir(parents=True, exist_ok=True)
            configs.mkdir(parents=True, exist_ok=True)

            fixture = (
                '{"variant":"production_swarm","task_success":86,"rag_quality":90,"orchestration_effectiveness":88,'
                '"continual_learning_value":75,"efficiency":68,"safety_groundedness":93,"maintainability":72,'
                '"p95_latency_ms":5200,"cost_per_task":0.11,"hallucination_rate":0.012,"critical_hallucinations":0,'
                '"fabricated_tool_call_claims":0,"tool_call_validity":0.97,"citation_validity":0.94}\n'
                '{"variant":"reduced_swarm","task_success":87,"rag_quality":89,"orchestration_effectiveness":83,'
                '"continual_learning_value":73,"efficiency":79,"safety_groundedness":92,"maintainability":78,'
                '"p95_latency_ms":4100,"cost_per_task":0.08,"hallucination_rate":0.011,"critical_hallucinations":0,'
                '"fabricated_tool_call_claims":0,"tool_call_validity":0.97,"citation_validity":0.94}\n'
            )
            _write(datasets / "golden_tasks.jsonl", fixture)
            for name in [
                "rag_grounding_cases.jsonl",
                "agent_orchestration_cases.jsonl",
                "hallucination_adversarial.jsonl",
                "memory_regression_cases.jsonl",
                "tool_failure_cases.jsonl",
            ]:
                _write(datasets / name, '{"note":"placeholder"}\n')

            for name in [
                "production_swarm.yaml",
                "single_agent_rag.yaml",
                "planner_executor.yaml",
                "reduced_swarm.yaml",
                "no_critic.yaml",
                "no_reflection.yaml",
                "no_rrf_memory.yaml",
                "no_mutation_engine.yaml",
                "no_coral_meta_llm.yaml",
            ]:
                _write(configs / name, f"variant: {name.replace('.yaml', '')}\nversion: v1.0.0\nmode: fixture\n")

            output = run_swarm_score(repo_root=root, mode="fixture", write_outputs=True)
            files = output["files"]
            self.assertTrue(Path(files["report_md"]).exists())
            self.assertTrue(Path(files["report_json"]).exists())
            self.assertTrue(Path(files["variant_csv"]).exists())
            self.assertTrue(Path(files["ablation_csv"]).exists())
            self.assertTrue(Path(files["dashboard_summary"]).exists())
            self.assertTrue(Path(files["dashboard_history"]).exists())

            result = output["result"]
            reduced = next(row for row in result["variant_scores"] if row["name"] == "Reduced Swarm + Current LLM")
            self.assertIsNotNone(reduced["aes"])
            single = next(row for row in result["variant_scores"] if row["name"] == "Single-Agent RAG")
            self.assertEqual(single["status"], "skipped")
            self.assertIsNone(single["aes"])

            summary = json.loads(Path(files["dashboard_summary"]).read_text(encoding="utf-8"))
            self.assertIn(summary["status"], {"pass", "hold", "fail", "shadow_recommended"})


if __name__ == "__main__":
    unittest.main()

