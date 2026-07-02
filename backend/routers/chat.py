"""
TradeTalk assistant chat — session bootstrap, concurrent RAG, token streaming.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..auth import UserInfo, get_current_user, get_optional_user
from ..deps import knowledge_store, llm_client, macro_connector
from .. import agent_memory
from .. import paper_portfolio as pp
from .. import chat_service
from ..chat_evidence_contract import build_evidence_contract
from ..chat_tool_family import EXPECTED_CHAT_TOOL_NAMES
from ..evidence_pack import build_chat_evidence_memo_markdown
from ..swarm_reliability.artifacts import write_chat_cycle_artifacts
from ..swarm_reliability.stale_gate import evaluate_chat_staleness
from .. import user_preferences as uprefs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

# Names of tools registered as ``tool_handlers`` in ``chat_send_message``.
# Kept in sync with :data:`backend.chat_tool_family.CHAT_TOOL_FAMILY_BY_NAME` —
# ``test_chat_tool_family.py`` enforces equality so renaming a tool without
# updating the family map fails the build.
CHAT_TOOL_NAMES: frozenset[str] = frozenset({
    "get_stock_quote",
    "get_price_history",
    "get_top_movers",
    "get_market_news",
    "get_deep_news",
    "get_sec_filing",
    "get_filing_intelligence",
    "get_options_flow",
    "scrape_url",
    "recall_financial_profile",
    "save_financial_preference",
    "get_risk_assessment",
    "run_what_if_backtest",
    "find_similar_setups",
    # Super-agent context tools
    "get_portfolio_snapshot",
    "get_macro_regime",
    "get_macro_flow_summary",
})

assert CHAT_TOOL_NAMES == EXPECTED_CHAT_TOOL_NAMES, (
    "Chat tool name set drifted from chat_tool_family.CHAT_TOOL_FAMILY_BY_NAME; "
    "update both in lockstep."
)

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
    page_context: Optional[str] = Field(None, max_length=500)


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


@router.get("/sessions")
def chat_list_sessions(
    limit: int = 50,
    user: UserInfo = Depends(get_current_user),
):
    """List past chat sessions with summary metadata for the signed-in user."""
    capped = max(1, min(int(limit), 100))
    sessions = agent_memory.list_sessions(user.id, limit=capped)
    return {"sessions": sessions, "user_id": user.id}


@router.get("/sessions/{session_id}")
def chat_get_session_transcript(
    session_id: str,
    limit: int = 200,
    user: UserInfo = Depends(get_current_user),
):
    """Return full transcript for a session owned by the signed-in user."""
    if not agent_memory.session_belongs_to_user(user.id, session_id):
        raise HTTPException(status_code=404, detail="session_not_found")
    capped = max(1, min(int(limit), 500))
    messages = agent_memory.load_memory(user.id, session_id, limit=capped)
    return {
        "session_id": session_id,
        "user_id": user.id,
        "messages": messages,
        "message_count": len(messages),
    }


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
    # Fetch portfolio context once per turn (avoids duplicate yfinance work).
    user_ctx = await chat_service.get_user_context_block(uid) if uid else {}
    # Refresh base system prompt every turn so instructions + L1/pipeline snapshot stay current
    # (no need to open a new session after backend updates).
    fresh_prompt = await chat_service.build_fresh_system_prompt(
        knowledge_store, uid, user_ctx=user_ctx
    )
    sess.system_prompt = fresh_prompt

    rag_block, meta = await chat_service.gather_message_context(
        knowledge_store, sess, body.message.strip(), user_ctx=user_ctx
    )
    full_system = fresh_prompt + rag_block
    # Append page context if the client sent it (app-level assistant panel)
    if body.page_context and body.page_context.strip():
        pc = body.page_context.strip()[:400]
        full_system += f"\n\n[App context: {pc}]\n"
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
    from ..chat_skill_classifier import classify_skill as _classify_skill_pre
    _pre_skill_name, _pre_skill_tier = _classify_skill_pre(
        user_message=user_content,
        tool_families_used=[],
    )
    cycle_id = f"chat-{body.session_id}-{int(time.time())}"
    stale_report = evaluate_chat_staleness(
        cycle_id=cycle_id,
        meta=meta,
        skill_tier=getattr(_pre_skill_tier, "value", str(_pre_skill_tier)),
    )
    if stale_report is not None:
        meta["stale_data_report"] = stale_report.model_dump()

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
                from ..connectors.base import clean_dividend_yield
                dividend_yield_pct = clean_dividend_yield(info.get("dividendYield"))

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
                if dividend_yield_pct > 0:
                    lines.append(f"- Dividend Yield: {dividend_yield_pct:.2f}%")

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
        def _compact_news_payload(text: str, max_lines: int = 8, max_chars: int = 1800) -> str:
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            out: list[str] = []
            for ln in lines:
                if ln.startswith("["):
                    if not out:
                        out.append(ln[:180])
                    continue
                if ln.startswith("•"):
                    out.append(ln[:220])
                else:
                    out.append(f"• {ln[:220]}")
                if len(out) >= max_lines:
                    break
            return "\n".join(out)[:max_chars]

        try:
            from ..fincrawler_client import fc

            # 1. If FinCrawler is up, scrape a richer set of articles
            if fc.enabled and query.lower() not in ("market", ""):
                fc_news = await fc.get_stock_news("SPY" if query in ("market", "") else query, limit=6)
                if fc_news:
                    raw = (
                        f"[Deep news via FinCrawler — topic: {query}]\n"
                        + "\n".join(f"• {item}" for item in fc_news)
                    )
                    return _compact_news_payload(raw)

            # 2. MIL preloaded RSS cache
            from .. import market_intel
            intel = market_intel.get_intel()
            headlines = intel.get("headlines") or []
            if headlines:
                import time as _t
                data_age = int(_t.time() - market_intel.updated_at_epoch())
                lines = [f"[Live market headlines, data age: {data_age}s]"]
                lines += [f"• {h}" for h in headlines[:20]]
                return _compact_news_payload("\n".join(lines))

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
            return _compact_news_payload("[Live market headlines]\n" + "\n".join(f"• {h}" for h in heads[:16]))
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
        def _compact_deep_news(text: str) -> str:
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            out: list[str] = []
            for ln in lines:
                if ln.startswith("["):
                    if not out:
                        out.append(ln[:180])
                    continue
                out.append((ln if ln.startswith("•") else f"• {ln}")[:260])
                if len(out) >= 8:
                    break
            return "\n".join(out)[:2200]
        try:
            from ..fincrawler_client import fc
            if fc.enabled:
                articles = await fc.get_stock_news(sym, limit=6)
                if articles:
                    raw = (
                        f"[Deep news for {sym} via FinCrawler]\n"
                        + "\n\n".join(f"• {a}" for a in articles)
                    )
                    return _compact_deep_news(raw)
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
                return _compact_deep_news("\n".join(lines))
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

    async def get_filing_intelligence(ticker: str, force_refresh: bool = False) -> str:
        """
        Structured filing intelligence: demand visibility, backlog, moat drivers,
        thematic tags, and a 6-category risk matrix from cached SEC extraction.
        Prefer over get_sec_filing when the user asks about backlog, book-to-bill,
        recurring revenue, customer concentration, or thematic AI-infrastructure exposure.
        """
        sym = ticker.upper().strip()
        if not sym:
            return "Please provide a ticker symbol."
        try:
            from ..connectors.filing_intelligence import (
                enabled,
                fetch_for_agent,
                format_filing_intelligence_for_chat,
            )

            agent_tool = os.environ.get("FILING_INTELLIGENCE_AGENT_TOOL", "0").strip().lower() in (
                "1", "true", "yes", "on",
            )
            if not agent_tool and not enabled():
                return (
                    f"Filing intelligence is disabled (set FILING_INTELLIGENCE_ENABLE=1 or "
                    f"FILING_INTELLIGENCE_AGENT_TOOL=1). Try get_sec_filing for raw {sym} 10-K text."
                )
            payload = await fetch_for_agent(sym, force_refresh=bool(force_refresh))
            return format_filing_intelligence_for_chat(payload)
        except Exception as e:
            return f"Error fetching filing intelligence for {sym}: {e}"

    async def get_options_flow(ticker: str) -> str:
        """
        Options market intelligence: put/call ratios, bull vs bear OI/volume split,
        expected move, top strike walls, unusual activity, near-expiry gamma risk.
        Use for: options sentiment, short-term price range, call/put comparison,
        implied volatility, whale/unusual flow, 'analyze options for TICKER'.
        """
        sym = ticker.upper().strip()
        if not sym:
            return "Please provide a ticker symbol."
        try:
            from ..connectors.options_flow import (
                OptionsFlowConnector,
                format_options_flow_for_chat,
            )

            payload = await OptionsFlowConnector().fetch_data(ticker=sym)
            return format_options_flow_for_chat(payload)
        except Exception as e:
            return f"Error fetching options flow for {sym}: {e}"

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

    async def get_risk_assessment(ticker: str) -> str:
        """
        Return a compact risk snapshot for one ticker:
        - realized volatility (30d)
        - ATR volatility context
        - regime classification (ranging / trending / crisis)
        - event-risk flags (FOMC/NFP/CPI windows)
        - stop-distance and position-size caution hints
        """
        sym = ticker.upper().strip()
        if not sym or len(sym) > 12:
            return "Invalid ticker."
        try:
            from ..connectors.risk_assessment import compute_risk_assessment

            payload = await compute_risk_assessment(sym)
            if payload.get("error"):
                return f"Risk assessment unavailable for {sym}: {payload['error']}"
            # Decision-Outcome Ledger (best-effort): risk surface verdict emit.
            try:
                from .. import decision_ledger as _dl
                from ..decision_ledger_registry import registry_attribution

                _pv, _snap, _model = registry_attribution()
                _dl.emit_decision(
                    decision_type="risk_assessment",
                    user_id=str(uid or ""),
                    symbol=sym,
                    horizon_hint="5d",
                    verdict="",
                    confidence=0.0,
                    output={
                        "source": "chat_risk_tool",
                        "regime": payload.get("regime"),
                        "position_size_caution": payload.get("position_size_caution"),
                        "vix_level": payload.get("vix_level"),
                        "event_risk_flags": payload.get("event_risk_flags"),
                        "realized_vol_30d": payload.get("realized_vol_30d"),
                        "atr_14_pct": payload.get("atr_14_pct"),
                    },
                    source_route="backend/routers/chat.py::get_risk_assessment",
                    prompt_versions=_pv,
                    registry_snapshot_id=_snap,
                    model=_model,
                )
            except Exception:
                pass
            flags = payload.get("event_risk_flags") or []
            flags_str = ", ".join(flags) if flags else "none"
            return (
                f"[Risk assessment for {sym}]\n"
                f"- Regime: {payload.get('regime', 'unknown')}\n"
                f"- Realized vol (30d): {float(payload.get('realized_vol_30d', 0.0)) * 100:.2f}%\n"
                f"- ATR(14) % of price: {float(payload.get('atr_14_pct', 0.0)) * 100:.2f}%\n"
                f"- VIX level: {payload.get('vix_level', 'N/A')}\n"
                f"- Event-risk flags (48h window): {flags_str}\n"
                f"- Stop-distance hint: {float(payload.get('stop_distance_pct_hint', 0.0)) * 100:.2f}%\n"
                f"- Position-size caution: {payload.get('position_size_caution', 'unknown')}"
            )
        except Exception as e:
            return f"Error running risk assessment for {sym}: {e}"

    async def run_what_if_backtest(preset_id: str, lookback_months: int = 12) -> str:
        """
        Run a bounded preset backtest for chat.
        Guardrail: preset_id only (no free-form strategy text in chat tool).
        """
        pid = (preset_id or "").strip()
        if not pid:
            return "Please provide a preset_id."
        lookback_months = max(1, min(int(lookback_months or 12), 60))
        try:
            from datetime import date, timedelta
            from ..backtest_engine import run_backtest
            from ..strategy_presets import get_preset_rules, list_preset_summaries

            valid_ids = {str(p.get("id", "")).strip() for p in list_preset_summaries()}
            if pid not in valid_ids:
                return (
                    f"Unknown preset_id '{pid}'. Available presets: "
                    + ", ".join(sorted(x for x in valid_ids if x)[:12])
                )
            end = date.today()
            start = end - timedelta(days=30 * lookback_months)
            rules = get_preset_rules(pid, start.isoformat(), end.isoformat())
            result = await run_backtest(rules, llm_client, knowledge_store)

            metrics = getattr(result, "metrics", None)
            win_rate = None
            total_trades = None
            avg_r = None
            if metrics is not None:
                win_rate = getattr(metrics, "win_rate", None)
                total_trades = getattr(metrics, "total_trades", None)
                avg_r = getattr(metrics, "avg_r", None)
            expectancy = None
            try:
                expectancy = (
                    getattr(result, "total_return_pct", None)
                    if hasattr(result, "total_return_pct")
                    else None
                )
            except Exception:
                expectancy = None

            # Decision-Outcome Ledger (best-effort)
            try:
                from .. import decision_ledger as _dl
                from ..decision_ledger_registry import registry_attribution

                _pv, _snap, _model = registry_attribution()
                _dl.emit_decision(
                    decision_type="what_if_backtest",
                    user_id=str(uid or ""),
                    symbol="",
                    horizon_hint="21d",
                    verdict="",
                    confidence=0.0,
                    output={
                        "source": "chat_backtest_tool",
                        "preset_id": pid,
                        "lookback_months": lookback_months,
                        "win_rate": win_rate,
                        "total_trades": total_trades,
                        "avg_r": avg_r,
                        "expectancy": expectancy,
                    },
                    source_route="backend/routers/chat.py::run_what_if_backtest",
                    prompt_versions=_pv,
                    registry_snapshot_id=_snap,
                    model=_model,
                )
            except Exception:
                pass

            return (
                f"[What-if backtest — preset={pid} | window={start.isoformat()}→{end.isoformat()}]\n"
                f"- Trades: {total_trades if total_trades is not None else 'N/A'}\n"
                f"- Win rate: {f'{float(win_rate)*100:.1f}%' if win_rate is not None else 'N/A'}\n"
                f"- Avg R: {f'{float(avg_r):.2f}' if avg_r is not None else 'N/A'}\n"
                f"- Expectancy/Return proxy: {f'{float(expectancy):+.2f}%' if expectancy is not None else 'N/A'}"
            )
        except Exception as e:
            return f"Error running what-if backtest for preset '{pid}': {e}"

    async def find_similar_setups(ticker: str, lookback_bars: int = 5) -> str:
        """
        Find historical pattern analogs from normalized OHLCV descriptors.
        """
        sym = (ticker or "").upper().strip()
        if not sym:
            return "Please provide a ticker symbol."
        lb = max(3, min(int(lookback_bars or 5), 20))
        try:
            import yfinance as yf
            from ..data_lake.ohlcv_normalizer import OHLCVBar, describe_window, normalize_bar

            def _fetch():
                hist = yf.Ticker(sym).history(period="6mo", interval="1d", auto_adjust=True)
                if hist is None or hist.empty:
                    return []
                rows = []
                for _, row in hist.tail(lb + 25).iterrows():
                    rows.append(
                        OHLCVBar(
                            open=float(row["Open"]),
                            high=float(row["High"]),
                            low=float(row["Low"]),
                            close=float(row["Close"]),
                            volume=float(row["Volume"]),
                        )
                    )
                return rows

            bars = await asyncio.to_thread(_fetch)
            if len(bars) < lb + 1:
                return f"Not enough OHLCV history for {sym}."
            window = bars[-lb:]
            vol_window = [b.volume for b in bars[-25:]]
            normalized: list[dict[str, float]] = []
            prev_close = bars[-lb - 1].close
            for b in window:
                normalized.append(normalize_bar(b, prev_close=prev_close, volume_window=vol_window))
                prev_close = b.close
            query_text = describe_window(normalized)
            docs, refs = await asyncio.to_thread(
                knowledge_store.query_with_refs,
                "ohlcv_patterns",
                query_text,
                10,
                {"ticker": sym},
            )
            if not docs:
                return (
                    f"[Pattern match for {sym}] No stored analogs yet in ohlcv_patterns.\n"
                    f"- Query descriptor: {query_text}\n"
                    "- Populate collection via Phase E5 backfill to enable analog lookups."
                )
            lines = [f"[Pattern match for {sym} | top_k={len(docs)}]", f"- Query: {query_text}"]
            for i, (d, r) in enumerate(zip(docs[:5], refs[:5]), start=1):
                rid = r.get("chunk_id") or f"pattern_{i}"
                lines.append(
                    f"- #{i} {rid} | distance={float(r.get('distance', 1.0)):.3f} | {str(d)[:140]}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error finding similar setups for {sym}: {e}"


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
                "name": "get_filing_intelligence",
                "description": (
                    "Structured SEC filing intelligence: demand visibility, order backlog, book-to-bill, "
                    "recurring revenue, moat drivers, customer concentration, thematic tags, and risk matrix. "
                    "Use for: backlog analysis, AI infrastructure exposure, revenue quality, moat assessment. "
                    "Faster than get_sec_filing when structured fields are enough."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Exact uppercase ticker e.g. ETN BE AAPL"},
                        "force_refresh": {
                            "type": "boolean",
                            "description": "Bypass cache and re-extract from latest 10-K",
                            "default": False,
                        },
                    },
                    "required": ["ticker"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_options_flow",
                "description": (
                    "Options chain intelligence: put/call volume and OI ratios, bull vs bear contract "
                    "counts, expected price move, IV, top call/put strike walls, unusual activity, "
                    "near-expiry open interest. Use when user asks about options sentiment, implied "
                    "move, short-term price prediction from options, put/call comparison, or short "
                    "positioning via the options market."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string", "description": "Exact uppercase ticker e.g. MRVL AAPL TSLA"},
                    },
                    "required": ["ticker"],
                },
            },
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
        {
            "type": "function",
            "function": {
                "name": "get_risk_assessment",
                "description": (
                    "Risk snapshot for one ticker: realized volatility, ATR context, regime classification, "
                    "event-risk flags (FOMC/NFP/CPI windows), and stop-distance / position-size caution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Exact uppercase ticker symbol e.g. AAPL NVDA XAUUSD",
                        }
                    },
                    "required": ["ticker"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_what_if_backtest",
                "description": (
                    "Run a bounded what-if backtest using a known preset_id only (no free-form strategy text). "
                    "Returns trades, win-rate, average R, and expectancy proxy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "preset_id": {
                            "type": "string",
                            "description": "Preset strategy id from /strategies/presets",
                        },
                        "lookback_months": {
                            "type": "integer",
                            "description": "Backtest lookback window in months (1..60)",
                            "default": 12,
                        },
                    },
                    "required": ["preset_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_similar_setups",
                "description": (
                    "Find historical analog setups by matching normalized OHLCV structure "
                    "against the ohlcv_patterns vector collection."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Exact uppercase ticker symbol e.g. NVDA",
                        },
                        "lookback_bars": {
                            "type": "integer",
                            "description": "Number of recent daily bars to encode (3..20)",
                            "default": 5,
                        },
                    },
                    "required": ["ticker"],
                },
            },
        },
        # ── Super-agent context tools ─────────────────────────────────────────
        {
            "type": "function",
            "function": {
                "name": "get_portfolio_snapshot",
                "description": (
                    "Return a detailed snapshot of the signed-in user's paper portfolio: "
                    "all open positions, current P&L, sector/cap-bucket breakdown, SPY benchmark "
                    "comparison, and top winners/losers. "
                    "Use when the user asks about their portfolio performance, exposure, "
                    "concentration risk, or wants exact P&L on their holdings. "
                    "The system prompt already has a summary — call this tool when the user "
                    "explicitly wants detail or the summary does not answer their question."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_macro_regime",
                "description": (
                    "Return the current global macro regime: VIX, credit stress index, "
                    "market regime label (BULL_NORMAL / BEAR_STRESS), fed funds rate, "
                    "2Y/10Y treasury yields, yield curve spread, DXY dollar index, CPI, "
                    "unemployment, and sector ETF moves. "
                    "Use for questions about the macro environment, interest rates, the dollar, "
                    "recession risk, Fed policy, or the overall market regime. "
                    "Do not fabricate rates or yields from memory — call this tool."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_macro_flow_summary",
                "description": (
                    "Return thematic sector capital-flow scores and QA verdicts from the "
                    "macro flow pipeline (CMF/RS momentum + fundamental qual). "
                    "Shows which sectors have strong inflows (durable/speculative) vs outflows, "
                    "and the leading tickers within each theme. "
                    "Use when the user asks about sector rotation, capital flows, thematic trends, "
                    "which sectors are leading or lagging, or wants macro flow analysis."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "interval": {
                            "type": "string",
                            "enum": ["1d", "1w", "1m", "1y"],
                            "description": "Lookback window for flow scores. Default '1w'.",
                            "default": "1w",
                        }
                    },
                    "required": [],
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

    async def get_portfolio_snapshot() -> str:
        """
        Return a detailed snapshot of the user's paper portfolio: all open positions,
        current P&L, sector/cap breakdown, SPY benchmark comparison, and top winners/losers.
        Use when the user asks about their portfolio performance, exposure, concentration,
        or wants to know how specific holdings are doing.
        """
        if not uid:
            return "Sign in to view your portfolio."
        try:
            perf = await asyncio.to_thread(pp.get_portfolio_performance, uid)
            positions = perf.get("positions") or []
            if not positions:
                return "Your portfolio is empty. Add positions via the Portfolio page."

            total_val = perf.get("total_value", 0.0)
            total_pnl = perf.get("total_pnl", 0.0)
            total_pnl_pct = perf.get("total_pnl_pct", 0.0)
            spy_pct = perf.get("spy_pnl_pct")
            beating = perf.get("beating_spy", False)
            analysis = perf.get("analysis") or {}

            lines = [
                f"**Portfolio Snapshot** ({len(positions)} open positions)",
                f"- Total Value: ${total_val:,.2f} | P&L: ${total_pnl:+,.2f} ({total_pnl_pct:+.2f}%)",
            ]
            if spy_pct is not None:
                lines.append(
                    f"- SPY benchmark: {spy_pct:+.2f}% | {'Beating' if beating else 'Trailing'} SPY"
                )

            # Sector breakdown
            by_sector = analysis.get("by_sector") or {}
            if by_sector:
                top_sectors = sorted(by_sector.items(), key=lambda x: x[1], reverse=True)[:4]
                sector_str = ", ".join(f"{s}: ${v:,.0f}" for s, v in top_sectors)
                lines.append(f"- Sector exposure: {sector_str}")

            # Cap bucket breakdown
            by_cap = analysis.get("by_cap_bucket") or {}
            if by_cap:
                cap_str = ", ".join(f"{c}: ${v:,.0f}" for c, v in sorted(by_cap.items(), key=lambda x: x[1], reverse=True)[:3])
                lines.append(f"- Cap breakdown: {cap_str}")

            # Top 3 winners and losers
            sorted_pos = sorted(positions, key=lambda p: p.get("pnl_pct", 0.0), reverse=True)
            winners = sorted_pos[:3]
            losers = sorted_pos[-3:][::-1]
            if winners:
                w_str = ", ".join(
                    f"{p['ticker']} {p.get('pnl_pct', 0):+.1f}%" for p in winners if p.get("pnl_pct", 0) > 0
                )
                if w_str:
                    lines.append(f"- Top gainers: {w_str}")
            if losers:
                l_str = ", ".join(
                    f"{p['ticker']} {p.get('pnl_pct', 0):+.1f}%" for p in losers if p.get("pnl_pct", 0) < 0
                )
                if l_str:
                    lines.append(f"- Top losers: {l_str}")

            # Full position table (compact)
            lines.append("\n**Open Positions:**")
            for p in positions:
                pnl_str = f"{p.get('pnl_pct', 0):+.1f}%"
                lines.append(
                    f"  {p['ticker']} ({p.get('direction','LONG')}) | "
                    f"Entry: ${p.get('entry_price', 0):.2f} | "
                    f"Current: ${p.get('current_price', 0):.2f} | "
                    f"P&L: ${p.get('pnl_dollar', 0):+.2f} ({pnl_str}) | "
                    f"Sector: {p.get('sector', 'Unknown')}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Portfolio snapshot unavailable: {e}"

    async def get_macro_regime() -> str:
        """
        Return the current global macro regime: VIX, credit stress, market regime label,
        fed funds rate, 2Y/10Y treasury yields, yield curve spread, DXY, CPI, unemployment.
        Use when the user asks about the macro environment, interest rates, the dollar,
        recession risk, or the overall market regime.
        """
        try:
            data = await macro_connector.fetch_data()
            ind = data.get("indicators") or {}
            vix = ind.get("vix_level")
            csi = ind.get("credit_stress_index")
            regime = "BULL_NORMAL" if (csi or 0) <= 1.1 else "BEAR_STRESS"
            narrative = ind.get("macro_narrative") or ""

            lines = [f"**Macro Regime: {regime}**"]
            if vix is not None:
                lines.append(f"- VIX: {vix:.1f} | Credit Stress Index: {csi:.2f}" if csi else f"- VIX: {vix:.1f}")

            fed = ind.get("fed_funds_rate")
            t2y = ind.get("treasury_2y")
            t10y = ind.get("treasury_10y")
            spread = ind.get("yield_curve_spread_10y_2y")
            if any(v is not None for v in [fed, t2y, t10y]):
                rate_parts = []
                if fed is not None:
                    rate_parts.append(f"Fed Funds: {fed:.2f}%")
                if t2y is not None:
                    rate_parts.append(f"2Y: {t2y:.2f}%")
                if t10y is not None:
                    rate_parts.append(f"10Y: {t10y:.2f}%")
                if spread is not None:
                    rate_parts.append(f"Spread (10Y-2Y): {spread:+.2f}%")
                lines.append(f"- Rates: {' | '.join(rate_parts)}")

            dxy = ind.get("dxy_level")
            dxy_chg = ind.get("dxy_change_5d_pct")
            dxy_lbl = ind.get("dxy_strength_label") or ""
            if dxy is not None:
                dxy_str = f"DXY: {dxy:.1f}"
                if dxy_chg is not None:
                    dxy_str += f" ({dxy_chg:+.2f}% 5d)"
                if dxy_lbl:
                    dxy_str += f" — {dxy_lbl}"
                lines.append(f"- Dollar: {dxy_str}")

            cpi = ind.get("cpi_yoy")
            unemp = ind.get("unemployment")
            if cpi is not None or unemp is not None:
                econ_parts = []
                if cpi is not None:
                    econ_parts.append(f"CPI YoY: {cpi:.1f}%")
                if unemp is not None:
                    econ_parts.append(f"Unemployment: {unemp:.1f}%")
                lines.append(f"- Economy: {' | '.join(econ_parts)}")

            # Sector ETF snapshot
            sectors = data.get("sectors") or []
            if sectors:
                sector_line = " | ".join(
                    f"{s.get('name','?')}: {s.get('change_pct', 0):+.1f}%"
                    for s in sectors[:5]
                )
                lines.append(f"- Sector moves: {sector_line}")

            if narrative:
                lines.append(f"- Narrative: {narrative[:400]}")

            return "\n".join(lines)
        except Exception as e:
            return f"Macro regime data unavailable: {e}"

    async def get_macro_flow_summary(interval: str = "1w") -> str:
        """
        Return thematic sector capital-flow scores and QA verdicts from the macro flow pipeline.
        Shows which sectors have strong inflows (durable/speculative) vs outflows, and the
        top movers within each theme. Use when the user asks about sector rotation, capital
        flows, thematic trends, which sectors are leading/lagging, or macro flow analysis.
        interval: '1d', '1w' (default), '1m', or '1y'
        """
        allowed = {"1d", "1w", "1m", "1y"}
        iv = interval.strip().lower() if interval.strip().lower() in allowed else "1w"
        try:
            from ..macro_flow.store import latest_rrg_payload

            pts = await asyncio.to_thread(latest_rrg_payload, iv)
            if not pts:
                return (
                    f"No macro flow data cached for interval={iv}. "
                    "Try refreshing via the Macro page or ask again shortly."
                )

            # Sort by flow_score descending
            pts_sorted = sorted(pts, key=lambda p: float(p.get("flow_score") or 0.0), reverse=True)

            lines = [f"**Macro Sector Flow Summary** (interval: {iv})"]
            lines.append("\nTop inflow sectors:")
            for p in pts_sorted[:5]:
                name = p.get("name") or p.get("category_id") or "Unknown"
                fs = float(p.get("flow_score") or 0.0)
                verdict = p.get("qa_verdict") or "—"
                top_m = p.get("top_movers") or []
                movers_str = ", ".join(str(m) for m in top_m[:3]) if top_m else ""
                line = f"  {name}: flow={fs:+.3f} | verdict={verdict}"
                if movers_str:
                    line += f" | leaders={movers_str}"
                lines.append(line)

            lines.append("\nBottom / outflow sectors:")
            for p in pts_sorted[-4:]:
                name = p.get("name") or p.get("category_id") or "Unknown"
                fs = float(p.get("flow_score") or 0.0)
                verdict = p.get("qa_verdict") or "—"
                lines.append(f"  {name}: flow={fs:+.3f} | verdict={verdict}")

            return "\n".join(lines)
        except Exception as e:
            return f"Macro flow data unavailable: {e}"

    tool_handlers = {
        "get_stock_quote": get_stock_quote,
        "get_price_history": get_price_history,
        "get_top_movers": get_top_movers,
        "get_market_news": get_market_news,
        "get_deep_news": get_deep_news,
        "get_sec_filing": get_sec_filing,
        "get_filing_intelligence": get_filing_intelligence,
        "get_options_flow": get_options_flow,
        "scrape_url": scrape_url,
        "recall_financial_profile": recall_financial_profile,
        "save_financial_preference": save_financial_preference,
        "get_risk_assessment": get_risk_assessment,
        "run_what_if_backtest": run_what_if_backtest,
        "find_similar_setups": find_similar_setups,
        "get_portfolio_snapshot": get_portfolio_snapshot,
        "get_macro_regime": get_macro_regime,
        "get_macro_flow_summary": get_macro_flow_summary,
    }

    # Phase A1: stable per-turn identifiers for trajectory telemetry.
    message_id = uuid.uuid4().hex
    trace_id = f"chat:{body.session_id}:{message_id}"

    async def event_stream():
        yield f"data: {json.dumps({'type': 'meta', 'data': meta})}\n\n"
        quote_card_tickers: list[str] = [qc_ticker] if qc_ticker else []
        tool_trace: list[dict] = []
        if stale_report is not None:
            stale_payload = stale_report.model_dump()
            stale_text = stale_payload.get("message") or "Required evidence is stale."
            yield f"data: {json.dumps({'type': 'token', 'text': stale_text})}\n\n"
            evidence = build_evidence_contract(
                tool_trace=[],
                quote_card_tickers=[],
                meta=meta,
                trajectory_summary={
                    "trace_id": trace_id,
                    "skill_name": getattr(_pre_skill_name, "value", str(_pre_skill_name)),
                    "skill_tier": getattr(_pre_skill_tier, "value", str(_pre_skill_tier)),
                    "trajectory_step_count": 0,
                    "valid_prefix_steps": 0,
                    "investigation_step_count": 0,
                    "synthesis_step_index": 0,
                    "answer_grounded_to_investigation": False,
                    "fatal_detected": False,
                    "tool_families_used": [],
                },
                trajectory_steps=[],
            )
            evidence["status"] = "STALE_DATA"
            evidence["stale_data_report"] = stale_payload
            evidence["abstain_reason"] = "stale_data_blocked"
            chat_service.update_session_last_turn(sess, user_content, stale_text, evidence, meta)
            try:
                write_chat_cycle_artifacts(
                    cycle_id=cycle_id,
                    meta={**meta, "session_id": body.session_id},
                    evidence=evidence,
                    tool_trace=[],
                    stale_data_report=stale_payload,
                )
            except Exception as _artifact_err:
                logger.debug("[Chat] artifact write skipped (stale): %s", _artifact_err)
            yield f"data: {json.dumps({'type': 'evidence_contract', 'data': evidence})}\n\n"
            yield "data: [DONE]\n\n"
            return
        if qc_ticker:
            try:
                qbody = await get_stock_quote(qc_ticker)
            except Exception as e:
                logger.warning("[Chat] quote_card prefetch failed: %s", e)
                qbody = f"Could not load quote for {qc_ticker}: {e}"
            qc_freshness = None
            try:
                from ..freshness import assess_spot

                qc_freshness = assess_spot(source="yfinance").model_dump()
            except Exception:
                qc_freshness = None
            yield f"data: {json.dumps({'type': 'quote_card', 'ticker': qc_ticker, 'body': qbody[:12000], 'data_freshness': qc_freshness})}\n\n"
        assistant_parts: list[str] = []
        try:
            stream_gen = llm_client.stream_chat_plain(
                full_system,
                messages,
                tools=tools,
                tool_handlers=tool_handlers,
                tool_trace_out=tool_trace,
                trace_id=trace_id,
                session_id=body.session_id,
                message_id=message_id,
            )
            async for chunk in stream_gen:
                if chunk:
                    assistant_parts.append(chunk)
                    yield f"data: {json.dumps({'type': 'token', 'text': chunk})}\n\n"
        except Exception as e:
            logger.warning("[Chat] stream error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:300]})}\n\n"

        # Phase A2: turn-level trajectory summary feeds the evidence contract
        # (B-hard-gate fields) and the handoff_chat_trace persistence row.
        from .. import chat_tool_telemetry as _ctt
        from ..chat_skill_classifier import classify_skill as _classify_skill

        # Phase E1 — heuristic skill classification runs *after* the tool
        # loop so we can use the actually-touched family set as evidence.
        # Quote-card prefetch contributes the ``quote`` family to the
        # classifier even when no quote tool was called explicitly.
        _families_for_classifier: list[str] = []
        _seen_for_classifier: set[str] = set()
        if quote_card_tickers:
            _families_for_classifier.append("quote")
            _seen_for_classifier.add("quote")
        for _row in tool_trace or []:
            _fam = str(_row.get("tool_family") or "")
            if _fam and _fam not in _seen_for_classifier:
                _families_for_classifier.append(_fam)
                _seen_for_classifier.add(_fam)
        try:
            _skill_name, _skill_tier = _classify_skill(
                user_message=user_content,
                tool_families_used=_families_for_classifier,
            )
        except Exception as e:
            logger.warning("[Chat] skill classification failed: %s", e)
            from ..chat_tool_family import SkillName as _SN, SkillTier as _ST
            _skill_name, _skill_tier = _SN.UNKNOWN, _ST.SIMPLE

        _final_answer_text = "".join(assistant_parts)

        trajectory_summary = _ctt.summarize_trace(
            tool_trace,
            trace_id=trace_id,
            session_id=body.session_id,
            message_id=message_id,
            quote_card_tickers=quote_card_tickers,
            skill_name=_skill_name,
            skill_tier=_skill_tier,
            final_answer_text=_final_answer_text,
        )
        evidence_steps_for_sse = _ctt.trajectory_steps_for_sse(tool_trace)
        evidence = build_evidence_contract(
            tool_trace=tool_trace,
            quote_card_tickers=quote_card_tickers,
            meta=meta,
            trajectory_summary=trajectory_summary,
            trajectory_steps=evidence_steps_for_sse,
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
            from ..decision_ledger_registry import registry_attribution

            _pv, _snap, _model = registry_attribution()
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
                prompt_versions=_pv,
                registry_snapshot_id=_snap,
                model=_model,
            )
        except Exception as _e:  # never block the stream
            logger.debug("[Chat] decision_ledger emit skipped: %s", _e)

        try:
            write_chat_cycle_artifacts(
                cycle_id=cycle_id,
                meta={**meta, "session_id": body.session_id},
                evidence=evidence,
                tool_trace=tool_trace,
                stale_data_report=meta.get("stale_data_report"),
            )
        except Exception as _artifact_err:
            logger.debug("[Chat] artifact write skipped: %s", _artifact_err)

        # Phase A1: best-effort persistence of the bounded trajectory payload.
        # Failures are logged at DEBUG so the user-facing stream never breaks.
        try:
            _ctt.log_chat_trace_event(
                summary=trajectory_summary,
                trajectory_steps=tool_trace,
                evidence_contract=evidence,
            )
        except Exception as _e:  # pragma: no cover - safety net
            logger.debug("[Chat] handoff_chat_trace skipped: %s", _e)

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
