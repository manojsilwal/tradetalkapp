"""
Point-in-time (PIT) reads for quarterly fundamentals in the data lake.

Rows without ``knowledge_date`` infer knowledge as period-end + filing lag (default
45 calendar days).
"""

from __future__ import annotations

import logging
import math
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

DEFAULT_FILING_LAG_DAYS = int(os.environ.get("PREDICTOR_PIT_FILING_LAG_DAYS", "45"))

FACTOR_TO_COLUMN: Dict[str, str] = {
    "roe": "roe",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_margin": "net_margin",
    "net_income": "Net Income",
    "total_revenue": "Total Revenue",
    "free_cash_flow": "Free Cash Flow",
    "total_debt": "Total Debt",
    "ebitda": "EBITDA",
}


def _parse_asof(asof: Union[str, date, datetime]) -> date:
    if isinstance(asof, datetime):
        return asof.date()
    if isinstance(asof, date):
        return asof
    s = str(asof).strip()
    if "T" in s:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
    return date.fromisoformat(s[:10])


def resolve_fundamentals_parquet_path(ticker: str) -> Optional[str]:
    from backend.data_lake.config import DATA_LAKE_SOURCE, FUNDAMENTALS_DIR, HF_DATASET_ID

    t = ticker.upper()
    path = os.path.join(FUNDAMENTALS_DIR, f"{t}.parquet")
    if os.path.isfile(path):
        return path
    if DATA_LAKE_SOURCE == "hf" and HF_DATASET_ID:
        try:
            from huggingface_hub import hf_hub_download

            token = os.environ.get("HF_TOKEN")
            return hf_hub_download(
                repo_id=HF_DATASET_ID,
                repo_type="dataset",
                filename=f"quarterly_financials/{t}.parquet",
                token=token,
            )
        except Exception as e:
            logger.debug("[PIT] HF fundamentals missing %s: %s", t, e)
    return None


def load_fundamentals_dataframe(ticker: str):
    import pandas as pd

    path = resolve_fundamentals_parquet_path(ticker)
    if not path:
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("[PIT] read failed %s: %s", ticker, e)
        return None


def _coerce_date(val: Any) -> Optional[date]:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    try:
        import pandas as pd

        return pd.Timestamp(val).date()
    except Exception:
        return None


def _knowledge_date_for_row(
    row: Any,
    period_end: date,
    *,
    filing_lag_days: int,
) -> date:
    kd = None
    try:
        if hasattr(row, "index") and "knowledge_date" in getattr(row, "index", []):
            kd = row["knowledge_date"]
        elif hasattr(row, "get"):
            kd = row.get("knowledge_date")
        elif isinstance(row, dict):
            kd = row.get("knowledge_date")
    except Exception:
        kd = None
    parsed = _coerce_date(kd)
    if parsed is not None:
        return parsed
    return period_end + timedelta(days=filing_lag_days)


def as_of(
    ticker: str,
    factor: str,
    asof: Union[str, date, datetime],
    *,
    filing_lag_days: int = DEFAULT_FILING_LAG_DAYS,
) -> Optional[float]:
    """Latest fundamentals value for ``factor`` observable at ``asof`` (UTC date)."""
    import pandas as pd

    col = FACTOR_TO_COLUMN.get(factor.lower())
    if not col:
        logger.debug("[PIT] unknown factor %s", factor)
        return None

    df = load_fundamentals_dataframe(ticker)
    if df is None or df.empty:
        return None

    cutoff = _parse_asof(asof)

    if isinstance(df.index, pd.DatetimeIndex):
        idx_dates = [pd.Timestamp(x).date() for x in df.index]
    elif "date" in df.columns:
        idx_dates = [pd.Timestamp(x).date() for x in df["date"].tolist()]
    else:
        idx_dates = []
        for x in df.index:
            d = _coerce_date(x)
            idx_dates.append(d if d is not None else date(1970, 1, 1))

    if col not in df.columns:
        return None

    best_val: Optional[float] = None
    best_pe: Optional[date] = None

    for i in range(len(df)):
        pe = idx_dates[i] if i < len(idx_dates) else None
        if pe is None:
            continue
        row_series = df.iloc[i]
        kd = _knowledge_date_for_row(row_series, pe, filing_lag_days=filing_lag_days)
        if kd > cutoff:
            continue
        raw = df.iloc[i][col]
        if raw is None or (isinstance(raw, float) and math.isnan(raw)):
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if best_pe is None or pe > best_pe:
            best_pe = pe
            best_val = val

    return best_val


def pit_probe_tickers() -> list[str]:
    from backend.data_lake.config import HISTORICAL_REMOVED_TICKERS

    return ["LEH", "AAPL"] + list(HISTORICAL_REMOVED_TICKERS[:5])
