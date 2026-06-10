"""
Nightly batch forecaster (Phase 2) — Cloud Run Job entry point.

Instead of serving TimesFM online per request (GPU/cold-start cost), this job
precomputes forecasts for the whole universe once per night on CPU and:

* emits each forecast to the Decision-Outcome Ledger (so the outcome grader
  scores pinball/coverage at T+H — the raw material of the self-learning loop),
* optionally writes a Parquet summary under ``DATA_LAKE_DIR/forecasts/`` for
  cheap reads by the API / analytics.

Usage::

    PYTHONPATH=. python -m backend.predictor.batch_forecast \
        --tickers AAPL,MSFT,NVDA --horizons 1d,5d,21d,63d
    PYTHONPATH=. python -m backend.predictor.batch_forecast --universe lake --limit 100

Cost guidance (GCP): run as a Cloud Run Job on the smallest CPU class; the
ensemble + (optional) remote TimesFM calls for ~500 tickers complete well
within free-tier vCPU-seconds. No GPU required.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

DEFAULT_HORIZONS = ["1d", "5d", "21d", "63d"]


def _universe_from_lake(limit: int) -> List[str]:
    try:
        from backend.data_lake.config import PRICES_DIR

        if not os.path.isdir(PRICES_DIR):
            return []
        names = sorted(f[:-8] for f in os.listdir(PRICES_DIR) if f.endswith(".parquet"))
        return names[: max(1, limit)]
    except Exception:
        return []


async def run_batch(
    tickers: List[str],
    horizons: List[str],
    *,
    concurrency: int = 4,
    write_parquet: bool = True,
) -> Dict[str, Any]:
    from backend.predictor.agent import run_predictor_forecast

    sem = asyncio.Semaphore(max(1, concurrency))
    rows: List[Dict[str, Any]] = []
    failures: List[str] = []

    async def _one(t: str) -> None:
        async with sem:
            try:
                resp = await run_predictor_forecast(
                    t, horizons=list(horizons), tool_registry=None, emit_ledger=True,
                )
            except Exception as e:
                logger.warning("[BatchForecast] %s failed: %s", t, e)
                failures.append(t)
                return
            if resp.status != "ok":
                failures.append(t)
                return
            for band in resp.horizon_bands_usd:
                rows.append(
                    {
                        "ticker": t.upper(),
                        "horizon": band.horizon,
                        "q10_usd": band.q10_usd,
                        "q50_usd": band.q50_usd,
                        "q90_usd": band.q90_usd,
                        "point_usd": band.point_usd,
                        "directional_bias": resp.directional_bias,
                        "model_version": resp.model_version,
                        "forecast_source": str(resp.meta.get("forecast_source") or ""),
                        "cycle_id": resp.cycle_id,
                    }
                )

    started = time.time()
    await asyncio.gather(*[_one(t) for t in tickers])

    parquet_path = ""
    if write_parquet and rows:
        try:
            import pandas as pd

            from backend.data_lake.config import DATA_DIR

            out_dir = os.path.join(DATA_DIR, "forecasts")
            os.makedirs(out_dir, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            parquet_path = os.path.join(out_dir, f"{day}.parquet")
            pd.DataFrame(rows).to_parquet(parquet_path, index=False)
        except Exception as e:
            logger.warning("[BatchForecast] parquet write skipped: %s", e)
            parquet_path = ""

    summary = {
        "n_tickers": len(tickers),
        "n_ok": len(tickers) - len(failures),
        "n_failed": len(failures),
        "n_rows": len(rows),
        "elapsed_s": round(time.time() - started, 1),
        "parquet_path": parquet_path,
        "failed": failures[:20],
    }
    logger.info("[BatchForecast] done %s", summary)
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Nightly batch forecaster")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers (overrides --universe)")
    parser.add_argument("--universe", default="lake", choices=["lake"], help="Ticker source when --tickers empty")
    parser.add_argument("--limit", type=int, default=int(os.getenv("BATCH_FORECAST_LIMIT", "100")))
    parser.add_argument("--horizons", default=",".join(DEFAULT_HORIZONS))
    parser.add_argument("--concurrency", type=int, default=int(os.getenv("BATCH_FORECAST_CONCURRENCY", "4")))
    parser.add_argument("--no-parquet", action="store_true")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        tickers = _universe_from_lake(args.limit)
    if not tickers:
        logger.error("[BatchForecast] no tickers resolved (empty data lake?)")
        return 1
    horizons = [h.strip() for h in args.horizons.split(",") if h.strip()]

    summary = asyncio.run(
        run_batch(
            tickers,
            horizons,
            concurrency=args.concurrency,
            write_parquet=not args.no_parquet,
        )
    )
    return 0 if summary["n_ok"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
