"""
Per-category yfinance capability circuit breaker.

Tracks failures for price, info, news, chart, and history so callers can skip
categories known to fail on free Yahoo / datacenter IPs (e.g. Cloud Run).
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import FrozenSet, Literal

YfCategory = Literal["price", "info", "news", "chart", "history"]

_ALL_CATEGORIES: FrozenSet[str] = frozenset({"price", "info", "news", "chart", "history"})

_lock = threading.Lock()
_states: dict[str, "_CategoryState"] = {}


@dataclass
class _CategoryState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open: bool = False


def _threshold() -> int:
    try:
        return max(1, int(os.environ.get("YF_BREAKER_THRESHOLD", "3")))
    except (TypeError, ValueError):
        return 3


def _cooldown_s() -> float:
    try:
        return max(30.0, float(os.environ.get("YF_BREAKER_COOLDOWN_S", "600")))
    except (TypeError, ValueError):
        return 600.0


def _force_disabled() -> set[str]:
    raw = os.environ.get("YF_DISABLED_CATEGORIES", "").strip().lower()
    if not raw:
        return set()
    parts = re.split(r"[,;]+", raw)
    return {c.strip() for c in parts if c.strip() in _ALL_CATEGORIES}


def _state(category: str) -> _CategoryState:
    cat = category.strip().lower()
    if cat not in _ALL_CATEGORIES:
        cat = "price"
    with _lock:
        if cat not in _states:
            _states[cat] = _CategoryState()
        return _states[cat]


def should_attempt(category: YfCategory) -> bool:
    """Return False when category is force-disabled or circuit is open."""
    cat = category.strip().lower()
    if cat in _force_disabled():
        return False

    st = _state(cat)
    with _lock:
        if st.opened_at is None:
            return True
        elapsed = time.time() - st.opened_at
        if elapsed >= _cooldown_s():
            st.half_open = True
            return True
        return False


def record_success(category: YfCategory) -> None:
    st = _state(category)
    with _lock:
        st.consecutive_failures = 0
        st.opened_at = None
        st.half_open = False


def record_failure(category: YfCategory) -> None:
    st = _state(category)
    with _lock:
        if st.half_open:
            st.consecutive_failures = _threshold()
            st.opened_at = time.time()
            st.half_open = False
            return
        st.consecutive_failures += 1
        if st.consecutive_failures >= _threshold():
            st.opened_at = time.time()


def status_snapshot() -> dict[str, dict]:
    """Diagnostics for logs / debug (no secrets)."""
    disabled = _force_disabled()
    out: dict[str, dict] = {}
    with _lock:
        for cat in sorted(_ALL_CATEGORIES):
            st = _states.get(cat) or _CategoryState()
            out[cat] = {
                "force_disabled": cat in disabled,
                "consecutive_failures": st.consecutive_failures,
                "open": st.opened_at is not None,
                "half_open": st.half_open,
            }
    return out


def reset_all_for_tests() -> None:
    """Test helper — clear in-process breaker state."""
    with _lock:
        _states.clear()
