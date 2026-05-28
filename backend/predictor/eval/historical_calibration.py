"""Point-in-time replay calibration — coverage and pinball vs realized forward close."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from backend.predictor.agent import HORIZON_TO_TD, _load_price_series_from_data_lake
from backend.predictor.baselines import seasonal_naive_forecast
from backend.predictor.calibration import (
    calibration_band,
    coverage_in_band,
    empirical_coverage_fraction,
    interval_pinball_mean,
    q10_q90_hit,
)
from backend.predictor.config_loader import load_yaml_cached
from backend.predictor.timesfm_client import MockTimesFMClient
from backend.predictor.timesfm_constants import IDX_Q10, IDX_Q50, IDX_Q90

logger = logging.getLogger(__name__)


@dataclass
class CalibrationRow:
    ticker: str
    as_of: str
    horizon: str
    spot: float
    realized: float
    q10: float
    q50: float
    q90: float
    hit: bool
    pinball: float
    naive_pinball: float
    price_source: str


def _corpus_path() -> Path:
    return Path(__file__).resolve().parents[1] / "replay_corpus.json"


def _synthetic_close_series(ticker: str, *, periods: int = 4500) -> pd.Series:
    """Deterministic business-day closes for offline replay when parquet is absent."""
    seed = int(hashlib.sha256(ticker.upper().encode()).hexdigest()[:12], 16)
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2011-01-01", periods=periods)
    r = rng.normal(0.00035, 0.012, size=periods)
    closes = 100.0 * np.exp(np.cumsum(r))
    return pd.Series(closes.astype(float), index=idx)


def load_close_series(ticker: str) -> tuple[pd.Series, str]:
    lake = _load_price_series_from_data_lake(ticker.upper())
    if lake is not None and lake.size >= 128:
        n = lake.size
        idx = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
        return pd.Series(lake.astype(float), index=idx), "data_lake"
    return _synthetic_close_series(ticker), "synthetic"


def _parse_as_of(as_of_s: str) -> pd.Timestamp:
    return pd.Timestamp(as_of_s).normalize()


def _pit_index(series: pd.Series, as_of: pd.Timestamp) -> int:
    mask = series.index <= as_of
    if not mask.any():
        return -1
    return int(np.where(mask)[0][-1])


def _forward_index(series: pd.Series, start_idx: int, horizon_td: int) -> int:
    target = start_idx + horizon_td
    if target >= len(series):
        return -1
    return target


def forecast_quantiles_usd(
    pit_closes: np.ndarray,
    *,
    ticker: str,
    as_of: str,
    horizon: str,
) -> tuple[float, float, float, float]:
    """Mock TimesFM path at PIT — mirrors production offline path."""
    log_p = np.log(np.maximum(pit_closes.astype(np.float64), 1e-8))
    td = HORIZON_TO_TD.get(horizon, 21)
    max_h = max(td, 63)
    cycle_id = f"replay-{ticker.upper()}-{as_of}-{horizon}"
    path = MockTimesFMClient().forecast_price_path(
        log_p,
        max_h,
        ticker=ticker.upper(),
        cycle_id=cycle_id,
    )
    row = path[min(td, path.shape[0]) - 1]
    spot = float(pit_closes[-1])
    q10 = float(np.exp(row[IDX_Q10]))
    q50 = float(np.exp(row[IDX_Q50]))
    q90 = float(np.exp(row[IDX_Q90]))
    lo, hi = (min(q10, q90), max(q10, q90))
    return spot, lo, q50, hi


def evaluate_replay_row(
    *,
    ticker: str,
    as_of: str,
    horizon: str,
    series: pd.Series,
    price_source: str,
) -> Optional[CalibrationRow]:
    as_of_ts = _parse_as_of(as_of)
    idx = _pit_index(series, as_of_ts)
    if idx < 64:
        return None
    td = HORIZON_TO_TD.get(horizon, 21)
    fwd = _forward_index(series, idx, td)
    if fwd < 0:
        return None

    pit = series.iloc[: idx + 1].values
    spot, q10, q50, q90 = forecast_quantiles_usd(
        pit, ticker=ticker, as_of=as_of, horizon=horizon
    )
    realized = float(series.iloc[fwd])
    naive_q50 = float(seasonal_naive_forecast(pit, td))
    hit = q10_q90_hit(realized, q10, q90)
    pin = interval_pinball_mean(realized, q10, q50, q90)
    naive_pin = abs(realized - naive_q50)
    return CalibrationRow(
        ticker=ticker.upper(),
        as_of=as_of,
        horizon=horizon,
        spot=spot,
        realized=realized,
        q10=q10,
        q50=q50,
        q90=q90,
        hit=hit,
        pinball=pin,
        naive_pinball=naive_pin,
        price_source=price_source,
    )


def _thresholds() -> Dict[str, float]:
    th = load_yaml_cached("predictor_thresholds.yaml")
    return {
        "min_rows": float(th.get("historical_calibration_min_rows") or 10),
        "coverage_lower": float(th.get("historical_calibration_coverage_lower") or 0.55),
        "coverage_upper": float(th.get("historical_calibration_coverage_upper") or 0.92),
        "max_pinball_ratio_vs_naive": float(
            th.get("historical_calibration_max_pinball_ratio_vs_naive") or 1.20
        ),
    }


def run_historical_calibration(*, limit: int = 50) -> Dict[str, Any]:
    """
    Replay corpus with PIT slices; score q10–q90 coverage and pinball vs realized close.

    Uses data-lake parquet when present; otherwise deterministic synthetic history so CI
    remains offline-safe.
    """
    path = _corpus_path()
    if not path.is_file():
        return {"ok": False, "error": f"missing {path}"}

    rows_raw: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    rows_raw = rows_raw[:limit]

    series_cache: Dict[str, tuple[pd.Series, str]] = {}
    evaluated: List[CalibrationRow] = []
    skipped: List[Dict[str, str]] = []

    for raw in rows_raw:
        ticker = str(raw.get("ticker") or "").upper()
        as_of = str(raw.get("as_of") or "")
        horizon = str(raw.get("horizon") or raw.get("horizons") or "5d")
        if not ticker or not as_of:
            skipped.append({"ticker": ticker, "reason": "missing ticker or as_of"})
            continue
        if ticker not in series_cache:
            series_cache[ticker] = load_close_series(ticker)
        series, src = series_cache[ticker]
        row = evaluate_replay_row(
            ticker=ticker,
            as_of=as_of,
            horizon=horizon,
            series=series,
            price_source=src,
        )
        if row is None:
            skipped.append({"ticker": ticker, "as_of": as_of, "reason": "insufficient history"})
            continue
        evaluated.append(row)

    th = _thresholds()
    min_rows = int(th["min_rows"])
    if len(evaluated) < min_rows:
        return {
            "ok": False,
            "error": f"only {len(evaluated)} evaluable rows (need {min_rows})",
            "evaluated": len(evaluated),
            "skipped": skipped[:10],
        }

    hits = [r.hit for r in evaluated]
    coverage = empirical_coverage_fraction(hits)
    pin_mean = sum(r.pinball for r in evaluated) / len(evaluated)
    naive_pin_mean = sum(r.naive_pinball for r in evaluated) / len(evaluated)
    pin_ratio = pin_mean / naive_pin_mean if naive_pin_mean > 1e-9 else pin_mean

    cov_lo, cov_hi = th["coverage_lower"], th["coverage_upper"]
    cov_ok = cov_lo <= coverage <= cov_hi
    pin_ok = pin_ratio <= th["max_pinball_ratio_vs_naive"]
    band_ok = coverage_in_band(coverage) or cov_ok

    ok = band_ok and pin_ok
    target_lo, target_hi = calibration_band()

    return {
        "ok": ok,
        "evaluated": len(evaluated),
        "skipped_count": len(skipped),
        "coverage": round(coverage, 4),
        "target_coverage_band": [target_lo, target_hi],
        "configured_coverage_gate": [cov_lo, cov_hi],
        "coverage_ok": cov_ok,
        "mean_pinball": round(pin_mean, 4),
        "mean_naive_abs_error": round(naive_pin_mean, 4),
        "pinball_ratio_vs_naive": round(pin_ratio, 4),
        "pinball_ok": pin_ok,
        "sample": [
            {
                "ticker": r.ticker,
                "as_of": r.as_of,
                "horizon": r.horizon,
                "hit": r.hit,
                "pinball": round(r.pinball, 4),
                "price_source": r.price_source,
            }
            for r in evaluated[:5]
        ],
        "skipped": skipped[:5],
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    lim = int(__import__("os").environ.get("PREDICTOR_CALIB_LIMIT", "50"))
    out = run_historical_calibration(limit=lim)
    logger.info("[predictor.calibration] %s", out)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
