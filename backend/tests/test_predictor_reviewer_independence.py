import os
import unittest

import yaml


class TestReviewerRouting(unittest.TestCase):
    def test_synthesis_and_reviewer_vendor_prefix_differ(self) -> None:
        root = os.path.join(os.path.dirname(__file__), "..", "..", "configs", "llm_routing.yaml")
        path = os.path.abspath(root)
        self.assertTrue(os.path.isfile(path), f"missing {path}")
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        pred = raw.get("predictor") or {}
        syn = (pred.get("synthesis") or {}).get("primary") or ""
        rev = (pred.get("reviewer") or {}).get("primary") or ""
        self.assertTrue(syn and rev)
        syn_pre = syn.split("/", 1)[0].lower()
        rev_pre = rev.split("/", 1)[0].lower()
        self.assertNotEqual(syn_pre, rev_pre)


if __name__ == "__main__":
    unittest.main()
