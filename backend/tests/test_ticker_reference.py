"""Offline tests for the bundled ticker reference + issuer-name lookup."""
import unittest

from backend import ticker_reference as tr


class TickerReferenceTest(unittest.TestCase):
    def test_known_tickers_have_sector(self):
        for sym in ("AAPL", "MSFT", "NVDA"):
            meta = tr.get_ticker_meta(sym)
            self.assertIsNotNone(meta, f"{sym} missing from reference")
            self.assertTrue(meta.get("company_name"))
            self.assertTrue(meta.get("sector"))

    def test_normalize_ticker_class_separator(self):
        self.assertEqual(tr.normalize_ticker(" brk.b "), "BRK-B")
        self.assertEqual(tr.normalize_ticker("aapl"), "AAPL")

    def test_normalize_issuer_name_strips_suffixes(self):
        # CORP must not be mangled into ORATION (the naive-replace bug).
        self.assertEqual(tr.normalize_issuer_name("MICROSOFT CORP"), "microsoft")
        self.assertEqual(tr.normalize_issuer_name("Apple Inc. Common Stock"), "apple")

    def test_issuer_lookup_resolves_majors(self):
        self.assertEqual(tr.lookup_by_issuer_name("APPLE INC"), "AAPL")
        self.assertEqual(tr.lookup_by_issuer_name("MICROSOFT CORP"), "MSFT")
        self.assertEqual(tr.lookup_by_issuer_name("NVIDIA CORP"), "NVDA")

    def test_issuer_lookup_berkshire_either_class(self):
        self.assertIn(tr.lookup_by_issuer_name("BERKSHIRE HATHAWAY INC"), {"BRK-A", "BRK-B"})

    def test_issuer_lookup_unknown_returns_none(self):
        self.assertIsNone(tr.lookup_by_issuer_name("Zzzz Nonexistent Holdings LLC 999"))


if __name__ == "__main__":
    unittest.main()
