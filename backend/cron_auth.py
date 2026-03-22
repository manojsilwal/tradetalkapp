"""
Optional shared secret for cron / external triggers (GitHub Actions, cron-job.org, etc.).

When ``PIPELINE_CRON_SECRET`` is **non-empty**, protected routes require **one** of:
  - ``Authorization: Bearer <secret>``
  - ``X-Cron-Secret: <secret>``

When unset (local dev), routes stay open — set the env var in Render Dashboard for production.
"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import Header, HTTPException, status

_ENV_KEY = "PIPELINE_CRON_SECRET"


def cron_secret_configured() -> str:
    return os.environ.get(_ENV_KEY, "").strip()


async def require_cron_secret(
    authorization: Annotated[str | None, Header()] = None,
    x_cron_secret: Annotated[str | None, Header(alias="X-Cron-Secret")] = None,
) -> None:
    """FastAPI dependency — no-op if secret not configured; else validate headers."""
    expected = cron_secret_configured()
    if not expected:
        return

    token: str | None = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if x_cron_secret:
        token = x_cron_secret.strip()
    if token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing cron secret. Use Authorization: Bearer <PIPELINE_CRON_SECRET> or X-Cron-Secret.",
        )
