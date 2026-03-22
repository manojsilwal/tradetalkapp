"""
Verify Parquet coverage vs config.ALL_TICKERS; optionally clear checkpoints and
re-fetch missing symbols (prices, fundamentals, events).

Usage:
  python -m backend.data_lake.fill_gaps --dry-run
  python -m backend.data_lake.fill_gaps
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import checkpoint
from . import config
from .ingest_prices import run as run_prices
from .ingest_fundamentals import run as run_fundamentals
from .ingest_events import run as run_events

logger = logging.getLogger(__name__)


def scan_missing() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"prices": [], "fundamentals": [], "events": []}
    for t in config.ALL_TICKERS:
        if not os.path.isfile(os.path.join(config.PRICES_DIR, f"{t}.parquet")):
            out["prices"].append(t)
        if not os.path.isfile(os.path.join(config.FUNDAMENTALS_DIR, f"{t}.parquet")):
            out["fundamentals"].append(t)
        has_flat = any(
            os.path.isfile(os.path.join(config.EVENTS_DIR, f"{t}_{k}.parquet"))
            for k in ("earnings", "insider", "recommendations")
        )
        ed = os.path.join(config.EVENTS_DIR, t)
        has_legacy = os.path.isdir(ed) and any(
            f.endswith(".parquet")
            for f in os.listdir(ed)
            if os.path.isfile(os.path.join(ed, f))
        )
        if not has_flat and not has_legacy:
            out["events"].append(t)
    return out


def clear_checkpoint_for_tickers(tickers: set[str]) -> None:
    path = config.CHECKPOINT_FILE
    if not os.path.exists(path):
        return
    with open(path) as f:
        data = json.load(f)
    phases = [
        "prices",
        "fundamentals",
        "events",
        "summarize_profiles",
        "summarize_earnings",
        "summarize_patterns",
    ]
    for ph in phases:
        if ph not in data:
            continue
        data[ph] = [x for x in data[ph] if x not in tickers]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("Cleared %d tickers from checkpoint phases for refetch", len(tickers))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Fill missing data lake Parquet files")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-fetch", action="store_true", help="Only report gaps, do not run ingesters")
    args = parser.parse_args()

    missing = scan_missing()
    union = set(missing["prices"]) | set(missing["fundamentals"]) | set(missing["events"])

    print(f"Canonical tickers: {len(config.ALL_TICKERS)}")
    print(f"Missing prices:    {len(missing['prices'])}")
    print(f"Missing fund:      {len(missing['fundamentals'])}")
    print(f"Missing events:    {len(missing['events'])}")
    print(f"Union (refetch):   {len(union)}")

    if missing["prices"]:
        print("  prices:", ",".join(sorted(missing["prices"])))
    if missing["fundamentals"] and len(missing["fundamentals"]) <= 50:
        print("  fund:", ",".join(sorted(missing["fundamentals"])))
    if missing["events"] and len(missing["events"]) <= 50:
        print("  events:", ",".join(sorted(missing["events"])))

    if not union:
        print("Nothing missing.")
        return

    if args.dry_run or args.no_fetch:
        print("Dry run / --no-fetch: not modifying checkpoint or fetching.")
        return

    if not args.dry_run:
        clear_checkpoint_for_tickers(union)
        tickers = sorted(union)
        cli = ",".join(tickers)
        logger.info("Re-fetching %d tickers...", len(tickers))
        run_prices(tickers, dry_run=False)
        run_fundamentals(tickers, dry_run=False)
        run_events(tickers, dry_run=False)
        print("Done. Re-run summarize + upload if needed:")
        print(f"  python -m backend.data_lake.summarize_for_rag --tickers {cli}")
        print("  python -m backend.data_lake.upload_to_supabase")


if __name__ == "__main__":
    main()
