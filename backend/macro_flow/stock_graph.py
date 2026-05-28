"""
S&P 500 stock-level capital flow graph — nodes are tickers; edges are co-flow / correlation links.

Uses batched OHLCV for the selected UI interval. Results are cached in-process (TTL) to avoid
re-downloading ~500 symbols on every request.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from ..data_lake.config import SP500_TICKERS, yfinance_symbol
from .flow_data import fetch_ohlcv_batch, yf_period_interval
from .sp500_sector_map import sector_for_ticker, ticker_sector_map

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_DEFAULT_TTL_SEC = 3600


def _cache_ttl() -> int:
    try:
        return max(60, int(os.environ.get("MACRO_STOCK_GRAPH_CACHE_TTL", str(_DEFAULT_TTL_SEC))))
    except ValueError:
        return _DEFAULT_TTL_SEC


def _max_tickers() -> int:
    try:
        return max(10, int(os.environ.get("MACRO_STOCK_GRAPH_MAX_TICKERS", "503")))
    except ValueError:
        return 503


def _top_k_per_node() -> int:
    try:
        return max(1, min(12, int(os.environ.get("MACRO_STOCK_GRAPH_TOP_K", "4"))))
    except ValueError:
        return 4


def _offline_mode() -> bool:
    return os.environ.get("MACRO_STOCK_GRAPH_OFFLINE", "").strip().lower() in ("1", "true", "yes")


def _period_return(df: pd.DataFrame) -> float:
    if df is None or df.empty or "Close" not in df.columns:
        return 0.0
    closes = df["Close"].dropna()
    if len(closes) < 2:
        return 0.0
    return float(closes.iloc[-1] / closes.iloc[0] - 1.0)


def _avg_dollar_volume(df: pd.DataFrame) -> float:
    if df is None or df.empty:
        return 0.0
    if "Volume" not in df.columns or "Close" not in df.columns:
        return 0.0
    dv = (df["Close"] * df["Volume"]).dropna()
    return float(dv.mean()) if len(dv) else 0.0


def _pick_frame(frames: Dict[str, pd.DataFrame], sym: str) -> pd.DataFrame:
    df = frames.get(sym)
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        alt = yfinance_symbol(sym)
        df = frames.get(alt)
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return pd.DataFrame()
    return df


def _offline_frames(tickers: List[str], interval: str) -> Dict[str, pd.DataFrame]:
    """Deterministic pseudo-OHLCV for unit tests (no network)."""
    period, yf_iv = yf_period_interval(interval)
    n = 30 if yf_iv == "1d" else 12
    out: Dict[str, pd.DataFrame] = {}
    for sym in tickers:
        seed = int(hashlib.md5(f"{sym}:{interval}".encode()).hexdigest()[:8], 16)
        rng = pd.Series(range(n), dtype=float)
        base = 50.0 + (seed % 200)
        drift = ((seed % 17) - 8) / 1000.0
        closes = base * (1.0 + drift) ** rng
        vol = 1e6 + (seed % 5) * 2e5
        out[sym] = pd.DataFrame(
            {
                "Open": closes,
                "High": closes * 1.01,
                "Low": closes * 0.99,
                "Close": closes,
                "Volume": vol,
            }
        )
    return out


def _bulk_download(tickers: List[str], interval: str) -> Dict[str, pd.DataFrame]:
    """Single yfinance download when available; falls back to per-ticker batch helper."""
    if _offline_mode():
        return _offline_frames(tickers, interval)

    period, yf_iv = yf_period_interval(interval)
    yf_syms = [yfinance_symbol(t) for t in tickers]
    sym_map = {yfinance_symbol(t): t.upper() for t in tickers}

    try:
        import yfinance as yf

        raw = yf.download(
            " ".join(sorted(set(yf_syms))),
            period=period,
            interval=yf_iv,
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        if raw is None or raw.empty:
            raise ValueError("empty download")

        out: Dict[str, pd.DataFrame] = {}
        if isinstance(raw.columns, pd.MultiIndex):
            for yf_sym in set(yf_syms):
                ui = sym_map.get(yf_sym, yf_sym.upper())
                try:
                    sub = raw[yf_sym].copy()
                    if sub is not None and not sub.empty:
                        out[ui] = sub
                except (KeyError, TypeError):
                    continue
        else:
            ui = tickers[0].upper() if tickers else "SPY"
            out[ui] = raw.copy()
        if out:
            return out
    except Exception as e:
        logger.warning("[stock_graph] bulk yfinance failed, falling back to batch: %s", e)

    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        raise RuntimeError("sync fetch required — call build_stock_flow_graph via async wrapper")

    return asyncio.run(fetch_ohlcv_batch(tickers, interval))


async def build_stock_flow_graph_async(
    interval: str,
    *,
    db_path: str | None = None,
) -> Dict[str, Any]:
    import asyncio

    iv = (interval or "1w").strip().lower()
    cache_key = f"stock:{iv}"
    now = time.time()
    hit = _CACHE.get(cache_key)
    if hit and (now - hit[0]) < _cache_ttl():
        return hit[1]

    sector_map = ticker_sector_map()
    universe = [t.upper() for t in SP500_TICKERS[: _max_tickers()]]
    top_k = _top_k_per_node()

    if _offline_mode():
        frames = _offline_frames(universe, iv)
    else:
        frames = await asyncio.to_thread(_bulk_download, universe, iv)
        nonempty = sum(1 for df in frames.values() if isinstance(df, pd.DataFrame) and not df.empty)
        if nonempty < max(20, len(universe) // 10):
            frames = await fetch_ohlcv_batch(universe, iv)

    nodes: List[Dict[str, Any]] = []
    rets: Dict[str, pd.Series] = {}
    meta: Dict[str, Dict[str, float]] = {}

    for sym in universe:
        df = _pick_frame(frames, sym)
        ret = _period_return(df)
        dv = _avg_dollar_volume(df)
        flow = ret * (dv ** 0.25) if dv > 0 else ret
        if not df.empty and "Close" in df.columns:
            rets[sym] = df["Close"].pct_change().dropna()
        meta[sym] = {"return_pct": ret, "flow_score": flow, "dollar_vol": dv}
        nodes.append(
            {
                "id": sym,
                "ticker": sym,
                "sector": sector_for_ticker(sym, sector_map),
                "period_return_pct": round(ret * 100.0, 2),
                "flow_score": round(float(flow), 6),
            }
        )

    edge_candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for src in universe:
        rs = rets.get(src)
        if rs is None or len(rs) < 5:
            continue
        scored: List[Tuple[str, float, float]] = []
        src_flow = abs(meta[src]["flow_score"])
        for tgt in universe:
            if tgt == src:
                continue
            rt = rets.get(tgt)
            if rt is None or len(rt) < 5:
                continue
            aligned = rs.align(rt, join="inner")
            if len(aligned[0]) < 5:
                continue
            corr = float(aligned[0].corr(aligned[1]))
            if corr != corr or corr < 0.2:  # NaN check
                continue
            tgt_flow = abs(meta[tgt]["flow_score"])
            magnitude = abs(corr) * (src_flow + tgt_flow) * 0.5
            if magnitude <= 0:
                continue
            scored.append((tgt, corr, magnitude))
        scored.sort(key=lambda x: x[2], reverse=True)
        for tgt, corr, magnitude in scored[:top_k]:
            key = (src, tgt)
            edge_candidates[key] = {
                "source": src,
                "target": tgt,
                "value": round(magnitude, 6),
                "correlation": round(corr, 4),
            }

    edges: List[Dict[str, Any]] = []
    seen_pairs: set[frozenset] = set()
    for (src, tgt), row in edge_candidates.items():
        rev = edge_candidates.get((tgt, src))
        pair = frozenset((src, tgt))
        if pair in seen_pairs:
            continue
        bidirectional = rev is not None
        if bidirectional:
            seen_pairs.add(pair)
            row = {
                **row,
                "bidirectional": True,
                "value": round((row["value"] + rev["value"]) / 2.0, 6),
                "correlation": round((row["correlation"] + rev["correlation"]) / 2.0, 4),
            }
        else:
            row = {**row, "bidirectional": False}
        edges.append(row)

    payload: Dict[str, Any] = {
        "interval": iv,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "as_of": now,
        "note": (
            "Edges link tickers with correlated period returns and shared capital-flow intensity "
            f"(top {top_k} neighbors per symbol). Bidirectional when both directions rank in top-{top_k}."
        ),
    }
    _CACHE[cache_key] = (now, payload)
    return payload


def build_stock_flow_graph(interval: str, *, db_path: str | None = None) -> Dict[str, Any]:
    """Sync entry — uses cache; intended for tests with MACRO_STOCK_GRAPH_OFFLINE=1."""
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(build_stock_flow_graph_async(interval, db_path=db_path))

    # Called from async route — should use async variant
    raise RuntimeError("use build_stock_flow_graph_async from async context")


def clear_stock_graph_cache() -> None:
    _CACHE.clear()
