"""Phase E5 — OHLCV normalization contract and pattern descriptors."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import pstdev
from typing import Iterable


@dataclass
class OHLCVBar:
    open: float
    high: float
    low: float
    close: float
    volume: float


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def zscore(value: float, window: Iterable[float]) -> float:
    vals = [float(v) for v in window]
    if not vals:
        return 0.0
    mu = sum(vals) / len(vals)
    sigma = pstdev(vals) if len(vals) > 1 else 0.0
    if sigma <= 0:
        return 0.0
    return (float(value) - mu) / sigma


def normalize_bar(bar: OHLCVBar, prev_close: float, volume_window: Iterable[float]) -> dict[str, float]:
    """
    Normalize one OHLCV bar into cross-asset comparable structural features.
    Raw prices are intentionally excluded from the output contract.
    """
    return {
        "open_gap": _safe_div((bar.open - prev_close), prev_close),
        "high_range": _safe_div((bar.high - bar.open), bar.open),
        "low_range": _safe_div((bar.low - bar.open), bar.open),
        "close_body": _safe_div((bar.close - bar.open), bar.open),
        "volume_zscore": zscore(bar.volume, volume_window),
    }


def describe_window(normalized_bars: list[dict[str, float]]) -> str:
    """
    Deterministic, bounded descriptor for embedding into vector_memory.
    """
    if not normalized_bars:
        return "PATTERN_EMPTY"
    n = len(normalized_bars)
    open_gap = sum(b.get("open_gap", 0.0) for b in normalized_bars) / n
    close_body = sum(b.get("close_body", 0.0) for b in normalized_bars) / n
    high_range = sum(b.get("high_range", 0.0) for b in normalized_bars) / n
    low_range = sum(b.get("low_range", 0.0) for b in normalized_bars) / n
    volz = sum(b.get("volume_zscore", 0.0) for b in normalized_bars) / n

    direction = "UP" if close_body > 0 else ("DOWN" if close_body < 0 else "FLAT")
    gap = "GAP_UP" if open_gap > 0 else ("GAP_DOWN" if open_gap < 0 else "NO_GAP")
    vol = "HIGH_VOL" if volz >= 1.0 else ("LOW_VOL" if volz <= -1.0 else "MID_VOL")
    return (
        f"{gap}_{direction}_{vol} "
        f"close_body={close_body:+.4f} open_gap={open_gap:+.4f} "
        f"high_range={high_range:+.4f} low_range={low_range:+.4f} "
        f"volume_z={volz:+.3f}"
    )
