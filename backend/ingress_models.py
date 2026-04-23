"""
Schema-first ingress models for debate, swarm trace, and analyze endpoints.

POST bodies use these models; GET query params are validated via validate_ticker().
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}$")


def normalize_ticker(value: str) -> str:
    """Strip, uppercase, and validate US-style equity symbol (1–5 letters)."""
    if value is None:
        raise ValueError("ticker is required")
    s = str(value).strip().upper()
    if not _TICKER_PATTERN.match(s):
        raise ValueError(
            "Ticker must be 1–5 Latin letters (e.g. AAPL, GME). "
            "No dots, numbers-only, or symbols."
        )
    return s


class DebateIngressRequest(BaseModel):
    """POST /debate JSON body."""

    ticker: str = Field(..., min_length=1, max_length=5)

    @field_validator("ticker", mode="before")
    @classmethod
    def _ticker_validate(cls, v: object) -> str:
        return normalize_ticker(v)  # type: ignore[arg-type]


class TraceIngressRequest(BaseModel):
    """POST /trace JSON body."""

    ticker: str = Field(..., min_length=1, max_length=5)
    credit_stress: Optional[float] = Field(
        None,
        description="Optional override for credit stress index (testing).",
    )

    @field_validator("ticker", mode="before")
    @classmethod
    def _ticker_validate(cls, v: object) -> str:
        return normalize_ticker(v)  # type: ignore[arg-type]


class AnalyzeIngressRequest(BaseModel):
    """POST /analyze JSON body."""

    ticker: str = Field(..., min_length=1, max_length=5)
    credit_stress: Optional[float] = Field(
        None,
        description="Optional override for credit stress index (testing).",
    )
    provider_audit: bool = Field(
        False,
        description="When true, POST /decision-terminal includes provider_audit metadata.",
    )
    audit: Optional[int] = Field(
        None,
        description="Alias for provider_audit: set to 1 to include provider_audit on decision-terminal.",
    )

    @field_validator("ticker", mode="before")
    @classmethod
    def _ticker_validate(cls, v: object) -> str:
        return normalize_ticker(v)  # type: ignore[arg-type]


class IngressValidationError(BaseModel):
    """Stable error shape for 422 validation failures (also used in error responses)."""

    error: str = "validation_error"
    message: str
    field: Optional[str] = None


def validate_ticker_query(ticker: str) -> str:
    """
    Validate a ticker from GET query params; raise HTTPException 422 on failure.
    Use from FastAPI route handlers.
    """
    from fastapi import HTTPException

    try:
        return normalize_ticker(ticker)
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail=IngressValidationError(
                error="invalid_ticker",
                message=str(e),
                field="ticker",
            ).model_dump(),
        ) from e
