"""
User Progress — XP, Streak, Level, and Badges (per-user via user_id).
"""
import sqlite3
import json
import os
import time
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local = threading.local()


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


XP_TABLE = {
    "valuation":        10,
    "debate":           15,
    "backtest":         20,
    "daily_challenge":  30,
    "lesson_complete":  35,
    "module_complete":  25,
    "prediction_log":   10,
    "prediction_right": 20,
    "streak_7":         50,
    "streak_30":        150,
    "streak_100":       500,
}

LEVELS = [
    (1,   "Novice",             0),
    (5,   "Analyst",            500),
    (10,  "Trader",             1500),
    (20,  "Portfolio Manager",  4000),
    (35,  "Market Wizard",      9000),
    (50,  "Quant Legend",       20000),
]

BADGES = {
    "first_blood":         {"name": "First Blood",         "desc": "Ran your first valuation",            "icon": "🩸"},
    "debate_starter":      {"name": "Debate Starter",      "desc": "Completed your first AI Debate",      "icon": "⚔️"},
    "strategy_architect":  {"name": "Strategy Architect",  "desc": "Ran 10 unique backtests",             "icon": "🏗️"},
    "contrarian":          {"name": "Contrarian",          "desc": "Picked 5 losing debate sides",        "icon": "🔄"},
    "data_nerd":           {"name": "Data Nerd",           "desc": "Used Developer Trace 10 times",       "icon": "🔬"},
    "macro_master":        {"name": "Macro Master",        "desc": "Opened Macro Dashboard 30 times",     "icon": "🌍"},
    "warren_mode":         {"name": "Warren Mode",         "desc": "5 value strategies beat SPY",         "icon": "🎩"},
    "streak_week":         {"name": "Streak: 7 Days",      "desc": "7-day login streak",                  "icon": "🔥"},
    "streak_month":        {"name": "Streak Legend",       "desc": "30-day login streak",                 "icon": "⚡"},
    "streak_century":      {"name": "Century Streak",      "desc": "100-day login streak",                "icon": "💯"},
    "cinephile":           {"name": "Cinephile",           "desc": "Watched 20 Academy lessons",          "icon": "🎬"},
    "market_wizard_badge": {"name": "Market Wizard",       "desc": "Reached Level 20",                    "icon": "🧙"},
    "portfolio_pro":       {"name": "Portfolio Pro",       "desc": "Paper portfolio beat SPY for 30 days","icon": "📈"},
    "challenge_master":    {"name": "Challenge Master",    "desc": "Completed 30 daily challenges",       "icon": "🏆"},
}


def init_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id        TEXT PRIMARY KEY,
            xp             INTEGER DEFAULT 0,
            level          INTEGER DEFAULT 1,
            level_title    TEXT    DEFAULT 'Novice',
            streak_days    INTEGER DEFAULT 0,
            last_active    TEXT    DEFAULT '',
            total_actions  TEXT    DEFAULT '{}',
            badges         TEXT    DEFAULT '[]',
            created_at     REAL    DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS xp_history (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     TEXT    NOT NULL,
            action      TEXT    NOT NULL,
            xp_awarded  INTEGER NOT NULL,
            note        TEXT    DEFAULT '',
            timestamp   REAL    NOT NULL
        );
    """)
    conn.commit()


def _ensure_user(user_id: str):
    conn = _get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO user_progress (user_id, created_at)
        VALUES (?, ?)
    """, (user_id, time.time()))
    conn.commit()


def _compute_level(xp: int):
    level, title = 1, "Novice"
    for lvl, name, threshold in LEVELS:
        if xp >= threshold:
            level, title = lvl, name
        else:
            break
    return level, title


def get_progress(user_id: str) -> Dict[str, Any]:
    _ensure_user(user_id)
    conn = _get_conn()
    row  = conn.execute("SELECT * FROM user_progress WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {}
    level, title = _compute_level(row["xp"])
    next_threshold = None
    for lvl, name, threshold in LEVELS:
        if threshold > row["xp"]:
            next_threshold = threshold
            break
    prev_threshold = 0
    for lvl, name, threshold in LEVELS:
        if threshold <= row["xp"]:
            prev_threshold = threshold
    xp_in_level  = row["xp"] - prev_threshold
    xp_for_level = (next_threshold - prev_threshold) if next_threshold else 1
    return {
        "xp":             row["xp"],
        "level":          level,
        "level_title":    title,
        "streak_days":    row["streak_days"],
        "last_active":    row["last_active"],
        "badges":         json.loads(row["badges"]),
        "total_actions":  json.loads(row["total_actions"]),
        "xp_in_level":    xp_in_level,
        "xp_for_level":   xp_for_level,
        "xp_pct":         round(xp_in_level / xp_for_level * 100) if xp_for_level else 100,
        "next_level_xp":  next_threshold,
        "all_badges":     BADGES,
    }


def award_xp(user_id: str, action: str, note: str = "") -> Dict[str, Any]:
    _ensure_user(user_id)
    conn = _get_conn()
    row  = conn.execute("SELECT * FROM user_progress WHERE user_id=?", (user_id,)).fetchone()
    if not row:
        return {}

    xp_award   = XP_TABLE.get(action, 5)
    old_xp     = row["xp"]
    new_xp     = old_xp + xp_award
    old_level, _ = _compute_level(old_xp)
    new_level, new_title = _compute_level(new_xp)
    leveled_up = new_level > old_level

    today     = date.today().isoformat()
    last      = row["last_active"]
    streak    = row["streak_days"]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if last == today:
        pass
    elif last == yesterday:
        streak += 1
    elif last == "":
        streak = 1
    else:
        streak = 1

    actions = json.loads(row["total_actions"])
    actions[action] = actions.get(action, 0) + 1

    badges     = json.loads(row["badges"])
    new_badges = _check_badges(actions, streak, new_level, badges, note)
    badges_all = list(set(badges + new_badges))

    bonus_xp = 0
    if streak == 7   and "streak_7"   not in badges: bonus_xp = XP_TABLE["streak_7"]
    if streak == 30  and "streak_30"  not in badges: bonus_xp = XP_TABLE["streak_30"]
    if streak == 100 and "streak_100" not in badges: bonus_xp = XP_TABLE["streak_100"]
    new_xp += bonus_xp

    conn.execute("""
        UPDATE user_progress
        SET xp=?, level=?, level_title=?, streak_days=?, last_active=?,
            total_actions=?, badges=?
        WHERE user_id=?
    """, (new_xp, new_level, new_title, streak, today,
          json.dumps(actions), json.dumps(badges_all), user_id))
    conn.execute("""
        INSERT INTO xp_history (user_id, action, xp_awarded, note, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, action, xp_award + bonus_xp, note, time.time()))
    conn.commit()

    return {
        "xp_awarded":   xp_award + bonus_xp,
        "new_total_xp": new_xp,
        "leveled_up":   leveled_up,
        "new_level":    new_level,
        "new_title":    new_title,
        "new_badges":   [BADGES[b] | {"id": b} for b in new_badges],
        "streak_days":  streak,
    }


def _check_badges(actions: dict, streak: int, level: int,
                  existing: List[str], note: str) -> List[str]:
    new_badges = []
    def _earn(bid):
        if bid not in existing and bid not in new_badges:
            new_badges.append(bid)
    if actions.get("valuation", 0) >= 1:         _earn("first_blood")
    if actions.get("debate", 0) >= 1:             _earn("debate_starter")
    if actions.get("backtest", 0) >= 10:          _earn("strategy_architect")
    if actions.get("macro_view", 0) >= 30:        _earn("macro_master")
    if actions.get("lesson_complete", 0) >= 20:   _earn("cinephile")
    if actions.get("daily_challenge", 0) >= 30:   _earn("challenge_master")
    if streak >= 7:                                _earn("streak_week")
    if streak >= 30:                               _earn("streak_month")
    if streak >= 100:                              _earn("streak_century")
    if level >= 20:                                _earn("market_wizard_badge")
    if note == "beat_spy":                         _earn("warren_mode")
    return new_badges


def get_xp_history(user_id: str, limit: int = 20) -> List[Dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM xp_history WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit)
    ).fetchall()
    return [dict(r) for r in rows]
