"""
TradeTalk assistant chat — session bootstrap, concurrent RAG, token streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import UserInfo, get_optional_user
from ..deps import knowledge_store, llm_client
from .. import agent_memory
from .. import chat_service
from ..chat_evidence_contract import build_evidence_contract
from ..evidence_pack import build_chat_evidence_memo_markdown
from .. import user_preferences as uprefs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_QUOTE_TICKER_STOP = frozenset({
    "THE", "AND", "FOR", "ARE", "WAS", "WHAT", "TODAY", "CHANGE", "PRICE", "QUOTE", "CURRENT",
    "STOCK", "YOUR", "FROM", "WITH", "THAT", "THIS", "HAVE", "BEEN", "WILL", "HOW", "WHEN",
    "DOES", "DID", "CAN", "ANY", "NOT", "ALL", "OUR", "OUT", "NEW", "NOW", "GET",
})


def _wants_live_quote(msg: str) -> bool:
    return bool(
        re.search(
            r"\b(price|quote|chang(e|es)?|%\s*change|percent|trading\s+at|last\s+close)\b",
            msg,
            re.I,
        )
    )


def _extract_quote_ticker(msg: str) -> Optional[str]:
    m = re.search(r"\bfor\s+([A-Z]{2,5})\b", msg, re.I)
    if m:
        return m.group(1).upper()
    cands = re.findall(r"\b([A-Z]{2,5})\b", msg.upper())
    for c in reversed(cands):
        if c not in _QUOTE_TICKER_STOP and 2 <= len(c) <= 5:
            return c
    return None


_LOSER_INTENT = (
    re.compile(r"\b(top\s+losers|biggest\s+losers|worst\s+performers|largest\s+declin|steepest\s+drop|most\s+down)\b", re.I),
    re.compile(r"\b(biggest|worst|largest)\s+decliners\b", re.I),
    re.compile(
        r"\b(market\s+losers|which\s+stocks\s+(are\s+)?(down|losing|falling)|"
        r"losers\s+today|stocks\s+down\s+today|biggest\s+declin\w*\s+today)\b",
        re.I,
    ),
    re.compile(r"\bwhat\s+(are\s+)?(the\s+)?(biggest|worst)\s+(losers|decliners)\b", re.I),
    re.compile(r"\bwho\s+(lost|are\s+losing)\s+(the\s+)?most\b", re.I),
)
_GAINER_INTENT = (
    re.compile(r"\b(top\s+gainers|best\s+performers|biggest\s+gainers|most\s+up)\b", re.I),
    re.compile(r"\b(market\s+gainers|which\s+stocks\s+(are\s+)?(up|rising)|gainers\s+today)\b", re.I),
)


def _mover_query_intent(message: str) -> Optional[str]:
    """If the user is asking for ranked gainers/losers, return losers | gainers; else None."""
    if not (message and message.strip()):
        return None
    t = message.strip()
    for p in _LOSER_INTENT:
        if p.search(t):
            return "losers"
    for p in _GAINER_INTENT:
        if p.search(t):
            return "gainers"
    return None


async def fetch_top_movers_table(direction: str = "losers", universe: str = "sp500") -> str:
    """
    Movers for chat: parallel Yahoo fast_info (session %) with TTL cache, else daily batch.
    `universe` reserved for API compatibility.
    """
    try:
        from .. import market_intel

        return await asyncio.to_thread(market_intel.format_movers_reply_for_chat, direction)
    except Exception as e:
        return f"Error reading movers: {e}"


class ChatMessageRequest(BaseModel):
    session_id: str = Field(..., min_length=8)
    message: str = Field(..., min_length=1, max_length=12000)
    history: list = Field(default_factory=list)

class ChatRefreshRequest(BaseModel):
    session_id: str = Field(..., min_length=8)


class ChatOpenSessionRequest(BaseModel):
    """Optional `resume_session_id` to continue after refresh (same client, same DB)."""
    resume_session_id: Optional[str] = None


class ChatEvidenceExportRequest(BaseModel):
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
    body: ChatOpenSessionRequest = ChatOpenSessionRequest(),
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    uid = _user.id if _user else None
    if body.resume_session_id and body.resume_session_id.strip():
        resumed = await chat_service.resume_session(
            knowledge_store, uid, body.resume_session_id.strip()
        )
        if resumed:
            sp = resumed.system_prompt or ""
            return {
                "session_id": resumed.session_id,
                "assembled_at": resumed.assembled_at,
                "expires_at": resumed.expires_at,
                "preview": sp[:500] + ("…" if len(sp) > 500 else ""),
                "status": "resumed",
            }
    sess = await chat_service.create_session(knowledge_store, uid)
    sp = sess.system_prompt or ""
    return {
        "session_id": sess.session_id,
        "assembled_at": sess.assembled_at,
        "expires_at": sess.expires_at,
        "preview": sp[:500] + ("…" if len(sp) > 500 else ""),
        "status": "new",
    }


@router.post("/evidence-export")
def chat_evidence_export(
    body: ChatEvidenceExportRequest,
    _user: Optional[UserInfo] = Depends(get_optional_user),
):
    """
    Export a frozen Markdown memo for the **last completed** assistant turn in this session.
    Requires at least one finished chat response so `evidence_contract` was emitted.
    """
    sess = chat_service.get_session(body.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="session_not_found")
    if _user and sess.user_id and sess.user_id != _user.id:
        raise HTTPException(status_code=403, detail="session_mismatch")
    if not sess.last_evidence_contract:
        raise HTTPException(
            status_code=400,
            detail="no_evidence_export_complete_one_chat_turn_first",
        )
    md = build_chat_evidence_memo_markdown(
        session_id=sess.session_id,
        user_message=sess.last_user_message,
        assistant_text=sess.last_assistant_text,
        evidence_contract=sess.last_evidence_contract,
        meta=sess.last_chat_meta,
    )
    return {
        "markdown": md,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
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

    uid = _user.id if _user else None
    # Refresh base system prompt every turn so instructions + L1/pipeline snapshot stay current
    # (no need to open a new session after backend updates).
    fresh_prompt = await chat_service.build_fresh_system_prompt(knowledge_store, uid)
    sess.system_prompt = fresh_prompt

    rag_block, meta = await chat_service.gather_message_context(
        knowledge_store, sess, body.message.strip()
    )
    full_system = fresh_prompt + rag_block
    memory_ok = bool(uid and sess.user_id and sess.user_id == uid)
    if memory_ok:
        mem_hits = agent_memory.search_memory(
            knowledge_store, uid, body.message.strip(), n_results=4
        )
        full_system += agent_memory.format_memory_context_block(mem_hits)

    # Update sticky state from this message
    chat_service.update_sticky_state(sess, body.message.strip())

    user_content = body.message.strip()
    qc_ticker: Optional[str] = None
    if _wants_live_quote(user_content):
        qc_ticker = _extract_quote_ticker(user_content)
    if qc_ticker:
        full_system += (
            "\n\nNote: A structured live quote card will be sent to the user first for this turn; "
            "keep your answer short and do not duplicate the full quote table.\n"
        )

    mover_intent = _mover_query_intent(user_content)
    if mover_intent:
        mover_blob = await fetch_top_movers_table(mover_intent)
        full_system += (
            "\n\n## AUTHORITATIVE MOVER DATA (mandatory for this turn)\n"
            "Answer using **only** the tickers, prices, and percentages below for rankings. "
            "Do not substitute symbols from memory or other websites. "
            "If the block says data is loading, say that clearly — do not fabricate a list.\n\n"
            f"{mover_blob}\n"
        )

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
        """Return preloaded top gainers/losers from full S&P 500 cache (same as AUTHORITATIVE block)."""
        return await fetch_top_movers_table(direction, universe)

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

            # 3. Live RSS fallback - Parallelized
            def _fetch_rss(url: str) -> list[str]:
                import urllib.request, defusedxml.ElementTree as ET
                titles = []
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=6) as r:
                        for item in ET.parse(r).findall(".//item")[:8]:
                            t = item.findtext("title", "").strip()
                            if t:
                                titles.append(t)
                except Exception:
                    pass
                return titles

            def _fetch_yf_news(symbol: str) -> list[str]:
                import yfinance as yf
                titles = []
                try:
                    for n in (yf.Ticker(symbol).news or [])[:8]:
                        t = n.get("title", "").strip()
                        if t:
                            titles.append(t)
                except Exception:
                    pass
                return titles

            rss_urls = [
                "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US",
                "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
            ]
            # Concurrent fetch to avoid sequential blocking wait
            tasks = [asyncio.to_thread(_fetch_rss, url) for url in rss_urls]
            tasks.append(asyncio.to_thread(_fetch_yf_news, "^GSPC"))
            results = await asyncio.gather(*tasks)

            heads = []
            seen = set()
            for titles in results:
                for t in titles:
                    if t and t not in seen:
                        heads.append(t)
                        seen.add(t)

            if not heads:
                return "No live news headlines available at this time."
            return "[Live market headlines]\n" + "\n".join(f"• {h}" for h in heads[:16])
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

    async def get_price_history(ticker: str, period: str = "1y") -> str:
        """
        Historical daily closes from Yahoo Finance — total return over period, high/low, best/worst day.
        Use for YTD, 1y/5y performance, \"how did AAPL do since...\", drawdown context (not live intraday).
        """
        sym = ticker.upper().strip()
        if not sym or len(sym) > 12:
            return "Invalid ticker."
        allowed = ("5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max")
        p = (period or "1y").lower().strip()
        if p not in allowed:
            p = "1y"

        def _sync() -> str:
            import yfinance as yf

            t = yf.Ticker(sym)
            hist = t.history(period=p, interval="1d", auto_adjust=True)
            if hist is None or hist.empty:
                return f"No historical daily data for {sym} (period={p}). Symbol may be invalid or delisted."
            close = hist["Close"].dropna()
            if len(close) < 1:
                return f"No close prices for {sym} (period={p})."
            first_date = close.index[0]
            last_date = close.index[-1]
            first_c = float(close.iloc[0])
            last_c = float(close.iloc[-1])
            total_ret = (last_c / first_c - 1.0) * 100.0 if first_c else 0.0
            hi = float(close.max())
            lo = float(close.min())
            hi_d = close.idxmax()
            lo_d = close.idxmin()
            rets = close.pct_change().dropna()
            lines = [
                f"[Historical daily closes for {sym} — Yahoo Finance · period={p} · auto-adjusted]",
                f"- Sessions: {len(close)} · {first_date.date()} → {last_date.date()}",
                f"- First close: ${first_c:.2f} · Last close: ${last_c:.2f}",
                f"- Total return over period: {total_ret:+.2f}%",
                f"- Period high close: ${hi:.2f} ({hi_d.date()}) · low: ${lo:.2f} ({lo_d.date()})",
            ]
            if len(rets) > 0:
                bd = rets.idxmax()
                wd = rets.idxmin()
                lines.append(
                    f"- Best day: {rets.max() * 100:+.2f}% ({bd.date()}) · "
                    f"worst day: {rets.min() * 100:+.2f}% ({wd.date()})"
                )
            lines.append(
                "- Note: Daily bars; not tick-level intraday. For current price use get_stock_quote."
            )
            return "\n".join(lines)

        try:
            return await asyncio.to_thread(_sync)
        except Exception as e:
            return f"Error fetching history for {sym}: {e}"


    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_stock_quote",
                "description": (
                    "Current snapshot for ONE ticker: price, day change, valuation fields (Yahoo fast_info / info). "
                    "Use for: \"what is AAPL at\", \"quote MSFT\", \"market cap of NVDA\". "
                    "For **historical** returns or YTD / multi-year performance use **get_price_history**, not this. "
                    "For ranked gainers/losers use **get_top_movers**. For macro \"why\" use **get_market_news**."
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
                "name": "get_price_history",
                "description": (
                    "Historical **daily** prices from Yahoo: total return over a period, period high/low, "
                    "best/worst days. Use for: YTD or 5-year performance, \"how did AAPL do last year\", "
                    "drawdown context, index performance (^GSPC). "
                    "Not for live quote (use get_stock_quote) or top movers list (use get_top_movers)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Symbol e.g. AAPL TSLA ^GSPC (index symbols allowed)",
                        },
                        "period": {
                            "type": "string",
                            "enum": ["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "ytd", "max"],
                            "description": "Lookback window for daily bars",
                            "default": "1y",
                        },
                    },
                    "required": ["ticker"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_top_movers",
                "description": (
                    "Fetch verified top gainers or losers from the S&P 500 batch cache "
                    "(daily % vs prior close, yfinance). "
                    "Use for: top losers, biggest declines, top gainers, best performers, "
                    "what is down today, what is up today, market movers. "
                    "Do not answer from memory — always call this for ranked lists."
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
                            "enum": ["sp500", "large_cap", "l1_only"],
                            "description": "sp500 = full cached S&P 500 scan (default); others reserved",
                            "default": "sp500",
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
        },
        {
            "type": "function",
            "function": {
                "name": "recall_financial_profile",
                "description": (
                    "Load this signed-in user's saved financial preferences (risk, horizon, position type, "
                    "signal format, currency, etc.). Call once early when personalization matters. "
                    "Returns JSON. Not available for anonymous users."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "save_financial_preference",
                "description": (
                    "Persist one user preference key/value (durable across sessions). "
                    "Valid keys include risk_tolerance, investment_horizon, position_type, preferred_signal_format, "
                    "alert_on_regimes, base_currency, trading_style, explain_style. Values must match allowed enums."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {
                            "type": "string",
                            "description": "Preference key e.g. risk_tolerance, position_type, trading_style",
                        },
                        "value": {
                            "type": "string",
                            "description": "Allowed value for that key (e.g. moderate, long, swing, yes)",
                        },
                    },
                    "required": ["key", "value"],
                },
            },
        },
    ]

    async def recall_financial_profile() -> str:
        if not uid:
            return "Sign in to recall saved financial preferences."
        return await asyncio.to_thread(uprefs.recall_financial_profile_json, uid)

    async def save_financial_preference(key: str, value: str) -> str:
        if not uid:
            return "Sign in to save preferences."
        return await asyncio.to_thread(uprefs.save_financial_preference_for_tool, uid, key, value)

    tool_handlers = {
        "get_stock_quote": get_stock_quote,
        "get_price_history": get_price_history,
        "get_top_movers": get_top_movers,
        "get_market_news": get_market_news,
        "get_deep_news": get_deep_news,
        "get_sec_filing": get_sec_filing,
        "scrape_url": scrape_url,
        "recall_financial_profile": recall_financial_profile,
        "save_financial_preference": save_financial_preference,
    }

    async def event_stream():
        yield f"data: {json.dumps({'type': 'meta', 'data': meta})}\n\n"
        quote_card_tickers: list[str] = [qc_ticker] if qc_ticker else []
        tool_trace: list[dict] = []
        if qc_ticker:
            try:
                qbody = await get_stock_quote(qc_ticker)
            except Exception as e:
                logger.warning("[Chat] quote_card prefetch failed: %s", e)
                qbody = f"Could not load quote for {qc_ticker}: {e}"
            yield f"data: {json.dumps({'type': 'quote_card', 'ticker': qc_ticker, 'body': qbody[:12000]})}\n\n"
        assistant_parts: list[str] = []
        try:
            stream_gen = llm_client.stream_chat_plain(
                full_system,
                messages,
                tools=tools,
                tool_handlers=tool_handlers,
                tool_trace_out=tool_trace,
            )
            async for chunk in stream_gen:
                if chunk:
                    assistant_parts.append(chunk)
                    yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
        except Exception as e:
            logger.warning("[Chat] stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"

        evidence = build_evidence_contract(
            tool_trace=tool_trace,
            quote_card_tickers=quote_card_tickers,
            meta=meta,
        )
        assistant_text = "".join(assistant_parts).strip()
        chat_service.update_session_last_turn(sess, user_content, assistant_text, evidence, meta)
        logger.info(
            "[ChatEvidence] session=%s tools=%s confidence=%s abstain=%s",
            body.session_id,
            evidence.get("tools_called"),
            evidence.get("confidence_band"),
            evidence.get("abstain_reason"),
        )

        # ── Decision-Outcome Ledger emission (Phase 2) ────────────────
        # One row per chat turn — lets us query which tools/routes produce
        # the highest-confidence answers and correlate chat-turn outcomes
        # against user preference learning. Best-effort; never raises.
        try:
            from .. import decision_ledger as _dl
            st = sess.sticky_state or {}
            active_ticker = str(st.get("active_ticker", "") or "")
            # Mix tool-level evidence with chunk-level RAG refs so the ledger
            # can answer "which vector row powered this answer?" via a SQL join
            # into decision_evidence. Tool refs preserve the chat-turn
            # attribution; RAG refs expose retrieval quality and collection
            # coverage for (feature, regime, horizon) analytics.
            refs = []
            for idx, t in enumerate(tool_trace or []):
                nm = str(t.get("name", "") or "").strip()
                oc = str(t.get("outcome", "") or "")
                if nm and oc == "success":
                    refs.append(
                        _dl.EvidenceRef(
                            chunk_id=f"tool:{nm}",
                            collection="chat_tool_trace",
                            rank=idx,
                        )
                    )
            for cref in (evidence.get("rag_chunk_refs") or []):
                try:
                    cid = str(cref.get("chunk_id") or "")
                    if not cid:
                        continue
                    try:
                        rel = max(0.0, min(1.0, 1.0 - float(cref.get("distance", 1.0))))
                    except Exception:
                        rel = None
                    refs.append(
                        _dl.EvidenceRef(
                            chunk_id=cid,
                            collection=str(cref.get("collection") or "rag"),
                            rank=int(cref.get("rank", 0)),
                            relevance=rel,
                        )
                    )
                except Exception:
                    continue
            feats = [
                _dl.FeatureValue(
                    name="confidence_band",
                    value_str=str(evidence.get("confidence_band", "") or ""),
                ),
                _dl.FeatureValue(
                    name="abstain_reason",
                    value_str=str(evidence.get("abstain_reason") or ""),
                ),
                _dl.FeatureValue(
                    name="n_tools",
                    value_num=float(len(tool_trace or [])),
                ),
            ]
            _dl.emit_decision(
                decision_type="chat_turn",
                user_id=str(uid or ""),
                symbol=active_ticker,
                horizon_hint="none",
                verdict="",
                confidence=None,
                output={
                    "session_id": body.session_id,
                    "user_message": user_content[:4000],
                    "assistant_text": assistant_text[:8000],
                    "evidence": evidence,
                    "meta": {k: v for k, v in (meta or {}).items() if k in (
                        "rag_nonempty", "coral_hub_nonempty", "model_used",
                        "route", "elapsed_ms",
                    )},
                    "mover_intent": mover_intent,
                    "quote_card_tickers": quote_card_tickers,
                },
                source_route="backend/routers/chat.py::chat_send_message",
                evidence=refs,
                features=feats,
            )
        except Exception as _e:  # never block the stream
            logger.debug("[Chat] decision_ledger emit skipped: %s", _e)

        yield f"data: {json.dumps({'type': 'evidence_contract', 'data': evidence})}\n\n"
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
