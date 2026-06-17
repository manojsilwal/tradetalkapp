"""Watchlist preference limits and AI infra basket size."""
from __future__ import annotations

import json
import os
import tempfile
import unittest

import backend.user_preferences as user_preferences

AI_INFRA_BASKET = [
    "GEV",
    "ETN",
    "SBGSY",
    "SIEGY",
    "VRT",
    "ECL",
    "ASTK.OL",
    "NVDA",
    "AVGO",
    "MRVL",
    "COHR",
    "LITE",
    "GLW",
]


def _normalize_watchlist(tickers: list[str]) -> list[str]:
    """Mirror backend/routers/preferences.py PUT normalization."""
    return [t.upper().strip() for t in tickers[:20] if t.strip()]


class TestWatchlistPreferences(unittest.TestCase):
    def setUp(self):
        self._orig_db = user_preferences.DB_PATH
        self._tmpdir = tempfile.mkdtemp()
        self._db_path = os.path.join(self._tmpdir, "prefs.db")
        user_preferences.DB_PATH = self._db_path

        # Clear cached thread-local connection
        if hasattr(user_preferences._local, "pref_conn"):
            try:
                user_preferences._local.pref_conn.close()
            except Exception:
                pass
            delattr(user_preferences._local, "pref_conn")

        user_preferences.init_preferences_db()

    def tearDown(self):
        if hasattr(user_preferences._local, "pref_conn"):
            try:
                user_preferences._local.pref_conn.close()
            except Exception:
                pass
            delattr(user_preferences._local, "pref_conn")
        user_preferences.DB_PATH = self._orig_db

    def test_thirteen_ticker_basket_within_cap(self):
        normalized = _normalize_watchlist(AI_INFRA_BASKET)
        self.assertEqual(len(normalized), 13)
        self.assertEqual(len(set(normalized)), 13)

    def test_preferences_persist_thirteen_tickers(self):
        user_id = "watchlist-test-user"
        normalized = _normalize_watchlist(AI_INFRA_BASKET)
        prefs = user_preferences.update_preferences(user_id, {"watchlist": normalized})
        self.assertEqual(prefs["watchlist"], normalized)

        reloaded = user_preferences.get_preferences(user_id)
        self.assertEqual(len(reloaded["watchlist"]), 13)
        self.assertIn("NVDA", reloaded["watchlist"])


if __name__ == "__main__":
    unittest.main()
