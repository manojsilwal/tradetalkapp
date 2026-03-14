"""
Persistent alert store using SQLite.
Alerts survive server restarts and are only deleted once the user has seen them.
"""
import sqlite3
import json
import os
import threading
from typing import List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "alerts.db")

_local = threading.local()

def _get_conn():
    """Thread-local SQLite connection."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

def init_db():
    """Create the alerts table if it doesn't exist."""
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            summary TEXT,
            urgency INTEGER DEFAULT 5,
            urgency_label TEXT DEFAULT 'moderate',
            affected_sectors TEXT DEFAULT '[]',
            source TEXT DEFAULT 'Unknown',
            source_reliability TEXT DEFAULT 'low',
            source_reliability_score REAL DEFAULT 0.4,
            link TEXT DEFAULT '',
            timestamp REAL NOT NULL,
            is_read INTEGER DEFAULT 0
        )
    """)
    conn.commit()

def insert_alert(alert: Dict[str, Any]):
    """Insert a new alert into the persistent store."""
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT OR IGNORE INTO alerts 
            (id, title, summary, urgency, urgency_label, affected_sectors, 
             source, source_reliability, source_reliability_score, link, timestamp, is_read)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            alert["id"], alert["title"], alert.get("summary", ""),
            alert.get("urgency", 5), alert.get("urgency_label", "moderate"),
            json.dumps(alert.get("affected_sectors", [])),
            alert.get("source", "Unknown"), alert.get("source_reliability", "low"),
            alert.get("source_reliability_score", 0.4),
            alert.get("link", ""), alert["timestamp"]
        ))
        conn.commit()
    except Exception:
        pass

def get_unseen_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """Get all unseen (unread) alerts, newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM alerts WHERE is_read = 0 ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]

def get_all_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """Get all alerts (seen + unseen), newest first."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM alerts ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]

def count_unread() -> int:
    conn = _get_conn()
    row = conn.execute("SELECT COUNT(*) as c FROM alerts WHERE is_read = 0").fetchone()
    return row["c"] if row else 0

def mark_seen(alert_id: str):
    """Mark a single alert as seen."""
    conn = _get_conn()
    conn.execute("UPDATE alerts SET is_read = 1 WHERE id = ?", (alert_id,))
    conn.commit()

def mark_all_seen():
    """Mark all alerts as seen (user opened the bell dropdown)."""
    conn = _get_conn()
    conn.execute("UPDATE alerts SET is_read = 1 WHERE is_read = 0")
    conn.commit()

def delete_seen():
    """Delete all alerts that have been marked as seen."""
    conn = _get_conn()
    conn.execute("DELETE FROM alerts WHERE is_read = 1")
    conn.commit()

def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "summary": row["summary"],
        "urgency": row["urgency"],
        "urgency_label": row["urgency_label"],
        "affected_sectors": json.loads(row["affected_sectors"]),
        "source": row["source"],
        "source_reliability": row["source_reliability"],
        "source_reliability_score": row["source_reliability_score"],
        "link": row["link"],
        "timestamp": row["timestamp"],
        "is_read": bool(row["is_read"]),
    }
