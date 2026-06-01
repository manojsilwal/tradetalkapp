"""
Convert local event parquets + recent news into events_curated (BigQuery).

Scope: all yfinance event files under data_lake_output/events/ — one row per
distinct event with published_at timestamp and affected_symbols.

Usage:
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.ingest_daily_events
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.ingest_daily_events --dry-run
    MCP_DATA_BACKEND=bigquery PYTHONPATH=. python -m backend.data_lake.ingest_daily_events --recent-news-days 90
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import logging
import os
import re
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

import pandas as pd

from . import config

logger = logging.getLogger(__name__)

KIND_CATEGORY = {
    "earnings": "earnings",
    "splits": "corporate_action",
    "dividends": "corporate_action",
    "insider": "insider_trade",
    "recommendations": "news",
    "institutional": "news",
    "major_holders": "news",
}


def _event_id(category: str, published_at: str, headline: str, symbol: str = "") -> str:
    raw = f"{category}|{published_at}|{headline}|{symbol}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _headline_hash(headline: str) -> str:
    return hashlib.md5((headline or "").encode()).hexdigest()[:16]


def _parse_ticker_from_filename(path: str) -> tuple[str, str]:
    """Parse AAPL_earnings.parquet -> (AAPL, earnings)."""
    base = os.path.basename(path).replace(".parquet", "")
    parts = base.rsplit("_", 1)
    if len(parts) == 2:
        return parts[0].upper(), parts[1]
    return base.upper(), "unknown"


def _to_ts(val) -> str:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return datetime.now(timezone.utc).isoformat()
    if isinstance(val, pd.Timestamp):
        dt = val.to_pydatetime()
    elif isinstance(val, datetime):
        dt = val
    else:
        try:
            dt = pd.to_datetime(val).to_pydatetime()
        except Exception:
            return datetime.now(timezone.utc).isoformat()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _earnings_events(ticker: str, df: pd.DataFrame) -> List[Dict]:
    events = []
    if df.empty:
        return events
    idx_col = df.index.name or "Date"
    for idx, row in df.iterrows():
        pub = _to_ts(idx if idx_col else row.get("date", idx))
        eps_est = row.get("EPS Estimate") or row.get("eps_estimate")
        eps_act = row.get("Reported EPS") or row.get("reported_eps") or row.get("Earnings")
        rev_est = row.get("Revenue Estimate") or row.get("revenue_estimate")
        rev_act = row.get("Reported Revenue") or row.get("reported_revenue") or row.get("Revenue")
        parts = [f"{ticker} earnings"]
        if eps_act is not None and not pd.isna(eps_act):
            parts.append(f"EPS {eps_act}")
            if eps_est is not None and not pd.isna(eps_est):
                parts.append(f"vs est {eps_est}")
        if rev_act is not None and not pd.isna(rev_act):
            parts.append(f"Revenue {rev_act}")
        headline = " — ".join(str(p) for p in parts)
        events.append({
            "event_id": _event_id("earnings", pub, headline, ticker),
            "published_at": pub,
            "category": "earnings",
            "source": "yfinance",
            "headline": headline[:1000],
            "body_text": str(dict(row))[:5000],
            "affected_symbols": [ticker],
            "dedupe_cluster_id": _headline_hash(headline),
        })
    return events


def _insider_events(ticker: str, df: pd.DataFrame) -> List[Dict]:
    events = []
    if df.empty:
        return events
    date_col = next((c for c in df.columns if str(c).lower() in ("date", "start_date")), None)
    for _, row in df.iterrows():
        pub = _to_ts(row.get(date_col) if date_col else row.iloc[0])
        insider = row.get("insider_name", row.get("Insider", "insider"))
        tx = row.get("transaction_type", row.get("Transaction", "trade"))
        shares = row.get("shares", row.get("Shares", ""))
        headline = f"{ticker} insider {tx}: {insider} ({shares} shares)"
        events.append({
            "event_id": _event_id("insider_trade", pub, headline, ticker),
            "published_at": pub,
            "category": "insider_trade",
            "source": "yfinance",
            "headline": headline[:1000],
            "body_text": str(dict(row))[:3000],
            "affected_symbols": [ticker],
            "dedupe_cluster_id": _headline_hash(headline),
        })
    return events


def _generic_dated_events(ticker: str, df: pd.DataFrame, category: str, label: str) -> List[Dict]:
    events = []
    if df.empty:
        return events
    for idx, row in df.iterrows():
        pub = _to_ts(idx if not isinstance(idx, (int, float)) else row.iloc[0])
        headline = f"{ticker} {label} — {pub[:10]}"
        events.append({
            "event_id": _event_id(category, pub, headline, ticker),
            "published_at": pub,
            "category": category,
            "source": "yfinance",
            "headline": headline[:1000],
            "body_text": str(dict(row))[:3000],
            "affected_symbols": [ticker],
            "dedupe_cluster_id": _headline_hash(headline),
        })
    return events


def _parquet_to_events(path: str) -> List[Dict]:
    ticker, kind = _parse_ticker_from_filename(path)
    category = KIND_CATEGORY.get(kind, "news")
    try:
        df = pd.read_parquet(path)
    except Exception as e:
        logger.debug("Skip %s: %s", path, e)
        return []

    if kind == "earnings":
        return _earnings_events(ticker, df)
    if kind == "insider":
        return _insider_events(ticker, df)
    if kind in ("splits", "dividends"):
        return _generic_dated_events(ticker, df, category, kind)
    if kind == "recommendations":
        return _recommendation_events(ticker, df)
    return _generic_dated_events(ticker, df, category, kind)


def _recommendation_events(ticker: str, df: pd.DataFrame) -> List[Dict]:
    events = []
    if df.empty:
        return events
    date_col = next((c for c in df.columns if "date" in str(c).lower()), None)
    for _, row in df.iterrows():
        pub = _to_ts(row.get(date_col) if date_col else row.iloc[0])
        firm = row.get("Firm") or row.get("firm") or "Analyst"
        grade = row.get("ToGrade") or row.get("to_grade") or row.get("grade") or ""
        action = row.get("Action") or row.get("action") or ""
        headline = f"{ticker} analyst {action} {grade} — {firm}".strip()
        events.append({
            "event_id": _event_id("news", pub, headline, ticker),
            "published_at": pub,
            "category": "news",
            "source": "yfinance",
            "headline": headline[:1000],
            "body_text": str(dict(row))[:3000],
            "affected_symbols": [ticker],
            "dedupe_cluster_id": _headline_hash(headline),
        })
    return events


def _fetch_news_for_date_range(
    tickers: List[str],
    start_date: date,
    end_date: date,
    throttle_sec: float = 0.08,
) -> List[Dict]:
    """Fetch yfinance news for tickers published within [start_date, end_date]."""
    events: List[Dict] = []
    if not tickers:
        return events

    try:
        import yfinance as yf
        import time
    except ImportError:
        return events

    start_ts = pd.Timestamp(start_date, tz=timezone.utc)
    end_ts = pd.Timestamp(end_date, tz=timezone.utc) + pd.Timedelta(days=1)

    for ticker in tickers:
        try:
            news = yf.Ticker(config.yfinance_symbol(ticker)).news or []
        except Exception:
            continue
        for item in news:
            pub_ms = item.get("providerPublishTime") or item.get("published_at")
            if not pub_ms:
                continue
            pub = datetime.fromtimestamp(int(pub_ms), tz=timezone.utc).isoformat()
            pub_ts = pd.Timestamp(pub)
            if pub_ts < start_ts or pub_ts >= end_ts:
                continue
            headline = item.get("title") or item.get("headline") or f"{ticker} news"
            events.append({
                "event_id": _event_id("news", pub, headline, ticker),
                "published_at": pub,
                "category": "news",
                "source": "yfinance_news",
                "headline": headline[:1000],
                "body_text": (item.get("summary") or "")[:5000],
                "affected_symbols": [ticker],
                "dedupe_cluster_id": _headline_hash(headline),
            })
        if throttle_sec > 0:
            time.sleep(throttle_sec)
    return events


def _fetch_recent_news(tickers: List[str], days: int = 90) -> List[Dict]:
    """Best-effort live news via FinCrawler / yfinance for recent dates."""
    events: List[Dict] = []
    if days <= 0:
        return events

    try:
        import yfinance as yf
    except ImportError:
        return events

    cutoff = pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(days=days)
    for ticker in tickers[:50]:  # cap to avoid rate limits in job
        try:
            news = yf.Ticker(config.yfinance_symbol(ticker)).news or []
        except Exception:
            continue
        for item in news:
            pub_ms = item.get("providerPublishTime") or item.get("published_at")
            if not pub_ms:
                continue
            pub = datetime.fromtimestamp(int(pub_ms), tz=timezone.utc).isoformat()
            if pd.Timestamp(pub) < cutoff:
                continue
            headline = item.get("title") or item.get("headline") or f"{ticker} news"
            events.append({
                "event_id": _event_id("news", pub, headline, ticker),
                "published_at": pub,
                "category": "news",
                "source": "yfinance_news",
                "headline": headline[:1000],
                "body_text": (item.get("summary") or "")[:5000],
                "affected_symbols": [ticker],
                "dedupe_cluster_id": _headline_hash(headline),
            })
    return events


def _fetch_sec_8k_events(tickers: List[str], limit_per_ticker: int = 40) -> List[Dict]:
    """Best-effort SEC 8-K filings from EDGAR submissions API."""
    events: List[Dict] = []
    try:
        from backend.connectors.backtest_data import _edgar_get, _ticker_to_cik
    except ImportError:
        return events

    for ticker in tickers:
        cik = _ticker_to_cik(ticker)
        if not cik:
            continue
        try:
            resp = _edgar_get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=30)
            resp.raise_for_status()
            recent = resp.json().get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            dates = recent.get("filingDate", [])
            descriptions = recent.get("primaryDocument", [])
            count = 0
            for form, fdate, doc in zip(forms, dates, descriptions):
                if form != "8-K":
                    continue
                pub = f"{fdate}T21:00:00+00:00"
                headline = f"{ticker} SEC 8-K — {fdate}"
                body = f"Form 8-K filed {fdate}; document={doc}"
                events.append({
                    "event_id": _event_id("sec_filing", pub, headline, ticker),
                    "published_at": pub,
                    "category": "sec_filing",
                    "source": "sec_edgar",
                    "headline": headline[:1000],
                    "body_text": body[:5000],
                    "affected_symbols": [ticker],
                    "dedupe_cluster_id": _headline_hash(headline),
                })
                count += 1
                if count >= limit_per_ticker:
                    break
        except Exception as e:
            logger.debug("SEC 8-K skip %s: %s", ticker, e)
    return events


def collect_incremental_events(
    start_date: date,
    end_date: date,
    tickers: List[str],
) -> List[Dict]:
    """News + SEC 8-K for a date window (append-only daily job)."""
    logger.info(
        "[DailyEvents] Incremental window %s → %s for %d tickers",
        start_date,
        end_date,
        len(tickers),
    )
    events = _fetch_news_for_date_range(tickers, start_date, end_date)
    sec_events = _fetch_sec_8k_events(tickers)
    seen = {e["event_id"] for e in events}
    start_ts = pd.Timestamp(start_date, tz=timezone.utc)
    end_ts = pd.Timestamp(end_date, tz=timezone.utc) + pd.Timedelta(days=1)
    for evt in sec_events:
        if evt["event_id"] in seen:
            continue
        pub_ts = pd.Timestamp(evt["published_at"])
        if start_ts <= pub_ts < end_ts:
            events.append(evt)
            seen.add(evt["event_id"])
    logger.info("[DailyEvents] Incremental collected %d events", len(events))
    return events


def collect_all_events(
    events_dir: Optional[str] = None,
    recent_news_days: int = 0,
    tickers: Optional[List[str]] = None,
    sec_filings: bool = True,
) -> List[Dict]:
    events_dir = events_dir or config.EVENTS_DIR
    pattern = os.path.join(events_dir, "*.parquet")
    paths = glob.glob(pattern)
    logger.info("[DailyEvents] Scanning %d parquet files in %s", len(paths), events_dir)

    all_events: List[Dict] = []
    seen_ids: set = set()

    for path in paths:
        for evt in _parquet_to_events(path):
            if evt["event_id"] not in seen_ids:
                seen_ids.add(evt["event_id"])
                all_events.append(evt)

    if recent_news_days > 0:
        tlist = tickers or config.ALL_TICKERS[:100]
        news = _fetch_recent_news(tlist, days=recent_news_days)
        for evt in news:
            if evt["event_id"] not in seen_ids:
                seen_ids.add(evt["event_id"])
                all_events.append(evt)

    if sec_filings:
        tlist = tickers or config.ALL_TICKERS[:100]
        sec_events = _fetch_sec_8k_events(tlist)
        for evt in sec_events:
            if evt["event_id"] not in seen_ids:
                seen_ids.add(evt["event_id"])
                all_events.append(evt)

    logger.info("[DailyEvents] Collected %d unique events", len(all_events))
    return all_events


def run(
    dry_run: bool = False,
    batch_size: int = 500,
    recent_news_days: int = 0,
) -> dict:
    events = collect_all_events(recent_news_days=recent_news_days)
    if dry_run:
        return {"events": len(events), "dry_run": True}

    from backend.mcp_server.persist import persist_events_curated

    total = 0
    for i in range(0, len(events), batch_size):
        batch = events[i : i + batch_size]
        total += persist_events_curated(batch)
        logger.info("[DailyEvents] Wrote batch %d–%d (%d rows)", i, i + len(batch), len(batch))

    return {"events": len(events), "written": total}


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Ingest daily events to events_curated")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--recent-news-days", type=int, default=0,
                        help="Fetch yfinance news for last N days (0=skip)")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run, batch_size=args.batch_size,
                 recent_news_days=args.recent_news_days)
    print(result)


if __name__ == "__main__":
    main()
