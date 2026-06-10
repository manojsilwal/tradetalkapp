"""
Canonical default model IDs — the single place to rotate models (Phase F).

Model-agnostic gateway contract: no module outside the LLM/embedding gateway
may hardcode a provider model ID. Every default below is still overridable via
its env var at runtime, so a model swap is a config change (followed by the
``/harness/replay`` champion/challenger gate) — never a code edit.

See docs/PHASE_F_INTELLIGENCE_FABRIC.md (workstream F2).
"""

# NVIDIA Build (OpenAI-compatible) chat cascade — env: NVIDIA_LLM_MODEL_PRO / _FLASH.
DEFAULT_NVIDIA_MODEL_PRO = "moonshotai/kimi-k2.6"
DEFAULT_NVIDIA_MODEL_FLASH = "deepseek-ai/deepseek-v4-pro"

# OpenRouter chat — env: OPENROUTER_MODEL / OPENROUTER_MODEL_LIGHT.
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-31b-it:free"

# Gemini (Google AI Studio) — env: GEMINI_MODEL / GEMINI_MODEL_LIGHT /
# GEMINI_FALLBACK_MODEL / GEMINI_VISION_MODEL.
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"

# Google Veo video generation — env: VIDEO_VEO_MODEL.
DEFAULT_VEO_MODEL = "veo-3.1-lite-generate-preview"

# OpenRouter embeddings (batch ETL) — env: OPENROUTER_EMBEDDING_MODEL.
DEFAULT_OPENROUTER_EMBEDDING_MODEL = "openai/text-embedding-3-small"
