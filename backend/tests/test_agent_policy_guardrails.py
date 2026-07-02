import unittest
import os
from unittest.mock import patch
from backend.agent_policy_guardrails import (
    redact_secret,
    redact_secrets_in_text,
    ensure_capability,
    guard_host,
    workload_scope,
    validate_startup_secrets,
    resolve_sandbox_profile,
    PolicyBlockedError,
)

class TestAgentPolicyGuardrails(unittest.TestCase):
    def test_redact_secret(self):
        self.assertEqual(redact_secret(""), "")
        self.assertEqual(redact_secret("123"), "***")
        self.assertEqual(redact_secret("1234"), "****")
        self.assertEqual(redact_secret("12345"), "*2345")  # keep = 4, so length - 4 is redacted
        self.assertEqual(redact_secret("my_super_secret_key", keep=4), "***************_key")

    def test_redact_secrets_in_text_env_and_explicit(self):
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "sk-or-v1-my-fake-openrouter-key-12345",
            "GOOGLE_API_KEY": "AIzaSyFakeGoogleKeyForTesting1234567",
            "HF_TOKEN": "hf_FakeHuggingFaceTokenForTesting1234",
        }):
            text = "Error occurred. Details: OpenRouter key is sk-or-v1-my-fake-openrouter-key-12345, Google key is AIzaSyFakeGoogleKeyForTesting1234567, HF token is hf_FakeHuggingFaceTokenForTesting1234."
            redacted = redact_secrets_in_text(text, secret_values=["another_secret_value"])
            self.assertNotIn("sk-or-v1-my-fake-openrouter-key-12345", redacted)
            self.assertNotIn("AIzaSyFakeGoogleKeyForTesting1234567", redacted)
            self.assertNotIn("hf_FakeHuggingFaceTokenForTesting1234", redacted)
            
            # Check explicit secret redaction
            redacted_with_explicit = redact_secrets_in_text("Secret value: explicit_val_here", secret_values=["explicit_val_here"])
            self.assertNotIn("explicit_val_here", redacted_with_explicit)

    def test_redact_secrets_in_text_patterns(self):
        # Even without env variables set, regex patterns should match and redact
        text = "Found openrouter key sk-or-v1-abcdefghijklmnopqrstuvwxyz123456 in logs. Also found openai sk-abcdefghijklmnopqrstuvwxyz123456."
        redacted = redact_secrets_in_text(text)
        self.assertNotIn("sk-or-v1-abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz123456", redacted)

        google_text = "Google API Key is AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q"
        redacted_google = redact_secrets_in_text(google_text)
        self.assertNotIn("AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q", redacted_google)

        hf_text = "HuggingFace Token is hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567"
        redacted_hf = redact_secrets_in_text(hf_text)
        self.assertNotIn("hf_aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567", redacted_hf)

    def test_ensure_capability(self):
        # 'debate' workload has capabilities 'knowledge_read', 'knowledge_write', 'llm_inference'
        ensure_capability("debate", "knowledge_read")
        ensure_capability("debate", "llm_inference")
        
        with self.assertRaises(PolicyBlockedError):
            ensure_capability("debate", "news_ingest")
            
        # Unrecognized workload gets default SandboxProfile with 'knowledge_read'
        ensure_capability("unknown_workload", "knowledge_read")
        with self.assertRaises(PolicyBlockedError):
            ensure_capability("unknown_workload", "llm_inference")

    def test_guard_host(self):
        # 'llm' workload allowed host openrouter.ai
        guard_host("llm", "https://openrouter.ai/api/v1/chat")
        
        with self.assertRaises(PolicyBlockedError):
            guard_host("llm", "https://malicious-outbound.com")
            
        with self.assertRaises(PolicyBlockedError):
            guard_host("llm", "invalid_url_no_host")

    def test_workload_scope_redacts_exceptions(self):
        with self.assertLogs("backend.agent_policy_guardrails", level="WARNING") as log_cm:
            with self.assertRaises(ValueError):
                with workload_scope("debate"):
                    raise ValueError("Failed with key sk-or-v1-abcdefghijklmnopqrstuvwxyz123456")
        
        log_output = "\n".join(log_cm.output)
        self.assertNotIn("sk-or-v1-abcdefghijklmnopqrstuvwxyz123456", log_output)
        self.assertIn("*************************************", log_output)

    def test_validate_startup_secrets(self):
        with patch.dict(os.environ, {
            "OPENROUTER_API_KEY": "",
            "VECTOR_BACKEND": "supabase",
            "SUPABASE_SERVICE_ROLE_KEY": "",
        }):
            issues = validate_startup_secrets()
            self.assertIn("OPENROUTER_API_KEY is not set — LLM inference will use rule-based fallback.", issues)
            self.assertIn("VECTOR_BACKEND=supabase requires SUPABASE_SERVICE_ROLE_KEY.", issues)

    def test_resolve_sandbox_profile(self):
        profile = resolve_sandbox_profile("debate")
        self.assertEqual(profile.workload, "debate")
        self.assertIn("llm_inference", profile.capabilities)

if __name__ == "__main__":
    unittest.main()
