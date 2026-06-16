"""
FRED Macro Connector — fetches key macroeconomic indicators from FRED public CSV.
No API key required.
"""
import asyncio
import json
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_CURL_TIMEOUT_S = 20

FRED_CORE_SERIES = {
    "fed_funds_rate": "FEDFUNDS",
}

FRED_CPI_CORE_SERIES = "CPILFESL"

_SEED_PATH = Path(__file__).resolve().parent.parent / "data" / "macro_fred_seed.json"

FRED_EXTENDED_SERIES = {
    "treasury_10y": "DGS10",
    "unemployment": "UNRATE",
    "m2_supply": "M2SL",
}


async def fetch_macro_snapshot(*, include_extended: bool = True) -> dict:
    """Fetch FRED indicators; core fields always attempted."""
    return await asyncio.to_thread(_sync_fetch_all, include_extended)


def _fred_csv_url(series_id: str, cosd: str) -> str:
    return f"{FRED_BASE}?{urlencode({'id': series_id, 'cosd': cosd})}"


def _fetch_csv_text(series_id: str, cosd: str) -> str:
    """Fetch FRED CSV — urllib first (reliable), curl fallback on container edge cases."""
    from urllib.error import URLError
    from urllib.request import Request, urlopen

    url = _fred_csv_url(series_id, cosd)
    try:
        req = Request(url, headers={"User-Agent": "TradeTalk/1.0 (macro-dashboard)"})
        with urlopen(req, timeout=_FRED_CURL_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8")
    except (URLError, TimeoutError, OSError) as urllib_err:
        logger.warning("[FREDConnector] urllib failed for %s: %s — trying curl", series_id, urllib_err)
        proc = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--http1.1",
                "-m",
                str(_FRED_CURL_TIMEOUT_S),
                "-A",
                "TradeTalk/1.0 (macro-dashboard)",
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            err = (proc.stderr or proc.stdout or str(urllib_err)).strip()
            raise RuntimeError(err) from urllib_err
        return proc.stdout


def _parse_series_values(text: str) -> list[float]:
    values: list[float] = []
    for line in text.strip().split("\n")[1:]:
        parts = line.strip().split(",")
        if len(parts) == 2 and parts[1] not in (".", ""):
            try:
                values.append(float(parts[1]))
            except ValueError:
                pass
    return values


def _fetch_series_latest(series_id: str, *, lookback_days: int = 120) -> Optional[float]:
    cosd = (date.today() - timedelta(days=lookback_days)).isoformat()
    text = _fetch_csv_text(series_id, cosd)
    lines = text.strip().split("\n")
    for line in reversed(lines[1:]):
        parts = line.strip().split(",")
        if len(parts) == 2 and parts[1] not in (".", ""):
            try:
                return round(float(parts[1]), 4)
            except ValueError:
                continue
    return None


def _compute_core_cpi_yoy() -> Optional[float]:
    """YoY % change from Core CPI index (CPILFESL)."""
    cosd = (date.today() - timedelta(days=400)).isoformat()
    values = _parse_series_values(_fetch_csv_text(FRED_CPI_CORE_SERIES, cosd))
    if len(values) >= 13:
        latest = values[-1]
        year_ago = values[-13]
        if year_ago:
            return round((latest / year_ago - 1) * 100, 2)
    return None


def _sync_fetch_all(include_extended: bool = True) -> dict:
    snapshot: Dict[str, Any] = {
        "fed_funds_rate": None,
        "cpi_yoy": None,
        "treasury_10y": None,
        "unemployment": None,
        "m2_supply": None,
    }

    def _fed() -> Optional[float]:
        return _fetch_series_latest(FRED_CORE_SERIES["fed_funds_rate"])

    def _cpi() -> Optional[float]:
        return _compute_core_cpi_yoy()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fed_future = pool.submit(_fed)
        cpi_future = pool.submit(_cpi)
        try:
            snapshot["fed_funds_rate"] = fed_future.result(timeout=_FRED_CURL_TIMEOUT_S + 5)
        except Exception as exc:
            logger.warning("[FREDConnector] Fed funds failed: %s", exc)
        try:
            snapshot["cpi_yoy"] = cpi_future.result(timeout=_FRED_CURL_TIMEOUT_S + 5)
        except Exception as exc:
            logger.warning("[FREDConnector] Core CPI YoY failed: %s", exc)

    if include_extended:
        with ThreadPoolExecutor(max_workers=len(FRED_EXTENDED_SERIES)) as pool:
            futures = {
                pool.submit(_fetch_series_latest, series_id): key
                for key, series_id in FRED_EXTENDED_SERIES.items()
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    snapshot[key] = future.result(timeout=_FRED_CURL_TIMEOUT_S + 5)
                except Exception as exc:
                    logger.warning("[FREDConnector] Failed for %s: %s", key, exc)
                    snapshot[key] = None

    snapshot["fetched_at"] = datetime.now(timezone.utc).isoformat()
    snapshot["source"] = "fred.stlouisfed.org"

    if snapshot.get("fed_funds_rate") is not None or snapshot.get("cpi_yoy") is not None:
        _save_fred_cache(snapshot)
    else:
        cached = _load_fred_cache()
        if cached:
            for key in ("fed_funds_rate", "cpi_yoy", "unemployment", "treasury_10y", "m2_supply", "fetched_at"):
                if snapshot.get(key) is None and cached.get(key) is not None:
                    snapshot[key] = cached[key]
            snapshot["degraded"] = True
        else:
            seed = _load_fred_seed()
            if seed:
                for key in ("fed_funds_rate", "cpi_yoy", "fetched_at", "source"):
                    if seed.get(key) is not None:
                        snapshot[key] = seed[key]
                snapshot["degraded"] = True

    return snapshot


_CACHE: Dict[str, Any] = {}


def _save_fred_cache(snapshot: Dict[str, Any]) -> None:
    global _CACHE
    _CACHE = {
        "fed_funds_rate": snapshot.get("fed_funds_rate"),
        "cpi_yoy": snapshot.get("cpi_yoy"),
        "unemployment": snapshot.get("unemployment"),
        "treasury_10y": snapshot.get("treasury_10y"),
        "m2_supply": snapshot.get("m2_supply"),
        "fetched_at": snapshot.get("fetched_at"),
    }


def _load_fred_cache() -> Optional[Dict[str, Any]]:
    return _CACHE or None


def _load_fred_seed() -> Optional[Dict[str, Any]]:
    try:
        with _SEED_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)
    except Exception as exc:
        logger.warning("[FREDConnector] seed load failed: %s", exc)
        return None
