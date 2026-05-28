"""
Extract supply-chain relationships from SEC filings via FinCrawler + Gemini.

Phase 1: preview-only — returns proposed edges without writing to DB.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """\
You are an expert financial analyst. Given the following excerpt from a {form} SEC filing \
for {ticker}, extract supplier-customer and capital-flow relationships.

Return a JSON array of objects with these fields:
- "source": payer company ticker or name (uppercase ticker if public)
- "target": payee / supplier company ticker or name
- "relationship_type": one of "subscription", "capex", "manufacturing", "components", "raw_materials", "equipment", "services", "licensing"
- "amount_est_usd": estimated annual USD flow (null if unknown)
- "amount_pct_of_revenue": percentage of source's revenue (null if unknown)
- "year": fiscal year the filing covers (integer)
- "confidence": 0.0–1.0 confidence in the relationship
- "citation": brief quote or reference from the filing supporting the relationship

Rules:
- Only include relationships where capital clearly flows from source to target.
- Do not invent relationships not supported by the text.
- Return an empty array [] if no clear supply-chain relationships are found.
- Return ONLY valid JSON, no markdown fences or explanation.

Filing text:
{filing_text}
"""


async def extract_supply_chain_preview(
    ticker: str,
    form: str = "10-K",
    filing_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch filing via FinCrawler (or accept pre-fetched text), extract relationships
    with Gemini, return preview payload (not persisted).
    """
    if not filing_text:
        filing_text = await _fetch_filing(ticker, form)

    if not filing_text:
        return {
            "ticker": ticker,
            "form": form,
            "edges": [],
            "warning": "No filing text available (FinCrawler not configured or filing not found).",
        }

    edges = await _extract_with_gemini(ticker, form, filing_text)
    return {"ticker": ticker, "form": form, "edges": edges}


async def _fetch_filing(ticker: str, form: str) -> str:
    try:
        from ..fincrawler_client import fc
        text = await fc.get_sec_filing(ticker, form=form, max_chars=8000)
        return text or ""
    except Exception as exc:
        logger.warning("[supply_chain] FinCrawler fetch failed for %s %s: %s", ticker, form, exc)
        return ""


async def _extract_with_gemini(ticker: str, form: str, filing_text: str) -> List[Dict[str, Any]]:
    try:
        from ..gemini_llm import resolve_gemini_api_key, resolve_gemini_model
    except ImportError:
        logger.warning("[supply_chain] gemini_llm not available")
        return []

    api_key = resolve_gemini_api_key()
    if not api_key:
        logger.warning("[supply_chain] No Gemini API key — returning empty extraction")
        return []

    try:
        from google import genai as genai_mod

        client = genai_mod.Client(api_key=api_key)
        prompt = _EXTRACTION_PROMPT.format(
            ticker=ticker.upper(), form=form, filing_text=filing_text[:8000],
        )
        model_name = resolve_gemini_model("light")
        response = client.models.generate_content(model=model_name, contents=prompt)
        text = response.text.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        edges = json.loads(text)
        if not isinstance(edges, list):
            return []
        return edges
    except Exception as exc:
        logger.warning("[supply_chain] Gemini extraction failed for %s: %s", ticker, exc)
        return []
