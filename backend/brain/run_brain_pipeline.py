"""Nightly brain pipeline — the production data pipeline for the finance brain.

Runs as a Cloud Run Job (``brain-nightly``) after the daily market ingest:

  1. Ensure a registered model version exists (train + register from a BigQuery
     training panel; fall back to the deterministic synthetic panel if the data
     lake is empty so the job is never a hard failure).
  2. Build per-ticker ``BrainSnapshot``s for the S&P universe from BigQuery
     prices (optionally enriched with TimesFM bands), persist them via the
     ``SnapshotStore`` (GCS in prod, local otherwise).
  3. Write ``status.json`` at the storage root so the Pipeline Ops page can show
     freshness without scanning every snapshot.

Run locally (offline, uses synthetic model + DuckDB/empty data):
    PYTHONPATH=. python -m backend.brain.run_brain_pipeline
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional

from . import DEFAULT_HORIZON_DAYS
from .data import bq_panel
from .inference import InferenceEngine
from .model_registry import ModelRegistry
from .ports.factory import get_storage
from .snapshot_store import SnapshotStore, build_base_snapshot

logger = logging.getLogger(__name__)

STATUS_KEY = "status.json"
DEFAULT_MODEL_NAME = os.environ.get("BRAIN_MODEL_NAME", "finrank-net")
DEFAULT_MODEL_VERSION = os.environ.get("BRAIN_MODEL_VERSION", "v2")
# v2 adds options_flow features (put_call_* ratios, iv_skew, unusual_activity_score).
# Run the brain pipeline with BRAIN_MODEL_VERSION=v2 after deploying options_flow contract changes.


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _ensure_model(registry: ModelRegistry, model_name: str, version: str) -> str:
    """Return the model version to serve, training one if none is registered."""
    if registry.exists(model_name, version):
        return version
    logger.info("[brain.run] no model %s-%s; training from BigQuery panel", model_name, version)
    from . import pipeline as brain_pipeline
    panel = bq_panel.build_training_panel(horizon_days=DEFAULT_HORIZON_DAYS)
    if len(set(panel.get("dates", []))) < 4 or len(panel.get("rows", [])) < 50:
        logger.warning("[brain.run] BigQuery panel too small; using synthetic panel")
        from .dataset import synthetic_panel
        panel = synthetic_panel(n_tickers=80, n_periods=20)
    try:
        brain_pipeline.train_and_register(panel, version=version, registry=registry,
                                          model_name=model_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain.run] training failed on real panel (%s); retrying synthetic", e)
        from .dataset import synthetic_panel
        brain_pipeline.train_and_register(synthetic_panel(n_tickers=80, n_periods=20),
                                          version=version, registry=registry,
                                          model_name=model_name)
    return version


async def _maybe_bands(ticker: str, enabled: bool):
    if not enabled:
        return [], None
    try:
        from .timesfm_adapter import fetch_timesfm_bands
        bands, mv, status = await fetch_timesfm_bands(ticker)
        return (bands or []), mv
    except Exception as e:  # noqa: BLE001
        logger.debug("[brain.run] timesfm bands failed for %s: %s", ticker, e)
        return [], None


async def run_brain_pipeline(model_name: str = DEFAULT_MODEL_NAME,
                             version: str = DEFAULT_MODEL_VERSION,
                             limit: Optional[int] = None,
                             timesfm: Optional[bool] = None) -> Dict:
    """Build + persist brain snapshots for the universe. Returns a status dict."""
    started = _now_iso()
    storage = get_storage()
    registry = ModelRegistry(storage=storage)
    store = SnapshotStore(storage=storage)
    timesfm_enabled = (os.environ.get("BRAIN_TIMESFM_ENABLE", "0") == "1"
                       if timesfm is None else timesfm)

    status: Dict = {
        "started_at": started,
        "finished_at": None,
        "as_of_date": None,
        "tickers_total": 0,
        "tickers_done": 0,
        "model_name": model_name,
        "model_version": version,
        "timesfm_enabled": bool(timesfm_enabled),
        "storage_backend": os.environ.get("STORAGE_BACKEND", "local"),
        "errors": [],
    }

    try:
        version = _ensure_model(registry, model_name, version)
        status["model_version"] = version
        engine = InferenceEngine(registry, model_name, version)
    except Exception as e:  # noqa: BLE001
        status["errors"].append(f"model_init: {e}")
        status["finished_at"] = _now_iso()
        _write_status(storage, status)
        logger.error("[brain.run] aborted: model init failed: %s", e)
        return status

    rows = bq_panel.build_inference_rows()
    if limit:
        rows = rows[:limit]
    status["tickers_total"] = len(rows)

    done = 0
    sem = asyncio.Semaphore(int(os.environ.get("BRAIN_TIMESFM_CONCURRENCY", "4")))

    async def _one(item: Dict) -> None:
        nonlocal done
        ticker = item["ticker"]
        try:
            async with sem:
                bands, band_mv = await _maybe_bands(ticker, timesfm_enabled)
            snapshot = build_base_snapshot(
                engine, ticker, item["as_of_date"],
                prices=item["prices"], sector_prices=item.get("sector_prices"),
                fundamentals=item.get("fundamentals") or {},
                timesfm_bands=bands or None, timesfm_model_version=band_mv,
            )
            store.save(snapshot)
            done += 1
        except Exception as e:  # noqa: BLE001
            status["errors"].append(f"{ticker}: {e}")
            logger.warning("[brain.run] snapshot failed for %s: %s", ticker, e)

    await asyncio.gather(*[_one(it) for it in rows])

    status["tickers_done"] = done
    if rows:
        status["as_of_date"] = rows[0]["as_of_date"]
    status["finished_at"] = _now_iso()
    _write_status(storage, status)
    logger.info("[brain.run] done: %s/%s snapshots, model=%s-%s",
                done, len(rows), model_name, version)
    return status


def _write_status(storage, status: Dict) -> None:
    try:
        storage.put(STATUS_KEY, json.dumps(status, indent=2).encode(),
                    content_type="application/json")
    except Exception as e:  # noqa: BLE001
        logger.warning("[brain.run] could not write status.json: %s", e)


def read_status() -> Optional[Dict]:
    """Read the last run's status.json (used by the Pipeline Ops endpoint)."""
    try:
        raw = get_storage().get(STATUS_KEY)
        return json.loads(raw.decode())
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Nightly finance-brain snapshot pipeline")
    ap.add_argument("--limit", type=int, default=None, help="cap number of tickers")
    ap.add_argument("--timesfm", action="store_true", help="fetch TimesFM bands per ticker")
    ap.add_argument("--model", default=DEFAULT_MODEL_NAME)
    ap.add_argument("--version", default=DEFAULT_MODEL_VERSION)
    args = ap.parse_args()
    status = asyncio.run(run_brain_pipeline(
        model_name=args.model, version=args.version,
        limit=args.limit, timesfm=(True if args.timesfm else None),
    ))
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
