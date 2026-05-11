"""Guardrails against lookahead in covariate time series."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Union


class LeakageError(ValueError):
    """Raised when a covariate uses information not yet available at ``as_of``."""


def _to_dt(x: Union[str, date, datetime]) -> datetime:
    if isinstance(x, datetime):
        return x
    if isinstance(x, date):
        return datetime(x.year, x.month, x.day)
    s = str(x).strip()
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    d = date.fromisoformat(s[:10])
    return datetime(d.year, d.month, d.day)


def assert_available_before_as_of(
    *,
    observed_at: Union[str, date, datetime],
    available_at: Union[str, date, datetime],
    label: str = "",
) -> None:
    """Ensure ``available_at <= observed_at`` (same calendar day allowed)."""
    obs = _to_dt(observed_at)
    av = _to_dt(available_at)
    if av.date() > obs.date():
        raise LeakageError(
            f"{label or 'covariate'}: knowledge available_at {av.date()} after observation {obs.date()}"
        )


def assert_column_timestamps(
    *,
    as_of: Union[str, date, datetime],
    rows: list[dict[str, Any]],
    value_key: str,
    available_at_key: str = "available_at",
) -> None:
    """Validate each row's ``available_at`` is on/before ``as_of``."""
    cutoff = _to_dt(as_of).date()
    for i, row in enumerate(rows):
        av_raw = row.get(available_at_key)
        if av_raw is None:
            continue
        av = _to_dt(av_raw).date()
        if av > cutoff:
            raise LeakageError(f"row {i} {value_key}: available_at {av} > as_of {cutoff}")
