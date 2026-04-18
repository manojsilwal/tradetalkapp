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
    # Finance profile (LLM tools + prompt framing; CORAL stays market-knowledge only)
    "position_type": "unknown",      # long | short | flat | unknown
    "preferred_signal_format": "balanced",  # directional | with_levels | with_reasoning | balanced
    "alert_on_regimes": "no",       # yes | no
    "base_currency": "USD",         # USD | GBP | EUR | other
    "trading_style": "swing",       # intraday | swing | position
}

# Keys the chat tools may set (values validated in save_financial_preference_for_tool).
FINANCIAL_TOOL_KEYS = frozenset(
    {
        "risk_tolerance",
        "investment_horizon",
        "explain_style",
        "position_type",
        "preferred_signal_format",
        "alert_on_regimes",
        "base_currency",
        "trading_style",
    }
)

_VALUE_ENUMS: Dict[str, frozenset] = {
    "risk_tolerance": frozenset({"conservative", "moderate", "aggressive"}),
    "investment_horizon": frozenset({"short", "medium", "long"}),
    "explain_style": frozenset({"simple", "balanced", "technical"}),
    "position_type": frozenset({"long", "short", "flat", "unknown"}),
    "preferred_signal_format": frozenset(
        {"directional", "with_levels", "with_reasoning", "balanced"}
    ),
    "alert_on_regimes": frozenset({"yes", "no"}),
    "base_currency": frozenset({"USD", "GBP", "EUR", "other"}),
    "trading_style": frozenset({"intraday", "swing", "position"}),
}

_MAX_PREF_VALUE_LEN = 256

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


def format_agent_memory_instructions() -> str:
    """
    Injected into chat system prompt for authenticated users.
    Directs use of recall_financial_profile / save_financial_preference tools.
    """
    return (
        "## User financial profile (tier-2 memory)\n"
        "- You have tools **recall_financial_profile** and **save_financial_preference** (this user only).\n"
        "- Early in the conversation, call **recall_financial_profile** once if tailoring or gold/macro/positioning "
        "context may matter. Use the result to **frame** explanations (not to invent prices or facts).\n"
        "- If the profile is still thin and the user asks about **gold, positioning, or horizon**, ask **one** "
        "short clarifying question (e.g. position type and time horizon) before a deep analysis.\n"
        "- When the user states a **durable** preference (risk, horizon, signal format, currency, regime alerts), "
        "call **save_financial_preference** in the same turn with a valid key/value.\n"
        "- CORAL/retrieval blocks are **market** priors; this profile is **the user** — do not mix them up.\n"
    )


def recall_financial_profile_json(user_id: str) -> str:
    """JSON string of merged preferences for tool return (bounded size)."""
    if not user_id:
        return json.dumps({"error": "not_authenticated"})
    prefs = get_preferences(user_id)
    # Only expose tool-relevant + stable keys (no raw signal counters)
    keys = sorted(set(FINANCIAL_TOOL_KEYS) | {"favorite_tickers", "watchlist"})
    out = {k: prefs.get(k) for k in keys if k in prefs}
    raw = json.dumps(out, default=str)
    return raw[:8000]


def save_financial_preference_for_tool(user_id: str, key: str, value: str) -> str:
    """
    Validate and persist one preference from chat tools.
    Returns a short confirmation or error message for the model.
    """
    if not user_id:
        return "Cannot save: user is not signed in."
    k = (key or "").strip()
    v = (value or "").strip()
    if not k:
        return "Missing key."
    if k not in FINANCIAL_TOOL_KEYS:
        return f"Unknown key: {k}. Allowed: {', '.join(sorted(FINANCIAL_TOOL_KEYS))}."
    if len(v) > _MAX_PREF_VALUE_LEN:
        return f"Value too long (max {_MAX_PREF_VALUE_LEN} characters)."
    allowed = _VALUE_ENUMS.get(k)
    if allowed is not None and v not in allowed:
        return f"Invalid value for {k}. Allowed: {', '.join(sorted(allowed))}."
    updates = {k: v}
    update_preferences(user_id, updates)
    return f"Saved {k} = {v}."


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

    pt = prefs.get("position_type") or "unknown"
    if pt not in ("unknown", "", None):
        lines.append(f"Position type: {pt}")

    ts = prefs.get("trading_style") or "swing"
    if ts != "swing":
        lines.append(f"Trading style / horizon: {ts}")

    psf = prefs.get("preferred_signal_format") or "balanced"
    if psf != "balanced":
        lines.append(f"Preferred signal format: {psf}")

    ar = prefs.get("alert_on_regimes") or "no"
    if ar == "yes":
        lines.append("Alert on regime shifts: yes")

    bc = prefs.get("base_currency") or "USD"
    if bc != "USD":
        lines.append(f"Base currency: {bc}")

    if not lines:
        return ""

    return "## User preferences (learned from past sessions)\n" + "\n".join(f"- {l}" for l in lines)
