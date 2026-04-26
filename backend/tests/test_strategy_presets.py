import unittest
import sys
from unittest.mock import MagicMock

# 1. Mock external dependencies BEFORE importing backend modules
# We mock these because the sandbox environment lacks the necessary Python packages
mock_modules = [
    "pydantic", "fastapi", "yfinance", "requests", "openai", "supabase",
    "google.genai", "jwt", "pandas", "apscheduler", "aiofiles",
    "huggingface_hub", "pyarrow", "httpx", "python-dotenv", "yaml"
]
for mod in mock_modules:
    sys.modules[mod] = MagicMock()

# 2. Specialized mock for Pydantic's BaseModel to support attribute access and copy()
# This allows us to test the logic of strategy_presets.py without a full Pydantic installation
class MockBaseModel:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
    def copy(self, update=None):
        import copy
        new_obj = copy.copy(self)
        if update:
            for k, v in update.items():
                setattr(new_obj, k, v)
        return new_obj
    def __getitem__(self, item):
        return getattr(self, item)

sys.modules["pydantic"].BaseModel = MockBaseModel

# 3. Import the module under test
import backend.strategy_presets as sp

class TestStrategyPresets(unittest.TestCase):
    def test_ff_qmj_preset(self):
        """Test the Fama-French Quality + Value (ff_quality_value) preset."""
        # Use the internal builder directly since we are unit testing the logic
        rules = sp._ff_qmj()

        self.assertEqual(rules.preset_id, "ff_quality_value")
        self.assertEqual(rules.name, "Fama-French Quality + Value")

        # Verify filters
        metrics = {f.metric: (f.op, f.value) for f in rules.filters}
        self.assertIn("roe", metrics)
        self.assertEqual(metrics["roe"], (">", 12.0))
        self.assertIn("gross_margins", metrics)
        self.assertEqual(metrics["gross_margins"], (">", 20.0))
        self.assertIn("debt_to_equity", metrics)
        self.assertEqual(metrics["debt_to_equity"], ("<", 120.0))

        # Verify ranking
        self.assertEqual(rules.rank_by_metric, "pb_ratio")
        self.assertFalse(rules.rank_higher_is_better)

        # Verify other params
        self.assertEqual(rules.select_top_n, 8)
        self.assertEqual(rules.rebalance_months, 3)
        self.assertEqual(rules.strategy_category, "Factor")

    def test_list_preset_summaries(self):
        """Test that list_preset_summaries returns expected presets and matches builders."""
        summaries = sp.list_preset_summaries()
        self.assertIsInstance(summaries, list)
        self.assertGreater(len(summaries), 0)

        # Check consistency between list_preset_summaries and builders
        for summary in summaries:
            pid = summary["preset_id"]
            rules = sp.get_preset_rules(pid, "2020-01-01", "2024-01-01")

            # The summary name and builder name should match
            self.assertEqual(summary["name"], rules.name, f"Name mismatch for {pid}")
            self.assertEqual(summary["category"], rules.strategy_category, f"Category mismatch for {pid}")

        # Specific check for ff_quality_value
        ff_summary = next(s for s in summaries if s["preset_id"] == "ff_quality_value")
        self.assertEqual(ff_summary["name"], "Fama-French Quality + Value")
        self.assertEqual(ff_summary["category"], "Factor")
        self.assertEqual(ff_summary["rebalance_freq"], "Quarterly")

    def test_get_preset_rules_logic(self):
        """Test get_preset_rules correctly retrieves and updates dates."""
        start_date = "2022-01-01"
        end_date = "2023-01-01"
        rules = sp.get_preset_rules("ff_quality_value", start_date, end_date)

        self.assertEqual(rules.preset_id, "ff_quality_value")
        self.assertEqual(rules.start_date, start_date)
        self.assertEqual(rules.end_date, end_date)

    def test_get_preset_rules_invalid_id(self):
        """Test get_preset_rules raises KeyError for invalid IDs."""
        with self.assertRaises(KeyError):
            sp.get_preset_rules("non_existent_preset", "2020-01-01", "2024-01-01")

    def test_all_registered_presets_instantiate(self):
        """Smoke test: all presets in list_preset_summaries should be gettable."""
        summaries = sp.list_preset_summaries()
        for s in summaries:
            pid = s["preset_id"]
            rules = sp.get_preset_rules(pid, "2020-01-01", "2024-01-01")
            self.assertIsNotNone(rules)
            self.assertEqual(rules.start_date, "2020-01-01")

if __name__ == "__main__":
    unittest.main()
