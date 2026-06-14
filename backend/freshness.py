"""
Data Trust Layer - freshness policy registry and ``assess()``.

This is the single place that decides whether a value is "fresh enough" for its
data class. Producers call :func:`assess` with the value's effective time
(``as_of``) and/or fetch time (``captured_at``) and get back a
:class:`backend.schemas.DataFreshness` envelope to attach to their payload.

Two staleness modes:
- ``age``     : compare ``captured_at`` (or ``as_of``) to wall-clock ``now`` and
                a max age in seconds (e.g. a live quote must be < ~60s old).
- ``session`` : compare the value's ``as_of`` *date* to the real last completed
                trading session from :mod:`backend.market_calendar` (e.g. EOD
                movers are stale once a newer session has closed).

All thresholds are env-tunable so production can tighten/loosen without a deploy
of code logic. Defaults are deliberately forgiving enough to avoid false
positives from normal ingestion lag while still catching multi-day/year gaps.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional, Union

from .schemas import DataFreshness, FreshnessTier

_TimeLike = Union[datetime, date, str, None]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


@dataclass(frozen=True)
class FreshnessPolicy:
    data_class: str
    tier: FreshnessTier
    mode: str                       # "age" | "session" | "never"
    max_age_s: Optional[float] = None      # for mode="age"
    tolerance_days: int = 0                # for mode="session"


def _policies() -> dict[str, FreshnessPolicy]:
    """Build the registry fresh each call so env overrides are honored in tests."""
    daily_tol = max(0, _env_int("DAILY_BRIEF_STALE_TOLERANCE_DAYS", 2))
    return {
        # Clock-age classes
        "live_quote": FreshnessPolicy("live_quote", FreshnessTier.LIVE, "age", _env_float("FRESHNESS_LIVE_QUOTE_MAX_S", 60.0)),
        "delayed_quote": FreshnessPolicy("delayed_quote", FreshnessTier.DELAYED, "age", _env_float("FRESHNESS_DELAYED_QUOTE_MAX_S", 900.0)),
        "macro_fred": FreshnessPolicy("macro_fred", FreshnessTier.EOD, "age", _env_float("FRESHNESS_MACRO_FRED_MAX_S", 36 * 3600.0)),
        "prediction_market": FreshnessPolicy("prediction_market", FreshnessTier.DELAYED, "age", _env_float("FRESHNESS_PREDICTION_MARKET_MAX_S", 900.0)),
        "model_forecast": FreshnessPolicy("model_forecast", FreshnessTier.HISTORICAL, "age", _env_float("FRESHNESS_MODEL_FORECAST_MAX_S", 24 * 3600.0)),
        # Freshly-computed analytics over a historical window (backtests, scorecards):
        # what matters is *when it was computed*, anchored on captured_at=now.
        "backtest": FreshnessPolicy("backtest", FreshnessTier.HISTORICAL, "age", _env_float("FRESHNESS_BACKTEST_MAX_S", 24 * 3600.0)),
        "scorecard": FreshnessPolicy("scorecard", FreshnessTier.HISTORICAL, "age", _env_float("FRESHNESS_SCORECARD_MAX_S", 24 * 3600.0)),
        # Session-anchored classes
        "session_pct": FreshnessPolicy("session_pct", FreshnessTier.EOD, "session", tolerance_days=0),
        "eod_movers": FreshnessPolicy("eod_movers", FreshnessTier.EOD, "session", tolerance_days=daily_tol),
        "daily_brief": FreshnessPolicy("daily_brief", FreshnessTier.EOD, "session", tolerance_days=daily_tol),
        "fundamentals": FreshnessPolicy("fundamentals", FreshnessTier.EOD, "session", tolerance_days=max(1, _env_int("FRESHNESS_FUNDAMENTALS_TOL_DAYS", 1))),
        # Static
        "reference": FreshnessPolicy("reference", FreshnessTier.REFERENCE, "never"),
    }


# Fallback policy for unknown data classes: never crash a producer over a typo;
# treat it as a session-anchored EOD value with the default daily tolerance.
def _policy_for(data_class: str) -> FreshnessPolicy:
    reg = _policies()
    if data_class in reg:
        return reg[data_class]
    return FreshnessPolicy(data_class, FreshnessTier.EOD, "session",
                           tolerance_days=max(0, _env_int("DAILY_BRIEF_STALE_TOLERANCE_DAYS", 2)))


def _to_datetime(x: _TimeLike) -> Optional[datetime]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    if isinstance(x, date):
        return datetime(x.year, x.month, x.day, tzinfo=timezone.utc)
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(float(x), tz=timezone.utc)
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime(*[int(p) for p in s.split("-")[:3]], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                return None
    return None


def _to_date(x: _TimeLike) -> Optional[date]:
    dt = _to_datetime(x)
    return dt.date() if dt is not None else None


def _iso(x: _TimeLike) -> Optional[str]:
    if x is None:
        return None
    if isinstance(x, datetime):
        return x.isoformat()
    if isinstance(x, date):
        return x.isoformat()
    return str(x)


def assess_spot(
    source: str,
    *,
    as_of: _TimeLike = None,
    captured_at: _TimeLike = None,
    degraded: bool = False,
    now: Optional[datetime] = None,
) -> DataFreshness:
    """Freshness for a value fetched live *now* (quotes, VIX, spot fundamentals).

    Picks ``live_quote`` during a regular session and ``delayed_quote`` otherwise,
    defaulting ``captured_at`` to now (the value was just fetched).
    """
    from .market_calendar import SESSION_REGULAR, session_status

    klass = "live_quote" if session_status() == SESSION_REGULAR else "delayed_quote"
    cap = captured_at if captured_at is not None else (now or datetime.now(timezone.utc))
    return assess(data_class=klass, source=source, as_of=as_of,
                  captured_at=cap, degraded=degraded, now=now)


def assess(
    *,
    data_class: str,
    source: str,
    as_of: _TimeLike = None,
    captured_at: _TimeLike = None,
    degraded: bool = False,
    tier: Optional[FreshnessTier] = None,
    now: Optional[datetime] = None,
    note: Optional[str] = None,
) -> DataFreshness:
    """Assess a value against its data-class policy and return a DataFreshness envelope."""
    policy = _policy_for(data_class)
    now_dt = now if now is not None else datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    resolved_tier = tier or policy.tier
    is_stale = False
    staleness_seconds: Optional[float] = None
    expected_as_of: Optional[str] = None
    policy_max_age_s: Optional[float] = policy.max_age_s

    if policy.mode == "never":
        resolved_tier = FreshnessTier.REFERENCE

    elif policy.mode == "age":
        ref = _to_datetime(captured_at) or _to_datetime(as_of)
        if ref is None:
            # No timestamp on an age-policed value => cannot vouch for it.
            is_stale = True
        else:
            age = (now_dt - ref).total_seconds()
            staleness_seconds = max(0.0, age)
            if policy.max_age_s is not None and age > policy.max_age_s:
                is_stale = True

    elif policy.mode == "session":
        from .market_calendar import last_completed_session

        expected = last_completed_session()
        expected_as_of = expected.isoformat()
        as_of_date = _to_date(as_of)
        if as_of_date is None:
            is_stale = True
        else:
            behind_days = (expected - as_of_date).days
            staleness_seconds = max(0.0, behind_days * 86400.0)
            if behind_days > policy.tolerance_days:
                is_stale = True

    return DataFreshness(
        data_class=data_class,
        source=source,
        tier=resolved_tier,
        as_of=_iso(as_of),
        captured_at=_iso(captured_at),
        expected_as_of=expected_as_of,
        is_stale=bool(is_stale),
        staleness_seconds=staleness_seconds,
        degraded=bool(degraded),
        policy_max_age_s=policy_max_age_s,
        note=note,
    )
