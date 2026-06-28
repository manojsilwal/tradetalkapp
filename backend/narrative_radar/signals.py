"""
Signal-family orchestration for NR-5..NR-9.

Builds the optional ``signals`` dict consumed by ``scoring.score_theme``:

  institutional  → NR-5  (13F theme aggregation, local DB; on by default)
  productization → NR-6  (EDGAR N-1A/S-1 search; flag NARRATIVE_RADAR_PRODUCTIZATION)
  etf_flow       → NR-9  (ETF dollar-volume flow proxy; flag NARRATIVE_RADAR_ETF_FLOWS)
  narrative      → NR-7  (news/media keyword velocity + sentiment proxy)
  retail         → NR-7  (social/influencer velocity + 'buy now' density)
  reality        → NR-8  (fundamentals: QoQ revenue acceleration aggregated)
  macro          → NR-5/§5.8 (theme ↔ regime fit)

Every sub-builder is resilient (degrades to ``{"available": False}``) and the
network-heavy ones are flag-gated OFF by default so the default scan stays as fast
as the MVP. The pure helpers (``keyword_hits``, ``buy_now_density``,
``reality_from_members``) are offline-testable; live wrappers add the I/O.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence

from . import institutional as nr_institutional
from . import themes as nr_themes

logger = logging.getLogger(__name__)

_BUY_NOW_PHRASES = [
    "best stocks to buy", "next nvidia", "next ai", "supercycle", "once-in-a-generation",
    "must buy", "to the moon", "explosive growth", "buy now", "millionaire",
]
_SELL_PHRASES = [
    "time to sell", "take profits", "take profit", "bubble", "overvalued", "crash incoming",
    "bear market", "get out", "sell now", "overheated", "too late to buy",
]
_POS_WORDS = {"surge", "soar", "beat", "record", "growth", "bullish", "rally", "boom", "upgrade", "demand"}
_NEG_WORDS = {"plunge", "miss", "warn", "cut", "bearish", "selloff", "crash", "downgrade", "weak", "glut"}

# Themes favored by macro regimes (Plan §5.8) — used for a lightweight regime-fit score.
_REGIME_FAVORS: Dict[str, set] = {
    "ai_capex_supercycle": {"ai_compute", "memory_hbm", "optical", "ai_networking", "semi_equipment",
                            "power_infra", "cooling", "data_center_re"},
    "rate_cut_beneficiary": {"data_center_re", "energy_utilities", "grid_construction"},
    "risk_on_growth": {"ai_compute", "cybersecurity", "ai_networking"},
    "energy_commodity_shock": {"energy_utilities", "power_infra"},
}


# ── Narrative / retail pure helpers (NR-7) ────────────────────────────────────


def keyword_hits(titles: Sequence[str], keywords: Sequence[str]) -> int:
    if not titles or not keywords:
        return 0
    kws = [k.lower() for k in keywords]
    n = 0
    for t in titles:
        tl = (t or "").lower()
        if any(k in tl for k in kws):
            n += 1
    return n


def buy_now_density(titles: Sequence[str]) -> float:
    """Hits of late-stage hype phrases per 10 titles."""
    if not titles:
        return 0.0
    hits = 0
    for t in titles:
        tl = (t or "").lower()
        hits += sum(1 for p in _BUY_NOW_PHRASES if p in tl)
    return round(10.0 * hits / len(titles), 2)


def sell_framing_density(titles: Sequence[str]) -> float:
    """Hits of sell-framing / shakeout phrases per 10 titles."""
    if not titles:
        return 0.0
    hits = 0
    for t in titles:
        tl = (t or "").lower()
        hits += sum(1 for p in _SELL_PHRASES if p in tl)
    return round(10.0 * hits / len(titles), 2)


def retail_direction_from_titles(titles: Sequence[str]) -> Optional[float]:
    """
    Retail narrative direction ∈ [-1, +1]: negative = sell-framing dominates,
    positive = buy-pump dominates.
    """
    if not titles:
        return None
    buy = buy_now_density(titles) / 10.0
    sell = sell_framing_density(titles) / 10.0
    if buy + sell == 0:
        return 0.0
    return round(max(-1.0, min(1.0, (buy - sell) / (buy + sell))), 3)


def _sentiment(titles: Sequence[str]) -> Optional[float]:
    if not titles:
        return None
    pos = neg = 0
    for t in titles:
        words = set(re.findall(r"[a-z]+", (t or "").lower()))
        pos += len(words & _POS_WORDS)
        neg += len(words & _NEG_WORDS)
    if pos + neg == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 3)


# ── Reality pure helper (NR-8) ────────────────────────────────────────────────


def reality_from_members(operating_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-member operating metrics into a theme reality signal."""
    accels = [r.get("qoq_revenue_accel_pct") for r in operating_rows if r.get("available")]
    accels = [a for a in accels if a is not None]
    growths = [r.get("qoq_revenue_growth_pct") for r in operating_rows if r.get("available")]
    growths = [g for g in growths if g is not None]
    if not accels and not growths:
        return {"available": False}
    median_accel = sorted(accels)[len(accels) // 2] if accels else None
    median_growth = sorted(growths)[len(growths) // 2] if growths else None
    return {
        "available": True,
        "revenue_accel_pct": median_accel if median_accel is not None else median_growth,
        "capex_growth_pct": None,
        "guidance_revision": None,
        "keyword_growth_pct": None,
        "estimate_revision_pct": None,
    }


# ── Macro regime fit (lightweight, offline) ───────────────────────────────────


def macro_fit(theme_id: str, regime: Optional[str]) -> Dict[str, Any]:
    if not regime:
        return {"available": False}
    favored = _REGIME_FAVORS.get(regime, set())
    if not favored:
        return {"available": False}
    return {"available": True, "regime_fit_pct": 80.0 if theme_id in favored else 35.0, "regime": regime}


# ── Live builders (flag-gated, resilient) ─────────────────────────────────────


def _narrative_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_NARRATIVE", "0").strip() == "1"


def _narrative_enabled_for_theme(theme_id: str) -> bool:
    if _narrative_enabled():
        return True
    if os.environ.get("NARRATIVE_RADAR_SECTORS", "1").strip() == "0":
        return False
    grp = nr_themes.theme_group(theme_id)
    return grp in (nr_themes.GROUP_SECTOR, nr_themes.GROUP_PRECIOUS_METALS)


def _reality_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_REALITY", "0").strip() == "1"


def _sample_members(members: Sequence[str], k: int = 5) -> List[str]:
    return list(members)[:k]


def build_narrative_retail(theme_id: str, keywords: Sequence[str], members: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """News + social keyword aggregation → (narrative, retail) signals. Resilient."""
    if not _narrative_enabled_for_theme(theme_id):
        return {"narrative": {"available": False}, "retail": {"available": False}}
    try:
        from ..connectors import social_sources as ss

        news_titles: List[str] = []
        social_titles: List[str] = []
        for tk in _sample_members(members):
            try:
                news_titles += ss.fetch_yfinance_news_titles(tk, limit=10) or []
                social_titles += ss.fetch_reddit_titles(tk, limit=10) or []
                social_titles += ss.fetch_stocktwits_titles(tk, limit=10) or []
            except Exception:
                continue
        if not news_titles and not social_titles:
            return {"narrative": {"available": False}, "retail": {"available": False}}

        news_hits = keyword_hits(news_titles, keywords)
        social_hits = keyword_hits(social_titles, keywords)
        # Velocity proxies: hit-rate scaled to 0-100 (no historical baseline yet).
        media_freq = min(100.0, 100.0 * news_hits / max(len(news_titles), 1))
        social_vel = min(100.0, 100.0 * social_hits / max(len(social_titles), 1))
        narrative = {
            "available": True,
            "mention_velocity_pct": round(media_freq, 2),
            "attention_percentile": round((media_freq + social_vel) / 2.0, 2),
            "sentiment": _sentiment(news_titles + social_titles),
        }
        all_titles = news_titles + social_titles
        direction = retail_direction_from_titles(all_titles)
        retail = {
            "available": True,
            "social_velocity_pct": round(social_vel, 2),
            "media_freq_pct": round(media_freq, 2),
            "youtube_score": None,
            "buy_now_density": buy_now_density(all_titles),
            "sell_framing_density": sell_framing_density(all_titles),
            "retail_direction": direction,
        }
        return {"narrative": narrative, "retail": retail}
    except Exception as e:
        logger.debug("[NarrativeRadar] narrative/retail build failed for %s: %s", theme_id, e)
        return {"narrative": {"available": False}, "retail": {"available": False}}


def build_reality(members: Sequence[str]) -> Dict[str, Any]:
    """Aggregate QoQ revenue acceleration across members (NR-8). Resilient + gated."""
    if not _reality_enabled():
        return {"available": False}
    try:
        from ..picks_shovels import data as ps_data

        rows = []
        for tk in _sample_members(members, k=8):
            try:
                rows.append(ps_data.fetch_operating_metrics(tk))
            except Exception:
                continue
        return reality_from_members(rows)
    except Exception as e:
        logger.debug("[NarrativeRadar] reality build failed: %s", e)
        return {"available": False}


def build_signals(
    theme_id: str,
    members: Sequence[str],
    *,
    regime: Optional[str] = None,
    member_rows: Optional[Sequence[Dict[str, Any]]] = None,
    options_cache: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Assemble the full optional signals dict for a theme. Each family is independent
    and resilient; unavailable families simply lower confidence (never fabricated).
    """
    from ..connectors import etf_filings, etf_flows
    from . import smart_money as nr_smart_money

    keywords = nr_themes.theme_keywords(theme_id)
    nr = build_narrative_retail(theme_id, keywords, members)
    rows = list(member_rows or [])
    return {
        "institutional": nr_institutional.aggregate_theme(members),
        "smart_money": nr_smart_money.build_smart_money_signal(
            theme_id, members, rows, options_cache=options_cache
        ),
        "productization": etf_filings.build_theme_productization(keywords),
        "etf_flow": etf_flows.build_theme_flow(theme_id),
        "narrative": nr["narrative"],
        "retail": nr["retail"],
        "reality": build_reality(members),
        "macro": macro_fit(theme_id, regime),
    }
