"""
FRED Macro Connector — fetches daily snapshots of key macroeconomic indicators
from the Federal Reserve Economic Data public CSV endpoint.
No API key required — uses the free public endpoint.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# FRED series IDs for key indicators
FRED_SERIES = {
    "fed_funds_rate":  "FEDFUNDS",        # Federal Funds Effective Rate (monthly)
    "cpi_yoy":         "CPIAUCSL",         # CPI (we compute YoY ourselves)
    "treasury_10y":    "DGS10",            # 10-Year Treasury Constant Maturity Rate (daily)
    "unemployment":    "UNRATE",           # Unemployment Rate (monthly)
    "m2_supply":       "M2SL",             # M2 Money Supply (monthly, billions USD)
}


async def fetch_macro_snapshot() -> dict:
    """
    Fetch the most recent value for each FRED indicator.
    Returns dict: {fed_funds_rate, cpi_yoy, treasury_10y, unemployment, m2_supply}
    """
    return await asyncio.to_thread(_sync_fetch_all)


def _sync_fetch_all() -> dict:
    import requests
    snapshot = {}
    for key, series_id in FRED_SERIES.items():
        try:
            val = _fetch_series_latest(requests, series_id)
            snapshot[key] = val
        except Exception as e:
            logger.warning(f"[FREDConnector] Failed for {series_id}: {e}")
            snapshot[key] = None

    # Compute CPI YoY: need 2 values, 12 months apart
    if snapshot.get("cpi_yoy") is not None:
        snapshot["cpi_yoy"] = _compute_cpi_yoy()

    snapshot["fetched_at"] = str(datetime.now(timezone.utc).date())
    return snapshot


def _fetch_series_latest(requests, series_id: str) -> Optional[float]:
    """Download the FRED CSV and return the last valid float value."""
    params = {"id": series_id}
    resp = requests.get(FRED_BASE, params=params, timeout=15)
    resp.raise_for_status()
    lines = resp.text.strip().split("\n")
    # Header is line 0: "DATE,VALUE"
    # Find the last non-empty, non-"." value
    for line in reversed(lines[1:]):
        parts = line.strip().split(",")
        if len(parts) == 2 and parts[1] not in (".", ""):
            try:
                return round(float(parts[1]), 4)
            except ValueError:
                continue
    return None


def _compute_cpi_yoy() -> Optional[float]:
    """
    Fetch 13 months of CPI data and compute the most recent YoY change.
    """
    try:
        import requests as req
        params = {"id": "CPIAUCSL"}
        resp = req.get(FRED_BASE, params=params, timeout=15)
        resp.raise_for_status()
        lines = resp.text.strip().split("\n")[1:]  # skip header
        values = []
        for line in lines:
            parts = line.strip().split(",")
            if len(parts) == 2 and parts[1] not in (".", ""):
                try:
                    values.append(float(parts[1]))
                except ValueError:
                    pass
        if len(values) >= 13:
            latest = values[-1]
            year_ago = values[-13]
            if year_ago:
                return round((latest / year_ago - 1) * 100, 2)
    except Exception as e:
        logger.warning(f"[FREDConnector] CPI YoY computation failed: {e}")
    return None
