"""Single-series FRED CSV fetch — shared by macro pipelines and gold advisor."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_fred_latest_sync(series_id: str, timeout: float = 15.0) -> Optional[float]:
    """Return the most recent numeric observation for a FRED series (no API key)."""
    try:
        import requests

        resp = requests.get(FRED_BASE, params={"id": series_id}, timeout=timeout)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")
        for line in reversed(lines[1:]):
            parts = line.strip().split(",")
            if len(parts) == 2 and parts[1] not in (".", ""):
                try:
                    return round(float(parts[1]), 4)
                except ValueError:
                    continue
    except Exception as e:
        logger.debug("[FRED] %s: %s", series_id, e)
    return None
