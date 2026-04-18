"""
Outcome grader (Harness Engineering — Phase 2).

Walks the Decision-Outcome Ledger and writes multi-horizon ``outcome_observations``
rows for every decision whose horizon has elapsed and has not been graded yet.
This is the "market truth" oracle that transforms raw LLM decisions into the
correlation data the finance moat is built on.

For each ungraded ``decision_events`` row at horizon H we record up to three
observations:

* ``abs_return``      — stock total return from ``created_at`` → T+H (close-to-close)
* ``excess_return``   — ``abs_return`` minus the SPY benchmark over the same window
* ``risk_adjusted``   — ``excess_return`` divided by realised daily-return volatility

``correct`` is only populated on ``excess_return`` rows and is derived from the
decision's ``verdict``: BUY / STRONG BUY → correct iff excess > 0,
SELL / STRONG SELL → correct iff excess < 0, everything else → None so the row
stays in the ledger as an unlabelled signal (useful for correlation mining but
not precision/recall).

This module replaces ``backend.daily_pipeline._track_swarm_outcomes`` as the
single source of truth for outcome grading. ``_track_swarm_outcomes`` still
runs today but its 1-horizon / yfinance-only logic will be removed once this
grader is wired into the scheduler (see the ``scheduler_hook`` todo).

Design constraints
------------------

* Never touches the network in tests: price fetching is abstracted behind
  :class:`PriceProvider`. The default :class:`YFinancePriceProvider` wraps
  ``yfinance`` and is used in production only.
* Never raises: every failure is logged and the decision is skipped so a
  broken symbol never stops grading the rest of the batch.
* Thread-safe against the SQLite ledger: uses ``decision_ledger.record_outcome``
  which takes the shared write lock already.
* Idempotent: repeated ``INSERT OR REPLACE`` on
  ``(decision_id, horizon, metric)`` means a replay run just refreshes values.
"""

from __future__ import annotations

import logging
import math
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import decision_ledger as _dl

logger = logging.getLogger(__name__)


# ── Horizons ────────────────────────────────────────────────────────────────

# Mapping: ledger horizon label → (approx. calendar days to wait before grading,
# trailing-window days for volatility). The calendar-day lookahead is slightly
# larger than trading-day count so we never grade a decision before its close.
HORIZONS: Dict[str, Tuple[int, int]] = {
    "1d": (2, 21),
    "5d": (8, 21),
    "21d": (32, 42),
    "63d": (95, 90),
}

DEFAULT_BENCHMARK = "SPY"
_BUY_VERDICTS = {"BUY", "STRONG BUY"}
_SELL_VERDICTS = {"SELL", "STRONG SELL"}


# ── Price provider (injectable, so tests never hit the network) ─────────────


class PriceProvider(ABC):
    """Minimal close-price + volatility interface the grader depends on."""

    @abstractmethod
    def close_price(self, symbol: str, as_of: datetime) -> Optional[float]:
        """Return the close price on the trading day on/before ``as_of``."""

    def trailing_vol(self, symbol: str, end: datetime, window_days: int) -> Optional[float]:
        """Std. dev. of daily close-to-close returns over trailing ``window_days``.

        Returns a *daily* volatility (not annualised). ``None`` if insufficient
        history. Default implementation falls back to a synthesized series from
        :meth:`close_price` — providers with native history APIs should override.
        """
        closes: List[float] = []
        day = end
        collected = 0
        # Pull window_days close prices by walking back. This is expensive for
        # the default provider but fine for typical 42 / 90 window sizes.
        while collected < window_days:
            p = self.close_price(symbol, day)
            if p is not None:
                closes.append(p)
                collected += 1
            day = day - timedelta(days=1)
            if day < end - timedelta(days=window_days * 3):
                break
        if len(closes) < 3:
            return None
        closes.reverse()
        rets: List[float] = []
        for i in range(1, len(closes)):
            if closes[i - 1] <= 0:
                continue
            rets.append(closes[i] / closes[i - 1] - 1.0)
        if len(rets) < 2:
            return None
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(max(0.0, var))


class YFinancePriceProvider(PriceProvider):
    """Production provider — yfinance wrapped so SQLite/grader tests stay offline.

    We intentionally keep the import lazy so the grader module can be imported
    (and smoke-tested) in CI containers that don't have yfinance installed.
    """

    def __init__(self, cache_ttl_s: float = 300.0) -> None:
        self._cache: Dict[Tuple[str, str], Tuple[float, float]] = {}
        self._ttl = float(cache_ttl_s)

    def _yf(self):
        import yfinance as yf  # lazy import
        return yf

    def close_price(self, symbol: str, as_of: datetime) -> Optional[float]:
        if not symbol:
            return None
        key = (symbol.upper(), as_of.date().isoformat())
        now = time.time()
        cached = self._cache.get(key)
        if cached and (now - cached[1]) < self._ttl:
            return cached[0] if cached[0] > 0 else None
        try:
            yf = self._yf()
            start = as_of - timedelta(days=7)
            # end is exclusive in yfinance; pad by one day so as_of is included.
            end = as_of + timedelta(days=1)
            hist = yf.Ticker(symbol).history(
                start=start.date().isoformat(),
                end=end.date().isoformat(),
                auto_adjust=False,
            )
            if hist is None or hist.empty:
                self._cache[key] = (0.0, now)
                return None
            closes = hist["Close"]
            px = float(closes.iloc[-1])
            self._cache[key] = (px, now)
            return px if px > 0 else None
        except Exception as e:
            logger.warning(
                "[OutcomeGrader] yfinance close_price failed %s@%s: %s",
                symbol, as_of.date(), e,
            )
            return None

    def trailing_vol(self, symbol: str, end: datetime, window_days: int) -> Optional[float]:
        if not symbol:
            return None
        try:
            yf = self._yf()
            start = end - timedelta(days=int(window_days * 1.6))
            hist = yf.Ticker(symbol).history(
                start=start.date().isoformat(),
                end=(end + timedelta(days=1)).date().isoformat(),
                auto_adjust=False,
            )
            if hist is None or hist.empty or len(hist) < 3:
                return None
            closes = hist["Close"].tolist()
            rets = []
            for i in range(1, len(closes)):
                if closes[i - 1] <= 0:
                    continue
                rets.append(closes[i] / closes[i - 1] - 1.0)
            if len(rets) < 2:
                return None
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
            return math.sqrt(max(0.0, var))
        except Exception as e:
            logger.warning("[OutcomeGrader] yfinance trailing_vol failed %s: %s", symbol, e)
            return None


# ── Grader ──────────────────────────────────────────────────────────────────


@dataclass
class GradingReport:
    """Return shape from :meth:`OutcomeGrader.grade_due` — stable for logging."""

    horizon: str
    considered: int = 0
    graded: int = 0
    skipped_no_horizon_hint: int = 0
    skipped_no_symbol: int = 0
    skipped_no_price: int = 0
    skipped_other: int = 0

    def as_dict(self) -> Dict[str, int]:
        return {
            "horizon": self.horizon,
            "considered": self.considered,
            "graded": self.graded,
            "skipped_no_horizon_hint": self.skipped_no_horizon_hint,
            "skipped_no_symbol": self.skipped_no_symbol,
            "skipped_no_price": self.skipped_no_price,
            "skipped_other": self.skipped_other,
        }


class OutcomeGrader:
    """Grade ungraded ledger decisions at each horizon.

    Parameters
    ----------
    price_provider: :class:`PriceProvider` — injected so tests never hit the
        network. Defaults to :class:`YFinancePriceProvider`.
    benchmark: symbol used for excess-return calculation (default SPY).
    ledger: :class:`decision_ledger.LedgerBackend` instance. When ``None``
        falls back to the process-wide ledger singleton.
    horizons: optional override of the {label: (cal_days, vol_window_days)}
        map. Useful in tests so we can grade a "1d" decision a second later.
    respect_horizon_hint: when True (default) skip decisions whose
        ``horizon_hint`` is ``"none"`` or a mismatched horizon. Set False in
        tests if you want to grade every row against every horizon.
    """

    def __init__(
        self,
        price_provider: Optional[PriceProvider] = None,
        *,
        benchmark: str = DEFAULT_BENCHMARK,
        ledger: Optional[Any] = None,
        horizons: Optional[Dict[str, Tuple[int, int]]] = None,
        respect_horizon_hint: bool = True,
    ) -> None:
        self.price_provider = price_provider or YFinancePriceProvider()
        self.benchmark = benchmark
        self._ledger = ledger
        self.horizons = horizons or HORIZONS
        self.respect_horizon_hint = bool(respect_horizon_hint)

    def _ledger_ref(self):
        if self._ledger is not None:
            return self._ledger
        return _dl.get_ledger()

    def grade_due(self, horizon: str, *, limit: int = 500) -> GradingReport:
        """Grade every ungraded decision whose ``horizon`` window has elapsed."""
        if horizon not in self.horizons:
            raise ValueError(f"unknown horizon: {horizon}")
        cal_days, vol_window = self.horizons[horizon]
        cutoff_ts = time.time() - cal_days * 86400
        ledger = self._ledger_ref()

        # ``ungraded_decisions_for_horizon`` is exposed on SQLite + Supabase
        # backends; Null backend returns []. Keeps cross-backend parity.
        if hasattr(ledger, "ungraded_decisions_for_horizon"):
            candidates = ledger.ungraded_decisions_for_horizon(
                horizon, older_than_ts=cutoff_ts, limit=limit,
            )
        else:
            candidates = []

        report = GradingReport(horizon=horizon, considered=len(candidates))
        for ev in candidates:
            try:
                ok = self._grade_one(ev, horizon=horizon, vol_window=vol_window, report=report)
                if ok:
                    report.graded += 1
            except Exception as e:
                logger.warning(
                    "[OutcomeGrader] grade_one failed decision=%s horizon=%s: %s",
                    ev.decision_id, horizon, e,
                )
                report.skipped_other += 1
        return report

    def grade_all(self, *, limit: int = 500) -> Dict[str, GradingReport]:
        """Grade every horizon; returns ``{horizon: GradingReport}``."""
        out: Dict[str, GradingReport] = {}
        for h in self.horizons:
            out[h] = self.grade_due(h, limit=limit)
        return out

    # ── internals ──────────────────────────────────────────────────────────

    def _grade_one(
        self,
        ev: _dl.DecisionEvent,
        *,
        horizon: str,
        vol_window: int,
        report: GradingReport,
    ) -> bool:
        """Grade one decision. Returns True iff at least one row was written."""
        if self.respect_horizon_hint:
            hh = (ev.horizon_hint or "").strip().lower()
            if hh in ("", "none"):
                report.skipped_no_horizon_hint += 1
                return False
            # If the decision was stamped with a specific horizon, only grade
            # it at that horizon. Saves yfinance calls for chat turns etc.
            if hh != horizon:
                return False

        symbol = (ev.symbol or "").upper().strip()
        if not symbol:
            report.skipped_no_symbol += 1
            return False

        # Map horizon label back to trading-day count for the exit timestamp.
        # Using ~1.4x multiplier to convert trading days to calendar days.
        td_map = {"1d": 1, "5d": 5, "21d": 21, "63d": 63}
        td = td_map.get(horizon, 5)
        cal_days = int(round(td * 1.45))

        entry_dt = datetime.fromtimestamp(ev.created_at, tz=timezone.utc)
        exit_dt = entry_dt + timedelta(days=cal_days)
        pp = self.price_provider

        stock_entry = pp.close_price(symbol, entry_dt)
        stock_exit = pp.close_price(symbol, exit_dt)
        bench_entry = pp.close_price(self.benchmark, entry_dt)
        bench_exit = pp.close_price(self.benchmark, exit_dt)

        if not stock_entry or not stock_exit or stock_entry <= 0:
            report.skipped_no_price += 1
            return False

        abs_return = (stock_exit / stock_entry) - 1.0
        excess_return: Optional[float] = None
        if bench_entry and bench_exit and bench_entry > 0:
            spy_ret = (bench_exit / bench_entry) - 1.0
            excess_return = abs_return - spy_ret

        vol = pp.trailing_vol(symbol, exit_dt, window_days=vol_window)
        risk_adj: Optional[float] = None
        if excess_return is not None and vol is not None and vol > 1e-6:
            risk_adj = excess_return / vol

        correct = _grade_correctness(ev.verdict, excess_return)

        ledger = self._ledger_ref()
        now_ts = time.time()
        label_source = "market_truth_v1"

        rows: List[_dl.OutcomeObservation] = [
            _dl.OutcomeObservation(
                decision_id=ev.decision_id,
                horizon=horizon,
                metric="abs_return",
                value=abs_return,
                as_of_ts=now_ts,
                benchmark="",
                excess_return=None,
                correct=None,
                label_source=label_source,
            ),
        ]
        if excess_return is not None:
            rows.append(
                _dl.OutcomeObservation(
                    decision_id=ev.decision_id,
                    horizon=horizon,
                    metric="excess_return",
                    value=excess_return,
                    as_of_ts=now_ts,
                    benchmark=self.benchmark,
                    excess_return=excess_return,
                    correct=correct,
                    label_source=label_source,
                )
            )
        if risk_adj is not None:
            rows.append(
                _dl.OutcomeObservation(
                    decision_id=ev.decision_id,
                    horizon=horizon,
                    metric="risk_adjusted",
                    value=risk_adj,
                    as_of_ts=now_ts,
                    benchmark=self.benchmark,
                    excess_return=excess_return,
                    correct=None,
                    label_source=label_source,
                )
            )

        wrote = 0
        for r in rows:
            if ledger.record_outcome(r):
                wrote += 1
        return wrote > 0


def _grade_correctness(verdict: str, excess_return: Optional[float]) -> Optional[bool]:
    """Map (verdict, excess_return) → correctness, or None when unlabelable.

    We only label directional decisions. Everything else (NEUTRAL, empty,
    chat-turn text) stays ``correct IS NULL`` so downstream analytics can
    filter cleanly.
    """
    if excess_return is None:
        return None
    v = (verdict or "").upper().strip()
    if v in _BUY_VERDICTS:
        return excess_return > 0
    if v in _SELL_VERDICTS:
        return excess_return < 0
    return None


# ── Scheduler entry point ───────────────────────────────────────────────────


async def run_grader_pass(knowledge_store=None, llm_client=None) -> Dict[str, Any]:
    """Grade every horizon once. Safe to call from APScheduler.

    Guarded by ``DECISION_LEDGER_ENABLE`` so operators can kill-switch the
    ledger without redeploying. Never raises; returns a summary dict that
    can land in the pipeline status block.
    """
    if os.getenv("DECISION_LEDGER_ENABLE", "1") not in ("1", "true", "TRUE", "yes", "on"):
        return {"grader_enabled": False}
    try:
        grader = OutcomeGrader()
        import asyncio

        def _sync_pass():
            return grader.grade_all(limit=int(os.getenv("OUTCOME_GRADER_BATCH", "500")))

        reports = await asyncio.to_thread(_sync_pass)
        summary = {h: rep.as_dict() for h, rep in reports.items()}
        total_graded = sum(r.graded for r in reports.values())
        logger.info(
            "[OutcomeGrader] pass complete graded=%s per_horizon=%s",
            total_graded, {h: r.graded for h, r in reports.items()},
        )
        return {"grader_enabled": True, "total_graded": total_graded, "per_horizon": summary}
    except Exception as e:
        logger.warning("[OutcomeGrader] run_grader_pass failed: %s", e)
        return {"grader_enabled": True, "error": str(e)[:300]}
