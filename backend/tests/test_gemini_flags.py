"""Unit tests for Gemini env flags (no network)."""
import os
import unittest


class TestGeminiFlags(unittest.TestCase):
    def tearDown(self):
        for k in (
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_PRIMARY",
            "GEMINI_LLM_FALLBACK",
        ):
            os.environ.pop(k, None)

    def test_primary_requires_key(self):
        from backend.gemini_llm import gemini_primary_enabled, resolve_gemini_api_key

        os.environ["GEMINI_PRIMARY"] = "1"
        self.assertFalse(gemini_primary_enabled())
        self.assertEqual(resolve_gemini_api_key(), "")
        os.environ["GEMINI_API_KEY"] = "x"
        self.assertTrue(gemini_primary_enabled())

    def test_usable_for_chat_primary_overrides_fallback_off(self):
        from backend.gemini_llm import gemini_usable_for_chat

        os.environ["GEMINI_API_KEY"] = "x"
        os.environ["GEMINI_LLM_FALLBACK"] = "0"
        os.environ["GEMINI_PRIMARY"] = "1"
        self.assertTrue(gemini_usable_for_chat())

    def test_usable_respects_fallback_off_without_primary(self):
        from backend.gemini_llm import gemini_usable_for_chat

        os.environ["GEMINI_API_KEY"] = "x"
        os.environ["GEMINI_LLM_FALLBACK"] = "0"
        os.environ.pop("GEMINI_PRIMARY", None)
        self.assertFalse(gemini_usable_for_chat())


if __name__ == "__main__":
    unittest.main()
