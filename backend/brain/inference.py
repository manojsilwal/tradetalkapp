"""Inference contract — the single shape the API/UI consume.

Loads a registered model version and turns a feature row into the additive,
UI-parity payload (docs Section 06): outperform_probability + 0-100 signal
scores + composite + drivers, ALWAYS stamped with model_version + as_of_date +
disclaimer (docs Rule 01 / 03). Numbers here are the ground truth the LLM layer
must cite (see agent_explainer).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from . import DISCLAIMER, FEATURE_LIST, SIGNAL_GROUPS
from . import rule_baseline
from .model_registry import ModelRegistry
from .pipeline import rows_to_matrix

# Driver phrases as (favorable, unfavorable) — the favorable phrase describes the
# state that supports the bull thesis, regardless of whether higher or lower raw
# values are good. DIRECTION captures that: +1 if higher is better, -1 if lower.
FEATURE_PHRASES = {
    "return_3m": ("strong 3-month price momentum", "weak 3-month price momentum"),
    "return_6m": ("strong 6-month momentum", "weak 6-month momentum"),
    "return_12m": ("strong 12-month trend", "weak 12-month trend"),
    "relative_strength_3m": ("outperforming its sector recently", "lagging its sector recently"),
    "roic": ("high return on invested capital", "low return on invested capital"),
    "operating_margin": ("healthy operating margins", "thin operating margins"),
    "fcf_margin": ("strong free-cash-flow margin", "weak free-cash-flow margin"),
    "fcf_yield": ("attractive free-cash-flow yield", "low free-cash-flow yield"),
    "revenue_growth_yoy": ("accelerating revenue growth", "decelerating revenue growth"),
    "pe_ratio": ("cheap P/E valuation", "rich P/E valuation"),
    "ev_ebitda": ("cheap EV/EBITDA valuation", "rich EV/EBITDA valuation"),
    "valuation_percentile_5y": ("trading cheap vs its 5y range", "trading rich vs its 5y range"),
    "volatility_3m": ("low volatility", "elevated volatility"),
    "max_drawdown_6m": ("shallow recent drawdown", "deep recent drawdown"),
    "debt_to_equity": ("low leverage", "high leverage"),
    "capital_flow_score": ("positive capital inflows", "capital outflows"),
    "institutional_accumulation_score": ("institutional accumulation", "institutional distribution"),
    "filing_risk_score": ("clean filing risk profile", "elevated filing risk language"),
    "sentiment_score": ("positive sentiment", "negative sentiment"),
    "tsfm_expected_return": ("positive TimesFM forward forecast", "negative TimesFM forward forecast"),
    "tsfm_band_width": ("tight TimesFM forecast band", "wide TimesFM forecast uncertainty"),
    "put_call_volume_ratio": ("balanced call volume", "elevated put/call volume ratio"),
    "put_call_oi_ratio": ("balanced open interest", "elevated put/call OI ratio"),
    "iv_skew": ("neutral IV skew", "elevated put-side IV skew"),
    "unusual_activity_score": ("quiet options flow", "unusual options activity"),
    "options_net_premium_bias_num": ("bullish options premium bias", "bearish options premium bias"),
}

# +1: higher raw value supports the thesis; -1: lower raw value supports it.
FEATURE_DIRECTION = {
    "pe_ratio": -1, "ev_ebitda": -1, "valuation_percentile_5y": -1,
    "volatility_3m": -1, "debt_to_equity": -1, "filing_risk_score": -1,
    "tsfm_band_width": -1,  # wider (more uncertain) forecast is worse
    "put_call_volume_ratio": -1, "put_call_oi_ratio": -1, "iv_skew": -1,
    # max_drawdown_6m is negative; higher (closer to 0) is better -> +1.
}


class InferenceEngine:
    def __init__(self, registry: ModelRegistry, model_name: str, version: str):
        art = registry.load(model_name, version)
        self.model = art["model"]
        self.scaler = art["scaler"]
        self.feature_list: List[str] = art["feature_list"]
        self.model_version: str = art["model_version"]
        self.meta = art["meta"]
        self.metrics = art["metrics"]

    # --- internals ---------------------------------------------------------
    def _vectorize(self, row: Dict) -> "tuple[np.ndarray, int, int]":
        x = rows_to_matrix([row], self.feature_list)  # (1, D), NaN missing
        n_total = len(self.feature_list)
        n_missing = int(np.isnan(x).sum())
        return x, n_missing, n_total

    def _zscores(self, row: Dict) -> np.ndarray:
        x, _, _ = self._vectorize(row)
        return self.scaler.transform(x).reshape(-1)

    def predict_proba_row(self, row: Dict) -> float:
        x, _, _ = self._vectorize(row)
        xs = self.scaler.transform(x)
        return float(self.model.predict_proba(xs)[0])

    def _drivers(self, row: Dict, top_k: int = 3, threshold: float = 0.5) -> Dict[str, List[str]]:
        """Rank features by their *contribution to the bull thesis* = z * direction.

        Positive contribution -> favorable phrase (supporting); negative -> the
        unfavorable phrase (detracting). This correctly handles lower-is-better
        features (e.g. low volatility is supporting, not detracting).
        """
        z = self._zscores(row)
        contributions = []  # (contribution, feature)
        for j, f in enumerate(self.feature_list):
            if f not in FEATURE_PHRASES:
                continue
            contributions.append((z[j] * FEATURE_DIRECTION.get(f, 1), f))
        contributions.sort(reverse=True)

        supporting = [FEATURE_PHRASES[f][0] for c, f in contributions
                      if c > threshold][:top_k]
        detracting = [FEATURE_PHRASES[f][1] for c, f in reversed(contributions)
                      if c < -threshold][:top_k]
        return {"supporting": supporting, "detracting": detracting}

    def _single_row_signal_scores(self, row: Dict) -> Dict[str, float]:
        """Group 0-100 scores from a single row's z-scores (no cross-section)."""
        z = {self.feature_list[i]: v for i, v in enumerate(self._zscores(row))}
        scores: Dict[str, float] = {}
        for group, defs in rule_baseline._GROUP_DEFS.items():
            num = wsum = 0.0
            for feature, w, invert in defs:
                zi = z.get(feature)
                if zi is None or np.isnan(zi):
                    continue
                signed = -zi if invert else zi
                s = 1.0 / (1.0 + np.exp(-signed))  # squash z to 0-1
                num += w * s
                wsum += w
            scores[group] = round(100.0 * (num / wsum) if wsum > 0 else 50.0, 2)
        composite = sum(
            rule_baseline.COMPOSITE_WEIGHTS[g] * scores.get(g, 50.0) for g in SIGNAL_GROUPS
        )
        scores["composite_score"] = round(composite, 2)
        return scores

    @staticmethod
    def _recommendation(proba: float) -> str:
        if proba >= 0.60:
            return "constructive"
        if proba <= 0.40:
            return "cautious"
        return "neutral"

    def _contract(self, ticker: str, as_of_date: str, proba: float,
                  signal_scores: Dict[str, float], row: Dict,
                  n_missing: int, n_total: int) -> Dict:
        completeness = (n_total - n_missing) / n_total if n_total else 0.0
        confidence = round(max(0.0, min(1.0, (0.5 + abs(proba - 0.5)) * completeness)), 4)
        risk_group = signal_scores.get("risk", 50.0)
        risk_score = round(1.0 - risk_group / 100.0, 4)
        return {
            "ticker": ticker,
            "as_of_date": as_of_date,
            "model_name": self.model.name,
            "model_version": self.model_version,
            "horizon_days": self.meta.get("training_window", {}).get("horizon_days"),
            "outperform_probability": round(proba, 4),
            "recommendation": self._recommendation(proba),
            "composite_score": signal_scores.get("composite_score"),
            "signal_scores": {g: signal_scores.get(g) for g in SIGNAL_GROUPS},
            "risk_score": risk_score,
            "confidence_score": confidence,
            "data_completeness": round(completeness, 4),
            "drivers": self._drivers(row),
            "disclaimer": DISCLAIMER,
        }

    # --- public API --------------------------------------------------------
    def predict_ticker(self, row: Dict, ticker: str, as_of_date: str,
                       peers: Optional[Sequence[Dict]] = None) -> Dict:
        """Single-ticker prediction. If ``peers`` is given, signal scores are
        computed cross-sectionally against peers; else from the row alone."""
        proba = self.predict_proba_row(row)
        _, n_missing, n_total = self._vectorize(row)
        if peers:
            cross = list(peers) + [row]
            scored = rule_baseline.score_cross_section(cross)
            signal_scores = scored[-1]
        else:
            signal_scores = self._single_row_signal_scores(row)
        return self._contract(ticker, as_of_date, proba, signal_scores, row, n_missing, n_total)

    def rank_universe(self, rows: Sequence[Dict], tickers: Sequence[str],
                      as_of_date: str) -> List[Dict]:
        """Rank a whole cross-section; signal scores are proper percentiles."""
        scored = rule_baseline.score_cross_section(list(rows))
        out: List[Dict] = []
        for row, ticker, ss in zip(rows, tickers, scored):
            proba = self.predict_proba_row(row)
            _, n_missing, n_total = self._vectorize(row)
            out.append(self._contract(ticker, as_of_date, proba, ss, row, n_missing, n_total))
        out.sort(key=lambda c: c["outperform_probability"], reverse=True)
        return out
