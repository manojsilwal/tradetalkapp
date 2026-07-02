"""
Free multi-provider options chain connector with EOD put/call aggregates.

Provider priority:
  1. Yahoo v7 options (keyless; 429 → fall through)
  2. CBOE delayed quotes (~15 min)
  3. Nasdaq option-chain (browser headers)
  4. Alpha Vantage HISTORICAL_OPTIONS (optional key; last resort)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from ..connector_cache import get_cached, set_cached
from ..data_errors import InsufficientDataError
from .base import DataConnector
from .fetch_utils import request_with_backoff

logger = logging.getLogger(__name__)

_UNUSUAL_VOL_OI_THRESHOLD = 3.0
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

ProviderResult = Union[Dict[str, Any], Dict[str, bool]]


def _options_enabled() -> bool:
    return os.environ.get("OPTIONS_FLOW_ENABLE", "1").strip().lower() in ("1", "true", "yes", "on")


def _yahoo_allowed() -> bool:
    return os.environ.get("OPTIONS_FLOW_ALLOW_YAHOO", "1").strip().lower() in ("1", "true", "yes", "on")


def _daily_cache_ttl() -> int:
    """Cache aggressively — one fetch per symbol per trading day."""
    try:
        from ..market_calendar import is_trading_day, now_et

        now = now_et()
        today = now.date()
        if is_trading_day(today) and now.hour >= 16:
            return 86400
        if is_trading_day(today):
            return max(3600, int((now.replace(hour=16, minute=0, second=0) - now).total_seconds()))
    except Exception:
        pass
    return 86400


def _contract(
    strike: float,
    volume: Optional[float],
    open_interest: Optional[float],
    iv: Optional[float],
    bid: Optional[float],
    ask: Optional[float],
    last: Optional[float],
    expiry: Optional[str] = None,
    option_type: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "strike": strike,
        "volume": int(volume or 0),
        "open_interest": int(open_interest or 0),
        "iv": float(iv) if iv is not None else None,
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
        "last": float(last) if last is not None else None,
        **({"expiry": expiry} if expiry else {}),
        **({"type": option_type} if option_type else {}),
    }


def _parse_yahoo_contract(raw: dict) -> Dict[str, Any]:
    return _contract(
        strike=float(raw.get("strike") or 0),
        volume=raw.get("volume"),
        open_interest=raw.get("openInterest"),
        iv=raw.get("impliedVolatility"),
        bid=raw.get("bid"),
        ask=raw.get("ask"),
        last=raw.get("lastPrice"),
    )


def _fetch_yahoo_options_sync(symbol: str) -> ProviderResult:
    sym = (symbol or "").upper().strip()
    if not sym:
        return {"unavailable": True, "reason": "empty symbol"}
    url = f"https://query1.finance.yahoo.com/v7/finance/options/{sym}"
    headers = {"User-Agent": _BROWSER_UA}
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=12) as resp:
            if getattr(resp, "status", 200) == 429:
                return {"unavailable": True, "reason": "yahoo_429"}
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return {"unavailable": True, "reason": "yahoo_429"}
        return {"unavailable": True, "reason": f"yahoo_http_{e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"unavailable": True, "reason": f"yahoo_error:{e}"}

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"unavailable": True, "reason": "yahoo_parse_error"}

    results = (payload.get("optionChain") or {}).get("result") or []
    if not results:
        return {"unavailable": True, "reason": "yahoo_empty_result"}

    block = results[0]
    quote = block.get("quote") or {}
    spot = quote.get("regularMarketPrice") or quote.get("postMarketPrice")
    if spot is None:
        try:
            from .spot import resolve_spot

            spot_q = resolve_spot(sym)
            if spot_q is not None and spot_q.price:
                spot = spot_q.price
        except Exception:
            spot = None
    expirations: List[Dict[str, Any]] = []
    for opt_block in block.get("options") or []:
        exp_ts = opt_block.get("expirationDate")
        expiry = (
            datetime.fromtimestamp(int(exp_ts), tz=timezone.utc).strftime("%Y-%m-%d")
            if exp_ts is not None
            else None
        )
        calls = [_parse_yahoo_contract(c) for c in (opt_block.get("calls") or [])]
        puts = [_parse_yahoo_contract(p) for p in (opt_block.get("puts") or [])]
        if calls or puts:
            expirations.append({"expiry": expiry, "calls": calls, "puts": puts})

    if not expirations:
        return {"unavailable": True, "reason": "yahoo_no_contracts"}

    return {
        "symbol": sym,
        "spot": float(spot) if spot is not None else None,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "yahoo",
        "expirations": expirations,
        "partial": len(expirations) == 1,
    }


def _fetch_cboe_options_sync(symbol: str) -> ProviderResult:
    sym = (symbol or "").upper().strip()
    url = f"https://cdn.cboe.com/api/global/delayed_quotes/options/{sym}.json"
    try:
        resp = request_with_backoff("GET", url, timeout=12, headers={"User-Agent": _BROWSER_UA})
        payload = resp.json()
    except Exception as e:
        return {"unavailable": True, "reason": f"cboe_error:{e}"}

    data = payload.get("data") or payload
    spot = data.get("current_price") or data.get("last_trade_price") or data.get("spot")
    options = data.get("options") or data.get("option_chain") or []
    if not options and isinstance(data.get("calls"), list):
        options = [{"expiry": data.get("expiration"), "calls": data.get("calls"), "puts": data.get("puts")}]

    expirations: List[Dict[str, Any]] = []
    if isinstance(options, dict):
        for expiry, chain in options.items():
            calls_raw = chain.get("calls") if isinstance(chain, dict) else []
            puts_raw = chain.get("puts") if isinstance(chain, dict) else []
            calls = [
                _contract(
                    strike=float(c.get("strike") or c.get("strike_price") or 0),
                    volume=c.get("volume"),
                    open_interest=c.get("open_interest") or c.get("openInterest"),
                    iv=c.get("iv") or c.get("implied_volatility"),
                    bid=c.get("bid"),
                    ask=c.get("ask"),
                    last=c.get("last") or c.get("last_price"),
                )
                for c in (calls_raw or [])
            ]
            puts = [
                _contract(
                    strike=float(p.get("strike") or p.get("strike_price") or 0),
                    volume=p.get("volume"),
                    open_interest=p.get("open_interest") or p.get("openInterest"),
                    iv=p.get("iv") or p.get("implied_volatility"),
                    bid=p.get("bid"),
                    ask=p.get("ask"),
                    last=p.get("last") or p.get("last_price"),
                )
                for p in (puts_raw or [])
            ]
            if calls or puts:
                expirations.append({"expiry": str(expiry), "calls": calls, "puts": puts})
    elif isinstance(options, list):
        for block in options:
            expiry = block.get("expiration") or block.get("expiry") or block.get("expiration_date")
            calls = [
                _contract(
                    strike=float(c.get("strike") or c.get("strike_price") or 0),
                    volume=c.get("volume"),
                    open_interest=c.get("open_interest") or c.get("openInterest"),
                    iv=c.get("iv") or c.get("implied_volatility"),
                    bid=c.get("bid"),
                    ask=c.get("ask"),
                    last=c.get("last") or c.get("last_price"),
                )
                for c in (block.get("calls") or [])
            ]
            puts = [
                _contract(
                    strike=float(p.get("strike") or p.get("strike_price") or 0),
                    volume=p.get("volume"),
                    open_interest=p.get("open_interest") or p.get("openInterest"),
                    iv=p.get("iv") or p.get("implied_volatility"),
                    bid=p.get("bid"),
                    ask=p.get("ask"),
                    last=p.get("last") or p.get("last_price"),
                )
                for p in (block.get("puts") or [])
            ]
            if calls or puts:
                expirations.append({"expiry": str(expiry) if expiry else None, "calls": calls, "puts": puts})

    if not expirations:
        return {"unavailable": True, "reason": "cboe_no_contracts"}

    return {
        "symbol": sym,
        "spot": float(spot) if spot is not None else None,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "cboe",
        "expirations": expirations,
        "partial": False,
    }


def _fetch_nasdaq_options_sync(symbol: str) -> ProviderResult:
    sym = (symbol or "").upper().strip()
    url = f"https://api.nasdaq.com/api/quote/{sym}/option-chain"
    params = {"assetclass": "stocks", "limit": 9999}
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/option-chain",
    }
    try:
        resp = request_with_backoff(
            "GET",
            f"{url}?{urllib.parse.urlencode(params)}",
            timeout=15,
            headers=headers,
        )
        payload = resp.json()
    except Exception as e:
        return {"unavailable": True, "reason": f"nasdaq_error:{e}"}

    body = payload.get("data") or {}
    rows = body.get("table") or body.get("rows") or body.get("optionChain") or []
    spot = body.get("lastSale") or body.get("last") or body.get("primaryData", {}).get("lastSalePrice")
    expirations_map: Dict[str, Dict[str, List]] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        expiry = row.get("expiryDate") or row.get("expiry") or row.get("expirationDate") or "unknown"
        side = (row.get("type") or row.get("optionType") or "").lower()
        c = _contract(
            strike=float(row.get("strike") or row.get("strikePrice") or 0),
            volume=row.get("volume") or row.get("vol"),
            open_interest=row.get("openInterest") or row.get("open_interest"),
            iv=row.get("iv") or row.get("impliedVolatility"),
            bid=row.get("bid"),
            ask=row.get("ask"),
            last=row.get("last") or row.get("lastSale"),
        )
        bucket = expirations_map.setdefault(str(expiry), {"calls": [], "puts": []})
        if side.startswith("c"):
            bucket["calls"].append(c)
        elif side.startswith("p"):
            bucket["puts"].append(c)

    expirations = [
        {"expiry": k, "calls": v["calls"], "puts": v["puts"]}
        for k, v in expirations_map.items()
        if v["calls"] or v["puts"]
    ]
    if not expirations:
        return {"unavailable": True, "reason": "nasdaq_no_contracts"}

    return {
        "symbol": sym,
        "spot": float(spot) if spot is not None else None,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "nasdaq",
        "expirations": expirations,
        "partial": False,
    }


def _fetch_alpha_vantage_options_sync(symbol: str) -> ProviderResult:
    key = (os.environ.get("ALPHAVANTAGE_API_KEY") or "").strip()
    if not key:
        return {"unavailable": True, "reason": "alphavantage_key_missing"}
    sym = (symbol or "").upper().strip()
    params = urllib.parse.urlencode({"function": "HISTORICAL_OPTIONS", "symbol": sym, "apikey": key})
    url = f"https://www.alphavantage.co/query?{params}"
    try:
        resp = request_with_backoff("GET", url, timeout=20, headers={"User-Agent": _BROWSER_UA})
        payload = resp.json()
    except Exception as e:
        return {"unavailable": True, "reason": f"alphavantage_error:{e}"}

    if payload.get("Note") or payload.get("Information"):
        return {"unavailable": True, "reason": "alphavantage_rate_limit"}
    rows = payload.get("data") or payload.get("option_chain") or []
    if not rows:
        return {"unavailable": True, "reason": "alphavantage_empty"}

    expirations_map: Dict[str, Dict[str, List]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        expiry = row.get("expiration") or row.get("expiration_date") or "unknown"
        side = (row.get("type") or row.get("option_type") or "").lower()
        c = _contract(
            strike=float(row.get("strike") or row.get("strike_price") or 0),
            volume=row.get("volume"),
            open_interest=row.get("open_interest"),
            iv=row.get("implied_volatility") or row.get("iv"),
            bid=row.get("bid"),
            ask=row.get("ask"),
            last=row.get("last") or row.get("mark"),
        )
        bucket = expirations_map.setdefault(str(expiry), {"calls": [], "puts": []})
        if side.startswith("c"):
            bucket["calls"].append(c)
        elif side.startswith("p"):
            bucket["puts"].append(c)

    expirations = [
        {"expiry": k, "calls": v["calls"], "puts": v["puts"]}
        for k, v in expirations_map.items()
        if v["calls"] or v["puts"]
    ]
    if not expirations:
        return {"unavailable": True, "reason": "alphavantage_no_contracts"}

    return {
        "symbol": sym,
        "spot": None,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "alphavantage",
        "expirations": expirations,
        "partial": True,
    }


def _atm_iv(contracts: List[Dict], spot: Optional[float]) -> Optional[float]:
    if not contracts or spot is None or spot <= 0:
        return None
    best = min(contracts, key=lambda c: abs(float(c.get("strike") or 0) - spot))
    iv = best.get("iv")
    return float(iv) if iv is not None else None


def compute_options_aggregates(chain: Dict[str, Any]) -> Dict[str, Any]:
    """EOD-style aggregates from a normalized chain."""
    spot = chain.get("spot")
    total_call_vol = total_put_vol = 0
    total_call_oi = total_put_oi = 0
    all_calls: List[Dict] = []
    all_puts: List[Dict] = []
    unusual: List[Dict[str, Any]] = []

    for exp in chain.get("expirations") or []:
        expiry = exp.get("expiry")
        for side, contracts in (("call", exp.get("calls") or []), ("put", exp.get("puts") or [])):
            for c in contracts:
                vol = int(c.get("volume") or 0)
                oi = int(c.get("open_interest") or 0)
                if side == "call":
                    total_call_vol += vol
                    total_call_oi += oi
                    all_calls.append(c)
                else:
                    total_put_vol += vol
                    total_put_oi += oi
                    all_puts.append(c)
                ratio = vol / max(oi, 1)
                if ratio >= _UNUSUAL_VOL_OI_THRESHOLD and vol > 0:
                    bid = c.get("bid")
                    ask = c.get("ask")
                    last = c.get("last")
                    premium = last if last is not None else (
                        (float(bid) + float(ask)) / 2.0 if bid is not None and ask is not None else None
                    )
                    unusual.append({
                        "strike": c.get("strike"),
                        "expiry": expiry,
                        "type": side,
                        "volume": vol,
                        "open_interest": oi,
                        "vol_oi_ratio": round(ratio, 2),
                        "premium": premium,
                    })

    pcr_vol = round(total_put_vol / total_call_vol, 4) if total_call_vol > 0 else None
    pcr_oi = round(total_put_oi / total_call_oi, 4) if total_call_oi > 0 else None
    iv_call = _atm_iv(all_calls, spot)
    iv_put = _atm_iv(all_puts, spot)
    iv_skew = round(iv_put - iv_call, 4) if iv_put is not None and iv_call is not None else None

    unusual.sort(key=lambda x: x.get("vol_oi_ratio") or 0, reverse=True)
    unusual_score = min(100.0, len(unusual) * 8.0 + (max(0.0, (pcr_vol or 1.0) - 1.0) * 40.0))

    if pcr_vol is not None:
        if pcr_vol >= 1.2:
            bias = "bearish"
        elif pcr_vol <= 0.8:
            bias = "bullish"
        else:
            bias = "neutral"
    else:
        bias = "neutral"

    return {
        "total_call_volume": total_call_vol,
        "total_put_volume": total_put_vol,
        "total_call_oi": total_call_oi,
        "total_put_oi": total_put_oi,
        "put_call_volume_ratio": pcr_vol,
        "put_call_oi_ratio": pcr_oi,
        "iv_atm_call": iv_call,
        "iv_atm_put": iv_put,
        "iv_skew": iv_skew,
        "unusual_contracts": unusual[:20],
        "unusual_activity_score": round(unusual_score, 1),
        "net_premium_bias": bias,
    }


def _contract_mid(contract: Dict[str, Any]) -> Optional[float]:
    last = contract.get("last")
    if last is not None:
        return float(last)
    bid, ask = contract.get("bid"), contract.get("ask")
    if bid is not None and ask is not None:
        return (float(bid) + float(ask)) / 2.0
    return None


def _days_to_expiry(expiry: Optional[str], *, ref: Optional[date] = None) -> Optional[int]:
    if not expiry:
        return None
    try:
        exp_date = date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return None
    today = ref or datetime.now(timezone.utc).date()
    return (exp_date - today).days


def _pcr_label(ratio: Optional[float]) -> str:
    if ratio is None:
        return "unknown"
    if ratio >= 1.05:
        return "bearish"
    if ratio <= 0.95:
        return "bullish"
    return "neutral"


def compute_options_intelligence(
    chain: Dict[str, Any],
    aggregates: Dict[str, Any],
) -> Dict[str, Any]:
    """Strike walls, expected move, bull/bear split, near-expiry OI, narrative."""
    spot = chain.get("spot")
    call_oi_by_strike: Dict[float, int] = {}
    put_oi_by_strike: Dict[float, int] = {}
    near_expiry_oi = 0
    total_oi_tracked = 0
    nearest_exp_block: Optional[Dict[str, Any]] = None
    nearest_days = 10_000

    for exp in chain.get("expirations") or []:
        expiry = exp.get("expiry")
        days = _days_to_expiry(expiry)
        if days is not None and 0 <= days < nearest_days:
            nearest_days = days
            nearest_exp_block = exp
        for side, contracts in (("call", exp.get("calls") or []), ("put", exp.get("puts") or [])):
            bucket = call_oi_by_strike if side == "call" else put_oi_by_strike
            for c in contracts:
                strike = float(c.get("strike") or 0)
                oi = int(c.get("open_interest") or 0)
                bucket[strike] = bucket.get(strike, 0) + oi
                total_oi_tracked += oi
                if days is not None and 0 <= days <= 7:
                    near_expiry_oi += oi

    total_call_oi = int(aggregates.get("total_call_oi") or 0)
    total_put_oi = int(aggregates.get("total_put_oi") or 0)
    total_call_vol = int(aggregates.get("total_call_volume") or 0)
    total_put_vol = int(aggregates.get("total_put_volume") or 0)
    oi_total = total_call_oi + total_put_oi
    vol_total = total_call_vol + total_put_vol

    top_calls = [
        {"strike": k, "open_interest": v}
        for k, v in sorted(call_oi_by_strike.items(), key=lambda x: -x[1])[:3]
        if v > 0
    ]
    top_puts = [
        {"strike": k, "open_interest": v}
        for k, v in sorted(put_oi_by_strike.items(), key=lambda x: -x[1])[:3]
        if v > 0
    ]

    expected_move_usd = None
    expected_move_pct = None
    nearest_expiry = None
    if nearest_exp_block and spot and float(spot) > 0:
        nearest_expiry = nearest_exp_block.get("expiry")
        calls = nearest_exp_block.get("calls") or []
        puts = nearest_exp_block.get("puts") or []
        if calls and puts:
            atm_call = min(calls, key=lambda c: abs(float(c.get("strike") or 0) - float(spot)))
            atm_put = min(puts, key=lambda c: abs(float(c.get("strike") or 0) - float(spot)))
            call_mid = _contract_mid(atm_call)
            put_mid = _contract_mid(atm_put)
            if call_mid is not None and put_mid is not None:
                straddle = call_mid + put_mid
                expected_move_usd = round(straddle, 2)
                expected_move_pct = round((straddle / float(spot)) * 100.0, 2)

    iv_vals = [
        float(c.get("iv"))
        for exp in chain.get("expirations") or []
        for c in (exp.get("calls") or []) + (exp.get("puts") or [])
        if c.get("iv") is not None and float(c.get("iv") or 0) > 0
    ]
    iv_rank_proxy = None
    atm_iv = aggregates.get("iv_atm_put") or aggregates.get("iv_atm_call")
    if iv_vals and atm_iv is not None:
        iv_rank_proxy = round(
            100.0 * sum(1 for v in iv_vals if v <= float(atm_iv)) / len(iv_vals),
            1,
        )

    pcr_oi = aggregates.get("put_call_oi_ratio")
    pcr_vol = aggregates.get("put_call_volume_ratio")
    oi_bias = _pcr_label(pcr_oi)
    vol_bias = _pcr_label(pcr_vol)

    narrative = build_options_narrative(
        aggregates,
        oi_bias=oi_bias,
        vol_bias=vol_bias,
        top_calls=top_calls,
        top_puts=top_puts,
        expected_move_pct=expected_move_pct,
        near_expiry_oi_pct=(
            round(100.0 * near_expiry_oi / total_oi_tracked, 1) if total_oi_tracked else None
        ),
    )

    return {
        "call_oi_pct": round(100.0 * total_call_oi / oi_total, 1) if oi_total else None,
        "put_oi_pct": round(100.0 * total_put_oi / oi_total, 1) if oi_total else None,
        "call_volume_pct": round(100.0 * total_call_vol / vol_total, 1) if vol_total else None,
        "put_volume_pct": round(100.0 * total_put_vol / vol_total, 1) if vol_total else None,
        "expected_move_usd": expected_move_usd,
        "expected_move_pct": expected_move_pct,
        "nearest_expiry": nearest_expiry,
        "top_call_strikes": top_calls,
        "top_put_strikes": top_puts,
        "near_expiry_oi_pct": (
            round(100.0 * near_expiry_oi / total_oi_tracked, 1) if total_oi_tracked else None
        ),
        "near_expiry_flag": bool(near_expiry_oi > 0 and total_oi_tracked > 0),
        "iv_rank_proxy": iv_rank_proxy,
        "oi_sentiment": oi_bias,
        "volume_sentiment": vol_bias,
        "narrative_summary": narrative,
        "spot_price_usd": float(spot) if spot is not None else None,
    }


def build_options_narrative(
    aggregates: Dict[str, Any],
    *,
    oi_bias: str,
    vol_bias: str,
    top_calls: List[Dict[str, Any]],
    top_puts: List[Dict[str, Any]],
    expected_move_pct: Optional[float],
    near_expiry_oi_pct: Optional[float],
) -> str:
    """One-line + short paragraph for DT header and chat tool."""
    pcr_oi = aggregates.get("put_call_oi_ratio")
    pcr_vol = aggregates.get("put_call_volume_ratio")
    parts: List[str] = []
    if pcr_oi is not None:
        parts.append(f"Open interest leans {oi_bias} (P/C OI {pcr_oi:.2f})")
    if pcr_vol is not None and vol_bias != oi_bias:
        parts.append(f"today's volume leans {vol_bias} (P/C vol {pcr_vol:.2f})")
    elif pcr_vol is not None and vol_bias == oi_bias:
        parts.append(f"volume confirms {vol_bias} bias (P/C vol {pcr_vol:.2f})")
    headline = "; ".join(parts) if parts else "Options flow captured."

    extras: List[str] = []
    if expected_move_pct is not None:
        extras.append(f"market-implied move ±{expected_move_pct:.1f}% to nearest expiry")
    if top_calls:
        strikes = ", ".join(f"${int(s['strike'])}" for s in top_calls[:2])
        extras.append(f"call OI walls at {strikes}")
    if top_puts:
        strikes = ", ".join(f"${int(s['strike'])}" for s in top_puts[:2])
        extras.append(f"put OI support at {strikes}")
    if near_expiry_oi_pct is not None and near_expiry_oi_pct >= 15:
        extras.append(f"{near_expiry_oi_pct:.0f}% of OI expires within 7 days (gamma/vol risk)")
    if extras:
        return f"{headline}. {'; '.join(extras)}."
    return f"{headline}."


def format_options_flow_for_chat(payload: Dict[str, Any]) -> str:
    """Readable options intelligence for chat tool responses."""
    if not payload.get("available"):
        sym = payload.get("symbol") or payload.get("ticker") or "?"
        return f"Options flow unavailable for {sym} ({payload.get('reason', 'no chain data')})."

    sym = payload.get("symbol") or "?"
    lines = [f"Options intelligence for {sym}:"]
    if payload.get("narrative_summary"):
        lines.append(payload["narrative_summary"])

    lines.append("")
    lines.append("Bull vs bear (contracts):")
    if payload.get("total_call_oi") is not None:
        lines.append(
            f"  Open interest — calls: {payload.get('total_call_oi', 0):,} "
            f"({payload.get('call_oi_pct', '—')}%) | puts: {payload.get('total_put_oi', 0):,} "
            f"({payload.get('put_oi_pct', '—')}%) | P/C OI {payload.get('put_call_oi_ratio', '—')}"
        )
    if payload.get("total_call_volume") is not None:
        lines.append(
            f"  Volume today — calls: {payload.get('total_call_volume', 0):,} "
            f"({payload.get('call_volume_pct', '—')}%) | puts: {payload.get('total_put_volume', 0):,} "
            f"({payload.get('put_volume_pct', '—')}%) | P/C vol {payload.get('put_call_volume_ratio', '—')}"
        )

    if payload.get("expected_move_pct") is not None:
        spot = payload.get("spot_price_usd") or payload.get("spot")
        move = payload.get("expected_move_usd")
        exp = payload.get("nearest_expiry") or "nearest expiry"
        if spot and move:
            lo = float(spot) - float(move)
            hi = float(spot) + float(move)
            lines.append(
                f"Expected move ({exp}): ±{payload['expected_move_pct']:.1f}% "
                f"(±${move:.2f}) → roughly ${lo:.0f}–${hi:.0f}"
            )

    atm_c = payload.get("iv_atm_call")
    atm_p = payload.get("iv_atm_put")
    if atm_c is not None or atm_p is not None:
        iv_line = f"ATM IV: call {atm_c:.0%}" if atm_c else ""
        if atm_p is not None:
            iv_line += f" / put {atm_p:.0%}" if iv_line else f"ATM IV put {atm_p:.0%}"
        if payload.get("iv_rank_proxy") is not None:
            iv_line += f" (IV percentile proxy {payload['iv_rank_proxy']:.0f}%)"
        lines.append(iv_line)

    for label, key in (("Resistance (top call OI)", "top_call_strikes"), ("Support (top put OI)", "top_put_strikes")):
        rows = payload.get(key) or []
        if rows:
            detail = ", ".join(f"${int(r['strike'])} ({r['open_interest']:,} OI)" for r in rows[:3])
            lines.append(f"{label}: {detail}")

    unusual = payload.get("unusual_contracts") or []
    if unusual:
        lines.append("Unusual activity (vol/OI ≥ 3×):")
        for row in unusual[:5]:
            prem = row.get("premium")
            prem_s = f" ~${prem:.2f}" if prem is not None else ""
            lines.append(
                f"  {row.get('type', '?').upper()} ${row.get('strike')} "
                f"exp {row.get('expiry')} vol/OI {row.get('vol_oi_ratio')}{prem_s}"
            )

    if payload.get("near_expiry_flag"):
        lines.append(
            f"Near-term expiry: {payload.get('near_expiry_oi_pct', 0):.0f}% of OI within 7 days."
        )
    lines.append(f"Source: {payload.get('source', 'unknown')} (research only, not advice).")
    return "\n".join(lines)


def options_summary_line(aggregates: Dict[str, Any], source: Optional[str]) -> str:
    pcr = aggregates.get("put_call_volume_ratio")
    src = source or "unknown"
    score = aggregates.get("unusual_activity_score")
    parts = []
    if pcr is not None:
        parts.append(f"P/C vol {pcr:.2f}")
    if score is not None and score >= 30:
        parts.append(f"unusual activity {score:.0f}/100")
    if aggregates.get("net_premium_bias"):
        parts.append(str(aggregates["net_premium_bias"]))
    detail = "; ".join(parts) if parts else "options flow captured"
    return f"Options ({src}): {detail}."


def options_to_brain_overlay(aggregates: Dict[str, Any]) -> Dict[str, float]:
    """Map aggregates to brain live-input / passthrough keys."""
    bias_map = {"bullish": 1.0, "neutral": 0.0, "bearish": -1.0}
    out: Dict[str, float] = {}
    for key in ("put_call_oi_ratio", "put_call_volume_ratio", "iv_skew", "unusual_activity_score"):
        val = aggregates.get(key)
        if val is not None:
            out[key] = float(val)
    bias = aggregates.get("net_premium_bias")
    if bias in bias_map:
        out["options_net_premium_bias_num"] = bias_map[bias]
    return out


def _pcr_signal_description(pcr: float) -> Tuple[str, str]:
    """Legacy market-intel signal labels from put/call volume ratio."""
    if pcr >= 1.5:
        return "EXTREME_FEAR", f"{pcr:.2f} puts/call — extreme bearish hedging."
    if pcr >= 1.2:
        return "BEARISH_FLOW", f"{pcr:.2f} puts/call — elevated fear."
    if pcr >= 0.9:
        return "NEUTRAL", f"{pcr:.2f} puts/call — balanced sentiment."
    if pcr >= 0.7:
        return "BULLISH_FLOW", f"{pcr:.2f} puts/call — options traders leaning bullish."
    return "EXTREME_GREED", f"{pcr:.2f} puts/call — very low fear / high risk appetite."


def to_legacy_market_intel_payload(aggregates: Dict[str, Any]) -> Dict[str, Any]:
    """Map connector aggregates to the legacy market_intel / narrative-radar shape."""
    pcr = aggregates.get("put_call_volume_ratio")
    if pcr is None:
        return {"error": "no put/call volume ratio"}
    signal, desc = _pcr_signal_description(float(pcr))
    expiries = []
    for row in aggregates.get("unusual_contracts") or []:
        exp = row.get("expiry")
        if exp and exp not in expiries:
            expiries.append(exp)
    return {
        "spy_put_call_ratio": round(float(pcr), 3),
        "put_call_ratio": round(float(pcr), 3),
        "calls_volume": aggregates.get("total_call_volume"),
        "puts_volume": aggregates.get("total_put_volume"),
        "signal": signal,
        "description": desc,
        "expiry_used": expiries[0] if expiries else None,
        "source": aggregates.get("source"),
        "as_of": aggregates.get("as_of"),
    }


def fetch_options_flow_sync(ticker: str = "SPY") -> Dict[str, Any]:
    """Sync fetch for market_intel refresh and narrative radar (thread-safe)."""
    if not _options_enabled():
        return {"error": "disabled"}
    try:
        raw = asyncio.run(OptionsFlowConnector().fetch_data(ticker=ticker))
    except InsufficientDataError as e:
        return {"error": str(e.message if hasattr(e, "message") else e)}
    except Exception as e:
        logger.debug("[options_flow] sync fetch failed for %s: %s", ticker, e)
        return {"error": str(e)}
    if raw.get("available") is False:
        return {"error": raw.get("reason", "unavailable")}
    return to_legacy_market_intel_payload(raw)


class OptionsFlowConnector(DataConnector):
    """Free multi-provider options chain + EOD aggregates."""

    async def fetch_data(self, ticker: str = "SPY", **kwargs) -> Dict[str, Any]:
        ticker = kwargs.get("ticker", ticker).upper()
        if not _options_enabled():
            return {"available": False, "reason": "disabled", "ticker": ticker}

        ttl = _daily_cache_ttl()
        cached = get_cached("options_flow", ticker, ttl=ttl)
        if cached is not None:
            return cached

        providers: List[Tuple[str, Any]] = []
        if _yahoo_allowed():
            providers.append(("yahoo", _fetch_yahoo_options_sync))
        providers.extend([
            ("cboe", _fetch_cboe_options_sync),
            ("nasdaq", _fetch_nasdaq_options_sync),
            ("alphavantage", _fetch_alpha_vantage_options_sync),
        ])

        errors: List[Dict[str, str]] = []
        chain: Optional[Dict[str, Any]] = None
        for name, fn in providers:
            try:
                result = await asyncio.to_thread(fn, ticker)
            except Exception as e:
                errors.append({"provider": name, "reason": str(e)})
                continue
            if result.get("unavailable"):
                errors.append({"provider": name, "reason": str(result.get("reason", "unavailable"))})
                continue
            chain = result
            break

        if chain is None:
            raise InsufficientDataError(
                "options",
                f"All options providers failed for {ticker}",
                ticker=ticker,
                missing=["options_chain"],
            )

        aggregates = compute_options_aggregates(chain)
        intelligence = compute_options_intelligence(chain, aggregates)
        payload = {
            **aggregates,
            **intelligence,
            "symbol": ticker,
            "spot": chain.get("spot"),
            "source": chain.get("source"),
            "as_of": chain.get("as_of"),
            "partial": bool(chain.get("partial")),
            "errors": errors,
            "available": True,
        }
        set_cached("options_flow", payload, ticker)
        return payload
