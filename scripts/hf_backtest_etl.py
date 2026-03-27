#!/usr/bin/env python3
"""
Daily (or manual) ETL: live Yahoo/SEC snapshot → Parquet layout → optional Hugging Face Dataset upload.

Run from repo root:
  PYTHONPATH=. python scripts/hf_backtest_etl.py --out ./dataset_staging
  PYTHONPATH=. python scripts/hf_backtest_etl.py --out ./dataset_staging --upload --repo-id user/dataset-name

Universe defaults to SP500_UNIVERSE ∪ MAG7_UNIVERSE ∪ SPY (same coverage as the backtest warehouse plan).
Override: --tickers AAPL,MSFT or env BACKTEST_ETL_TICKERS=comma,separated

Requires backend dependencies (yfinance, pandas, pyarrow, huggingface_hub).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Repo root on path
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _default_tickers() -> list[str]:
    raw = os.environ.get("BACKTEST_ETL_TICKERS")
    if raw:
        return sorted({t.strip().upper() for t in raw.split(",") if t.strip()})
    from backend.connectors.backtest_data import MAG7_UNIVERSE, SP500_UNIVERSE

    return sorted(set(SP500_UNIVERSE) | set(MAG7_UNIVERSE) | {"SPY"})


def write_parquet_layout(out: Path, data: dict[str, dict], tickers: list[str]) -> None:
    import pandas as pd

    by_upper = {k.upper(): v for k, v in data.items()}
    manifest_tickers: list[str] = []
    last_bar_dates: list[str] = []

    for t in tickers:
        u = t.upper()
        bundle = by_upper.get(u)
        if not bundle:
            continue
        manifest_tickers.append(u)

        prices = bundle.get("prices") or []
        if prices:
            last_bar_dates.append(max(p["date"] for p in prices))

        pdir = out / "prices" / f"symbol={u}"
        pdir.mkdir(parents=True, exist_ok=True)
        pdf = pd.DataFrame(prices) if prices else pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        pdf.to_parquet(pdir / "data.parquet", index=False)

        qdir = out / "quarterly_eps" / f"symbol={u}"
        qdir.mkdir(parents=True, exist_ok=True)
        qe = bundle.get("quarterly_eps") or []
        qdf = pd.DataFrame(qe) if qe else pd.DataFrame(columns=["date", "eps"])
        qdf.to_parquet(qdir / "data.parquet", index=False)

        annual = bundle.get("annual_financials") or {}
        rows = []
        for year, metrics in annual.items():
            ys = str(year)[:4]
            row = {"year": int(ys) if ys.isdigit() else year}
            row.update(metrics)
            rows.append(row)
        adir = out / "annual_financials" / f"symbol={u}"
        adir.mkdir(parents=True, exist_ok=True)
        adf = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["year", "total_revenue", "net_income"])
        adf.to_parquet(adir / "data.parquet", index=False)

        info = bundle.get("info") or {}
        idir = out / "info" / f"symbol={u}"
        idir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"info_json": json.dumps(info, default=str)}]).to_parquet(
            idir / "data.parquet", index=False
        )

    last_bar = max(last_bar_dates) if last_bar_dates else ""
    manifest = {
        "version": 1,
        "last_bar_date": last_bar,
        "etl_generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": sorted(set(manifest_tickers)),
        "n_tickers": len(set(manifest_tickers)),
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def upload_folder(local_dir: Path, repo_id: str, token: str | None) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.upload_folder(
        folder_path=str(local_dir),
        repo_id=repo_id,
        repo_type="dataset",
        ignore_patterns=[".git*", "*.md"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest data ETL → Parquet → HF Dataset")
    parser.add_argument("--out", type=Path, default=Path("dataset_staging"), help="Output directory")
    parser.add_argument("--start", default="2010-01-01", help="History start (ISO date)")
    parser.add_argument(
        "--end",
        default=None,
        help="History end (ISO date), default: today UTC",
    )
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers (overrides default universe)")
    parser.add_argument("--upload", action="store_true", help="Upload --out to Hugging Face")
    parser.add_argument("--repo-id", default=None, help="HF dataset repo (e.g. org/name)")
    args = parser.parse_args()

    end = args.end or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.tickers:
        tickers = sorted({t.strip().upper() for t in args.tickers.split(",") if t.strip()})
    else:
        tickers = _default_tickers()

    from backend.connectors.backtest_data import fetch_backtest_data_live

    print(f"[ETL] {len(tickers)} tickers {args.start} → {end}", flush=True)
    data = asyncio.run(fetch_backtest_data_live(tickers, args.start, end))

    out = args.out.resolve()
    write_parquet_layout(out, data, tickers)
    print(f"[ETL] wrote Parquet + manifest.json → {out}", flush=True)

    if args.upload:
        repo = args.repo_id or os.environ.get("HF_DATASET_REPO")
        if not repo:
            print("[ETL] --upload requires --repo-id or HF_DATASET_REPO", file=sys.stderr)
            return 1
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
        if not token:
            print("[ETL] --upload requires HF_TOKEN or HUGGING_FACE_HUB_TOKEN", file=sys.stderr)
            return 1
        upload_folder(out, repo, token)
        print(f"[ETL] uploaded to {repo}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
