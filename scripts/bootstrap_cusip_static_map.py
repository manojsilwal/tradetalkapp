#!/usr/bin/env python3
"""Bootstrap a bundled CUSIP -> ticker map via OpenFIGI.

Collects unique CUSIPs from the fund-leaderboard holdings store (Postgres in
prod, SQLite locally), resolves them through OpenFIGI in rate-limited batches,
enriches sector/company from the bundled ticker reference, and writes:

    backend/data/cusip_ticker_static.json
        { "037833100": {"ticker": "AAPL", "name": "APPLE INC", "sector": "Technology"}, ... }

Idempotent: CUSIPs already present in the output (with a ticker) are skipped, so
re-running only resolves new misses. Requires ``OPENFIGI_API_KEY`` for the
higher rate limit; works keyless but slowly.

Usage:
    OPENFIGI_API_KEY=... python scripts/bootstrap_cusip_static_map.py
    # Resolve CUSIPs from a newline/comma file instead of the DB:
    python scripts/bootstrap_cusip_static_map.py --cusip-file cusips.txt
    # Re-resolve everything, ignoring existing entries:
    python scripts/bootstrap_cusip_static_map.py --force
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

OUT_PATH = REPO_ROOT / "backend" / "data" / "cusip_ticker_static.json"


def _collect_cusips_from_store() -> list[str]:
    """Pull distinct non-empty CUSIPs from the holdings table (either backend)."""
    from backend import fund_leaderboard_store as store

    store.init_schema()
    cusips: set[str] = set()
    with store._cursor() as (_c, cur):
        cur.execute("SELECT DISTINCT cusip FROM thirteen_f_holdings WHERE cusip IS NOT NULL AND cusip != ''")
        for row in cur.fetchall():
            val = (store._row_to_dict(row).get("cusip") or "").strip()
            if val:
                cusips.add(val)
    return sorted(cusips)


def _collect_cusips_from_file(path: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8")
    raw = text.replace(",", "\n").split("\n")
    return sorted({c.strip() for c in raw if c.strip()})


def _load_existing() -> dict:
    if OUT_PATH.exists():
        try:
            return json.loads(OUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


async def _run(args: argparse.Namespace) -> int:
    from backend.coral_skills.security_mapper import _openfigi_resolve
    from backend import ticker_reference

    if args.cusip_file:
        all_cusips = _collect_cusips_from_file(args.cusip_file)
        print(f"Collected {len(all_cusips):,} CUSIPs from {args.cusip_file}")
    else:
        all_cusips = _collect_cusips_from_store()
        print(f"Collected {len(all_cusips):,} distinct CUSIPs from holdings store")

    existing = {} if args.force else _load_existing()
    resolved_ok = {c for c, v in existing.items() if isinstance(v, dict) and v.get("ticker")}
    misses = [c for c in all_cusips if c not in resolved_ok]
    print(f"{len(resolved_ok):,} already resolved; {len(misses):,} to resolve")

    if not misses:
        print("Nothing to do.")
        return 0

    out = dict(existing)
    batch = args.batch
    new_hits = 0
    for start in range(0, len(misses), batch):
        chunk = misses[start:start + batch]
        results = await _openfigi_resolve(chunk)
        for cusip in chunk:
            hit = results.get(cusip)
            if hit and hit.get("ticker"):
                ticker = ticker_reference.normalize_ticker(hit["ticker"])
                meta = ticker_reference.get_ticker_meta(ticker) or {}
                out[cusip] = {
                    "ticker": ticker,
                    "name": hit.get("name") or meta.get("company_name") or "",
                    "sector": meta.get("sector") or None,
                }
                new_hits += 1
            else:
                out.setdefault(cusip, {"ticker": None, "name": "", "sector": None})
        # Periodic checkpoint so a long run is resumable.
        OUT_PATH.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
        print(f"  resolved {min(start + batch, len(misses)):,}/{len(misses):,} "
              f"(+{new_hits} tickers so far)", flush=True)

    mapped = sum(1 for v in out.values() if isinstance(v, dict) and v.get("ticker"))
    OUT_PATH.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    print(f"Done. {mapped:,}/{len(out):,} CUSIPs mapped -> {OUT_PATH}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cusip-file", help="Resolve CUSIPs from a file instead of the DB")
    ap.add_argument("--force", action="store_true", help="Re-resolve all, ignore existing entries")
    ap.add_argument("--batch", type=int, default=500, help="CUSIPs per OpenFIGI flush (checkpoint cadence)")
    args = ap.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
