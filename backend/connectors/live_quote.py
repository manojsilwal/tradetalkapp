"""
Hedged multi-provider live quote engine for S&P 500 symbols.

Strategy (keyless providers only):
  1. Per-symbol TTL cache (default 45s).
  2. Primary: yahoo fast_info in a thread; wait up to LIVE_QUOTE_HEDGE_DELAY_MS.
  3. On miss: fan out yahoo_chart + stooq (parallel, first valid wins).
  4. On still miss: FinCrawler GET /quote (last resort, when configured).
  5. On all-live-fail: data-lake last close (sp500-ingest cron fallback), stamped EOD/stale.

Every result carries a DataFreshness envelope via the Data Trust Layer.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import DataFreshness

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Tuple[float, Dict[str, Any], DataFreshness]] = {}
_CACHE_LOCK = threading.Lock()

_PRIMARY = "yahoo_fast_info"
_PARALLEL_FALLBACKS = ("yahoo_chart", "stooq")
_LAST_RESORT = "fincrawler"
_LIVE_PROVIDERS = frozenset({"yahoo_fast_info", "yahoo_chart"})


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _ttl_sec() -> float:
    try:
        from .spot import SPOT_CACHE_TTL_S
        return max(1.0, float(SPOT_CACHE_TTL_S))
    except Exception:
        return max(1.0, _env_float("LIVE_QUOTE_TTL_SEC", 60.0))


def _hedge_delay_sec() -> float:
    return max(0.05, _env_float("LIVE_QUOTE_HEDGE_DELAY_MS", 250.0) / 1000.0)


def _hard_deadline_sec() -> float:
    return max(0.5, _env_float("LIVE_QUOTE_HARD_DEADLINE_S", 3.0))


def _sp500_universe() -> frozenset[str]:
    from ..market_intel import _get_sp500_universe

    return frozenset(_get_sp500_universe())


def _validate_symbol(symbol: str) -> str:
    sym = (symbol or "").upper().strip()
    if not sym or len(sym) > 10:
        raise ValueError(f"invalid symbol {symbol!r}")
    if sym not in _sp500_universe():
        raise ValueError(f"{sym} is not in the S&P 500 universe")
    return sym


def _cache_get(sym: str) -> Optional[Tuple[Dict[str, Any], DataFreshness]]:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(sym)
        if entry and (now - entry[0]) < _ttl_sec():
            return entry[1], entry[2]
    return None


def _cache_put(sym: str, payload: Dict[str, Any], fresh: DataFreshness) -> None:
    with _CACHE_LOCK:
        _CACHE[sym] = (time.time(), payload, fresh)


def _row(
    symbol: str,
    *,
    price: float,
    change_pct: Optional[float] = None,
    previous_close: Optional[float] = None,
    source: str,
) -> Dict[str, Any]:
    return {
        "symbol": symbol,
        "price": round(float(price), 2),
        "change_pct": round(float(change_pct), 2) if change_pct is not None else None,
        "previous_close": round(float(previous_close), 2) if previous_close is not None else None,
        "source": source,
    }


def _stamp_live(source: str) -> DataFreshness:
    from ..freshness import assess_spot

    degraded = source not in _LIVE_PROVIDERS
    return assess_spot(source=source, captured_at=datetime.now(timezone.utc), degraded=degraded)


def _stamp_lake(trade_date: Optional[str]) -> DataFreshness:
    from ..freshness import assess

    return assess(
        data_class="session_pct",
        source="data_lake",
        as_of=trade_date,
        note="Last ingested EOD close from sp500-ingest data lake; not a live quote.",
    )


def _fetch_yahoo_fast_info(sym: str) -> Optional[Dict[str, Any]]:
    from ..market_intel import _fetch_single_rt_quote

    res = _fetch_single_rt_quote(sym)
    if not res:
        return None
    _, q = res
    price = q.get("price")
    if price is None or float(price) <= 0:
        return None
    return _row(
        sym,
        price=float(price),
        change_pct=q.get("pct"),
        previous_close=q.get("previous_close"),
        source=_PRIMARY,
    )


def _fetch_yahoo_chart(sym: str) -> Optional[Dict[str, Any]]:
    from .quote_fallbacks import _yahoo_chart_meta, yahoo_chart_change_pct

    meta = _yahoo_chart_meta(sym)
    if not meta:
        return None
    try:
        price = float(meta.get("regularMarketPrice"))
        if price <= 0:
            return None
    except (TypeError, ValueError):
        return None
    prev_raw = meta.get("chartPreviousClose", meta.get("previousClose"))
    prev = float(prev_raw) if prev_raw is not None else None
    pct = yahoo_chart_change_pct(sym)
    return _row(sym, price=price, change_pct=pct, previous_close=prev, source="yahoo_chart")


def _fetch_stooq(sym: str) -> Optional[Dict[str, Any]]:
    from .quote_fallbacks import _stooq_us_spot

    price = _stooq_us_spot(sym)
    if price is None or float(price) <= 0:
        return None
    return _row(sym, price=float(price), source="stooq")


def _fetch_fincrawler(sym: str) -> Optional[Dict[str, Any]]:
    from .quote_fallbacks import _fincrawler_quote_sync

    price = _fincrawler_quote_sync(sym)
    if price is None or float(price) <= 0:
        return None
    return _row(sym, price=float(price), source="fincrawler")


def _provider_fetch(name: str, sym: str) -> Optional[Dict[str, Any]]:
    from .yfinance_capability import should_attempt

    if name == _PRIMARY:
        if not should_attempt("price"):
            return None
        row = _fetch_yahoo_fast_info(sym)
        if row:
            from .yfinance_capability import record_success
            record_success("price")
        else:
            from .yfinance_capability import record_failure
            record_failure("price")
        return row
    if name == "yahoo_chart":
        if not should_attempt("chart"):
            return None
        row = _fetch_yahoo_chart(sym)
        if row:
            from .yfinance_capability import record_success
            record_success("chart")
        else:
            from .yfinance_capability import record_failure
            record_failure("chart")
        return row
    if name == "stooq":
        return _fetch_stooq(sym)
    if name == "fincrawler":
        return _fetch_fincrawler(sym)
    return None


def _parallel_fallbacks() -> List[str]:
    names = list(_PARALLEL_FALLBACKS)
    from .quote_fallbacks import _allow_yahoo_chart_fallback

    if not _allow_yahoo_chart_fallback():
        names = [n for n in names if n != "yahoo_chart"]
    return names


def _fincrawler_enabled() -> bool:
    from backend.fincrawler_client import fc

    return fc.enabled


async def _fetch_one_provider(name: str, sym: str) -> Optional[Dict[str, Any]]:
    try:
        return await asyncio.to_thread(_provider_fetch, name, sym)
    except Exception as e:
        logger.debug("[LiveQuote] provider %s failed %s: %s", name, sym, e)
        return None


async def _hedged_live_fetch(sym: str) -> Optional[Dict[str, Any]]:
    """Primary first; fan out fallbacks only after hedge delay."""
    from .yfinance_capability import should_attempt

    primary_task = None
    if should_attempt("price"):
        primary_task = asyncio.create_task(_fetch_one_provider(_PRIMARY, sym))
    done, pending = (
        await asyncio.wait({primary_task}, timeout=_hedge_delay_sec())
        if primary_task
        else (set(), set())
    )

    if primary_task and primary_task in done:
        try:
            row = primary_task.result()
            if row:
                return row
        except Exception:
            pass
    else:
        # Let primary continue in background while we may fan out.
        pass

    fallbacks = _parallel_fallbacks()
    fb_tasks = [asyncio.create_task(_fetch_one_provider(n, sym)) for n in fallbacks]
    wait_set = {t for t in ([primary_task] if primary_task else []) + fb_tasks if t is not None}
    deadline = _hard_deadline_sec() - _hedge_delay_sec()
    deadline = max(0.25, deadline)

    try:
        while wait_set:
            done, wait_set = await asyncio.wait(
                wait_set,
                timeout=deadline,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in done:
                try:
                    row = t.result()
                    if row:
                        for p in wait_set:
                            p.cancel()
                        return row
                except Exception:
                    pass
            if not wait_set:
                break
    finally:
        for t in fb_tasks:
            if not t.done():
                t.cancel()

    if _fincrawler_enabled():
        fc_row = await _fetch_one_provider(_LAST_RESORT, sym)
        if fc_row:
            return fc_row

    # Last chance: primary may have finished after fan-out loop.
    if primary_task is not None:
        if not primary_task.done():
            try:
                row = await asyncio.wait_for(primary_task, timeout=0.05)
                if row:
                    return row
            except Exception:
                pass
        else:
            try:
                row = primary_task.result()
                if row:
                    return row
            except Exception:
                pass

    return None


def latest_close_from_lake(symbol: str) -> Optional[Dict[str, Any]]:
    """Last EOD close from sp500-ingest data lake (daily_prices)."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return None
    try:
        from ..mcp_server.backend import backend

        rows = backend().query(
            f"""
            SELECT trade_date, close
            FROM daily_prices
            WHERE symbol = '{sym}'
            ORDER BY trade_date DESC
            LIMIT 1
            """
        )
        if not rows:
            return None
        row = rows[0]
        close = row.get("close")
        if close is None or float(close) <= 0:
            return None
        td = row.get("trade_date")
        if hasattr(td, "isoformat"):
            trade_date = td.isoformat()
        else:
            trade_date = str(td) if td else None
        return {"trade_date": trade_date, "close": float(close)}
    except Exception as e:
        logger.debug("[LiveQuote] lake fallback failed %s: %s", sym, e)
        return None


async def get_live_quote(symbol: str) -> Tuple[Dict[str, Any], DataFreshness]:
    """
    Return (payload, DataFreshness) for one S&P 500 symbol.
    Raises ValueError for invalid/non-universe symbols.
    Returns payload with price=None only when every source failed (caller may 503).
    """
    sym = _validate_symbol(symbol)

    cached = _cache_get(sym)
    if cached:
        payload, fresh = cached
        return {**payload, "data_freshness": fresh.model_dump()}, fresh

    live = await _hedged_live_fetch(sym)
    if live:
        fresh = _stamp_live(live["source"])
        payload = {**live, "data_freshness": fresh.model_dump()}
        _cache_put(sym, live, fresh)
        return payload, fresh

    lake = latest_close_from_lake(sym)
    if lake:
        fresh = _stamp_lake(lake.get("trade_date"))
        payload = {
            **_row(sym, price=lake["close"], source="data_lake"),
            "data_freshness": fresh.model_dump(),
        }
        _cache_put(sym, {k: v for k, v in payload.items() if k != "data_freshness"}, fresh)
        return payload, fresh

    from ..freshness import assess

    fresh = assess(data_class="live_quote", source="none")
    payload = {
        "symbol": sym,
        "price": None,
        "change_pct": None,
        "previous_close": None,
        "source": "none",
        "data_freshness": fresh.model_dump(),
    }
    return payload, fresh


async def get_live_quotes(symbols: List[str]) -> List[Dict[str, Any]]:
    """Bulk quotes: Yahoo parallel batch first, per-symbol hedged fetch for misses."""
    validated: List[str] = []
    for s in symbols:
        try:
            validated.append(_validate_symbol(s))
        except ValueError:
            continue

    if not validated:
        return []

    from ..market_intel import fetch_realtime_quotes

    rt = await asyncio.to_thread(fetch_realtime_quotes, validated, force=True)
    out: List[Dict[str, Any]] = []
    missing: List[str] = []

    for sym in validated:
        q = rt.get(sym)
        if q and q.get("price"):
            fresh = _stamp_live(_PRIMARY)
            row = {
                **_row(
                    sym,
                    price=float(q["price"]),
                    change_pct=q.get("pct"),
                    previous_close=q.get("previous_close"),
                    source=_PRIMARY,
                ),
                "data_freshness": fresh.model_dump(),
            }
            _cache_put(sym, {k: v for k, v in row.items() if k != "data_freshness"}, fresh)
            out.append(row)
        else:
            missing.append(sym)

    for sym in missing:
        payload, _ = await get_live_quote(sym)
        out.append(payload)

    return out
