"""
Optional shared secret for cron / external triggers (GitHub Actions, cron-job.org, etc.).

When ``PIPELINE_CRON_SECRET`` is **non-empty**, protected routes require **one** of:
  - ``Authorization: Bearer <secret>``
  - ``X-Cron-Secret: <secret>``

When unset (local dev), routes stay open — set the env var in Render Dashboard for production.
"""
from __future__ import annotations

import os
import logging
from typing import Annotated, Optional

from fastapi import Header, HTTPException, status

_ENV_KEY = "PIPELINE_CRON_SECRET"
_logger = logging.getLogger(__name__)


def cron_secret_configured() -> str:
    return os.environ.get(_ENV_KEY, "").strip()


# Warn at import time if cron secret is unprotected in production
_RENDER = os.environ.get("RENDER", "").strip().lower() in ("true", "1", "yes")
if not cron_secret_configured() and _RENDER:
    _logger.warning(
        "[CronAuth] PIPELINE_CRON_SECRET is not set on Render! "
        "Cron endpoints (/knowledge/pipeline-run, /knowledge/sp500-ingest) are open to anyone."
    )


async def require_cron_secret(
    authorization: Annotated[Optional[str], Header()] = None,
    x_cron_secret: Annotated[Optional[str], Header(alias="X-Cron-Secret")] = None,
) -> None:
    """FastAPI dependency — no-op if secret not configured; else validate headers."""
    expected = cron_secret_configured()
    if not expected:
        return

    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if x_cron_secret:
        token = x_cron_secret.strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing cron secret. Use Authorization: Bearer <PIPELINE_CRON_SECRET> or X-Cron-Secret.",
        )
