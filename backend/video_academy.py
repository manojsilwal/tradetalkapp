"""
Video Academy — shared lesson catalogue, per-user watch history.

The academy_lessons table is SHARED (one row per lesson, all users share it).
Generation (expensive Veo calls) happens once for all users.
Per-user watched state lives in user_lesson_progress.
"""
import sqlite3
import json
import os
import time
import threading
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "progress.db")
_local  = threading.local()


def _get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_academy_db():
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS academy_lessons (
            id            TEXT PRIMARY KEY,
            track         TEXT NOT NULL,
            level         INTEGER NOT NULL,
            title         TEXT NOT NULL,
            topic         TEXT NOT NULL,
            thumbnail     TEXT DEFAULT '',
            status        TEXT DEFAULT 'pending',
            playlist_json TEXT DEFAULT '[]',
            created_at    REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS user_lesson_progress (
            user_id    TEXT NOT NULL,
            lesson_id  TEXT NOT NULL,
            watched    INTEGER DEFAULT 0,
            watched_at TEXT DEFAULT NULL,
            PRIMARY KEY (user_id, lesson_id)
        );
        CREATE TABLE IF NOT EXISTS lesson_watch_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT NOT NULL,
            lesson_id  TEXT NOT NULL,
            watched_at REAL NOT NULL,
            completed  INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    _migrate_academy_columns(conn)
    _seed_lessons()


def _migrate_academy_columns(conn):
    try:
        conn.execute("ALTER TABLE academy_lessons ADD COLUMN last_error TEXT DEFAULT ''")
        conn.commit()
    except sqlite3.OperationalError:
        pass


_NO_ERR_UPDATE = object()


LESSON_CATALOGUE: List[Dict] = [
    {"id": "v001", "track": "Value Investing",  "level": 1, "title": "P/E Ratio Explained",          "topic": "The Price-to-Earnings ratio: what it is, how to calculate it, and why it matters for picking stocks",                                              "thumbnail": "📊"},
    {"id": "v002", "track": "Value Investing",  "level": 1, "title": "Free Cash Flow = Real Money",  "topic": "Free Cash Flow vs Net Income: why FCF is the metric billionaire investors use to find hidden gems",                                              "thumbnail": "💰"},
    {"id": "v003", "track": "Value Investing",  "level": 2, "title": "Margin of Safety",             "topic": "Ben Graham's Margin of Safety concept: how buying below intrinsic value protects your downside",                                                 "thumbnail": "🛡️"},
    {"id": "v004", "track": "Value Investing",  "level": 2, "title": "ROIC: The Moat Detector",      "topic": "Return on Invested Capital: the single metric that identifies businesses with durable competitive advantages",                                   "thumbnail": "🏰"},
    {"id": "v005", "track": "Value Investing",  "level": 3, "title": "DCF Model Basics",             "topic": "Discounted Cash Flow valuation: projecting future cash flows and discounting them to present value",                                             "thumbnail": "🔢"},
    {"id": "m001", "track": "Market Structure", "level": 1, "title": "How Markets Actually Work",    "topic": "Order books, bid-ask spreads, market makers, and how your trade actually gets executed",                                                          "thumbnail": "⚙️"},
    {"id": "m002", "track": "Market Structure", "level": 1, "title": "The Fear Gauge: VIX Explained","topic": "The VIX volatility index: how it's calculated, what readings mean, and how to use it in your strategy",                                          "thumbnail": "😱"},
    {"id": "m003", "track": "Market Structure", "level": 2, "title": "Yield Curve Inversion",        "topic": "The yield curve: what it shows, why an inversion historically predicts recessions, and current signals",                                         "thumbnail": "📉"},
    {"id": "m004", "track": "Market Structure", "level": 2, "title": "Sector Rotation",              "topic": "How money flows between sectors through the business cycle and how to position ahead of rotations",                                               "thumbnail": "🔄"},
    {"id": "m005", "track": "Market Structure", "level": 3, "title": "Short Selling & Squeezes",     "topic": "How short selling works, why short squeezes happen, and the famous GameStop story as a case study",                                              "thumbnail": "🚀"},
    {"id": "q001", "track": "Quant Strategies", "level": 1, "title": "Moving Averages 101",          "topic": "Simple and exponential moving averages: how trend-following investors use them to time entries and exits",                                        "thumbnail": "📈"},
    {"id": "q002", "track": "Quant Strategies", "level": 1, "title": "Backtesting a Strategy",       "topic": "How to test an investment strategy on historical data, what CAGR means, and avoiding overfitting",                                               "thumbnail": "🔬"},
    {"id": "q003", "track": "Quant Strategies", "level": 2, "title": "Sharpe Ratio Deep Dive",       "topic": "Risk-adjusted returns: how Sharpe Ratio works, what separates a great from a mediocre strategy",                                                "thumbnail": "⚖️"},
    {"id": "q004", "track": "Quant Strategies", "level": 2, "title": "Factor Investing",             "topic": "The academic research behind size, value, quality, and momentum factors that drive stock returns",                                                "thumbnail": "🎯"},
    {"id": "q005", "track": "Quant Strategies", "level": 3, "title": "Portfolio Construction",       "topic": "Modern Portfolio Theory: efficient frontier, correlation, and how to combine assets for optimal risk/return",                                     "thumbnail": "🏗️"},
    {"id": "a001", "track": "AI in Finance",    "level": 1, "title": "How AI Agents Debate Stocks",  "topic": "Multi-agent AI systems: how bull, bear, and macro agents argue about a stock to reach a consensus verdict",                                     "thumbnail": "🤖"},
    {"id": "a002", "track": "AI in Finance",    "level": 2, "title": "RAG for Investment Research",  "topic": "Retrieval Augmented Generation: how AI systems use historical knowledge bases to generate better analysis",                                      "thumbnail": "🧠"},
    {"id": "a003", "track": "AI in Finance",    "level": 2, "title": "NLP & Market Sentiment",       "topic": "How AI reads news, earnings calls, and social media to extract market-moving sentiment signals",                                                 "thumbnail": "📰"},
    {"id": "a004", "track": "AI in Finance",    "level": 3, "title": "Alternative Data Edge",        "topic": "Satellite imagery, credit card data, web traffic: how hedge funds gain an edge with unconventional datasets",                                    "thumbnail": "🛰️"},
]


def _seed_lessons():
    conn = _get_conn()
    for lesson in LESSON_CATALOGUE:
        conn.execute("""
            INSERT OR IGNORE INTO academy_lessons
            (id, track, level, title, topic, thumbnail, status, created_at)
            VALUES (?,?,?,?,?,?, 'pending', ?)
        """, (lesson["id"], lesson["track"], lesson["level"],
              lesson["title"], lesson["topic"], lesson["thumbnail"], time.time()))
    conn.commit()


def get_catalogue(user_id: str, track: Optional[str] = None) -> List[Dict]:
    conn = _get_conn()
    q    = "SELECT * FROM academy_lessons"
    args = []
    if track:
        q    += " WHERE track=?"
        args.append(track)
    q += " ORDER BY level, id"
    rows = conn.execute(q, args).fetchall()

    # Fetch per-user watched status in one query
    watched_ids: set = set()
    if user_id:
        wrows = conn.execute(
            "SELECT lesson_id FROM user_lesson_progress WHERE user_id=? AND watched=1",
            (user_id,)
        ).fetchall()
        watched_ids = {r["lesson_id"] for r in wrows}

    return [{
        "id":        r["id"],
        "track":     r["track"],
        "level":     r["level"],
        "title":     r["title"],
        "topic":     r["topic"],
        "thumbnail": r["thumbnail"],
        "status":    r["status"],
        "watched":   r["id"] in watched_ids,
        "playlist":  json.loads(r["playlist_json"]),
        "last_error": dict(r).get("last_error") or "",
    } for r in rows]


def get_lesson(user_id: str, lesson_id: str) -> Optional[Dict]:
    conn = _get_conn()
    r    = conn.execute("SELECT * FROM academy_lessons WHERE id=?", (lesson_id,)).fetchone()
    if not r:
        return None
    watched_row = conn.execute(
        "SELECT watched FROM user_lesson_progress WHERE user_id=? AND lesson_id=?",
        (user_id, lesson_id)
    ).fetchone()
    return {
        "id":        r["id"],
        "track":     r["track"],
        "level":     r["level"],
        "title":     r["title"],
        "topic":     r["topic"],
        "thumbnail": r["thumbnail"],
        "status":    r["status"],
        "watched":   bool(watched_row["watched"]) if watched_row else False,
        "playlist":  json.loads(r["playlist_json"]),
        "last_error": dict(r).get("last_error") or "",
    }


def mark_lesson_watched(user_id: str, lesson_id: str):
    conn = _get_conn()
    conn.execute("""
        INSERT OR REPLACE INTO user_lesson_progress (user_id, lesson_id, watched, watched_at)
        VALUES (?, ?, 1, ?)
    """, (user_id, lesson_id, str(time.time())))
    conn.execute("""
        INSERT INTO lesson_watch_history (user_id, lesson_id, watched_at, completed)
        VALUES (?, ?, ?, 1)
    """, (user_id, lesson_id, time.time()))
    conn.commit()


def set_lesson_status(
    lesson_id: str,
    status: str,
    playlist: Optional[List] = None,
    *,
    last_error: Any = _NO_ERR_UPDATE,
):
    """Shared — status and playlist apply to all users (generation is shared)."""
    conn = _get_conn()
    if playlist is not None and last_error is not _NO_ERR_UPDATE:
        conn.execute(
            "UPDATE academy_lessons SET status=?, playlist_json=?, last_error=? WHERE id=?",
            (status, json.dumps(playlist), last_error or "", lesson_id),
        )
    elif playlist is not None:
        conn.execute(
            "UPDATE academy_lessons SET status=?, playlist_json=? WHERE id=?",
            (status, json.dumps(playlist), lesson_id),
        )
    elif last_error is not _NO_ERR_UPDATE:
        conn.execute(
            "UPDATE academy_lessons SET status=?, last_error=? WHERE id=?",
            (status, last_error or "", lesson_id),
        )
    else:
        conn.execute("UPDATE academy_lessons SET status=? WHERE id=?", (status, lesson_id))
    conn.commit()
