"""
Weeks-fresh smart-money accumulation signals for the Narrative Rotation Radar.

Combines Chaikin Money Flow + relative-volume z-score (price/volume proxies) with
optional Form 4 insider activity (8-week window) and per-ETF options skew (Phase C).

Pure helpers are offline-testable; live wrappers degrade to ``{"available": False}``.
"""
from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from ..actionable_companies import _clamp, _linscore
from ..macro_flow.macro_flow_agent import chaikin_money_flow

logger = logging.getLogger(__name__)

_RECENT_VOL_DAYS = 21
_BASELINE_VOL_DAYS = 60


def _smart_money_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_SMART_MONEY", "1").strip() != "0"


def _options_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_OPTIONS", "0").strip() == "1"


def relative_volume_zscore(
    volumes: Sequence[float],
    *,
    recent_days: int = _RECENT_VOL_DAYS,
    baseline_days: int = _BASELINE_VOL_DAYS,
) -> Optional[float]:
    """Z-score of recent average volume vs trailing baseline. None if insufficient data."""
    clean = [float(v) for v in volumes if v is not None and not math.isnan(float(v)) and float(v) >= 0]
    if len(clean) < recent_days + 5:
        return None
    recent = clean[-recent_days:]
    base = clean[-(baseline_days + recent_days):-recent_days] if len(clean) > baseline_days else clean[:-recent_days]
    if not base or not recent:
        return None
    mu = sum(base) / len(base)
    if mu <= 0:
        return None
    var = sum((x - mu) ** 2 for x in base) / len(base)
    sigma = math.sqrt(var) if var > 0 else 1e-9
    recent_avg = sum(recent) / len(recent)
    return round((recent_avg - mu) / sigma, 3)


def _cmf_to_score(cmf: float) -> float:
    """Map CMF [-1, 1] to 0-100."""
    return round(_clamp(50.0 + cmf * 50.0), 2)


def _vol_z_to_score(z: Optional[float]) -> Optional[float]:
    if z is None:
        return None
    return round(_linscore(z, -1.0, 2.5), 2)


def etf_accumulation_signal(ohlcv: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """CMF + relative-volume z-score blended into accumulation_score 0-100."""
    if ohlcv is None or ohlcv.empty or "Close" not in ohlcv.columns:
        return {"available": False}
    df = ohlcv.copy()
    for col in ("High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return {"available": False}
    cmf = chaikin_money_flow(df, n=21)
    vols = df["Volume"].astype(float).tolist()
    vol_z = relative_volume_zscore(vols)
    cmf_score = _cmf_to_score(cmf)
    vol_score = _vol_z_to_score(vol_z)
    parts = [(0.65, cmf_score), (0.35, vol_score)]
    acc = used = 0.0
    for w, v in parts:
        if v is None:
            continue
        acc += w * v
        used += w
    if used == 0:
        return {"available": False}
    score = round(acc / used, 2)
    return {
        "available": True,
        "cmf": round(cmf, 4),
        "relative_volume_zscore": vol_z,
        "accumulation_score": score,
    }


def _insider_activity_window(ticker: str, *, days: int = 56) -> tuple[int, int, float]:
    """Open-market Form 4 buys vs sells in the last ``days`` (default 8 weeks)."""
    try:
        import yfinance as yf
    except Exception:
        return 0, 0, 0.0
    try:
        t = yf.Ticker(ticker)
        df = t.insider_transactions
    except Exception:
        return 0, 0, 0.0
    if df is None or getattr(df, "empty", True):
        return 0, 0, 0.0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    buys = sells = 0
    net_shares = 0.0
    date_col = None
    for c in df.columns:
        if "date" in str(c).lower():
            date_col = c
            break
    type_col = None
    for c in df.columns:
        cl = str(c).lower()
        if "transaction" in cl or cl == "text":
            type_col = c
            break
    shares_col = None
    for c in df.columns:
        if "share" in str(c).lower():
            shares_col = c
            break
    for _, row in df.iterrows():
        try:
            if date_col is not None:
                raw = row[date_col]
                if hasattr(raw, "to_pydatetime"):
                    dt = raw.to_pydatetime()
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt < cutoff:
                        continue
            tx = str(row.get(type_col, "") if type_col else "").upper()
            sh = float(row.get(shares_col, 0) or 0) if shares_col else 0.0
            if "P" in tx or "PURCHASE" in tx or "BUY" in tx:
                buys += 1
                net_shares += abs(sh)
            elif "S" in tx or "SALE" in tx or "SELL" in tx:
                sells += 1
                net_shares -= abs(sh)
        except Exception:
            continue
    return buys, sells, net_shares


def insider_signal_8w(members: Sequence[str], *, sample_k: int = 3) -> Dict[str, Any]:
    """Aggregate 8-week Form 4 activity across a sample of basket members."""
    if not _smart_money_enabled():
        return {"available": False}
    tickers = list(members)[:sample_k]
    if not tickers:
        return {"available": False}
    total_buys = total_sells = 0
    net = 0.0
    sampled = 0
    for tk in tickers:
        try:
            b, s, n = _insider_activity_window(tk)
            if b + s > 0:
                sampled += 1
            total_buys += b
            total_sells += s
            net += n
        except Exception:
            continue
    if sampled == 0 and total_buys == 0 and total_sells == 0:
        return {"available": False}
    insider_score = _linscore(total_buys - total_sells, -3.0, 5.0)
    net_score = _linscore(net, -1e6, 1e6) if net else 50.0
    blend = round((0.6 * insider_score + 0.4 * net_score), 2)
    return {
        "available": True,
        "insider_buy_count_8w": total_buys,
        "insider_sell_count_8w": total_sells,
        "insider_net_shares_8w": round(net, 0),
        "insider_score": blend,
    }


def options_skew_signal(etf: Optional[str], cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Per-ETF put/call skew from yfinance options chain (flag-gated)."""
    if not _options_enabled() or not etf:
        return {"available": False}
    cache = cache if cache is not None else {}
    if etf in cache:
        return cache[etf]
    try:
        from ..market_intel import _fetch_options_flow

        raw = _fetch_options_flow(etf)
        if not raw or raw.get("error"):
            out = {"available": False}
        else:
            pcr = raw.get("spy_put_call_ratio") or raw.get("put_call_ratio")
            if pcr is None:
                out = {"available": False}
            else:
                # Low PCR (call-heavy) → bullish institutional positioning proxy.
                skew_score = round(_clamp(100.0 - _linscore(float(pcr), 0.5, 1.5)), 2)
                out = {
                    "available": True,
                    "put_call_ratio": round(float(pcr), 3),
                    "options_skew_score": skew_score,
                }
        cache[etf] = out
        return out
    except Exception as e:
        logger.debug("[NarrativeRadar] options skew failed for %s: %s", etf, e)
        return {"available": False}


def build_smart_money_signal(
    theme_id: str,
    members: Sequence[str],
    member_rows: Sequence[Dict[str, Any]],
    *,
    options_cache: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble weeks-fresh smart-money signal for a theme."""
    if not _smart_money_enabled():
        return {"available": False}

    from . import themes as nr_themes

    # Prefer primary ETF OHLCV (sector ETF or first member).
    primary_etf = nr_themes.sector_etf_for(theme_id) or (members[0] if members else None)
    ohlcv = None
    for m in member_rows:
        if m.get("ticker") == primary_etf or not ohlcv:
            ohlcv = m.get("ohlcv")
    etf_sig = etf_accumulation_signal(ohlcv)
    insider = insider_signal_8w(members)
    options = options_skew_signal(primary_etf, cache=options_cache)

    from . import activist_filings as nr_activist

    activist_cache = options_cache.get("_activist_tickers") if options_cache is not None else None
    activist = nr_activist.activist_signal_8w(
        members,
        recent_tickers=activist_cache if isinstance(activist_cache, set) else None,
    )

    if (
        not etf_sig.get("available")
        and not insider.get("available")
        and not options.get("available")
        and not activist.get("available")
    ):
        return {"available": False}

    parts = []
    if etf_sig.get("available"):
        parts.append((0.50, etf_sig.get("accumulation_score")))
    if insider.get("available"):
        parts.append((0.22, insider.get("insider_score")))
    if activist.get("available"):
        parts.append((0.13, activist.get("activist_score")))
    if options.get("available"):
        parts.append((0.15, options.get("options_skew_score")))

    acc = used = 0.0
    for w, v in parts:
        if v is None:
            continue
        acc += w * v
        used += w
    if used == 0:
        return {"available": False}

    return {
        "available": True,
        "accumulation_score": round(acc / used, 2),
        "cmf": etf_sig.get("cmf"),
        "relative_volume_zscore": etf_sig.get("relative_volume_zscore"),
        "insider_buy_count_8w": insider.get("insider_buy_count_8w"),
        "insider_sell_count_8w": insider.get("insider_sell_count_8w"),
        "activist_filing_count": activist.get("activist_filing_count"),
        "activist_hit_tickers": activist.get("activist_hit_tickers"),
        "put_call_ratio": options.get("put_call_ratio"),
    }


def smart_money_divergence_score(
    accumulation_score: Optional[float],
    retail_direction: Optional[float],
    retail_saturation_score: Optional[float],
) -> Optional[float]:
    """
    High → stealth accumulation (smart money up, retail bearish/quiet).
    Low  → distribution into hype (smart money down, retail euphoric).
    """
    if accumulation_score is None:
        return None
    # retail_direction ∈ [-1 sell-framing, +1 buy-pump]; invert for stealth signal.
    direction = retail_direction if retail_direction is not None else 0.0
    saturation = retail_saturation_score if retail_saturation_score is not None else 50.0
    # Bearish retail framing boosts divergence; euphoric saturation lowers it.
    retail_bearish = _clamp(50.0 - direction * 40.0 - (saturation - 50.0) * 0.3)
    return round(_clamp(0.55 * accumulation_score + 0.45 * retail_bearish), 2)
