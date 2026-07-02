"""Single entry point each legacy surface calls to (optionally) use the brain.

``serve_for_surface`` returns the brain serving result when the surface's
cutover flag is on AND a snapshot exists; otherwise ``None`` so the caller
falls back to its existing engine. Every failure degrades to ``None`` — the
brain can never break a surface during the cutover.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from .flags import brain_surface_enabled

logger = logging.getLogger(__name__)


def serve_for_surface(ticker: str, surface: str, *,
                      knowledge_store: Any = None,
                      options_overlay: Optional[Dict[str, float]] = None) -> Optional[Dict]:
    """Brain result for ``ticker`` if ``surface`` is cut over, else None."""
    if not brain_surface_enabled(surface):
        return None
    try:
        from .serving import serve_ticker
        result = serve_ticker(
            ticker,
            knowledge_store=knowledge_store,
            options_overlay=options_overlay,
        )
        if not result or result.get("status") == "no_snapshot":
            return None
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain.cutover] %s serve failed for %s: %s", surface, ticker, e)
        return None


async def aserve_for_surface(ticker: str, surface: str, *,
                             knowledge_store: Any = None,
                             options_overlay: Optional[Dict[str, float]] = None) -> Optional[Dict]:
    """Async wrapper — runs the (blocking) serve in a thread for event loops."""
    if not brain_surface_enabled(surface):
        return None
    try:
        return await asyncio.to_thread(
            serve_for_surface, ticker, surface,
            knowledge_store=knowledge_store,
            options_overlay=options_overlay,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain.cutover] async %s serve failed for %s: %s", surface, ticker, e)
        return None
