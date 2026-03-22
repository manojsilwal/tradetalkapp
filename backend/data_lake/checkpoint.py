"""
Checkpoint system — tracks which tickers have been ingested per phase
so interrupted runs can resume without re-fetching.
"""
import json
import os
import logging
from .config import CHECKPOINT_FILE

logger = logging.getLogger(__name__)


def _load() -> dict:
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(CHECKPOINT_FILE), exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_done(phase: str, ticker: str) -> bool:
    """Check if a ticker has already been ingested for a phase."""
    data = _load()
    return ticker in data.get(phase, [])


def mark_done(phase: str, ticker: str) -> None:
    """Record that a ticker has been ingested for a phase."""
    data = _load()
    if phase not in data:
        data[phase] = []
    if ticker not in data[phase]:
        data[phase].append(ticker)
    _save(data)


def mark_batch_done(phase: str, tickers: list[str]) -> None:
    """Record a batch of tickers as done for a phase."""
    data = _load()
    if phase not in data:
        data[phase] = []
    existing = set(data[phase])
    existing.update(tickers)
    data[phase] = sorted(existing)
    _save(data)


def get_remaining(phase: str, all_tickers: list[str]) -> list[str]:
    """Return tickers not yet ingested for a phase."""
    data = _load()
    done = set(data.get(phase, []))
    return [t for t in all_tickers if t not in done]


def get_stats() -> dict:
    """Summary of checkpoint state."""
    data = _load()
    return {phase: len(tickers) for phase, tickers in data.items()}


def reset(phase: str | None = None) -> None:
    """Clear checkpoint for a phase, or all phases if None."""
    if phase is None:
        _save({})
    else:
        data = _load()
        data.pop(phase, None)
        _save(data)
