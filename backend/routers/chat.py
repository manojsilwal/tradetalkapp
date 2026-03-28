"""
TradeTalk assistant chat — session bootstrap, concurrent RAG, token streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import UserInfo, get_optional_user
from ..deps import knowledge_store, llm_client
from .. import chat_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessageRequest(BaseModel):
    session_id: str = Field(..., min_length=8)
    message: str = Field(..., min_length=1, max_length=12000)
    history: list = Field(default_factory=list)

class ChatRefreshRequest(BaseModel):
    session_id: str = Field(..., min_length=8)


@router.get("/bootstrap")
def chat_bootstrap(_user: Optional[UserInfo] = Depends(get_optional_user)):
    """Global L1 + pipeline snapshot for parallel prefetch (no session required)."""
    payload = chat_service.chat_bootstrap_payload(knowledge_store)
    uid = _user.id if _user else None
    if uid:
        payload["user_prefetch_hint"] = {"user_id": uid, "has_auth": True}
    else:
        payload["user_prefetch_hint"] = {"has_auth": False}
    return payload


@router.get("/user-context")
async def chat_user_context(_user: Optional[UserInfo] = Depends(get_optional_user)):
    """Per-user portfolio context for parallel prefetch at app load."""
    if not _user:
        return {"authenticated": False, "context": {}}
    ctx = await chat_service.get_user_context_block(_user.id)
    return {"authenticated": True, "user_id": _user.id, "context": ctx}


@router.post("/session")
async def chat_open_session(
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    uid = _user.id if _user else None
    sess = await chat_service.create_session(knowledge_store, uid)
    return {
        "session_id": sess.session_id,
        "assembled_at": sess.assembled_at,
        "expires_at": sess.expires_at,
        "preview": sess.system_prompt[:500] + ("…" if len(sess.system_prompt) > 500 else ""),
    }


@router.post("/context/refresh")
async def chat_refresh_context(
    body: ChatRefreshRequest,
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    sess = chat_service.get_session(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session_not_found")
    if _user and sess.user_id and sess.user_id != _user.id:
        raise HTTPException(status_code=403, detail="session_mismatch")

    async def _run():
        await chat_service.refresh_session_task(body.session_id, knowledge_store)

    asyncio.create_task(_run())
    return {"status": "refreshing"}


@router.post("/message")
async def chat_send_message(
    body: ChatMessageRequest,
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    sess = chat_service.get_session(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session_not_found")
    if _user and sess.user_id and sess.user_id != _user.id:
        raise HTTPException(status_code=403, detail="session_mismatch")
    if time.time() > sess.expires_at:
        raise HTTPException(status_code=410, detail="session_expired")

    rag_block, meta = await chat_service.gather_message_context(
        knowledge_store, sess, body.message.strip()
    )
    full_system = sess.system_prompt + rag_block

    user_content = body.message.strip()
    messages = []
    
    # Optional history cutoff: limit to last 8 turns to prevent context bloat
    for m in body.history[-8:]:
        if isinstance(m, dict) and "role" in m and "content" in m:
            if m["role"] in ("user", "assistant"):
                messages.append({"role": m["role"], "content": str(m["content"])})
                
    messages.append({"role": "user", "content": user_content})

    async def get_stock_quote(ticker: str) -> str:
        """
        Fetch a rich snapshot for a specific ticker.
        - S&P 500 tickers: use preloaded price/% from cache, then augment with live fundamentals
        - Non-S&P500 (SMR, OKLO, etc.): full live fetch from yfinance
        Returns: price, % change, market cap, 52w range, volume, P/E, EPS, analyst target, sector
        """
        sym = ticker.upper().strip()
        if not sym or len(sym) > 10 or " " in sym:
            return "Invalid ticker. Use get_top_movers for market-wide queries."
        try:
            def _fetch_rich():
                import yfinance as yf
                t = yf.Ticker(sym)

                # fast_info: price, prev close, market cap (fast, no full info call needed)
                fi = t.fast_info
                price = fi.get("lastPrice") or fi.get("regularMarketPrice")
                prev = fi.get("previousClose")
                market_cap = fi.get("marketCap")
                year_high = fi.get("yearHigh")
                year_low = fi.get("yearLow")
                volume = fi.get("lastVolume") or fi.get("regularMarketVolume")

                if not price:
                    return f"Ticker {sym}: No price data found. It may be delisted or an invalid symbol."

                # Compute % change
                pct_str = f"{(price-prev)/prev*100:+.2f}%" if prev else "N/A"

                # info(): fundamentals (PE, EPS, analyst target, sector, avg volume)
                info = {}
                try:
                    info = t.info or {}
                except Exception:
                    pass

                pe = info.get("trailingPE") or info.get("forwardPE")
                eps = info.get("trailingEps")
                analyst_target = info.get("targetMeanPrice")
                sector = info.get("sector") or info.get("quoteType")
                avg_vol = info.get("averageVolume") or info.get("averageVolume10days")
                short_float = info.get("shortPercentOfFloat")
                name = info.get("shortName") or info.get("longName") or sym
                beta = info.get("beta")
                dividend_yield = info.get("dividendYield")

                # Format all fields
                def fmt_num(n, prefix="$", suffix="", decimals=2):
                    if n is None:
                        return "N/A"
                    if prefix == "$" and isinstance(n, (int, float)) and n > 1e9:
                        return f"${n/1e9:.2f}B"
                    if prefix == "$" and isinstance(n, (int, float)) and n > 1e6:
                        return f"${n/1e6:.2f}M"
                    return f"{prefix}{n:.{decimals}f}{suffix}"

                vol_vs_avg = ""
                if volume and avg_vol and avg_vol > 0:
                    ratio = volume / avg_vol
                    vol_vs_avg = f" ({ratio:.1f}x avg)"

                lines = [
                    f"**{name} ({sym})** — Full Quote Snapshot",
                    f"- Price: ${price:.2f} | Change: {pct_str} today",
                    f"- Market Cap: {fmt_num(market_cap)}",
                    f"- 52-Week Range: {fmt_num(year_low, '$', '', 2)} – {fmt_num(year_high, '$', '', 2)}",
                    f"- Volume: {int(volume):,}{vol_vs_avg}" if volume else "- Volume: N/A",
                    f"- P/E Ratio: {fmt_num(pe, '', '', 1)} | EPS (TTM): {fmt_num(eps, '$', '', 2)}",
                    f"- Analyst Target: {fmt_num(analyst_target)} | Beta: {fmt_num(beta, '', '', 2)}",
                    f"- Sector: {sector or 'N/A'}",
                ]
                if short_float:
                    lines.append(f"- Short Float: {short_float*100:.1f}%")
                if dividend_yield:
                    lines.append(f"- Dividend Yield: {dividend_yield*100:.2f}%")

                return "\n".join(lines)

            return await asyncio.to_thread(_fetch_rich)
        except Exception as e:
            return f"Error fetching quote for {sym}: {e}"


    async def get_top_movers(direction: str = "losers", universe: str = "sp500") -> str:
        """Return preloaded top gainers/losers from full S&P 500 cache. Zero latency."""
        try:
            from .. import market_intel
            intel = market_intel.get_intel()
            key = "top_losers" if direction == "losers" else "top_gainers"
            movers = intel.get(key) or []

            if not movers:
                return "Mover data is still loading (preload runs 10s after startup). Try again shortly."

            age = int(market_intel.updated_at_epoch())
            import time
            data_age = int(time.time() - market_intel.updated_at_epoch())
            label = "TOP LOSERS" if direction == "losers" else "TOP GAINERS"
            lines = [f"[{label} — S&P 500, intraday % change, data age: {data_age}s]"]
            for i, m in enumerate(movers[:15], 1):
                sign = "+" if m["pct"] >= 0 else ""
                sym_v = m["sym"]
                p_v = m["price"]
                pct_v = m["pct"]
                lines.append(f"{i}. {sym_v}: ${p_v:.2f} ({sign}{pct_v:.2f}%)")
            return "\n".join(lines)
        except Exception as e:
            return f"Error reading movers cache: {e}"

    async def get_market_news(query: str = "market") -> str:
        """Return preloaded news headlines from cache. Falls back to live fetch if cache is empty."""
        try:
            from .. import market_intel
            intel = market_intel.get_intel()
            headlines = intel.get("headlines") or []

            if headlines:
                import time
                data_age = int(time.time() - market_intel.updated_at_epoch())
                filtered = headlines  # could filter by query keyword in future
                lines = [f"[Live market headlines, data age: {data_age}s]"]
                lines += [f"• {h}" for h in filtered[:20]]
                return "\n".join(lines)

            # Cache empty (server just started) — fetch live
            def _fetch():
                import urllib.request, xml.etree.ElementTree as ET, yfinance as yf
                headlines = []
                rss_urls = [
                    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
                    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
                ]
                for url in rss_urls:
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=6) as r:
                            for item in ET.parse(r).findall(".//item")[:8]:
                                t = item.findtext("title", "").strip()
                                if t:
                                    headlines.append(t)
                    except Exception:
                        pass
                try:
                    for n in (yf.Ticker("^GSPC").news or [])[:8]:
                        t = n.get("title", "").strip()
                        if t and t not in headlines:
                            headlines.append(t)
                except Exception:
                    pass
                if not headlines:
                    return "No live news headlines available at this time."
                return "[Live market headlines]\n" + "\n".join(f"• {h}" for h in headlines[:16])
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            return f"Error fetching news: {e}"

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_stock_quote",
                "description": (
                    "Fetch real-time price for ONE specific stock ticker symbol like AAPL, PLTR, SNOW. "
                    "NEVER call this with generic words like market, stocks, index, information, data. "
                    "For market-wide rankings use get_top_movers. For why/reason questions use get_market_news."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Exact uppercase ticker symbol 1-5 chars e.g. AAPL MSFT PLTR"}
                    },
                    "required": ["ticker"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_top_movers",
                "description": (
                    "Fetch real intraday top gainers or losers ranked by % price change. "
                    "Use for: top losers, biggest declines, top gainers, best performers, "
                    "what is down today, what is up today, market movers, sector leaders/laggards."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["losers", "gainers"],
                            "description": "losers for biggest declining, gainers for biggest rising"
                        },
                        "universe": {
                            "type": "string",
                            "enum": ["large_cap", "l1_only"],
                            "description": "large_cap scans 45 names default, l1_only uses cached snapshot only"
                        }
                    },
                    "required": ["direction"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_market_news",
                "description": (
                    "Fetch live news headlines for WHY/REASON questions about the market. "
                    "Use for: why is market down, geopolitical events (tariffs, Iran, Trump, Fed, war, oil). "
                    "Do NOT use for price or movers ranking queries."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Short topic keyword e.g. tariffs, iran oil, tech selloff", "default": "market"}
                    },
                    "required": []
                }
            }
        }
    ]

    tool_handlers = {
        "get_stock_quote": get_stock_quote,
        "get_top_movers": get_top_movers,
        "get_market_news": get_market_news,
    }

    async def event_stream():
        yield f"data: {json.dumps({'type': 'meta', 'data': meta})}\n\n"
        try:
            stream_gen = llm_client.stream_chat_plain(
                full_system, 
                messages, 
                tools=tools, 
                tool_handlers=tool_handlers
            )
            async for chunk in stream_gen:
                if chunk:
                    yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
        except Exception as e:
            logger.warning("[Chat] stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
