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
from .portfolio_holdings_reconcile import aggregate_open_long_positions

logger = logging.getLogger(__name__)

_DEFAULT_INTEREST = 0.5
_MAX_CARDS = 3


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
        move = float(c.get("daily_return_pct") or 0)
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

    missing = [s for s in symbols if s.upper() not in out]
    for sym in missing[:10]:
        try:
            from .mcp_server.tools import get_movement_context

            ctx = get_movement_context(sym, td.isoformat())
            price = ctx.get("price") or {}
            out[sym.upper()] = {
                "symbol": sym.upper(),
                "daily_return_pct": price.get("daily_return_pct"),
                "primary_cause_category": ctx.get("primary_cause_category"),
                "primary_cause_headline": ctx.get("primary_cause_headline"),
                "one_line_reason": ctx.get("one_line_reason"),
                "primary_cause_weight": ctx.get("primary_cause_weight"),
            }
        except Exception:
            pass
    return out


def _build_candidates_from_positions(
    user_id: str,
    positions: List[Dict[str, Any]],
    total_value: float,
    movement: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for p in positions:
        sym = (p.get("ticker") or "").upper()
        if not sym:
            continue
        pos_value = float(p.get("current_value") or 0)
        weight = (pos_value / total_value) if total_value > 0 else 0.0
        daily_ret = float(p.get("daily_return_pct") or p.get("pnl_pct") or 0)
        mov = movement.get(sym) or {}
        if mov.get("daily_return_pct") is not None:
            daily_ret = float(mov["daily_return_pct"])
        impact = round(weight * daily_ret, 4)
        cum = float(p.get("pnl_pct") or 0)
        candidates.append({
            "symbol": sym,
            "type": "top_negative_contributor" if daily_ret < 0 else "top_positive_contributor",
            "daily_return_pct": daily_ret,
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
    daily = float(c.get("daily_return_pct") or 0)
    is_neg = daily < 0
    if card_type == "macro_sector_watch":
        title = "What may matter to your portfolio"
        primary_metric = "—"
    elif card_type == "quiet_day":
        title = c.get("title") or "Your portfolio was quiet today"
        primary_metric = _fmt_pct(daily)
    elif sym:
        title = (
            f"{sym} moved your portfolio lower"
            if is_neg
            else f"{sym} helped your portfolio today"
        )
        primary_metric = _fmt_pct(daily)
    else:
        title = "What moved your money today"
        primary_metric = _fmt_pct(daily)
    body = (c.get("one_line_reason") or "").strip()
    if not body:
        body = "Price moved on market activity today."
    memory = _since_entry_line(
        sym,
        c.get("entry_date"),
        c.get("cumulative_return_since_entry_pct"),
    )
    return {
        "id": f"card_{idx + 1}",
        "type": card_type or ("top_negative_contributor" if is_neg else "top_positive_contributor"),
        "symbol": sym or None,
        "title": title,
        "primary_metric": primary_metric,
        "body": body[:240],
        "memory_context": memory,
        "portfolio_impact_pct": c.get("portfolio_impact_pct"),
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
        "type": "macro_sector_watch",
        "daily_return_pct": 0.0,
        "portfolio_weight": weight,
        "portfolio_impact_pct": 0.0,
        "one_line_reason": reason,
        "entry_date": None,
        "cumulative_return_since_entry_pct": None,
        "user_interest_score": _DEFAULT_INTEREST,
        "novelty_score": 0.6,
        "primary_cause_category": "macro_data",
    }


def _select_cards(ranked: List[Dict[str, Any]], max_cards: int = _MAX_CARDS) -> List[Dict[str, Any]]:
    """Preferred mix: negative, positive, macro/watch."""
    neg = [c for c in ranked if float(c.get("daily_return_pct") or 0) < -0.05]
    pos = [c for c in ranked if float(c.get("daily_return_pct") or 0) > 0.05]
    macro = [c for c in ranked if c.get("type") == "macro_sector_watch"]
    other = [c for c in ranked if c not in neg and c not in pos and c not in macro]

    picked: List[Dict[str, Any]] = []
    if neg:
        picked.append(neg[0])
    if pos:
        picked.append(pos[0])
    if macro and len(picked) < max_cards:
        picked.append(macro[0])
    for pool in (ranked, other):
        for c in pool:
            if len(picked) >= max_cards:
                break
            if c not in picked:
                picked.append(c)
    return picked[:max_cards]


def _continue_where_you_left_off(user_id: str, holdings: List[str]) -> Optional[Dict[str, Any]]:
    actions = pm.list_user_actions(user_id, limit=30)
    hold_set = {h.upper() for h in holdings}
    for a in actions:
        sym = (a.get("symbol") or "").upper()
        if sym and sym in hold_set:
            return {
                "type": "ticker",
                "symbol": sym,
                "label": f"Continue reviewing {sym}",
            }
    try:
        from . import user_preferences as uprefs

        prefs = uprefs.get_preferences(user_id)
        for sym in prefs.get("favorite_tickers") or []:
            if sym.upper() in hold_set:
                return {
                    "type": "ticker",
                    "symbol": sym.upper(),
                    "label": f"Continue reviewing {sym.upper()}",
                }
    except Exception:
        pass
    if holdings:
        return {
            "type": "ticker",
            "symbol": holdings[0].upper(),
            "label": f"Continue reviewing {holdings[0].upper()}",
        }
    return None


def _headline_from_summary(daily_return_pct: Optional[float]) -> str:
    if daily_return_pct is None:
        return "Here is what we noticed in your portfolio today."
    if abs(daily_return_pct) < 0.05:
        return "Your portfolio was mostly quiet today."
    direction = "up" if daily_return_pct > 0 else "down"
    return f"Your portfolio is {direction} {abs(daily_return_pct):.1f}% today."


def build_morning_brief(user_id: str) -> Dict[str, Any]:
    """Build personalized morning brief for one user."""
    now = datetime.now(timezone.utc).isoformat()
    base: Dict[str, Any] = {
        "as_of": now,
        "user_id": user_id,
        "greeting": _greeting(),
        "headline": "Your Morning starts once you add your portfolio.",
        "summary": None,
        "cards": [],
        "watch_next": [],
        "continue_where_you_left_off": None,
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

    # Live fallback when no snapshot yet today
    if daily_return_pct is None:
        movement_pre = _movement_rows_for_symbols(symbols)
        impact_sum = 0.0
        total_w = 0.0
        for p in enriched:
            sym = p["ticker"].upper()
            w = float(p.get("current_value") or 0) / total_value if total_value else 0
            m = movement_pre.get(sym) or {}
            dr = float(m.get("daily_return_pct") or 0)
            impact_sum += w * dr
            p["daily_return_pct"] = dr
            total_w += w
        daily_return_pct = round(impact_sum, 4) if total_w else 0.0

    by_sector = perf.get("analysis", {}).get("by_sector") or {}
    if not sector_exposures and total_value > 0:
        sector_exposures = {
            k: round(v / total_value, 4) for k, v in by_sector.items()
        }

    movement = _movement_rows_for_symbols(symbols)
    candidates = _build_candidates_from_positions(
        user_id, enriched, total_value, movement
    )
    macro_c = _macro_watch_card(sector_exposures, movement)
    if macro_c:
        candidates.append(macro_c)

    ranked = rank_card_candidates(candidates)
    selected = _select_cards(ranked)
    cards = [_card_from_candidate(c, i) for i, c in enumerate(selected)]

    if not cards or all(abs(float(c.get("portfolio_impact_pct") or 0)) < 0.01 for c in selected):
        longest = min(enriched, key=lambda p: p.get("entry_date") or "9999")
        cards = [{
            "id": "card_1",
            "type": "quiet_day",
            "symbol": longest.get("ticker"),
            "title": "Your portfolio was quiet today",
            "primary_metric": _fmt_pct(daily_return_pct),
            "body": "No large holding moves stood out in the latest session.",
            "memory_context": _since_entry_line(
                longest.get("ticker", ""),
                longest.get("entry_date"),
                longest.get("pnl_pct"),
            ),
            "portfolio_impact_pct": 0.0,
            "rank_score": 0.5,
            "actions": [{"label": "Ask AI", "action": "open_chat"}],
        }]

    contributors = sorted(
        ranked,
        key=lambda c: abs(float(c.get("portfolio_impact_pct") or 0)),
        reverse=True,
    )
    top_pos = next((c for c in contributors if float(c.get("daily_return_pct") or 0) > 0), None)
    top_neg = next((c for c in contributors if float(c.get("daily_return_pct") or 0) < 0), None)

    watch_next: List[Dict[str, Any]] = []
    if sector_exposures:
        top_s = max(sector_exposures.items(), key=lambda x: x[1])
        watch_next.append({
            "type": "sector_exposure",
            "title": f"{top_s[0]} is your largest sector exposure",
            "reason": f"About {top_s[1] * 100:.0f}% of your portfolio sits in {top_s[0]}.",
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
            "spy_daily_return_pct": spy_daily,
            "qqq_daily_return_pct": qqq_daily,
        },
    }
    base["cards"] = cards[:_MAX_CARDS]
    base["watch_next"] = watch_next
    base["continue_where_you_left_off"] = _continue_where_you_left_off(user_id, symbols)
    return base
