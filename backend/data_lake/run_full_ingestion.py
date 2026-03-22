"""
Orchestrator — runs all data lake ingestion phases in sequence.

Usage:
    python -m backend.data_lake.run_full_ingestion --dry-run
    python -m backend.data_lake.run_full_ingestion --tickers AAPL,MSFT
    python -m backend.data_lake.run_full_ingestion --phase prices      # single phase
    python -m backend.data_lake.run_full_ingestion                      # full run
"""
import argparse
import json
import logging
import time

from . import config
from . import checkpoint
from .ingest_prices import run as run_prices
from .ingest_fundamentals import run as run_fundamentals
from .ingest_events import run as run_events
from .ingest_macro import run as run_macro
from .summarize_for_rag import run as run_summaries
from .upload_to_supabase import run as run_upload

logger = logging.getLogger(__name__)

PHASES = {
    "prices": run_prices,
    "fundamentals": run_fundamentals,
    "events": run_events,
    "macro": None,
    "summarize": None,
    "upload": None,
}


def run_all(
    tickers: list[str],
    dry_run: bool = False,
    phase: str | None = None,
    summarize_use_llm: bool = True,
) -> dict:
    """Execute all ingestion phases. Returns combined results."""
    config.ensure_dirs()
    results = {}
    start = time.time()

    phases_to_run = [phase] if phase else ["prices", "fundamentals", "events", "macro", "summarize", "upload"]

    for p in phases_to_run:
        logger.info("=" * 60)
        logger.info("PHASE: %s", p.upper())
        logger.info("=" * 60)

        try:
            if p in ("prices", "fundamentals", "events"):
                results[p] = PHASES[p](tickers, dry_run=dry_run)
            elif p == "macro":
                results[p] = run_macro(dry_run=dry_run)
            elif p == "summarize":
                results[p] = run_summaries(tickers, dry_run=dry_run)
            elif p == "upload":
                results[p] = run_upload(dry_run=dry_run)
            else:
                logger.warning("Unknown phase: %s", p)
        except Exception as e:
            logger.error("Phase %s failed: %s", p, e)
            results[p] = {"error": str(e)}

    elapsed = time.time() - start
    results["elapsed_seconds"] = round(elapsed, 1)
    results["checkpoint_stats"] = checkpoint.get_stats()

    logger.info("=" * 60)
    logger.info("ALL PHASES COMPLETE in %.1fs", elapsed)
    logger.info("Results: %s", json.dumps(results, indent=2, default=str))
    return results


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    parser = argparse.ArgumentParser(description="Run full data lake ingestion pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Preview what would happen without fetching data")
    parser.add_argument("--tickers", type=str, default=None, help="Comma-separated tickers (default: all S&P 500)")
    parser.add_argument("--phase", type=str, default=None,
                        choices=["prices", "fundamentals", "events", "macro", "summarize", "upload"],
                        help="Run only a specific phase")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Clear checkpoint before running")
    parser.add_argument("--status", action="store_true", help="Show checkpoint status and exit")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Summarize phase: template-only summaries (skip OpenRouter polish)",
    )
    args = parser.parse_args()

    if args.status:
        stats = checkpoint.get_stats()
        logger.info("Checkpoint status:")
        for phase, count in stats.items():
            logger.info("  %s: %d tickers done", phase, count)
        tickers = config.get_tickers(args.tickers, args.dry_run)
        logger.info("Total tickers: %d", len(tickers))
        return

    if args.reset_checkpoint:
        if args.phase:
            checkpoint.reset(args.phase)
            logger.info("Reset checkpoint for phase: %s", args.phase)
        else:
            checkpoint.reset()
            logger.info("Reset all checkpoints")

    tickers = config.get_tickers(args.tickers, args.dry_run)
    logger.info("Ticker count: %d | Dry run: %s | Phase: %s",
                len(tickers), args.dry_run, args.phase or "all")
    run_all(
        tickers,
        dry_run=args.dry_run,
        phase=args.phase,
        summarize_use_llm=not args.no_llm,
    )


if __name__ == "__main__":
    main()
