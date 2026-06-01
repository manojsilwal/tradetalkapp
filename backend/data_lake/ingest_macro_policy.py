"""
Ingest macro/policy release calendar into BigQuery macro_policy_events.

Sources:
  - FOMC meeting dates (Federal Reserve calendar)
  - CPI release dates (~12th of month, 8:30 ET)
  - NFP release dates (first Friday of month, 8:30 ET)
  - GDP advance release dates (last week of month following quarter end)

Usage:
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.ingest_macro_policy
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.ingest_macro_policy --dry-run
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

# FOMC statement release dates (approximate — 2pm ET on meeting day)
FOMC_DATES = [
    "2011-01-26", "2011-03-15", "2011-04-27", "2011-06-22", "2011-08-09",
    "2011-09-21", "2011-11-02", "2011-12-13",
    "2012-01-25", "2012-03-13", "2012-04-25", "2012-06-20", "2012-08-01",
    "2012-09-13", "2012-10-24", "2012-12-12",
    "2013-01-30", "2013-03-20", "2013-05-01", "2013-06-19", "2013-07-31",
    "2013-09-18", "2013-10-30", "2013-12-18",
    "2014-01-29", "2014-03-19", "2014-04-30", "2014-06-18", "2014-07-30",
    "2014-09-17", "2014-10-29", "2014-12-17",
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17", "2015-07-29",
    "2015-09-17", "2015-10-28", "2015-12-16",
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15", "2016-07-27",
    "2016-09-21", "2016-11-02", "2016-12-14",
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14", "2017-07-26",
    "2017-09-20", "2017-11-01", "2017-12-13",
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13", "2018-08-01",
    "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19", "2019-07-31",
    "2019-09-18", "2019-10-30", "2019-12-11",
    "2020-01-29", "2020-03-03", "2020-03-15", "2020-04-29", "2020-06-10",
    "2020-07-29", "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28",
    "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27",
    "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26",
    "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31",
    "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30",
    "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
    "2026-09-16", "2026-11-04", "2026-12-16",
]


def _event_id(category: str, published_at: str, headline: str) -> str:
    raw = f"{category}|{published_at}|{headline}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _first_friday(year: int, month: int) -> date:
    """First Friday of month (typical NFP release)."""
    d = date(year, month, 1)
    while d.weekday() != 4:
        d += timedelta(days=1)
    return d


def _cpi_release_date(year: int, month: int) -> date:
    """CPI typically released ~12th of month (8:30 ET)."""
    day = min(13, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _nfp_release_dates(start: date, end: date) -> List[date]:
    out: List[date] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = _first_friday(y, m)
        if start <= d <= end:
            out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _cpi_release_dates(start: date, end: date) -> List[date]:
    out: List[date] = []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        d = _cpi_release_date(y, m)
        if start <= d <= end:
            out.append(d)
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _ts_et(d: date, hour: int = 8, minute: int = 30) -> str:
    """Release timestamp as UTC ISO (8:30 ET ≈ 13:30 UTC during EST)."""
    dt = datetime(d.year, d.month, d.day, hour + 5, minute, tzinfo=timezone.utc)
    return dt.isoformat()


def _fomc_ts(d: date) -> str:
    """FOMC statement ~2pm ET = 19:00 UTC (EST) / 18:00 UTC (EDT)."""
    dt = datetime(d.year, d.month, d.day, 19, 0, tzinfo=timezone.utc)
    return dt.isoformat()


def build_macro_events(start: str = "2011-01-01", end: str | None = None) -> List[Dict]:
    """Build macro_policy_events rows for the date range."""
    end_d = date.fromisoformat(end) if end else date.today()
    start_d = date.fromisoformat(start)
    events: List[Dict] = []

    for ds in FOMC_DATES:
        d = date.fromisoformat(ds)
        if start_d <= d <= end_d:
            pub = _fomc_ts(d)
            headline = f"FOMC policy statement — {ds}"
            events.append({
                "event_id": _event_id("fed_decision", pub, headline),
                "published_at": pub,
                "category": "fed_decision",
                "headline": headline,
                "body_text": "Federal Open Market Committee monetary policy decision and statement.",
                "affected_symbols": [],
                "source": "fred_calendar",
            })

    for d in _nfp_release_dates(start_d, end_d):
        pub = _ts_et(d, 8, 30)
        headline = f"Nonfarm Payrolls (NFP) — {d.isoformat()}"
        events.append({
            "event_id": _event_id("macro_data", pub, headline),
            "published_at": pub,
            "category": "macro_data",
            "headline": headline,
            "body_text": "BLS Employment Situation report (Nonfarm Payrolls).",
            "affected_symbols": [],
            "source": "fred_calendar",
        })

    for d in _cpi_release_dates(start_d, end_d):
        pub = _ts_et(d, 8, 30)
        headline = f"CPI release — {d.isoformat()}"
        events.append({
            "event_id": _event_id("macro_data", pub, headline),
            "published_at": pub,
            "category": "macro_data",
            "headline": headline,
            "body_text": "BLS Consumer Price Index release.",
            "affected_symbols": [],
            "source": "fred_calendar",
        })

    return events


def run(dry_run: bool = False, start: str = "2011-01-01", end: str | None = None) -> dict:
    events = build_macro_events(start=start, end=end)
    logger.info("[MacroPolicy] Built %d macro/policy events", len(events))

    if dry_run:
        return {"events": len(events), "dry_run": True}

    from backend.mcp_server.persist import persist_macro_policy_events
    written = persist_macro_policy_events(events)
    logger.info("[MacroPolicy] Persisted %d rows to macro_policy_events", written)
    return {"events": len(events), "written": written}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Ingest macro/policy events to BigQuery")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start", default="2011-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    result = run(dry_run=args.dry_run, start=args.start, end=args.end)
    print(result)


if __name__ == "__main__":
    main()
