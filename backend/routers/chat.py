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
from .. import agent_memory
from .. import chat_service
from .. import user_preferences as uprefs

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

    uid = _user.id if _user else None
    memory_ok = bool(uid and sess.user_id and sess.user_id == uid)
    if memory_ok:
        mem_hits = agent_memory.search_memory(
            knowledge_store, uid, body.message.strip(), n_results=4
        )
        full_system += agent_memory.format_memory_context_block(mem_hits)

    # Update sticky state from this message
    chat_service.update_sticky_state(sess, body.message.strip())

    user_content = body.message.strip()
    messages = []

    if memory_ok:
        server_hist = agent_memory.load_memory(uid, sess.session_id)
        hist_source = server_hist if server_hist else body.history[-8:]
        # Persist this user turn after loading prior history (avoids duplicating the current message).
        agent_memory.save_memory(
            knowledge_store, uid, sess.session_id, "user", user_content,
        )
    else:
        hist_source = body.history[-8:]

    # Optional history cutoff: limit to last 8 turns to prevent context bloat
    for m in hist_source[-8:]:
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
        """
        Return market news headlines + optional full-article summaries.
        Priority: FinCrawler (rich text) → MIL cache (RSS headlines) → live Yahoo RSS fallback.
        """
        try:
            from ..fincrawler_client import fc

            # 1. If FinCrawler is up, scrape a richer set of articles
            if fc.enabled and query.lower() not in ("market", ""):
                fc_news = await fc.get_stock_news("SPY" if query in ("market", "") else query, limit=6)
                if fc_news:
                    return (
                        f"[Deep news via FinCrawler — topic: {query}]\n"
                        + "\n".join(f"• {item}" for item in fc_news)
                    )

            # 2. MIL preloaded RSS cache
            from .. import market_intel
            intel = market_intel.get_intel()
            headlines = intel.get("headlines") or []
            if headlines:
                import time as _t
                data_age = int(_t.time() - market_intel.updated_at_epoch())
                lines = [f"[Live market headlines, data age: {data_age}s]"]
                lines += [f"• {h}" for h in headlines[:20]]
                return "\n".join(lines)

            # 3. Live RSS fallback
            def _fetch():
                import urllib.request, xml.etree.ElementTree as ET, yfinance as yf
                heads = []
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
                                    heads.append(t)
                    except Exception:
                        pass
                try:
                    for n in (yf.Ticker("^GSPC").news or [])[:8]:
                        t = n.get("title", "").strip()
                        if t and t not in heads:
                            heads.append(t)
                except Exception:
                    pass
                if not heads:
                    return "No live news headlines available at this time."
                return "[Live market headlines]\n" + "\n".join(f"• {h}" for h in heads[:16])
            return await asyncio.to_thread(_fetch)
        except Exception as e:
            return f"Error fetching news: {e}"

    async def get_deep_news(ticker: str) -> str:
        """
        Use FinCrawler to fetch deep, full-text news articles for a specific company.
        Returns richer context than headlines — ideal for stock-specific 'why' questions.
        Falls back to yfinance news titles if FinCrawler is unavailable.
        """
        sym = ticker.upper().strip()
        if not sym:
            return "Please provide a ticker symbol."
        try:
            from ..fincrawler_client import fc
            if fc.enabled:
                articles = await fc.get_stock_news(sym, limit=6)
                if articles:
                    return (
                        f"[Deep news for {sym} via FinCrawler]\n"
                        + "\n\n".join(f"• {a}" for a in articles)
                    )
            # Fallback to yfinance news titles
            def _yf_news():
                import yfinance as yf
                t = yf.Ticker(sym)
                news = t.news or []
                if not news:
                    return f"No recent news found for {sym}."
                lines = [f"[Recent news for {sym} — yfinance fallback]"]
                for n in news[:8]:
                    title = n.get("title", "").strip()
                    if title:
                        lines.append(f"• {title}")
                return "\n".join(lines)
            return await asyncio.to_thread(_yf_news)
        except Exception as e:
            return f"Error fetching deep news for {sym}: {e}"

    async def get_sec_filing(ticker: str, form: str = "10-K") -> str:
        """
        Fetch the most recent SEC filing (10-K annual report, 10-Q quarterly, or 8-K event)
        for a company using FinCrawler's SEC EDGAR scraper.
        Returns LLM-ready extracted text from the filing.
        """
        sym = ticker.upper().strip()
        if not sym:
            return "Please provide a ticker symbol."
        valid_forms = ("10-K", "10-Q", "8-K", "DEF 14A")
        form_clean = form.upper().strip() if form.upper().strip() in valid_forms else "10-K"
        try:
            from ..fincrawler_client import fc
            if not fc.enabled:
                return (
                    f"SEC filing lookup is unavailable (FinCrawler not configured). "
                    f"Try searching EDGAR manually: https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={sym}&type={form_clean}"
                )
            text = await fc.get_sec_filing(sym, form=form_clean)
            return text or f"No {form_clean} filing found for {sym} via FinCrawler."
        except Exception as e:
            return f"Error fetching {form_clean} for {sym}: {e}"

    async def scrape_url(url: str) -> str:
        """
        Scrape any public URL and return clean, LLM-ready text.
        Use for hedge fund letters, earnings call transcripts, news articles, or any web page.
        """
        if not url.startswith("http"):
            return "Invalid URL. Must start with http:// or https://"
        try:
            from ..fincrawler_client import fc
            if not fc.enabled:
                return "URL scraping is unavailable (FinCrawler not configured)."
            text = await fc.scrape_text(url)
            return text[:6000] if text else f"No content found at {url}."
        except Exception as e:
            return f"Error scraping {url}: {e}"


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
                    "Fetch live news headlines for WHY/REASON questions about the macro market. "
                    "Use for: why is market down, geopolitical events (tariffs, Iran, Trump, Fed, war, oil). "
                    "Do NOT use for company-specific news — use get_deep_news for that."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Short topic keyword e.g. tariffs, iran oil, tech selloff", "default": "market"}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_deep_news",
                "description": (
                    "Fetch rich, full-text news articles for a SPECIFIC COMPANY using FinCrawler. "
                    "Use for: 'why is TSLA down', 'what happened to NVDA', 'latest news on AAPL'. "
                    "Returns much richer content than get_market_news. Requires a ticker symbol."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Exact uppercase ticker symbol e.g. TSLA NVDA AAPL"}
                    },
                    "required": ["ticker"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_sec_filing",
                "description": (
                    "Fetch the most recent SEC filing for a company (10-K annual report, 10-Q quarterly earnings, or 8-K event filing). "
                    "Use for: 'show me Apple's annual report', 'what does Tesla's 10-K say about risk factors', "
                    "'get Microsoft's latest 10-Q', 'any recent 8-K filings for Amazon'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Exact uppercase ticker symbol e.g. AAPL MSFT TSLA"},
                        "form": {
                            "type": "string",
                            "enum": ["10-K", "10-Q", "8-K", "DEF 14A"],
                            "description": "SEC form type: 10-K annual, 10-Q quarterly, 8-K event, DEF 14A proxy",
                            "default": "10-K"
                        }
                    },
                    "required": ["ticker"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "scrape_url",
                "description": (
                    "Scrape any public URL and return clean text — hedge fund letters, earnings transcripts, "
                    "news articles, investor presentations, Reddit posts, or any web page. "
                    "Use when the user pastes a link or mentions a specific article/document URL."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "Full URL starting with https://"}
                    },
                    "required": ["url"]
                }
            }
        }
    ]

    tool_handlers = {
        "get_stock_quote": get_stock_quote,
        "get_top_movers": get_top_movers,
        "get_market_news": get_market_news,
        "get_deep_news": get_deep_news,
        "get_sec_filing": get_sec_filing,
        "scrape_url": scrape_url,
    }

    async def event_stream():
        yield f"data: {json.dumps({'type': 'meta', 'data': meta})}\n\n"
        assistant_parts: list[str] = []
        try:
            stream_gen = llm_client.stream_chat_plain(
                full_system, 
                messages, 
                tools=tools, 
                tool_handlers=tool_handlers
            )
            async for chunk in stream_gen:
                if chunk:
                    assistant_parts.append(chunk)
                    yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
        except Exception as e:
            logger.warning("[Chat] stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"
        yield "data: [DONE]\n\n"

        # ── Post-stream: durable assistant turn + semantic embedding ───────
        if memory_ok and uid:
            assistant_text = "".join(assistant_parts).strip()
            if assistant_text:
                tickers: list = []
                st = sess.sticky_state
                if st.get("active_ticker"):
                    tickers.append(st["active_ticker"])
                for t in st.get("mentioned_tickers") or []:
                    if t not in tickers:
                        tickers.append(t)
                topic = str(st.get("analysis_mode") or "chat")
                sem = f"User: {user_content[:1200]}\nAssistant: {assistant_text[:2000]}"
                agent_memory.save_memory(
                    knowledge_store,
                    uid,
                    sess.session_id,
                    "assistant",
                    assistant_text,
                    semantic_summary=sem,
                    tickers=tickers[:10],
                    topic=topic[:80],
                )

        # ── Post-stream: preference learning (fire-and-forget) ────────────
        if _user and _user.id:
            try:
                ticker = sess.sticky_state.get("active_ticker", "")
                uprefs.learn_from_action(
                    _user.id,
                    "chat_ticker" if ticker else "chat",
                    {"ticker": ticker} if ticker else {},
                )
            except Exception:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
