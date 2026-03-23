"""
keep_alive.py
──────────────────────────────────────────────────────────────────────────────
Keeps the Hugging Face Space alive by pinging it every 5 minutes.
Hugging Face free-tier Spaces go to sleep after ~15 minutes of inactivity.
This runs as a background thread when the FastAPI app starts.

HOW IT WORKS:
  - Sends GET / to the Space's own public URL every 5 minutes
  - If the Space is already awake, the ping is instant (<1s)
  - If the Space woke up from sleep, this ensures it stays awake after the
    first real user request
  - Also triggers the S&P 500 ingestion after each successful wake

CONFIGURATION (Hugging Face Space secrets):
  HF_SPACE_URL  — the public URL of this Space, e.g.
                  https://tradetalkapp-finance-agent-backend-tta.hf.space
                  (set this in HF Space Settings → Repository secrets)
"""
import os
import time
import threading
import logging
import requests

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
PING_INTERVAL_SECONDS = 5 * 60           # ping every 5 minutes
HF_SPACE_URL = os.environ.get(
    "HF_SPACE_URL",
    "https://tradetalkapp-finance-agent-backend-tta.hf.space",
).rstrip("/")

PING_ENDPOINT  = f"{HF_SPACE_URL}/"
INGEST_ENDPOINT = f"{HF_SPACE_URL}/knowledge/sp500-ingest"

# Must match backend ``PIPELINE_CRON_SECRET`` when protected routes are enabled.
PIPELINE_CRON_SECRET = os.environ.get("PIPELINE_CRON_SECRET", "").strip()


def _ping_loop():
    """Background thread: pings the Space every PING_INTERVAL_SECONDS."""
    logger.info(f"[KeepAlive] Starting ping loop → {PING_ENDPOINT} every {PING_INTERVAL_SECONDS}s")
    cycle = 0
    while True:
        time.sleep(PING_INTERVAL_SECONDS)
        try:
            resp = requests.get(PING_ENDPOINT, timeout=30)
            logger.info(f"[KeepAlive] Ping #{cycle} → {resp.status_code}")
        except Exception as e:
            logger.warning(f"[KeepAlive] Ping #{cycle} failed: {e}")

        # Every 12th cycle (~1 hour), re-trigger the S&P 500 ingestion
        # to refresh fundamentals with latest yFinance data
        if cycle > 0 and cycle % 12 == 0:
            try:
                headers = {}
                if PIPELINE_CRON_SECRET:
                    headers["Authorization"] = f"Bearer {PIPELINE_CRON_SECRET}"
                resp = requests.post(INGEST_ENDPOINT, timeout=120, headers=headers)
                logger.info(f"[KeepAlive] SP500 re-ingest triggered → {resp.status_code}")
            except Exception as e:
                logger.warning(f"[KeepAlive] SP500 re-ingest failed: {e}")

        cycle += 1


def start_keep_alive():
    """
    Start the keep-alive ping loop in a daemon thread.
    Call this once from app startup.
    """
    # Render.com (and similar) should not ping a Hugging Face URL — wastes egress and
    # can delay or confuse health checks.
    if os.environ.get("RENDER", "").strip().lower() in ("true", "1", "yes"):
        logger.info("[KeepAlive] Skipping on Render (set HF_SPACE_URL only for Hugging Face Spaces).")
        return
    if not HF_SPACE_URL:
        logger.warning("[KeepAlive] HF_SPACE_URL not set — keep-alive disabled.")
        return

    thread = threading.Thread(target=_ping_loop, daemon=True, name="keep-alive")
    thread.start()
    logger.info("[KeepAlive] Background thread started.")
