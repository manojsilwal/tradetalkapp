import unittest

from backend.llm_client import LLMClient


class TestLlmJsonParser(unittest.TestCase):
    def setUp(self):
        self.client = LLMClient()

    def test_parses_codefenced_json_with_trailing_commas(self):
        raw = """```json
{
  "scenes": [
    {"scene": 1, "caption": "A", "visual_prompt": "x", "duration": 4,}
  ],
}
```"""
        out = self.client._parse_json_response(raw, "video_scene_director")
        self.assertIn("scenes", out)
        self.assertEqual(out["scenes"][0]["scene"], 1)

    def test_parses_prefixed_balanced_json_object(self):
        raw = 'Here you go:\n{"caption":"Lesson","body":"Text body"}\nThanks!'
        out = self.client._parse_json_response(raw, "video_veo_text_fallback")
        self.assertEqual(out.get("caption"), "Lesson")
        self.assertIn("body", out)


if __name__ == "__main__":
    unittest.main()
