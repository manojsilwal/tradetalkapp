"""Reflex layer — the live, on-demand half of the dynamic brain.

Given a precomputed snapshot + live inputs, it re-derives the price-sensitive
features from the stored anchors and RE-RUNS the model (one O(1) forward pass),
then re-aggregates with the same functions the brain used. It never adds
hand-tuned point deltas; the base-vs-current "deltas" are an explanation
waterfall, not the computation.

Design choices that resolve the analyst flaws:
  - #1/#2  Re-infer from recomputed features (price moves both momentum AND
           valuation consistently); deltas are display-only.
  - #3     Anchor-breaking inputs (material events, rate moves, age) INVALIDATE
           and request a fresh brain run. A pure price move does NOT invalidate
           (recomputing intrinsic/price is exactly the Reflex layer's job).
  - #5     Freshness lowers CONFIDENCE, never the score.
  - #6     Verdict uses hysteresis bands to avoid BUY/HOLD whipsaw.
  - #7     Corporate-action (split) guard + sanity bounds on the live price.
  - #8     A market-wide move leaves the sector-relative thesis ~unchanged.
  - #9     Optional ledger emit hook for the LIVE-adjusted decision.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

import numpy as np

from . import DISCLAIMER
from . import finance_math as fm
from . import features as feat
from . import timeseries as ts
from . import valuation as val
from . import reconciliation as rec
from .inference import InferenceEngine
from .snapshot_store import BrainSnapshot, _apply_valuation_score


# --- Live inputs & policy ---------------------------------------------------

@dataclass
class LiveInputs:
    price: float
    sector_return_since_base: float = 0.0
    event_flags: List[str] = field(default_factory=list)
    sentiment: Optional[float] = None      # cheap cached sentiment, NOT an LLM call
    split_ratio: float = 1.0               # new shares per old share (2-for-1 -> 2.0)
    rate_move_bps: float = 0.0             # change in discount-rate proxy since base
    as_of: Optional[str] = None            # ISO timestamp of the live price
    put_call_oi_ratio: Optional[float] = None
    put_call_volume_ratio: Optional[float] = None
    iv_skew: Optional[float] = None
    unusual_activity_score: Optional[float] = None
    options_net_premium_bias_num: Optional[float] = None


@dataclass
class InvalidationPolicy:
    # Anchor-breaking thresholds (NOT triggered by price alone).
    invalidating_event_types: frozenset = frozenset({
        "earnings", "guidance_cut", "guidance_change", "8-K_material",
        "mna", "bankruptcy", "restatement", "fraud", "going_concern",
        "sec_investigation", "accounting_issue",
    })
    max_rate_move_bps: float = 50.0
    max_age_hours: float = 168.0           # 7 days -> stale, queue recompute
    # Soft warning (still recomputes live, just lowers confidence more).
    soft_move_warn_pct: float = 0.35
    # Sanity bounds on the live price relative to base.
    min_price_ratio: float = 0.2
    max_price_ratio: float = 5.0


# --- Status constants -------------------------------------------------------
STATUS_LIVE = "LIVE"            # recomputed live, anchors valid
STATUS_STALE = "STALE"         # too old; show base, queue recompute
STATUS_INVALID = "INVALID"     # anchor broken (event/rates); show base, force recompute
STATUS_BAD_INPUT = "INVALID_INPUT"  # implausible live price


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None


def _age_hours(computed_at: str, now_iso: Optional[str]) -> float:
    base = _parse_iso(computed_at)
    now = _parse_iso(now_iso) or datetime.now(timezone.utc)
    if base is None:
        return 0.0
    return max(0.0, (now - base).total_seconds() / 3600.0)


class ReflexEngine:
    def __init__(self, engine: InferenceEngine,
                 policy: Optional[InvalidationPolicy] = None,
                 emit_fn: Optional[Callable[[Dict], None]] = None):
        self.engine = engine
        self.policy = policy or InvalidationPolicy()
        self.emit_fn = emit_fn

    # --- input validation / corporate actions ------------------------------
    def _adjusted_price(self, snapshot: BrainSnapshot, live: LiveInputs):
        """Apply split adjustment + sanity bounds. Returns (adj_price, issues)."""
        issues: List[str] = []
        # A 2-for-1 split prints price at ~half; multiply by ratio to compare to
        # the pre-split base_price (so a split is not seen as a -50% crash).
        adj = float(live.price) * float(live.split_ratio or 1.0)
        if adj <= 0:
            issues.append("non_positive_price")
            return adj, issues
        ratio = adj / snapshot.base_price if snapshot.base_price else 0.0
        if ratio < self.policy.min_price_ratio or ratio > self.policy.max_price_ratio:
            issues.append(f"implausible_price ratio={ratio:.2f}")
        return adj, issues

    # --- invalidation gate -------------------------------------------------
    def _invalidation_reasons(self, snapshot: BrainSnapshot, live: LiveInputs,
                              age_hours: float) -> List[str]:
        reasons: List[str] = []
        events = {e.lower() for e in (live.event_flags or [])}
        hit = events & {e.lower() for e in self.policy.invalidating_event_types}
        for e in sorted(hit):
            reasons.append(f"material_event:{e}")
        if abs(live.rate_move_bps) > self.policy.max_rate_move_bps:
            reasons.append(f"discount_rate_moved:{live.rate_move_bps:.0f}bps")
        if age_hours > self.policy.max_age_hours:
            reasons.append(f"stale_age:{age_hours:.0f}h")
        return reasons

    # --- feature recompute -------------------------------------------------
    def _recompute_row(self, snapshot: BrainSnapshot, adj_price: float,
                       live: LiveInputs) -> Dict:
        row = dict(snapshot.base_feature_row)
        tail = snapshot.price_tail
        r = adj_price / snapshot.base_price if snapshot.base_price else 1.0

        for key, lb in (("return_1m", feat.LB_1M), ("return_3m", feat.LB_3M),
                        ("return_6m", feat.LB_6M), ("return_12m", feat.LB_12M)):
            if len(tail) > lb and tail[-1 - lb] != 0:
                row[key] = adj_price / tail[-1 - lb] - 1.0
        if len(tail) >= 50:
            ma50 = float(np.mean(tail[-50:]))
            row["price_vs_50dma"] = adj_price / ma50 - 1.0 if ma50 else None
        if len(tail) >= 200:
            ma200 = float(np.mean(tail[-200:]))
            row["price_vs_200dma"] = adj_price / ma200 - 1.0 if ma200 else None

        # Sector-relative (a market-wide move leaves this ~unchanged -> flaw #8).
        sref = snapshot.sector_ref_tail
        if sref:
            sector_now = sref[-1] * (1.0 + live.sector_return_since_base)
            for key, lb in (("relative_strength_3m", feat.LB_3M),
                            ("relative_strength_6m", feat.LB_6M)):
                if len(tail) > lb and len(sref) > lb and tail[-1 - lb] and sref[-1 - lb]:
                    s = adj_price / tail[-1 - lb] - 1.0
                    b = sector_now / sref[-1 - lb] - 1.0
                    row[key] = s - b

        # Valuation features scale with price (EPS / FCF / net debt fixed).
        if row.get("pe_ratio") is not None:
            row["pe_ratio"] = float(row["pe_ratio"]) * r
        if row.get("fcf_yield") is not None and r != 0:
            row["fcf_yield"] = float(row["fcf_yield"]) / r
        if row.get("ev_ebitda") is not None:
            # Only the equity portion of EV moves with price.
            row["ev_ebitda"] = float(row["ev_ebitda"]) * (1.0 + snapshot.equity_to_ev * (r - 1.0))
        # Price percentile within its own 5y range (rises as price rises).
        pct = fm.percentile_rank(adj_price, tail)
        if pct is not None:
            row["valuation_percentile_5y"] = pct

        # Live sentiment is a cheap cached signal (never an LLM call here).
        if live.sentiment is not None:
            row["sentiment_score"] = float(live.sentiment)

        # Options flow passthrough (fetched at request time, not in nightly snapshot).
        for key in (
            "put_call_oi_ratio",
            "put_call_volume_ratio",
            "iv_skew",
            "unusual_activity_score",
            "options_net_premium_bias_num",
        ):
            val = getattr(live, key, None)
            if val is not None:
                row[key] = float(val)

        # TimesFM forward view: bands are fixed USD anchors, so a live price move
        # changes the implied forward return WITHOUT re-running TimesFM. Band
        # width (forecast uncertainty) is price-independent and stays put.
        if snapshot.timesfm_bands:
            tsf = ts.to_brain_features(snapshot.timesfm_bands, adj_price)
            if tsf.get("tsfm_expected_return") is not None:
                row["tsfm_expected_return"] = tsf["tsfm_expected_return"]
            if tsf.get("tsfm_band_width") is not None:
                row["tsfm_band_width"] = tsf["tsfm_band_width"]
        return row

    # --- verdict hysteresis (flaw #6) --------------------------------------
    @staticmethod
    def _live_recommendation(base_rec: str, live_proba: float) -> str:
        buy_in, buy_out = 0.60, 0.55
        caut_in, caut_out = 0.40, 0.45
        if base_rec == "constructive":
            if live_proba >= buy_out:
                return "constructive"
            return "cautious" if live_proba <= caut_in else "neutral"
        if base_rec == "cautious":
            if live_proba <= caut_out:
                return "cautious"
            return "constructive" if live_proba >= buy_in else "neutral"
        # neutral base: standard thresholds
        if live_proba >= buy_in:
            return "constructive"
        if live_proba <= caut_in:
            return "cautious"
        return "neutral"

    def _freshness(self, age_hours: float, move: float, soft_warn: bool) -> Dict:
        age_pen = min(0.3, (age_hours / self.policy.max_age_hours) * 0.3)
        move_pen = min(0.3, abs(move) * 0.5)
        extra = 0.2 if soft_warn else 0.0
        factor = max(0.2, 1.0 - age_pen - move_pen - extra)
        return {"age_hours": round(age_hours, 2), "move_since_base": round(move, 4),
                "soft_move_warning": soft_warn, "confidence_factor": round(factor, 4)}

    # --- waterfall ---------------------------------------------------------
    @staticmethod
    def _waterfall(base: Dict, live: Dict, dcf_base: Optional[float],
                   dcf_live: Optional[float], tsfm_base: Optional[float] = None,
                   tsfm_live: Optional[float] = None) -> List[Dict]:
        rows: List[Dict] = []

        def add(component, b, c, reason):
            if b is None or c is None:
                return
            rows.append({"component": component, "base": round(b, 4),
                         "current": round(c, 4), "delta": round(c - b, 4),
                         "reason": reason})

        add("outperform_probability", base["outperform_probability"],
            live["outperform_probability"], "model re-run on live-updated features")
        add("composite_score", base["composite_score"], live["composite_score"],
            "re-aggregated from updated components")
        bss, lss = base.get("signal_scores", {}), live.get("signal_scores", {})
        add("valuation", bss.get("valuation"), lss.get("valuation"),
            "price move changed valuation attractiveness")
        add("momentum", bss.get("momentum"), lss.get("momentum"),
            "price move changed momentum")
        add("timeseries", bss.get("timeseries"), lss.get("timeseries"),
            "TimesFM forward return recomputed vs live price")
        if dcf_base is not None and dcf_live is not None:
            rows.append({"component": "dcf_upside", "base": round(dcf_base, 4),
                         "current": round(dcf_live, 4),
                         "delta": round(dcf_live - dcf_base, 4),
                         "reason": "intrinsic value fixed; upside recomputed vs live price"})
        if tsfm_base is not None and tsfm_live is not None:
            rows.append({"component": "tsfm_expected_return", "base": round(tsfm_base, 4),
                         "current": round(tsfm_live, 4),
                         "delta": round(tsfm_live - tsfm_base, 4),
                         "reason": "TimesFM bands fixed; forward return recomputed vs live price"})
        return rows

    # --- public ------------------------------------------------------------
    def reflex(self, snapshot: BrainSnapshot, live: LiveInputs,
               now_iso: Optional[str] = None) -> Dict:
        now_iso = now_iso or live.as_of
        age_hours = _age_hours(snapshot.computed_at, now_iso)
        base_contract = snapshot.base_contract

        adj_price, input_issues = self._adjusted_price(snapshot, live)
        move = (adj_price / snapshot.base_price - 1.0) if snapshot.base_price else 0.0

        result = {
            "ticker": snapshot.ticker,
            "as_of_date": snapshot.as_of_date,
            "computed_at": snapshot.computed_at,
            "live_as_of": now_iso,
            "model_name": snapshot.model_name,
            "model_version": snapshot.model_version,
            "base": base_contract,
            "valuation": {
                "base_price": snapshot.base_price,
                "live_price": round(adj_price, 4),
                "status": snapshot.valuation_status,
                "business_type": snapshot.business_type,
                "intrinsic_value_low": snapshot.intrinsic_value_low,
                "intrinsic_value_mid": snapshot.intrinsic_value_mid,
                "intrinsic_value_high": snapshot.intrinsic_value_high,
                "dcf_upside_at_base": snapshot.dcf_upside_at_base,
                "margin_of_safety_base": snapshot.margin_of_safety_base,
                "valuation_score": snapshot.valuation_score,
                "method_breakdown": snapshot.valuation_method_breakdown,
                "reverse_dcf": snapshot.reverse_dcf,
            },
            "business": {
                "business_type": snapshot.business_type,
                "type_scores": snapshot.business_type_scores,
                "classification_confidence": snapshot.business_classification_confidence,
                "classification_reason": snapshot.business_classification_reason,
            },
            "reconciliation": snapshot.reconciliation,
            "disclaimer": DISCLAIMER,
        }

        # Bad input: never show a confident live number.
        if input_issues:
            result.update({"status": STATUS_BAD_INPUT, "live": None,
                           "reasons": input_issues, "recompute_requested": True,
                           "confidence_score": 0.0, "waterfall": []})
            return self._finalize(result)

        # Anchor-breaking conditions -> invalidate, show base, queue recompute.
        reasons = self._invalidation_reasons(snapshot, live, age_hours)
        if reasons:
            status = STATUS_STALE if all(x.startswith("stale_age") for x in reasons) else STATUS_INVALID
            result.update({
                "status": status, "live": None, "reasons": reasons,
                "recompute_requested": True,
                # Confidence in the BASE drops, but the base score is unchanged.
                "confidence_score": round(base_contract.get("confidence_score", 0.0)
                                          * max(0.2, 1.0 - min(1.0, age_hours / self.policy.max_age_hours)), 4),
                "waterfall": [],
            })
            return self._finalize(result)

        # Valid regime -> recompute features and RE-RUN the model.
        updated_row = self._recompute_row(snapshot, adj_price, live)
        live_contract = self.engine.predict_ticker(updated_row, snapshot.ticker,
                                                    now_iso or snapshot.as_of_date)
        dcf_live = val.dcf_upside(snapshot.intrinsic_value_mid, adj_price) \
            if snapshot.intrinsic_value_mid is not None else None
        live_valuation_score = None if dcf_live is None else max(0.0, min(100.0, 50.0 + dcf_live * 100.0))
        _apply_valuation_score(live_contract, live_valuation_score)
        # TimesFM forward forecast recomputed vs the live price (bands fixed).
        tsfm_live_block = ts.forecast_block(snapshot.timesfm_bands, adj_price,
                                            snapshot.timesfm_model_version) \
            if snapshot.timesfm_bands else None
        tsfm_base_er = (snapshot.timeseries_forecast or {}).get("expected_return")
        tsfm_live_er = tsfm_live_block.get("expected_return") if tsfm_live_block else None

        soft_warn = abs(move) >= self.policy.soft_move_warn_pct
        fresh = self._freshness(age_hours, move, soft_warn)
        # Score is the recomputed estimate; freshness only scales confidence.
        confidence = round(live_contract["confidence_score"] * fresh["confidence_factor"], 4)
        live_rec = self._live_recommendation(base_contract.get("recommendation", "neutral"),
                                             live_contract["outperform_probability"])

        live_block = {
            "outperform_probability": live_contract["outperform_probability"],
            "recommendation": live_rec,
            "composite_score": live_contract["composite_score"],
            "signal_scores": live_contract["signal_scores"],
            "risk_score": live_contract["risk_score"],
            "dcf_upside": round(dcf_live, 4) if dcf_live is not None else None,
            "live_price": round(adj_price, 4),
            "timeseries_forecast": tsfm_live_block,
            "drivers": live_contract["drivers"],
        }
        result["valuation"]["dcf_upside_live"] = round(dcf_live, 4) if dcf_live is not None else None
        result["valuation"]["valuation_score_live"] = round(live_valuation_score, 2) \
            if live_valuation_score is not None else None
        result["reconciliation_live"] = rec.reconcile_value_price(
            {
                "status": snapshot.valuation_status,
                "margin_of_safety_base": dcf_live,
                "valuation_score": live_valuation_score,
            },
            None,
            risk_score=live_contract.get("risk_score"),
        )
        result["timeseries"] = {
            "base": snapshot.timeseries_forecast,
            "live": tsfm_live_block,
        }
        result.update({
            "status": STATUS_LIVE,
            "live": live_block,
            "reasons": ["soft_move_warning"] if soft_warn else [],
            "recompute_requested": False,
            "confidence_score": confidence,
            "freshness": fresh,
            "waterfall": self._waterfall(base_contract, live_block,
                                         snapshot.dcf_upside_at_base, dcf_live,
                                         tsfm_base_er, tsfm_live_er),
        })
        return self._finalize(result)

    def _finalize(self, result: Dict) -> Dict:
        # Ledger hook: emit the LIVE-adjusted decision the user actually saw.
        # Ledger failure must never break user-facing behavior (AGENTS.md).
        if self.emit_fn is not None:
            try:
                self.emit_fn(result)
            except Exception:  # noqa: BLE001 - intentionally swallow
                pass
        return result
