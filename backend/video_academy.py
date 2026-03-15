"""
Video Academy — catalogue of TikTok-style investment lessons.
Lessons are generated on demand using Veo and cached in SQLite + static files.
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
            watched       INTEGER DEFAULT 0,
            watched_at    TEXT DEFAULT NULL,
            created_at    REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS lesson_watch_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id  TEXT NOT NULL,
            watched_at REAL NOT NULL,
            completed  INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    _seed_lessons()


LESSON_CATALOGUE: List[Dict] = [
    # Track 1: Value Investing
    {"id": "v001", "track": "Value Investing", "level": 1, "title": "P/E Ratio Explained",
     "topic": "The Price-to-Earnings ratio: what it is, how to calculate it, and why it matters for picking stocks",
     "thumbnail": "📊"},
    {"id": "v002", "track": "Value Investing", "level": 1, "title": "Free Cash Flow = Real Money",
     "topic": "Free Cash Flow vs Net Income: why FCF is the metric billionaire investors use to find hidden gems",
     "thumbnail": "💰"},
    {"id": "v003", "track": "Value Investing", "level": 2, "title": "Margin of Safety",
     "topic": "Ben Graham's Margin of Safety concept: how buying below intrinsic value protects your downside",
     "thumbnail": "🛡️"},
    {"id": "v004", "track": "Value Investing", "level": 2, "title": "ROIC: The Moat Detector",
     "topic": "Return on Invested Capital: the single metric that identifies businesses with durable competitive advantages",
     "thumbnail": "🏰"},
    {"id": "v005", "track": "Value Investing", "level": 3, "title": "DCF Model Basics",
     "topic": "Discounted Cash Flow valuation: projecting future cash flows and discounting them to present value",
     "thumbnail": "🔢"},
    # Track 2: Market Structure
    {"id": "m001", "track": "Market Structure", "level": 1, "title": "How Markets Actually Work",
     "topic": "Order books, bid-ask spreads, market makers, and how your trade actually gets executed",
     "thumbnail": "⚙️"},
    {"id": "m002", "track": "Market Structure", "level": 1, "title": "The Fear Gauge: VIX Explained",
     "topic": "The VIX volatility index: how it's calculated, what readings mean, and how to use it in your strategy",
     "thumbnail": "😱"},
    {"id": "m003", "track": "Market Structure", "level": 2, "title": "Yield Curve Inversion",
     "topic": "The yield curve: what it shows, why an inversion historically predicts recessions, and current signals",
     "thumbnail": "📉"},
    {"id": "m004", "track": "Market Structure", "level": 2, "title": "Sector Rotation",
     "topic": "How money flows between sectors through the business cycle and how to position ahead of rotations",
     "thumbnail": "🔄"},
    {"id": "m005", "track": "Market Structure", "level": 3, "title": "Short Selling & Squeezes",
     "topic": "How short selling works, why short squeezes happen, and the famous GameStop story as a case study",
     "thumbnail": "🚀"},
    # Track 3: Quantitative Strategies
    {"id": "q001", "track": "Quant Strategies", "level": 1, "title": "Moving Averages 101",
     "topic": "Simple and exponential moving averages: how trend-following investors use them to time entries and exits",
     "thumbnail": "📈"},
    {"id": "q002", "track": "Quant Strategies", "level": 1, "title": "Backtesting a Strategy",
     "topic": "How to test an investment strategy on historical data, what CAGR means, and avoiding overfitting",
     "thumbnail": "🔬"},
    {"id": "q003", "track": "Quant Strategies", "level": 2, "title": "Sharpe Ratio Deep Dive",
     "topic": "Risk-adjusted returns: how Sharpe Ratio works, what separates a great from a mediocre strategy",
     "thumbnail": "⚖️"},
    {"id": "q004", "track": "Quant Strategies", "level": 2, "title": "Factor Investing",
     "topic": "The academic research behind size, value, quality, and momentum factors that drive stock returns",
     "thumbnail": "🎯"},
    {"id": "q005", "track": "Quant Strategies", "level": 3, "title": "Portfolio Construction",
     "topic": "Modern Portfolio Theory: efficient frontier, correlation, and how to combine assets for optimal risk/return",
     "thumbnail": "🏗️"},
    # Track 4: AI & Modern Finance
    {"id": "a001", "track": "AI in Finance", "level": 1, "title": "How AI Agents Debate Stocks",
     "topic": "Multi-agent AI systems: how bull, bear, and macro agents argue about a stock to reach a consensus verdict",
     "thumbnail": "🤖"},
    {"id": "a002", "track": "AI in Finance", "level": 2, "title": "RAG for Investment Research",
     "topic": "Retrieval Augmented Generation: how AI systems use historical knowledge bases to generate better analysis",
     "thumbnail": "🧠"},
    {"id": "a003", "track": "AI in Finance", "level": 2, "title": "NLP & Market Sentiment",
     "topic": "How AI reads news, earnings calls, and social media to extract market-moving sentiment signals",
     "thumbnail": "📰"},
    {"id": "a004", "track": "AI in Finance", "level": 3, "title": "Alternative Data Edge",
     "topic": "Satellite imagery, credit card data, web traffic: how hedge funds gain an edge with unconventional datasets",
     "thumbnail": "🛰️"},
]


def _seed_lessons():
    """Insert catalogue entries that don't yet exist in DB."""
    conn = _get_conn()
    for lesson in LESSON_CATALOGUE:
        conn.execute("""
            INSERT OR IGNORE INTO academy_lessons
            (id, track, level, title, topic, thumbnail, status, created_at)
            VALUES (?,?,?,?,?,?, 'pending', ?)
        """, (lesson["id"], lesson["track"], lesson["level"],
              lesson["title"], lesson["topic"], lesson["thumbnail"],
              time.time()))
    conn.commit()


def get_catalogue(track: Optional[str] = None) -> List[Dict]:
    conn = _get_conn()
    q    = "SELECT * FROM academy_lessons"
    args = []
    if track:
        q   += " WHERE track=?"
        args.append(track)
    q += " ORDER BY level, id"
    rows = conn.execute(q, args).fetchall()
    result = []
    for r in rows:
        result.append({
            "id":           r["id"],
            "track":        r["track"],
            "level":        r["level"],
            "title":        r["title"],
            "topic":        r["topic"],
            "thumbnail":    r["thumbnail"],
            "status":       r["status"],
            "watched":      bool(r["watched"]),
            "playlist":     json.loads(r["playlist_json"]),
        })
    return result


def get_lesson(lesson_id: str) -> Optional[Dict]:
    conn = _get_conn()
    r    = conn.execute(
        "SELECT * FROM academy_lessons WHERE id=?", (lesson_id,)
    ).fetchone()
    if not r:
        return None
    return {
        "id":        r["id"],
        "track":     r["track"],
        "level":     r["level"],
        "title":     r["title"],
        "topic":     r["topic"],
        "thumbnail": r["thumbnail"],
        "status":    r["status"],
        "watched":   bool(r["watched"]),
        "playlist":  json.loads(r["playlist_json"]),
    }


def mark_lesson_watched(lesson_id: str):
    conn = _get_conn()
    conn.execute("""
        UPDATE academy_lessons SET watched=1, watched_at=? WHERE id=?
    """, (str(time.time()), lesson_id))
    conn.execute("""
        INSERT INTO lesson_watch_history (lesson_id, watched_at, completed)
        VALUES (?, ?, 1)
    """, (lesson_id, time.time()))
    conn.commit()


def set_lesson_status(lesson_id: str, status: str, playlist: Optional[List] = None):
    conn = _get_conn()
    if playlist is not None:
        conn.execute("""
            UPDATE academy_lessons SET status=?, playlist_json=? WHERE id=?
        """, (status, json.dumps(playlist), lesson_id))
    else:
        conn.execute("UPDATE academy_lessons SET status=? WHERE id=?",
                     (status, lesson_id))
    conn.commit()


def _scene_complete_callback(lesson_id: str, scene_idx: int, status: str):
    if status == "complete":
        conn = _get_conn()
        row  = conn.execute(
            "SELECT playlist_json FROM academy_lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        current = json.loads(row["playlist_json"]) if row else []
        conn.close()
    # Full playlist will be saved when generation completes


Optional = Optional  # re-export
