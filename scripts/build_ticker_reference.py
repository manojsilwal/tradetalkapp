#!/usr/bin/env python3
"""Build the bundled ticker reference from a wide ticker CSV.

Input CSV columns (header required):
    ticker, company_name, sector, priority_score, cache_tier, avg_volume, views_30d

Outputs two files under backend/data/:
    ticker_reference.json    — {TICKER: {company_name, sector, priority_score, ...}}
    ticker_name_index.json   — {normalized_company_name: TICKER}  (for issuer lookup)

The name index keeps the highest-priority_score ticker on collisions so the most
liquid / most-viewed issuer wins for ambiguous names.

Usage:
    python scripts/build_ticker_reference.py path/to/tickers.csv
    cat tickers.csv | python scripts/build_ticker_reference.py -
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "backend" / "data"
REF_PATH = DATA_DIR / "ticker_reference.json"
INDEX_PATH = DATA_DIR / "ticker_name_index.json"


def _load_normalizers():
    """Load ticker_reference.py directly so the build-time index matches runtime
    lookups, without importing the heavy ``backend`` package (__init__ pulls in
    pydantic/yfinance which the build environment may lack)."""
    spec = importlib.util.spec_from_file_location(
        "_ticker_reference_mod", REPO_ROOT / "backend" / "ticker_reference.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.normalize_issuer_name, mod.normalize_ticker


normalize_issuer_name, normalize_ticker = _load_normalizers()


def _to_float(val: str) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def build(rows: list[dict]) -> tuple[dict, dict]:
    reference: dict[str, dict] = {}
    name_index: dict[str, str] = {}
    # Track the priority_score behind each name-index entry to resolve collisions.
    index_score: dict[str, float] = {}

    for row in rows:
        ticker = normalize_ticker(row.get("ticker") or "")
        company = (row.get("company_name") or "").strip()
        if not ticker or not company:
            continue

        priority = _to_float(row.get("priority_score"))
        reference[ticker] = {
            "company_name": company,
            "sector": (row.get("sector") or "").strip() or "Unknown",
            "priority_score": priority,
            "cache_tier": (row.get("cache_tier") or "").strip() or None,
            "avg_volume": _to_float(row.get("avg_volume")),
            "views_30d": _to_float(row.get("views_30d")),
        }

        norm = normalize_issuer_name(company)
        if not norm:
            continue
        score = priority if priority is not None else 0.0
        if norm not in name_index or score > index_score.get(norm, -1.0):
            name_index[norm] = ticker
            index_score[norm] = score

    return reference, name_index


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    src = sys.argv[1]
    if src == "-":
        text = sys.stdin.read()
    else:
        text = Path(src).read_text(encoding="utf-8")

    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)

    reference, name_index = build(rows)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REF_PATH.write_text(json.dumps(reference, indent=1, sort_keys=True), encoding="utf-8")
    INDEX_PATH.write_text(json.dumps(name_index, indent=1, sort_keys=True), encoding="utf-8")

    print(f"tickers:    {len(reference):,} -> {REF_PATH}")
    print(f"name index: {len(name_index):,} -> {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
