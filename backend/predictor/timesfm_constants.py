"""
TimesFM 2.5 quantile-head and horizon constants.

The model card for ``google/timesfm-2.5-200m-pytorch`` documents a
``(B, H, 10)`` output where the last axis is::

    index 0     -> mean / point forecast
    index 1..9  -> q10, q20, q30, q40, q50, q60, q70, q80, q90

We pin those indices here so client code never indexes by literal numbers,
and a Phase-0 unit test (``test_predictor_constants_quantile_indices``)
asserts the values are exactly what the model card prescribes.
"""
from __future__ import annotations

from typing import Final, Tuple

# ── Quantile-head layout (TimesFM 2.5) ──────────────────────────────────────

#: Last-axis size of the quantile head. Hard-coded to detect upstream changes.
QUANTILE_HEAD_SIZE: Final[int] = 10

#: Index of the mean / point forecast on the last axis.
IDX_MEAN: Final[int] = 0

#: Index of the q10 quantile on the last axis (lower 80% PI bound).
IDX_Q10: Final[int] = 1

#: Index of the median quantile on the last axis.
IDX_Q50: Final[int] = 5

#: Index of the q90 quantile on the last axis (upper 80% PI bound).
IDX_Q90: Final[int] = 9

#: All quantile levels in last-axis order, indexed 1..9. Exposed for tests
#: and for client code that needs to iterate every quantile (calibration).
QUANTILE_LEVELS: Final[Tuple[float, ...]] = (
    0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90,
)

# ── Horizon contract ────────────────────────────────────────────────────────

#: Canonical predictor horizons. Match backend.outcome_grader.HORIZONS so
#: graded outcomes flow into SEPL/TEVV without bespoke wiring.
DEFAULT_HORIZONS: Final[Tuple[str, ...]] = ("1d", "5d", "21d", "63d")

#: Mapping horizon-tag -> trading days (NOT calendar days).
HORIZON_TO_TRADING_DAYS: Final[dict[str, int]] = {
    "1d": 1,
    "5d": 5,
    "21d": 21,
    "63d": 63,
}

#: Canonical model identifier. The real microservice replaces this with a
#: weights-pinned string (e.g. ``timesfm-2.5-200m-pytorch@<sha>``).
MOCK_MODEL_VERSION: Final[str] = "timesfm-2.5-mock-v1"

#: Fixed model identifier used by the baseline-only fallback path.
BASELINES_MODEL_VERSION: Final[str] = "predictor-baselines-only-v1"
