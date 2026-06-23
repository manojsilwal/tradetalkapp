"""Per-ticker brain snapshot: the precomputed base PLUS the anchors needed to
recompute price-sensitive features live.

This is the fix for the "frozen score" anti-pattern. We do NOT store only
``{score, verdict}``. We store the base scores, the base feature row, and the
anchors (price tail, moving averages, sector reference, intrinsic-value range,
discount rate, fundamentals as-of) so the Reflex layer can re-derive the
price-driven features from a live price and re-run the model — O(1) per ticker.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from . import finance_math as fm
from . import features as feat
from . import timeseries as ts
from . import valuation as val
from .inference import InferenceEngine
from .ports.base import StoragePort
from .ports.factory import get_storage


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class BrainSnapshot:
    ticker: str
    as_of_date: str
    computed_at: str
    model_name: str
    model_version: str
    horizon_days: Optional[int]

    # The precomputed base intelligence (the canonical nightly result).
    base_contract: Dict
    base_feature_row: Dict

    # --- Price anchors (recompute price-sensitive features from a live price) ---
    base_price: float
    price_tail: List[float]          # adj closes up to as_of (>= 253 ideal)
    sector_ref_tail: List[float] = field(default_factory=list)
    ma50: Optional[float] = None
    ma200: Optional[float] = None

    # --- Valuation anchors (intrinsic is fixed until fundamentals/rates move) ---
    intrinsic_value_low: Optional[float] = None
    intrinsic_value_mid: Optional[float] = None
    intrinsic_value_high: Optional[float] = None
    dcf_upside_at_base: Optional[float] = None
    discount_rate: Optional[float] = None
    equity_to_ev: float = 1.0
    fundamentals_as_of: Optional[str] = None
    sector: Optional[str] = None

    # --- TimesFM time-series anchors (absolute USD quantile bands per horizon) ---
    # These let the Reflex layer recompute the forward forecast vs a live price
    # without re-running TimesFM (the bands are fixed until the next brain run).
    timesfm_bands: List[Dict] = field(default_factory=list)
    timesfm_model_version: Optional[str] = None
    timeseries_forecast: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict) -> "BrainSnapshot":
        return cls(**d)


class SnapshotStore:
    """Persist/load snapshots via the StoragePort (cloud-portable, offline default)."""

    def __init__(self, root: str = "predictions", storage: Optional[StoragePort] = None):
        self.root = root.rstrip("/")
        self.storage = storage or get_storage()

    def _key(self, ticker: str, as_of_date: str) -> str:
        return f"{self.root}/{as_of_date}/{ticker}.json"

    def save(self, snapshot: BrainSnapshot) -> str:
        key = self._key(snapshot.ticker, snapshot.as_of_date)
        self.storage.put(key, json.dumps(snapshot.to_dict(), indent=2).encode(),
                         content_type="application/json")
        return key

    def load(self, ticker: str, as_of_date: str) -> BrainSnapshot:
        raw = self.storage.get(self._key(ticker, as_of_date))
        return BrainSnapshot.from_dict(json.loads(raw.decode()))

    def exists(self, ticker: str, as_of_date: str) -> bool:
        return self.storage.exists(self._key(ticker, as_of_date))


def build_base_snapshot(engine: InferenceEngine, ticker: str, as_of_date: str,
                        prices: List[float], sector_prices: Optional[List[float]],
                        fundamentals: Dict,
                        dcf_inputs: Optional[Dict] = None,
                        timesfm_bands: Optional[List[Dict]] = None,
                        timesfm_model_version: Optional[str] = None,
                        sector: Optional[str] = None,
                        fundamentals_as_of: Optional[str] = None,
                        tail_len: int = 260) -> BrainSnapshot:
    """Compose a snapshot: base features + base contract + price/valuation/TimesFM anchors.

    ``dcf_inputs`` (optional): {fcf0, growth, years, terminal_growth, discount_rate,
    equity_to_ev} for the intrinsic-value anchor. If absent, valuation anchors are
    left None and the Reflex layer simply skips DCF upside.

    ``timesfm_bands`` (optional): list of {horizon, q10, q50, q90} USD quantiles
    from the TimesFM predictor (see backend/predictor/). When present, the
    TimesFM features are injected into the feature row so the model consumes them,
    and a forward forecast block is attached for the UI.
    """
    base_price = float(prices[-1])
    feature_row = feat.build_feature_row(prices, sector_prices, fundamentals)

    # Inject the TimesFM forward features so the cross-sectional model uses them.
    timesfm_bands = timesfm_bands or []
    if timesfm_bands:
        feature_row.update(ts.to_brain_features(timesfm_bands, base_price))
    base_contract = engine.predict_ticker(feature_row, ticker, as_of_date)

    intrinsic = {"intrinsic_value_low": None, "intrinsic_value_mid": None,
                 "intrinsic_value_high": None}
    dcf_up = None
    discount_rate = None
    equity_to_ev = 1.0
    if dcf_inputs:
        rng = val.intrinsic_range(
            fcf0=dcf_inputs["fcf0"], growth=dcf_inputs["growth"],
            years=dcf_inputs.get("years", 5),
            terminal_growth=dcf_inputs.get("terminal_growth", 0.025),
            discount_rate=dcf_inputs.get("discount_rate", 0.09),
        )
        intrinsic = rng
        discount_rate = dcf_inputs.get("discount_rate", 0.09)
        equity_to_ev = float(dcf_inputs.get("equity_to_ev", 1.0))
        dcf_up = val.dcf_upside(rng["intrinsic_value_mid"], base_price)

    return BrainSnapshot(
        ticker=ticker, as_of_date=as_of_date, computed_at=_now_iso(),
        model_name=engine.model.name, model_version=engine.model_version,
        horizon_days=base_contract.get("horizon_days"),
        base_contract=base_contract, base_feature_row=feature_row,
        base_price=base_price,
        price_tail=[float(p) for p in prices[-tail_len:]],
        sector_ref_tail=[float(p) for p in (sector_prices or [])[-tail_len:]],
        ma50=fm.moving_average(prices, 50), ma200=fm.moving_average(prices, 200),
        intrinsic_value_low=intrinsic["intrinsic_value_low"],
        intrinsic_value_mid=intrinsic["intrinsic_value_mid"],
        intrinsic_value_high=intrinsic["intrinsic_value_high"],
        dcf_upside_at_base=dcf_up, discount_rate=discount_rate,
        equity_to_ev=equity_to_ev, fundamentals_as_of=fundamentals_as_of,
        sector=sector,
        timesfm_bands=timesfm_bands,
        timesfm_model_version=timesfm_model_version,
        timeseries_forecast=ts.forecast_block(timesfm_bands, base_price,
                                              timesfm_model_version) if timesfm_bands else None,
    )
