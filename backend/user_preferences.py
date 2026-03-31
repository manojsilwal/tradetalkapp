"""
User Preferences — durable learned traits persisted across sessions.

Stores user behaviour signals (favourite tickers, risk tolerance, explanation
style, preferred tools) in SQLite.  Preferences are injected into the chat
system prompt so every new session starts personalised.

Two update paths:
  1. **Explicit** — user sets preferences via API (PUT /preferences)
  2. **Implicit** — ``learn_from_action()`` infers preferences from behaviour
     (debated AAPL 5 times → "favorite_tickers" includes AAPL).
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local = threading.local()

# ── Default preference values ─────────────────────────────────────────────────
DEFAULT_PREFERENCES: Dict[str, Any] = {
    "favorite_tickers": [],          # auto-populated from usage
    "preferred_sectors": [],         # auto-populated from usage
    "risk_tolerance": "moderate",    # conservative | moderate | aggressive
    "investment_horizon": "medium",  # short | medium | long
    "explain_style": "balanced",     # simple | balanced | technical
    "preferred_tools": [],           # ["debate", "backtest", "chat", "gold"]
    "watchlist": [],                 # user-curated watchlist tickers
}

# Maximum favourite tickers / sectors to keep (rolling window)
_MAX_FAVOURITES = 20
_MAX_SECTORS = 10

# Minimum interactions before a ticker/sector is promoted to favourite
_TICKER_THRESHOLD = 2
_SECTOR_THRESHOLD = 3

# ── SQLite helpers ────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "pref_conn"):
        _local.pref_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.pref_conn.row_factory = sqlite3.Row
    return _local.pref_conn


def init_preferences_db() -> None:
    """Create the preferences table if it doesn't exist (idempotent)."""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id     TEXT PRIMARY KEY,
            preferences TEXT DEFAULT '{}',
            signals     TEXT DEFAULT '{}',
            updated_at  REAL DEFAULT 0
        );
    """)
    conn.commit()
    logger.info("[UserPreferences] table ready")


def _ensure_row(user_id: str) -> None:
    conn = _get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO user_preferences (user_id, preferences, signals, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (user_id, json.dumps(DEFAULT_PREFERENCES), "{}", time.time()),
    )
    conn.commit()


# ── Public API ────────────────────────────────────────────────────────────────

def get_preferences(user_id: str) -> Dict[str, Any]:
    """Return merged preferences (defaults + user overrides)."""
    _ensure_row(user_id)
    conn = _get_conn()
    row = conn.execute(
        "SELECT preferences FROM user_preferences WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return dict(DEFAULT_PREFERENCES)
    stored = json.loads(row["preferences"])
    merged = {**DEFAULT_PREFERENCES, **stored}
    return merged


def update_preferences(user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge explicit preference updates into the stored state.

    Only keys present in DEFAULT_PREFERENCES are accepted (unknown keys ignored).
    Returns the full merged preferences dict.
    """
    _ensure_row(user_id)
    current = get_preferences(user_id)

    valid_keys = set(DEFAULT_PREFERENCES.keys())
    for k, v in updates.items():
        if k in valid_keys:
            current[k] = v

    conn = _get_conn()
    conn.execute(
        "UPDATE user_preferences SET preferences = ?, updated_at = ? WHERE user_id = ?",
        (json.dumps(current), time.time(), user_id),
    )
    conn.commit()
    logger.info("[UserPreferences] explicit update for %s: %s", user_id, list(updates.keys()))
    return current


def get_signals(user_id: str) -> Dict[str, Any]:
    """Return raw behavioural signal counters."""
    _ensure_row(user_id)
    conn = _get_conn()
    row = conn.execute(
        "SELECT signals FROM user_preferences WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return {}
    return json.loads(row["signals"])


def learn_from_action(
    user_id: str,
    action: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Implicit preference learning — called after key user actions.

    Accumulates behavioural signals and auto-promotes tickers/sectors/tools
    into preferences when thresholds are crossed.

    Actions: "debate", "backtest", "trace", "gold_advisor", "chat_ticker",
             "decision_terminal", "macro_view"
    Context: {"ticker": "AAPL", "sector": "Technology", ...}
    """
    if not user_id:
        return
    ctx = context or {}
    _ensure_row(user_id)

    conn = _get_conn()
    row = conn.execute(
        "SELECT signals, preferences FROM user_preferences WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    signals: Dict[str, Any] = json.loads(row["signals"]) if row else {}
    prefs: Dict[str, Any] = json.loads(row["preferences"]) if row else dict(DEFAULT_PREFERENCES)

    # ── Accumulate signals ────────────────────────────────────────────────
    # Ticker interactions
    ticker = ctx.get("ticker", "").upper().strip()
    if ticker and len(ticker) <= 5:
        ticker_counts = signals.setdefault("ticker_counts", {})
        ticker_counts[ticker] = ticker_counts.get(ticker, 0) + 1

    # Sector interactions
    sector = ctx.get("sector", "").strip()
    if sector:
        sector_counts = signals.setdefault("sector_counts", {})
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    # Tool usage
    tool_counts = signals.setdefault("tool_counts", {})
    tool_counts[action] = tool_counts.get(action, 0) + 1

    # ── Promote to preferences when thresholds crossed ────────────────────
    # Favourite tickers
    ticker_counts = signals.get("ticker_counts", {})
    top_tickers = sorted(ticker_counts, key=ticker_counts.get, reverse=True)[:_MAX_FAVOURITES]
    prefs["favorite_tickers"] = [
        t for t in top_tickers if ticker_counts.get(t, 0) >= _TICKER_THRESHOLD
    ]

    # Preferred sectors
    sector_counts = signals.get("sector_counts", {})
    top_sectors = sorted(sector_counts, key=sector_counts.get, reverse=True)[:_MAX_SECTORS]
    prefs["preferred_sectors"] = [
        s for s in top_sectors if sector_counts.get(s, 0) >= _SECTOR_THRESHOLD
    ]

    # Preferred tools
    tool_ctr = signals.get("tool_counts", {})
    if tool_ctr:
        top_tools = sorted(tool_ctr, key=tool_ctr.get, reverse=True)[:5]
        prefs["preferred_tools"] = top_tools

    # ── Persist ───────────────────────────────────────────────────────────
    conn.execute(
        "UPDATE user_preferences SET preferences = ?, signals = ?, updated_at = ? "
        "WHERE user_id = ?",
        (json.dumps(prefs), json.dumps(signals), time.time(), user_id),
    )
    conn.commit()


def format_for_system_prompt(user_id: str) -> str:
    """
    Build a concise preference block for injection into the chat system prompt.

    Returns empty string if no meaningful preferences yet.
    """
    prefs = get_preferences(user_id)

    lines: List[str] = []

    if prefs.get("favorite_tickers"):
        lines.append(f"Frequently discussed tickers: {', '.join(prefs['favorite_tickers'][:10])}")

    if prefs.get("preferred_sectors"):
        lines.append(f"Preferred sectors: {', '.join(prefs['preferred_sectors'][:5])}")

    rt = prefs.get("risk_tolerance", "moderate")
    if rt != "moderate":
        lines.append(f"Risk tolerance: {rt}")

    horizon = prefs.get("investment_horizon", "medium")
    if horizon != "medium":
        horizon_map = {"short": "short-term (weeks/months)", "long": "long-term (years/decades)"}
        lines.append(f"Investment horizon: {horizon_map.get(horizon, horizon)}")

    style = prefs.get("explain_style", "balanced")
    if style == "simple":
        lines.append("Communication preference: explain in simple, jargon-free language")
    elif style == "technical":
        lines.append("Communication preference: use technical detail and precise terminology")

    if prefs.get("watchlist"):
        lines.append(f"Watchlist: {', '.join(prefs['watchlist'][:10])}")

    if not lines:
        return ""

    return "## User preferences (learned from past sessions)\n" + "\n".join(f"- {l}" for l in lines)
