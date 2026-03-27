"""
Hugging Face Hub reader for backtest warehouse Parquet layout.

Design choices (see plan: backtest_data_vs_rag):
- **Public vs private dataset:** set `HF_TOKEN` or `HUGGING_FACE_HUB_TOKEN` only if the repo is private.
- **Universe:** ETL should cover at least `WAREHOUSE_DEFAULT_TICKERS` (SP500 sample + MAG7 + SPY);
  tickers outside the warehouse fall back to live APIs in `fetch_backtest_data`.
- **Cadence:** daily bars → daily ETL is enough; revision pin via `HF_DATASET_REVISION` for reproducibility.

Layout on the dataset repo (repo_type=dataset):

  manifest.json
  prices/symbol={TICKER}/data.parquet       — date, open, high, low, close, volume
  quarterly_eps/symbol={TICKER}/data.parquet — date, eps
  annual_financials/symbol={TICKER}/data.parquet — year, total_revenue, net_income
  info/symbol={TICKER}/data.parquet       — single row, column info_json (object from yfinance .info)
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _hf_token() -> Optional[str]:
    return os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")


def _dataset_repo() -> Optional[str]:
    return os.environ.get("HF_DATASET_REPO") or os.environ.get("BACKTEST_HF_DATASET_REPO")


def _dataset_revision() -> str:
    return os.environ.get("HF_DATASET_REVISION") or os.environ.get("BACKTEST_HF_DATASET_REVISION") or "main"


def hub_reads_enabled() -> bool:
    """True when Hub env is configured and source mode allows Hub (checked in backtest_data)."""
    return bool(_dataset_repo())


def download_hub_file(repo_id: str, rel_path: str, revision: str, token: Optional[str]) -> Optional[Path]:
    """Download a single file from a Hub dataset repo; returns local path or None if missing."""
    try:
        from huggingface_hub import hf_hub_download

        p = hf_hub_download(
            repo_id=repo_id,
            filename=rel_path,
            repo_type="dataset",
            revision=revision,
            token=token,
        )
        return Path(p)
    except Exception as e:
        logger.debug("[BacktestHub] missing or failed %s: %s", rel_path, e)
        return None


def _parquet_to_records(path: Path) -> list[dict]:
    import pandas as pd

    df = pd.read_parquet(path)
    if df.empty:
        return []
    # Normalize date column to YYYY-MM-DD strings
    if "date" in df.columns:
        df = df.copy()
        df["date"] = df["date"].apply(lambda x: str(x)[:10] if x is not None else "")
    return df.to_dict(orient="records")


def _read_prices_filtered(path: Path, start: str, end: str) -> list[dict]:
    import pandas as pd

    df = pd.read_parquet(path)
    if df.empty:
        return []
    df = df.copy()
    df["date"] = df["date"].astype(str).str[:10]
    df = df[(df["date"] >= start) & (df["date"] <= end)]
    out = []
    for _, row in df.iterrows():
        try:
            out.append(
                {
                    "date": row["date"],
                    "open": round(float(row["open"]), 4),
                    "high": round(float(row["high"]), 4),
                    "low": round(float(row["low"]), 4),
                    "close": round(float(row["close"]), 4),
                    "volume": int(row.get("volume", 0) or 0),
                }
            )
        except Exception:
            continue
    out.sort(key=lambda r: r["date"])
    return out


def _read_annual_financials(path: Path) -> dict:
    import pandas as pd

    df = pd.read_parquet(path)
    if df.empty:
        return {}
    annual: dict = {}
    for _, row in df.iterrows():
        yr = row["year"]
        try:
            y = str(int(float(yr)))
        except (TypeError, ValueError):
            y = str(yr)[:4]
        entry: dict = {}
        tr = row.get("total_revenue")
        ni = row.get("net_income")
        if tr is not None and not (isinstance(tr, float) and str(tr) == "nan"):
            entry["total_revenue"] = float(tr)
        if ni is not None and not (isinstance(ni, float) and str(ni) == "nan"):
            entry["net_income"] = float(ni)
        if entry:
            annual[y] = entry
    return annual


def _read_info(path: Path) -> dict:
    import pandas as pd

    df = pd.read_parquet(path)
    if df.empty or "info_json" not in df.columns:
        return {}
    raw = df.iloc[0]["info_json"]
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return {}


def load_ticker_bundle_from_hub(
    repo_id: str,
    revision: str,
    token: Optional[str],
    ticker: str,
    start: str,
    end: str,
) -> tuple[Optional[dict], int]:
    """
    Load one ticker's backtest bundle from Hub Parquet files.
    Returns (bundle_or_none, bytes_downloaded_est).
    If any required object is missing, returns (None, bytes).
    """
    t = ticker.upper()
    base_paths = [
        f"prices/symbol={t}/data.parquet",
        f"quarterly_eps/symbol={t}/data.parquet",
        f"annual_financials/symbol={t}/data.parquet",
        f"info/symbol={t}/data.parquet",
    ]
    paths: list[Path] = []
    total_bytes = 0
    for rel in base_paths:
        local = download_hub_file(repo_id, rel, revision, token)
        if local is None or not local.is_file():
            return None, total_bytes
        total_bytes += local.stat().st_size
        paths.append(local)

    prices = _read_prices_filtered(paths[0], start, end)
    q_eps = _parquet_to_records(paths[1])
    # normalize eps records
    norm_eps = []
    for r in q_eps:
        try:
            norm_eps.append({"date": str(r.get("date", ""))[:10], "eps": float(r["eps"])})
        except Exception:
            continue
    norm_eps.sort(key=lambda x: x["date"])

    annual = _read_annual_financials(paths[2])
    info = _read_info(paths[3])

    bundle = {
        "prices": prices,
        "quarterly_eps": norm_eps,
        "annual_financials": annual,
        "info": info,
    }
    return bundle, total_bytes


def assemble_from_hub(
    tickers: list[str],
    start: str,
    end: str,
    repo_id: Optional[str] = None,
    revision: Optional[str] = None,
    token: Optional[str] = None,
) -> tuple[dict[str, dict], int, str]:
    """
    Build full {ticker: bundle} from Hub. Tickers with missing files are omitted (caller fills live).

    Returns (partial_results, total_bytes_downloaded, revision_used).
    """
    rid = repo_id or _dataset_repo()
    rev = revision or _dataset_revision()
    tok = token if token is not None else _hf_token()
    if not rid:
        return {}, 0, rev

    out: dict[str, dict] = {}
    total_b = 0
    for t in tickers:
        u = t.upper()
        bundle, nb = load_ticker_bundle_from_hub(rid, rev, tok, u, start, end)
        total_b += nb
        if bundle is not None and bundle["prices"]:
            out[u] = bundle
        elif bundle is not None:
            logger.debug("[BacktestHub] %s: empty prices in range %s–%s", u, start, end)
    logger.info(
        "[BacktestHub] repo=%s revision=%s tickers_from_hub=%s/%s bytes≈%s",
        rid,
        rev,
        len(out),
        len(tickers),
        total_b,
    )
    return out, total_b, rev
