"""
Gemini (Google AI Studio) helpers for LLM fallback when OpenRouter fails.

Uses the supported ``google.genai`` client (see ``google-genai`` on PyPI).
Configure ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY``.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Model tier selection ────────────────────────────────────────────────────
#
# ``GEMINI_MODEL``        — heavy reasoning paths (bull/bear/moderator, sepl_improver,
#                           strategy parsing, backtest explanation, gold advisor).
# ``GEMINI_MODEL_LIGHT``  — lightweight paths (swarm_analyst factor calls, swarm
#                           synthesizer, reflection writer, RAG polish, video scene
#                           director).
# ``GEMINI_FALLBACK_MODEL`` — legacy alias retained for backward compat; still used
#                           when a caller does not specify a tier (= heavy default).
#
# Both tiers default to ``gemini-3.5-flash`` (see ai.google.dev/gemini-api/docs/models/gemini-3.5-flash).
# OpenRouter is the primary provider; Gemini 3.5 Flash is the fallback when OpenRouter fails.
#
# Gemini 3.x models use internal "thinking" tokens that share ``max_output_tokens``.
# JSON agent calls set ``thinking_level=minimal`` so structured output is not truncated.
GEMINI_MODEL = os.environ.get(
    "GEMINI_MODEL",
    os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash"),
).strip()
GEMINI_MODEL_LIGHT = os.environ.get(
    "GEMINI_MODEL_LIGHT", "gemini-3.5-flash"
).strip()
# Kept for callers that still import the old name.
GEMINI_FALLBACK_MODEL = GEMINI_MODEL

_GUARDRAILS_ON = os.environ.get("GUARDRAILS_ENABLE", "1").strip() != "0"
_GEMINI_GUARD_URL = "https://generativelanguage.googleapis.com/"


def _is_gemini_3_model(model: str) -> bool:
    m = (model or "").strip().lower()
    return m.startswith("gemini-3") or "gemini-3." in m


def _gemini_thinking_level(*, json_mode: bool) -> str:
    if json_mode:
        return "minimal"
    level = os.environ.get("GEMINI_THINKING_LEVEL", "medium").strip().lower()
    if level in ("minimal", "low", "medium", "high"):
        return level
    return "medium"


def _effective_max_output_tokens(model: str, max_tokens: int, *, json_mode: bool) -> int:
    """Gemini 3.x thinking shares the output budget — keep a floor for JSON roles."""
    floor = 512 if json_mode and _is_gemini_3_model(model) else 256
    return max(int(max_tokens), floor)


def _apply_thinking_config(cfg: Any, model: str, *, json_mode: bool) -> None:
    if not _is_gemini_3_model(model):
        return
    from google.genai import types

    cfg.thinking_config = types.ThinkingConfig(
        thinking_level=_gemini_thinking_level(json_mode=json_mode)
    )


def resolve_gemini_model(tier: str = "heavy") -> str:
    """Return the Gemini model id for the given workload tier.

    ``tier`` is ``"heavy"`` or ``"light"``. Unknown tiers default to heavy so a
    typo never silently downgrades frontier-class reasoning to flash.
    """
    return GEMINI_MODEL_LIGHT if tier == "light" else GEMINI_MODEL


def _with_llm_guard(fn):
    from .agent_policy_guardrails import (
        guard_host,
        is_enabled as policy_guardrails_enabled,
        workload_scope,
    )

    if _GUARDRAILS_ON and policy_guardrails_enabled():
        guard_host("llm", _GEMINI_GUARD_URL)
        with workload_scope("llm", "llm_inference"):
            return fn()
    return fn()


def resolve_gemini_api_key() -> str:
    """Prefer ``GEMINI_API_KEY``; ``GOOGLE_API_KEY`` is legacy fallback only."""
    return os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()


def _genai_client():
    from google import genai as genai_mod

    key = resolve_gemini_api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return genai_mod.Client(api_key=key)


def gemini_llm_fallback_enabled() -> bool:
    if os.environ.get("GEMINI_LLM_FALLBACK", "1").strip().lower() in ("0", "false", "no"):
        return False
    return bool(resolve_gemini_api_key())


def gemini_primary_enabled() -> bool:
    """When True, TradeTalk chat uses Gemini first (see ``GEMINI_PRIMARY``). Requires a Gemini API key."""
    v = os.environ.get("GEMINI_PRIMARY", "").strip().lower()
    if v not in ("1", "true", "yes"):
        return False
    return bool(resolve_gemini_api_key())


def gemini_usable_for_chat() -> bool:
    """Gemini may serve streaming chat (primary and/or OpenRouter failover)."""
    if not resolve_gemini_api_key():
        return False
    if gemini_primary_enabled():
        return True
    return gemini_llm_fallback_enabled()


def gemini_instant_openrouter_failover() -> bool:
    """
    When True (default if Gemini fallback is enabled), the first OpenRouter 429 skips
    sleeps and extra OpenRouter retries so the request can move to Gemini immediately.
    Set GEMINI_INSTANT_OPENROUTER_FAILOVER=0 to use the slower multi-retry OpenRouter path.
    """
    if not gemini_llm_fallback_enabled():
        return False
    v = os.environ.get("GEMINI_INSTANT_OPENROUTER_FAILOVER", "1").strip().lower()
    return v not in ("0", "false", "no")


def _strip_unsupported_json_schema_for_gemini(obj: Any) -> Any:
    """Gemini Schema rejects some OpenAI JSON Schema keys (e.g. ``default``)."""
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if k == "default":
                continue
            out[k] = _strip_unsupported_json_schema_for_gemini(v)
        return out
    if isinstance(obj, list):
        return [_strip_unsupported_json_schema_for_gemini(x) for x in obj]
    return obj


def _openai_tools_to_genai(tools: Optional[List[dict]]) -> Optional[List[Any]]:
    """Build ``google.genai.types.Tool`` list for ``GenerateContentConfig``."""
    if not tools:
        return None
    from google.genai import types

    decls = []
    for t in tools:
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        fn = t.get("function") or {}
        name = (fn.get("name") or "").strip()
        if not name:
            continue
        params = fn.get("parameters")
        if not isinstance(params, dict):
            params = {"type": "object", "properties": {}}
        params = _strip_unsupported_json_schema_for_gemini(params)
        decls.append(
            types.FunctionDeclaration(
                name=name[:256],
                description=(fn.get("description") or "")[:8000],
                parameters=params,
            )
        )
    if not decls:
        return None
    return [types.Tool(function_declarations=decls)]


def _openai_messages_to_genai_contents(messages: List[dict]) -> List[Any]:
    """Map OpenAI-style history (no leading system) to ``types.Content`` list."""
    from google.genai import types

    out: List[Any] = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            out.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text=str(m.get("content") or ""))],
                )
            )
        elif role == "assistant":
            parts_text: List[str] = []
            c = m.get("content")
            if c:
                parts_text.append(str(c))
            if m.get("tool_calls"):
                parts_text.append("\n[Previous tool_calls in OpenAI format omitted for brevity]")
            if parts_text:
                out.append(
                    types.Content(
                        role="model",
                        parts=[types.Part(text="".join(parts_text))],
                    )
                )
        elif role == "tool":
            name = m.get("name") or "tool"
            body = str(m.get("content") or "")
            out.append(
                types.Content(
                    role="user",
                    parts=[types.Part(text=f"[Tool {name} result]\n{body}")],
                )
            )
    return out


def _response_text(response: Any) -> str:
    try:
        t = (response.text or "").strip()
        if t:
            return t
    except Exception:
        pass
    try:
        cand = response.candidates[0]
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if parts:
            chunks = []
            for p in parts:
                if getattr(p, "text", None):
                    chunks.append(p.text)
            return "".join(chunks).strip()
    except Exception:
        pass
    return ""


def gemini_simple_completion_sync(
    *,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    json_mode: bool,
    model: Optional[str] = None,
) -> str:
    """Single-turn text or JSON completion (used by agent JSON paths and plain text).

    ``model`` overrides the default ``GEMINI_MODEL`` (heavy) for this call — pass
    :data:`GEMINI_MODEL_LIGHT` (or :func:`resolve_gemini_model`\"light\"``) for
    cheap/fast paths like ``swarm_analyst`` factor checks.
    """
    from google.genai import types
    import time
    from .decision_ledger import log_llm_api_call

    chosen_model = (model or GEMINI_MODEL).strip() or GEMINI_MODEL
    start_time = time.time()
    response_text = ""

    def _call() -> str:
        nonlocal response_text
        client = _genai_client()
        out_tokens = _effective_max_output_tokens(chosen_model, max_tokens, json_mode=json_mode)
        cfg = types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=temperature,
            max_output_tokens=out_tokens,
        )
        if json_mode:
            cfg.response_mime_type = "application/json"
        _apply_thinking_config(cfg, chosen_model, json_mode=json_mode)
        response = client.models.generate_content(
            model=chosen_model,
            contents=user,
            config=cfg,
        )
        response_text = _response_text(response)

        prompt_tokens = 0
        completion_tokens = 0
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
        except Exception:
            pass

        latency = time.time() - start_time
        log_llm_api_call(
            prompt_text=f"{system}\n{user}" if system else user,
            model=chosen_model,
            latency=latency,
            response_text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            api_url=_GEMINI_GUARD_URL,
        )
        return response_text

    return _with_llm_guard(_call)


def gemini_chat_turn_result_sync(
    *,
    system: str,
    openai_messages: List[dict],
    tools: Optional[List[dict]],
    max_tokens: int,
    temperature: float,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    One chat turn with optional tools. Returns:
      {"ok": True, "kind": "text", "text": str}
      {"ok": True, "kind": "tool", "name": str, "args": str}
      {"ok": False, "error": str}
    """
    from google.genai import types

    chosen_model = (model or GEMINI_MODEL).strip() or GEMINI_MODEL

    def _call() -> Dict[str, Any]:
        import time
        from .decision_ledger import log_llm_api_call
        client = _genai_client()
        gen_tools = _openai_tools_to_genai(tools)
        cfg_kwargs: Dict[str, Any] = {
            "system_instruction": system or None,
            "temperature": temperature,
            "max_output_tokens": _effective_max_output_tokens(
                chosen_model, max_tokens, json_mode=False
            ),
        }
        if gen_tools:
            cfg_kwargs["tools"] = gen_tools
        config = types.GenerateContentConfig(**cfg_kwargs)
        _apply_thinking_config(config, chosen_model, json_mode=False)
        contents = _openai_messages_to_genai_contents(openai_messages)
        if not contents:
            return {"ok": False, "error": "empty_messages"}

        start_time = time.time()
        try:
            response = client.models.generate_content(
                model=chosen_model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            logger.warning("[GeminiLLM] generate_content failed: %s", e)
            return {"ok": False, "error": str(e)[:500]}

        latency = time.time() - start_time
        prompt_text = ""
        if openai_messages:
            prompt_text = str(openai_messages[-1].get("content") or "")

        prompt_tokens = 0
        completion_tokens = 0
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
        except Exception:
            pass

        result_text = ""
        fc = None
        try:
            cand = response.candidates[0]
            for part in cand.content.parts:
                fc_part = getattr(part, "function_call", None)
                if fc_part:
                    fc = fc_part
                    result_text = f"Tool call: {getattr(fc, 'name', '')}"
                    break
                if getattr(part, "text", None):
                    result_text = part.text
        except Exception:
            pass

        log_llm_api_call(
            prompt_text=f"{system}\n{prompt_text}" if system else prompt_text,
            model=chosen_model,
            latency=latency,
            response_text=result_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            api_url=_GEMINI_GUARD_URL,
        )

        if fc:
            name = getattr(fc, "name", "") or ""
            raw_args = getattr(fc, "args", None)
            try:
                if isinstance(raw_args, dict):
                    args_s = json.dumps(raw_args)
                elif raw_args is None:
                    args_s = "{}"
                else:
                    args_s = json.dumps(dict(raw_args)) if hasattr(raw_args, "keys") else str(raw_args)
            except Exception:
                args_s = "{}"
            return {"ok": True, "kind": "tool", "name": name, "args": args_s}

        txt = _response_text(response)
        if txt:
            return {"ok": True, "kind": "text", "text": txt}
        return {"ok": False, "error": "empty_response"}

    return _with_llm_guard(_call)


_HOLDINGS_VISION_INSTRUCTION = (
    "You are extracting stock and ETF holdings from a broker app screenshot "
    "(e.g. Robinhood, Webull, Fidelity mobile). For each equity line, output ticker, "
    "share quantity, and average cost per share in USD. "
    "Rules: use plain US tickers (AAPL, MSFT). Uppercase tickers. "
    "Ignore cash, buying power, and options unless it is clearly common stock. "
    "If quantity or average cost is missing or illegible, use null for that field. "
    "Respond with JSON only: {\"holdings\":[{\"ticker\":string,\"shares\":number|null,\"avg_cost\":number|null}]} "
    "with no markdown or commentary."
)


def gemini_extract_holdings_from_image(
    *,
    image_bytes: bytes,
    mime_type: str,
    max_tokens: int = 2048,
) -> str:
    """
    Multimodal Gemini call: screenshot → JSON text body (holdings list).
    Requires ``GEMINI_API_KEY`` or ``GOOGLE_API_KEY``.
    """
    from google.genai import types

    if not image_bytes:
        raise ValueError("empty image")
    mt = (mime_type or "image/jpeg").split(";")[0].strip().lower()
    if mt not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
        mt = "image/jpeg"

    vision_model = os.environ.get("GEMINI_VISION_MODEL", "").strip() or "gemini-3.5-flash"

    def _call() -> str:
        import time
        from .decision_ledger import log_llm_api_call
        client = _genai_client()
        parts = [
            types.Part(inline_data=types.Blob(data=image_bytes, mime_type=mt)),
            types.Part(text=_HOLDINGS_VISION_INSTRUCTION),
        ]
        contents = [types.Content(role="user", parts=parts)]
        cfg = types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=_effective_max_output_tokens(
                vision_model, max_tokens, json_mode=True
            ),
            response_mime_type="application/json",
        )
        _apply_thinking_config(cfg, vision_model, json_mode=True)
        start_time = time.time()
        response = client.models.generate_content(
            model=vision_model,
            contents=contents,
            config=cfg,
        )
        response_text = _response_text(response)
        latency = time.time() - start_time

        prompt_tokens = 0
        completion_tokens = 0
        try:
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                prompt_tokens = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
                completion_tokens = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
        except Exception:
            pass

        log_llm_api_call(
            prompt_text="[Image Holdings Extraction] " + _HOLDINGS_VISION_INSTRUCTION,
            model=vision_model,
            latency=latency,
            response_text=response_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            api_url=_GEMINI_GUARD_URL,
        )
        return response_text

    return _with_llm_guard(_call)


async def gemini_fallback_chat_events(
    *,
    system: str,
    openai_messages: List[dict],
    tools: Optional[List[dict]],
    max_tokens: int,
    temperature: float,
    text_chunk_size: int = 120,
    model: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Async wrapper: runs sync Gemini turn in a thread, then yields chunked text events
    or a single tool event.

    ``model`` defaults to :data:`GEMINI_MODEL`. The public chat path passes the
    heavy model; SEPL / internal light paths can override per-call.
    """
    import asyncio

    loop = asyncio.get_event_loop()

    def _run() -> Dict[str, Any]:
        return gemini_chat_turn_result_sync(
            system=system,
            openai_messages=openai_messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )

    result = await loop.run_in_executor(None, _run)
    if not result.get("ok"):
        yield {"kind": "error", "message": result.get("error", "unknown")}
        return
    if result.get("kind") == "tool":
        yield {
            "kind": "tool",
            "name": result.get("name", ""),
            "args": result.get("args", "{}"),
        }
        return
    text = result.get("text") or ""
    for i in range(0, len(text), text_chunk_size):
        yield {"kind": "text", "text": text[i : i + text_chunk_size]}
    if not text:
        yield {"kind": "text", "text": ""}
