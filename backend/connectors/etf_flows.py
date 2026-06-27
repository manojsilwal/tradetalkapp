"""
NR-9 — ETF flow proxy per theme.

Clean weekly fund-flow feeds are a paid-data gap (Plan §4). As an honest MVP proxy
we map a theme to a representative thematic ETF and derive a flow proxy from
dollar-volume (close × volume) momentum: recent 5-day average vs the trailing
20-day average. Rising dollar volume into a rising price is an inflow proxy.

This is explicitly a *proxy* (flagged with lower confidence): it is not true
creation/redemption flow. Flag-gated (``NARRATIVE_RADAR_ETF_FLOWS``); resilient to
any failure. The math lives in the pure ``flow_from_series`` for offline tests.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Representative thematic ETF per theme (seed; extend as products launch).
THEME_ETF: Dict[str, str] = {
    "ai_compute": "SMH",
    "semi_equipment": "SOXX",
    "cybersecurity": "CIBR",
    "data_center_re": "SRVR",
    "energy_utilities": "XLU",
    "power_infra": "GRID",
    "grid_construction": "PAVE",
}


def enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_ETF_FLOWS", "0").strip() == "1"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def flow_from_series(closes: Sequence[float], volumes: Sequence[float]) -> Dict[str, Any]:
    """
    Pure dollar-volume flow proxy. ``closes``/``volumes`` newest last.
    flow_score 0-100 (50 = neutral); flow_acceleration_pct = recent vs trailing $vol.
    """
    n = min(len(closes), len(volumes))
    if n < 25:
        return {"available": False}
    dvol = [float(closes[i]) * float(volumes[i]) for i in range(-n, 0) if volumes[i] is not None]
    if len(dvol) < 25:
        return {"available": False}
    recent = sum(dvol[-5:]) / 5.0
    trailing = sum(dvol[-20:]) / 20.0
    if trailing <= 0:
        return {"available": False}
    accel = (recent / trailing - 1.0) * 100.0
    # Direction: did price rise over the window? Inflow proxy = rising $vol + rising price.
    price_chg = (float(closes[-1]) / float(closes[-20]) - 1.0) * 100.0 if closes[-20] else 0.0
    flow_score = _clamp(50.0 + accel * 0.8 + (10.0 if price_chg > 0 else -10.0))
    return {
        "available": True,
        "flow_score": round(flow_score, 2),
        "flow_acceleration_pct": round(_clamp(50.0 + accel), 2),
        "is_proxy": True,
    }


def build_theme_flow(theme_id: str) -> Dict[str, Any]:
    """Live entry point (flag-gated). Resilient → unavailable on any failure."""
    if not enabled():
        return {"available": False}
    etf = THEME_ETF.get(theme_id)
    if not etf:
        return {"available": False}
    try:
        import yfinance as yf

        df = yf.Ticker(etf).history(period="3mo", interval="1d", auto_adjust=True)
        if df is None or df.empty or "Close" not in df.columns or "Volume" not in df.columns:
            return {"available": False}
        closes = [float(x) for x in df["Close"].tolist()]
        volumes = [float(x) for x in df["Volume"].tolist()]
        out = flow_from_series(closes, volumes)
        if out.get("available"):
            out["etf"] = etf
        return out
    except Exception as e:
        logger.debug("[NarrativeRadar] ETF flow build failed for %s: %s", theme_id, e)
        return {"available": False}
