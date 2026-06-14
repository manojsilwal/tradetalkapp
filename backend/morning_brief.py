"""
Your Morning — portfolio-scoped daily brief (Your Morning v0 Phase 3).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from . import paper_portfolio as pp
from . import portfolio_memory as pm
from .portfolio_continuity import find_continuity_moments
from .portfolio_holdings_reconcile import aggregate_open_long_positions

logger = logging.getLogger(__name__)

_DEFAULT_INTEREST = 0.5
_MAX_CARDS = 3
_MAX_IMPACT_MOVERS = 5
_MOVER_EXTRAS_POOL = 8

_SECTOR_SHORT: Dict[str, str] = {
    "Technology": "Tech",
    "Financial Services": "Financials",
    "Healthcare": "Health",
    "Consumer Cyclical": "Consumer",
    "Consumer Defensive": "Consumer",
    "Communication Services": "Comms",
    "Basic Materials": "Materials",
    "Real Estate": "Real Est.",
    "Energy": "Energy",
    "Industrials": "Industrial",
    "Utilities": "Utilities",
}


def _greeting() -> str:
    hour = datetime.now(timezone.utc).hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _fmt_pct(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "—"
    sign = "+" if signed and v > 0 else ""
    return f"{sign}{v:.1f}%"


def _since_entry_line(symbol: str, entry_date: Optional[str], cum_pct: Optional[float]) -> str:
    if not entry_date:
        return ""
    pct = cum_pct if cum_pct is not None else 0.0
    direction = "up" if pct >= 0 else "down"
    return f"You are still {direction} {abs(pct):.1f}% since adding {symbol} on {entry_date}."


def _user_interest_score(user_id: str, symbol: str) -> float:
    """0..1 from recent user_actions + preference signals."""
    sym = symbol.upper()
    actions = pm.list_user_actions(user_id, limit=100)
    hits = sum(
        1 for a in actions
        if (a.get("symbol") or "").upper() == sym
        or sym in json.dumps(a.get("metadata") or {})
    )
    score = min(1.0, hits / 10.0)
    if score > 0:
        return score
    try:
        from . import user_preferences as uprefs

        prefs = uprefs.get_preferences(user_id)
        signals = uprefs.get_signals(user_id)
        favs = set((prefs.get("favorite_tickers") or []))
        if sym in favs:
            return 0.75
        tc = (signals.get("ticker_counts") or {}).get(sym, 0)
        if tc:
            return min(1.0, tc / 5.0)
    except Exception:
        pass
    return _DEFAULT_INTEREST


def _normalize_portfolio_impact(impact_pct: float) -> float:
    """Map portfolio impact % to 0..1 (2% impact ≈ 1.0)."""
    return min(1.0, abs(float(impact_pct or 0)) / 2.0)


def _normalize_move(move_pct: float) -> float:
    return min(1.0, abs(move_pct) / 10.0)


def _reason_confidence(cause_category: Optional[str], cause_weight: Optional[float]) -> float:
    if cause_category and cause_category not in ("none", "no_catalyst", ""):
        base = 0.6
        if cause_weight is not None:
            try:
                base = min(1.0, max(0.3, float(cause_weight)))
            except (TypeError, ValueError):
                pass
        return base
    return 0.2


def rank_card_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rank brief card candidates; returns sorted copy with rank_score."""
    ranked = []
    for c in candidates:
        impact = float(c.get("portfolio_impact_pct") or 0)
        daily_raw = c.get("daily_return_pct")
        move = float(daily_raw) if daily_raw is not None else 0.0
        interest = float(c.get("user_interest_score") or _DEFAULT_INTEREST)
        novelty = float(c.get("novelty_score") or 0.5)
        reason = _reason_confidence(
            c.get("primary_cause_category"),
            c.get("primary_cause_weight"),
        )
        rank_score = (
            0.60 * _normalize_portfolio_impact(impact)
            + 0.15 * _normalize_move(move)
            + 0.10 * reason
            + 0.10 * interest
            + 0.05 * novelty
        )
        ranked.append({**c, "rank_score": round(rank_score, 4)})
    ranked.sort(key=lambda x: x["rank_score"], reverse=True)
    return ranked


def _resolve_trade_date() -> date:
    from .market_calendar import adjust_to_trading_day, last_completed_session

    try:
        from .daily_brief import get_latest_trade_date

        td = get_latest_trade_date()
        if td is not None:
            return adjust_to_trading_day(td)
    except Exception:
        pass
    # No stored trade date available — fall back to the real last session
    # (weekend- and holiday-aware) rather than raw today's date.
    return last_completed_session()


def _daily_pct_from_hist(hist) -> Optional[float]:
    if hist is None or hist.empty or len(hist) < 2:
        return None
    prev_close = float(hist["Close"].iloc[-2])
    last_close = float(hist["Close"].iloc[-1])
    if prev_close <= 0:
        return None
    return round((last_close - prev_close) / prev_close * 100, 4)


def _fetch_daily_returns_batch(symbols: List[str], trade_date: date) -> Dict[str, Optional[float]]:
    """Batch Yahoo session % for many tickers (one network round-trip)."""
    out: Dict[str, Optional[float]] = {s.upper(): None for s in symbols}
    if not symbols:
        return out
    try:
        from datetime import timedelta

        import yfinance as yf

        start = trade_date - timedelta(days=10)
        end = trade_date + timedelta(days=1)
        tickers = list(dict.fromkeys(s.upper() for s in symbols))
        if len(tickers) == 1:
            hist = yf.Ticker(tickers[0]).history(
                start=start.isoformat(), end=end.isoformat(), auto_adjust=True
            )
            out[tickers[0]] = _daily_pct_from_hist(hist)
            return out
        raw = yf.download(
            tickers,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            return out
        for sym in tickers:
            try:
                if len(tickers) > 1:
                    closes = raw["Close"][sym].dropna()
                else:
                    closes = raw["Close"].dropna()
                if len(closes) < 2:
                    continue
                prev_close = float(closes.iloc[-2])
                last_close = float(closes.iloc[-1])
                if prev_close > 0:
                    out[sym] = round((last_close - prev_close) / prev_close * 100, 4)
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[morning_brief] batch daily return fetch failed: %s", exc)
    return out


def _looks_like_pnl_not_session(daily: float, pnl_pct: Optional[float]) -> bool:
    """Detect lifetime P&L copied into a daily field (e.g. MRVL +341%)."""
    if pnl_pct is None:
        return abs(daily) > 35.0
    pnl = float(pnl_pct)
    if abs(daily) > 15.0 and abs(abs(daily) - abs(pnl)) < 1.0:
        return True
    return abs(daily) > 50.0


def _daily_returns_for_symbols(
    symbols: List[str],
    movement: Dict[str, Dict[str, Any]],
    trade_date: Optional[date] = None,
    *,
    pnl_by_symbol: Optional[Dict[str, float]] = None,
) -> Dict[str, Optional[float]]:
    """Per-symbol session daily % — movement when sane, batched Yahoo for gaps."""
    td = trade_date or _resolve_trade_date()
    pnl_map = pnl_by_symbol or {}
    out: Dict[str, Optional[float]] = {}
    need_yahoo: List[str] = []
    for sym in symbols:
        s = sym.upper()
        mov = movement.get(s) or {}
        raw = mov.get("daily_return_pct")
        if raw is not None:
            daily = float(raw)
            if not _looks_like_pnl_not_session(daily, pnl_map.get(s)):
                out[s] = daily
                continue
        need_yahoo.append(s)
    if need_yahoo:
        yahoo = _fetch_daily_returns_batch(need_yahoo, td)
        for s in need_yahoo:
            out[s] = yahoo.get(s)
    return out


def _impact_label(impact_pct: Optional[float]) -> Optional[str]:
    if impact_pct is None:
        return None
    return f"{_fmt_pct(float(impact_pct))} of portfolio"


def _direction_and_chip(daily: Optional[float], card_type: str) -> tuple[str, str]:
    if card_type == "macro_sector_watch":
        return "flat", "EXPOSURE"
    if daily is None:
        return "flat", "PENDING"
    if daily < -0.05:
        return "down", "DRAG"
    if daily > 0.05:
        return "up", "LIFT"
    return "flat", "FLAT"


def _movement_rows_for_symbols(symbols: List[str], trade_date: Optional[date] = None) -> Dict[str, Dict[str, Any]]:
    """Best-effort movement context per held symbol."""
    out: Dict[str, Dict[str, Any]] = {}
    if not symbols:
        return out
    td = trade_date or date.today()
    try:
        from .daily_brief import build_daily_brief, get_latest_trade_date

        latest = get_latest_trade_date() or td
        brief = build_daily_brief(trade_date=latest, n_losers=50, n_gainers=50, use_snapshot=True)
        sym_set = {s.upper() for s in symbols}
        for row in brief.get("rows") or []:
            sym = (row.get("symbol") or "").upper()
            if sym in sym_set:
                out[sym] = row
    except Exception as exc:
        logger.debug("[morning_brief] daily_brief filter failed: %s", exc)
    return out


def _build_candidates_from_positions(
    user_id: str,
    positions: List[Dict[str, Any]],
    total_value: float,
    movement: Dict[str, Dict[str, Any]],
    daily_returns: Dict[str, Optional[float]],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for p in positions:
        sym = (p.get("ticker") or "").upper()
        if not sym:
            continue
        pos_value = float(p.get("current_value") or 0)
        weight = (pos_value / total_value) if total_value > 0 else 0.0
        mov = movement.get(sym) or {}
        daily_ret = daily_returns.get(sym)
        daily_verified = daily_ret is not None
        daily_for_math = float(daily_ret) if daily_verified else 0.0
        impact = round(weight * daily_for_math, 4) if daily_verified else None
        cum = float(p.get("pnl_pct") or 0)
        card_type = "holding_move"
        if daily_verified:
            if daily_for_math < -0.05:
                card_type = "top_negative_contributor"
            elif daily_for_math > 0.05:
                card_type = "top_positive_contributor"
        candidates.append({
            "symbol": sym,
            "type": card_type,
            "daily_return_pct": daily_ret,
            "daily_verified": daily_verified,
            "portfolio_weight": weight,
            "portfolio_impact_pct": impact,
            "primary_cause_category": mov.get("primary_cause_category"),
            "primary_cause_headline": mov.get("primary_cause_headline"),
            "primary_cause_weight": mov.get("primary_cause_weight"),
            "one_line_reason": mov.get("one_line_reason") or mov.get("primary_cause_headline"),
            "entry_date": p.get("entry_date"),
            "cumulative_return_since_entry_pct": cum,
            "user_interest_score": _user_interest_score(user_id, sym),
            "novelty_score": 0.5,
        })
    return candidates


def _card_from_candidate(c: Dict[str, Any], idx: int) -> Dict[str, Any]:
    sym = c.get("symbol") or ""
    card_type = c.get("type") or ""
    daily_raw = c.get("daily_return_pct")
    daily = float(daily_raw) if daily_raw is not None else None
    direction, chip = _direction_and_chip(daily, card_type)
    sector_name = c.get("sector_name")
    allocation_pct = c.get("allocation_pct")

    if card_type == "macro_sector_watch":
        title = sector_name or "Sector exposure"
        primary_metric = f"{allocation_pct:.0f}%" if allocation_pct is not None else "—"
    elif card_type == "quiet_day":
        title = c.get("title") or "Quiet session"
        primary_metric = _fmt_pct(daily)
    elif sym:
        title = sym
        primary_metric = _fmt_pct(daily) if daily is not None else "—"
    else:
        title = "Portfolio"
        primary_metric = _fmt_pct(daily) if daily is not None else "—"

    body = (c.get("one_line_reason") or "").strip()
    if not body and daily is not None:
        body = "Session move from latest market close."
    elif not body and card_type == "macro_sector_watch":
        body = "Largest sector allocation in your portfolio."

    memory = _since_entry_line(
        sym,
        c.get("entry_date"),
        c.get("cumulative_return_since_entry_pct"),
    )
    impact = c.get("portfolio_impact_pct")
    return {
        "id": f"card_{idx + 1}",
        "type": card_type,
        "symbol": sym or None,
        "sector_name": sector_name,
        "allocation_pct": allocation_pct,
        "title": title,
        "primary_metric": primary_metric,
        "direction": direction,
        "chip": chip,
        "impact_label": _impact_label(impact) if impact is not None else None,
        "body": body[:240] if body else "",
        "memory_context": memory,
        "portfolio_impact_pct": impact,
        "daily_return_pct": daily,
        "rank_score": c.get("rank_score"),
        "actions": [
            {"label": "View why", "action": "open_trace"},
            {"label": "Ask AI", "action": "open_chat"},
        ],
    }


def _macro_watch_card(
    sector_exposures: Dict[str, float],
    movement: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    if not sector_exposures:
        return None
    top_sector = max(sector_exposures.items(), key=lambda x: x[1])
    sector_name, weight = top_sector
    if weight < 0.2:
        return None
    macro_moves = [
        (sym, row)
        for sym, row in movement.items()
        if (row.get("primary_cause_category") or "") in ("macro_data", "geopolitical", "tariff_policy", "fed_decision")
    ]
    reason = f"Your portfolio has {weight * 100:.0f}% exposure to {sector_name}."
    if macro_moves:
        sym, row = macro_moves[0]
        reason = row.get("one_line_reason") or reason
    return {
        "symbol": None,
        "sector_name": sector_name,
        "allocation_pct": round(weight * 100, 1),
        "type": "macro_sector_watch",
        "daily_return_pct": 0.0,
        "daily_verified": True,
        "portfolio_weight": weight,
        "portfolio_impact_pct": 0.0,
        "one_line_reason": reason,
        "entry_date": None,
        "cumulative_return_since_entry_pct": None,
        "user_interest_score": _DEFAULT_INTEREST,
        "novelty_score": 0.6,
        "primary_cause_category": "macro_data",
    }


def _select_cards(
    ranked: List[Dict[str, Any]],
    *,
    portfolio_daily_pct: Optional[float] = None,
    max_cards: int = _MAX_CARDS,
) -> List[Dict[str, Any]]:
    """Pick tiles aligned with portfolio day direction; verified daily moves only."""
    macro = [c for c in ranked if c.get("type") == "macro_sector_watch"]
    holdings = [
        c for c in ranked
        if c.get("type") != "macro_sector_watch" and c.get("daily_verified")
    ]
    by_impact = sorted(
        holdings,
        key=lambda x: abs(float(x.get("portfolio_impact_pct") or 0)),
        reverse=True,
    )
    neg = [c for c in by_impact if float(c.get("daily_return_pct") or 0) < -0.05]
    pos = [c for c in by_impact if float(c.get("daily_return_pct") or 0) > 0.05]

    port_down = portfolio_daily_pct is not None and portfolio_daily_pct < -0.05
    port_up = portfolio_daily_pct is not None and portfolio_daily_pct > 0.05
    reserve = 1 if macro else 0
    slot_count = max(0, max_cards - reserve)

    picked: List[Dict[str, Any]] = []
    if port_down:
        for c in neg[:slot_count]:
            picked.append(c)
        if len(picked) < slot_count and pos:
            picked.append(pos[0])
    elif port_up:
        for c in pos[:slot_count]:
            picked.append(c)
        if len(picked) < slot_count and neg:
            picked.append(neg[0])
    else:
        for c in by_impact[:slot_count]:
            picked.append(c)

    if macro and len(picked) < max_cards:
        picked.append(macro[0])

    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for c in picked:
        key = c.get("type", "") + ":" + str(c.get("symbol") or c.get("sector_name") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
    return deduped[:max_cards]


def _market_session_context() -> Dict[str, Any]:
    """US cash session hint for copy (closed / after-hours vs open).

    Holiday-aware via the single market calendar. The public ``status`` contract
    is kept at ``open`` | ``after_hours`` | ``weekend`` (the frontend keys off
    ``weekend`` to show the "markets closed" banner); holidays map to ``weekend``
    with holiday-specific copy.
    """
    from .market_calendar import (
        SESSION_CLOSED_HOLIDAY,
        SESSION_CLOSED_WEEKEND,
        SESSION_REGULAR,
        session_status,
    )

    status = session_status()
    if status == SESSION_CLOSED_HOLIDAY:
        return {
            "status": "weekend",
            "message": "U.S. markets are closed for a holiday today. Here's what changed during the last trading session.",
        }
    if status == SESSION_CLOSED_WEEKEND:
        return {
            "status": "weekend",
            "message": "Markets are closed today. Here's what changed during the last trading session.",
        }
    if status != SESSION_REGULAR:  # pre_market / post_market
        return {
            "status": "after_hours",
            "message": "Here's what changed in your portfolio during the last trading session.",
        }
    return {"status": "open", "message": None}


def _emit_morning_brief_decision(user_id: str, brief: Dict[str, Any]) -> None:
    try:
        from . import decision_ledger as dl
        from .decision_ledger_registry import registry_attribution

        pv, snap_id = registry_attribution()
        dl.emit_decision(
            decision_type="morning_brief",
            user_id=user_id,
            output={
                "headline": brief.get("headline"),
                "summary": brief.get("summary"),
                "card_count": len(brief.get("cards") or []),
            },
            verdict=(brief.get("headline") or "morning_brief")[:120],
            horizon_hint="1d",
            prompt_versions=pv,
            registry_snapshot_id=snap_id,
            source_route="backend/morning_brief.py::build_morning_brief",
        )
    except Exception as exc:
        logger.debug("[morning_brief] ledger emit skipped: %s", exc)


def _headline_from_summary(daily_return_pct: Optional[float]) -> str:
    if daily_return_pct is None:
        return "Here is what we noticed in your portfolio today."
    session = _market_session_context()
    if session.get("status") == "weekend":
        return "Your portfolio is quiet today (markets closed)."
    if abs(daily_return_pct) < 0.05:
        return "Your portfolio was mostly quiet today."
    direction = "up" if daily_return_pct > 0 else "down"
    return f"Your portfolio is {direction} {abs(daily_return_pct):.1f}% today."


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _sector_tag_short(sector: str) -> str:
    s = (sector or "").strip()
    if not s or s == "Unknown":
        return ""
    return _SECTOR_SHORT.get(s, s.split()[0] if s else "")


def _industry_tag_short(industry: str) -> str:
    ind = (industry or "").strip()
    if not ind:
        return ""
    if len(ind) <= 12:
        return ind
    words = ind.replace(",", " ").split()
    if len(words) >= 2:
        return " ".join(w[:4] for w in words[:2])
    return ind[:12]


def _fetch_sparklines_5d(symbols: List[str]) -> Dict[str, List[float]]:
    """Last 5 session closes per symbol for mini charts."""
    out: Dict[str, List[float]] = {s.upper(): [] for s in symbols}
    if not symbols:
        return out
    try:
        import yfinance as yf

        tickers = list(dict.fromkeys(s.upper() for s in symbols))
        if len(tickers) == 1:
            hist = yf.Ticker(tickers[0]).history(period="5d", auto_adjust=True)
            if hist is not None and not hist.empty and "Close" in hist:
                closes = [round(float(v), 4) for v in hist["Close"].dropna().tolist()]
                out[tickers[0]] = closes[-5:]
            return out
        raw = yf.download(
            tickers,
            period="5d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            return out
        for sym in tickers:
            try:
                if len(tickers) > 1:
                    closes = raw["Close"][sym].dropna()
                else:
                    closes = raw["Close"].dropna()
                out[sym] = [round(float(v), 4) for v in closes.tolist()[-5:]]
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[morning_brief] sparkline fetch failed: %s", exc)
    return out


def _fetch_relative_volume_batch(symbols: List[str]) -> Dict[str, float]:
    """last_vol / mean_vol_60d per symbol (same idea as market_intel movers)."""
    out: Dict[str, float] = {s.upper(): 1.0 for s in symbols}
    if not symbols:
        return out
    try:
        import yfinance as yf

        tickers = list(dict.fromkeys(s.upper() for s in symbols))
        raw = yf.download(
            tickers,
            period="60d",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        if raw is None or raw.empty:
            return out
        volume = raw.get("Volume")
        if volume is None:
            return out
        if len(tickers) == 1:
            sym = tickers[0]
            vols = volume.dropna()
            if len(vols) >= 2:
                mean_vol = float(vols.mean())
                last_vol = float(vols.iloc[-1])
                if mean_vol > 0:
                    out[sym] = round(last_vol / mean_vol, 4)
            return out
        for sym in tickers:
            try:
                vols = volume[sym].dropna()
                if len(vols) < 2:
                    continue
                mean_vol = float(vols.mean())
                last_vol = float(vols.iloc[-1])
                if mean_vol > 0:
                    out[sym] = round(last_vol / mean_vol, 4)
            except Exception:
                continue
    except Exception as exc:
        logger.debug("[morning_brief] relative volume fetch failed: %s", exc)
    return out


def _fetch_company_metadata(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    from .daily_brief import _fetch_ticker_enrichment

    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        s = sym.upper()
        meta = _fetch_ticker_enrichment(s)
        if meta:
            out[s] = {
                "company_name": meta.get("company_name") or s,
                "sector": meta.get("sector") or "Unknown",
                "industry": meta.get("industry") or "Unknown",
                "market_cap": meta.get("market_cap"),
                "pe_ratio": meta.get("pe_ratio"),
                "forward_pe": meta.get("forward_pe"),
                "insider_sentiment": meta.get("insider_sentiment") or "N/A",
            }
        else:
            out[s] = {
                "company_name": s,
                "sector": "Unknown",
                "industry": "Unknown",
                "market_cap": None,
                "pe_ratio": None,
                "forward_pe": None,
                "insider_sentiment": "N/A",
            }
    return out


def _relative_volume_for_symbol(
    sym: str,
    movement: Dict[str, Dict[str, Any]],
    batch: Dict[str, float],
) -> float:
    mov = movement.get(sym.upper()) or {}
    raw = mov.get("relative_volume")
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return float(batch.get(sym.upper(), 1.0))


def _build_impact_movers(
    ranked: List[Dict[str, Any]],
    movement: Dict[str, Dict[str, Any]],
    enriched: List[Dict[str, Any]],
    *,
    max_movers: int = _MAX_IMPACT_MOVERS,
) -> List[Dict[str, Any]]:
    holdings = [
        c for c in ranked
        if c.get("type") != "macro_sector_watch"
        and c.get("symbol")
        and c.get("daily_verified")
    ]
    pool = sorted(
        holdings,
        key=lambda x: abs(float(x.get("portfolio_impact_pct") or 0)),
        reverse=True,
    )[:_MOVER_EXTRAS_POOL]
    symbols = [(c.get("symbol") or "").upper() for c in pool]
    sparklines = _fetch_sparklines_5d(symbols)
    rel_vols = _fetch_relative_volume_batch(symbols)
    meta = _fetch_company_metadata(symbols)
    sector_by_sym = {
        (p.get("ticker") or "").upper(): p.get("sector") or "Unknown"
        for p in enriched
    }

    movers: List[Dict[str, Any]] = []
    for c in pool[:max_movers]:
        sym = (c.get("symbol") or "").upper()
        m = meta.get(sym, {})
        sector = m.get("sector") or sector_by_sym.get(sym) or "Unknown"
        industry = m.get("industry") or ""
        tags: List[str] = []
        st = _sector_tag_short(sector)
        if st:
            tags.append(st)
        it = _industry_tag_short(industry)
        if it and it not in tags:
            tags.append(it)
        rank_score = float(c.get("rank_score") or 0)
        movers.append({
            "symbol": sym,
            "company_name": m.get("company_name") or sym,
            "sector": sector,
            "industry": industry,
            "sector_tags": tags,
            "daily_return_pct": c.get("daily_return_pct"),
            "portfolio_impact_pct": c.get("portfolio_impact_pct"),
            "rank_score": rank_score,
            "impact_score": round(rank_score * 100),
            "relative_volume": _relative_volume_for_symbol(sym, movement, rel_vols),
            "sparkline_5d": sparklines.get(sym, []),
            "market_cap": m.get("market_cap"),
            "pe_ratio": m.get("pe_ratio"),
            "forward_pe": m.get("forward_pe"),
            "insider_sentiment": m.get("insider_sentiment"),
        })
    return movers


def _portfolio_sentiment(
    port_daily: Optional[float],
    spy_daily: Optional[float],
    enriched: List[Dict[str, Any]],
    daily_returns: Dict[str, Optional[float]],
    total_value: float,
) -> Dict[str, Any]:
    if total_value <= 0:
        return {"score": 0.5, "label": "NEUTRAL", "gauge_position_pct": 50}

    up_w = 0.0
    down_w = 0.0
    for p in enriched:
        sym = (p.get("ticker") or "").upper()
        w = float(p.get("current_value") or 0) / total_value
        dr = daily_returns.get(sym)
        if dr is None:
            continue
        if float(dr) > 0.05:
            up_w += w
        elif float(dr) < -0.05:
            down_w += w

    if up_w + down_w > 0:
        breadth = up_w / (up_w + down_w)
    else:
        breadth = 0.5

    alpha = 0.0
    if port_daily is not None and spy_daily is not None:
        alpha = float(port_daily) - float(spy_daily)

    score = _clamp01(0.6 * breadth + 0.4 * (0.5 + alpha / 10.0))
    if score >= 0.55:
        label = "BULLISH"
    elif score <= 0.45:
        label = "BEARISH"
    else:
        label = "NEUTRAL"
    return {
        "score": round(score, 2),
        "label": label,
        "gauge_position_pct": round(score * 100),
    }


def _sector_swings(
    enriched: List[Dict[str, Any]],
    daily_returns: Dict[str, Optional[float]],
    total_value: float,
    *,
    max_sectors: int = 3,
) -> List[Dict[str, Any]]:
    if total_value <= 0:
        return []

    groups: Dict[str, Dict[str, float]] = {}
    for p in enriched:
        sector = p.get("sector") or "Unknown"
        sym = (p.get("ticker") or "").upper()
        val = float(p.get("current_value") or 0)
        dr = daily_returns.get(sym)
        if sector not in groups:
            groups[sector] = {"value": 0.0, "weighted_ret": 0.0}
        groups[sector]["value"] += val
        if dr is not None:
            groups[sector]["weighted_ret"] += val * float(dr)

    swings: List[Dict[str, Any]] = []
    for sector, g in groups.items():
        alloc = g["value"] / total_value * 100.0
        daily = (g["weighted_ret"] / g["value"]) if g["value"] > 0 else 0.0
        swings.append({
            "sector_name": sector,
            "daily_return_pct": round(daily, 2),
            "allocation_pct": round(alloc, 1),
            "_sort": abs(daily) * (alloc / 100.0),
        })
    swings.sort(key=lambda x: float(x["_sort"]), reverse=True)
    return [
        {
            "sector_name": s["sector_name"],
            "daily_return_pct": s["daily_return_pct"],
            "allocation_pct": s["allocation_pct"],
        }
        for s in swings[:max_sectors]
    ]


def build_morning_brief(user_id: str) -> Dict[str, Any]:
    """Build personalized morning brief for one user."""
    # Fetch real-time benchmark info
    from .market_intel import fetch_realtime_quotes
    spy_daily_rt = None
    qqq_daily_rt = None
    ijr_daily_rt = None
    try:
        bench_quotes = fetch_realtime_quotes(["SPY", "QQQ", "IJR"], force=True)
        spy_daily_rt = bench_quotes.get("SPY", {}).get("pct")
        qqq_daily_rt = bench_quotes.get("QQQ", {}).get("pct")
        ijr_daily_rt = bench_quotes.get("IJR", {}).get("pct")
    except Exception as e:
        logger.warning("[MorningBrief] failed to fetch realtime quotes for benchmarks: %s", e)

    now = datetime.now(timezone.utc).isoformat()
    try:
        from .daily_brief import compute_data_freshness, get_latest_trade_date

        _data_freshness = compute_data_freshness(get_latest_trade_date(), source="portfolio")
    except Exception as e:
        logger.warning("[MorningBrief] freshness compute failed: %s", e)
        _data_freshness = None
    base: Dict[str, Any] = {
        "as_of": now,
        "data_freshness": _data_freshness,
        "user_id": user_id,
        "greeting": _greeting(),
        "headline": "Your Morning starts once you add your portfolio.",
        "summary": {
            "total_value": None,
            "daily_return_pct": None,
            "daily_return_value": None,
            "top_positive_contributor": None,
            "top_negative_contributor": None,
            "benchmark_context": {
                "spy_daily_return_pct": spy_daily_rt,
                "qqq_daily_return_pct": qqq_daily_rt,
                "ijr_daily_return_pct": ijr_daily_rt,
            },
        },
        "cards": [],
        "impact_movers": [],
        "portfolio_sentiment": None,
        "sector_swings": [],
        "watch_next": [],
        "continue_where_you_left_off": None,
        "continuity_moments": [],
        "market_session": _market_session_context(),
        "has_portfolio": False,
        "disclaimer": (
            "This is informational analysis, not financial advice. "
            "Markets are uncertain, and outcomes may differ from past patterns."
        ),
    }

    positions_raw = pp.get_positions(user_id, include_closed=False)
    if not positions_raw:
        return base

    base["has_portfolio"] = True
    perf = pp.get_portfolio_performance(user_id)
    enriched = perf.get("positions") or []
    if not enriched:
        return base

    total_value = float(perf.get("total_value") or 0)
    symbols = [p["ticker"] for p in enriched if p.get("ticker")]

    snap = pm.get_latest_snapshot(user_id)
    daily_return_pct = None
    daily_return_value = None
    spy_daily = None
    qqq_daily = None
    sector_exposures: Dict[str, float] = {}

    if snap:
        daily_return_pct = snap.get("daily_return_pct")
        daily_return_value = snap.get("daily_return_value")
        spy_daily = snap.get("spy_return_pct")
        qqq_daily = snap.get("qqq_return_pct")
        raw_sectors = snap.get("sector_exposures")
        if isinstance(raw_sectors, str):
            try:
                sector_exposures = json.loads(raw_sectors)
            except json.JSONDecodeError:
                sector_exposures = {}
        elif isinstance(raw_sectors, dict):
            sector_exposures = raw_sectors

    trade_date = _resolve_trade_date()
    movement = _movement_rows_for_symbols(symbols, trade_date)
    pnl_by_symbol = {
        (p.get("ticker") or "").upper(): float(p.get("pnl_pct") or 0)
        for p in enriched
        if p.get("ticker")
    }
    daily_returns = _daily_returns_for_symbols(
        symbols, movement, trade_date, pnl_by_symbol=pnl_by_symbol
    )

    # Live fallback when no snapshot yet today
    if daily_return_pct is None:
        impact_sum = 0.0
        total_w = 0.0
        for p in enriched:
            sym = p["ticker"].upper()
            w = float(p.get("current_value") or 0) / total_value if total_value else 0
            dr = daily_returns.get(sym)
            if dr is not None:
                impact_sum += w * float(dr)
                total_w += w
        daily_return_pct = round(impact_sum, 4) if total_w else 0.0

    by_sector = perf.get("analysis", {}).get("by_sector") or {}
    if not sector_exposures and total_value > 0:
        sector_exposures = {
            k: round(v / total_value, 4) for k, v in by_sector.items()
        }

    sector_swings_list = _sector_swings(enriched, daily_returns, total_value)

    candidates = _build_candidates_from_positions(
        user_id, enriched, total_value, movement, daily_returns
    )
    macro_c = _macro_watch_card(sector_exposures, movement)
    if macro_c and not sector_swings_list:
        candidates.append(macro_c)

    port_daily = float(daily_return_pct) if daily_return_pct is not None else None
    ranked = rank_card_candidates(candidates)
    selected = _select_cards(ranked, portfolio_daily_pct=port_daily)
    if sector_swings_list:
        selected = [c for c in selected if c.get("type") != "macro_sector_watch"]
    cards = [_card_from_candidate(c, i) for i, c in enumerate(selected)]

    has_mover = any(
        c.get("daily_verified") and abs(float(c.get("portfolio_impact_pct") or 0)) >= 0.01
        for c in selected
        if c.get("type") != "macro_sector_watch"
    )
    if not cards or not has_mover:
        longest = min(enriched, key=lambda p: p.get("entry_date") or "9999")
        cards = [_card_from_candidate({
            "type": "quiet_day",
            "symbol": longest.get("ticker"),
            "title": "Quiet session",
            "daily_return_pct": port_daily,
            "daily_verified": port_daily is not None,
            "portfolio_impact_pct": 0.0,
            "one_line_reason": "No large holding moves stood out in the latest session.",
            "entry_date": longest.get("entry_date"),
            "cumulative_return_since_entry_pct": longest.get("pnl_pct"),
            "rank_score": 0.5,
        }, 0)]

    contributors = sorted(
        [c for c in ranked if c.get("daily_verified")],
        key=lambda c: abs(float(c.get("portfolio_impact_pct") or 0)),
        reverse=True,
    )
    top_pos = next(
        (c for c in contributors if float(c.get("daily_return_pct") or 0) > 0.05),
        None,
    )
    top_neg = next(
        (c for c in contributors if float(c.get("daily_return_pct") or 0) < -0.05),
        None,
    )

    has_sector_panel = bool(sector_swings_list)
    has_macro_card = any(c.get("type") == "macro_sector_watch" for c in cards)
    watch_next: List[Dict[str, Any]] = []
    if not has_macro_card and not has_sector_panel and sector_exposures:
        top_s = max(sector_exposures.items(), key=lambda x: x[1])
        watch_next.append({
            "type": "sector_exposure",
            "title": f"{top_s[0]} exposure",
            "reason": f"{top_s[1] * 100:.0f}% of portfolio",
        })



    base["headline"] = _headline_from_summary(
        float(daily_return_pct) if daily_return_pct is not None else None
    )
    base["summary"] = {
        "total_value": round(total_value, 2),
        "daily_return_pct": daily_return_pct,
        "daily_return_value": daily_return_value,
        "top_positive_contributor": (top_pos or {}).get("symbol"),
        "top_negative_contributor": (top_neg or {}).get("symbol"),
        "benchmark_context": {
            "spy_daily_return_pct": spy_daily_rt if spy_daily_rt is not None else spy_daily,
            "qqq_daily_return_pct": qqq_daily_rt if qqq_daily_rt is not None else qqq_daily,
            "ijr_daily_return_pct": ijr_daily_rt if ijr_daily_rt is not None else None,
        },
    }
    impact_movers = _build_impact_movers(ranked, movement, enriched)
    base["impact_movers"] = impact_movers
    base["portfolio_sentiment"] = _portfolio_sentiment(
        port_daily, spy_daily_rt if spy_daily_rt is not None else spy_daily, enriched, daily_returns, total_value
    )
    base["sector_swings"] = sector_swings_list
    base["cards"] = cards[:_MAX_CARDS]
    base["watch_next"] = watch_next
    # Footer follow-up link is client-driven (selected / visible impact mover).
    base["continue_where_you_left_off"] = None
    base["continuity_moments"] = find_continuity_moments(
        user_id,
        symbols=symbols,
        today_daily_return_pct=float(daily_return_pct) if daily_return_pct is not None else None,
        top_movers=selected,
    )
    _emit_morning_brief_decision(user_id, base)
    return base
