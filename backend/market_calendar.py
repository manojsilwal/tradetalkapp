"""
Single source of truth for the US cash-equity (NYSE/Nasdaq) trading calendar.

Why this exists: freshness/"as of" logic must know the *real* last completed
trading session, which requires weekend **and** holiday awareness. Previously
several modules hand-rolled weekend math and one module carried a hardcoded
holiday set that expired at the end of 2026. This module **computes** the
standard NYSE full-day holidays from their defining rules, so it never expires
and there is exactly one calculator to reason about.

Scope and honesty notes:
- Covers the 10 recurring NYSE full-day closures with their official observance
  rules (Sat -> preceding Friday, Sun -> following Monday; New Year's Day is the
  one exception: a Saturday New Year's is **not** observed on the prior Friday).
- Good Friday is derived from the Gregorian Easter computus.
- It does **not** model rare ad-hoc closures (e.g. national days of mourning,
  weather closures) or intraday early-close half days. Those affect intraday
  state, not the date of the last *completed* session, which is what the
  freshness layer needs. If exact ad-hoc handling is ever required, swap the
  holiday source for ``pandas_market_calendars`` behind these same functions.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

__all__ = [
    "is_market_holiday",
    "is_trading_day",
    "previous_trading_day",
    "adjust_to_trading_day",
    "last_completed_session",
    "us_market_holidays",
    "now_et",
    "session_status",
    "is_market_open",
]

# Regular US cash-equity session bounds, in ET minutes-since-midnight.
_REGULAR_OPEN_MIN = 9 * 60 + 30   # 09:30 ET
_REGULAR_CLOSE_MIN = 16 * 60      # 16:00 ET

# Session status values. Kept as plain strings so callers can map them onto
# their own (narrower) public contracts without importing an enum.
SESSION_REGULAR = "regular"
SESSION_PRE_MARKET = "pre_market"
SESSION_POST_MARKET = "post_market"
SESSION_CLOSED_WEEKEND = "closed_weekend"
SESSION_CLOSED_HOLIDAY = "closed_holiday"

# Juneteenth National Independence Day became a US federal holiday (and an NYSE
# market holiday) starting in 2021. Before then it was a normal trading day.
_JUNETEENTH_FIRST_YEAR = 2021


def now_et() -> datetime:
    """Current time in US Eastern, falling back to UTC if tz data is missing."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        try:
            from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

            return datetime.now(ZoneInfo("America/New_York"))
        except Exception:
            return datetime.now(timezone.utc)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th ``weekday`` (Mon=0 .. Sun=6) of ``month`` in ``year`` (n>=1)."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` (Mon=0 .. Sun=6) of ``month`` in ``year``."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter_sunday(year: int) -> date:
    """Gregorian Easter Sunday via the Anonymous (Meeus/Jones/Butcher) algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed_std(d: date) -> date:
    """Standard NYSE observance: Saturday -> preceding Friday, Sunday -> following Monday."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=256)
def us_market_holidays(year: int) -> frozenset[date]:
    """Compute the set of NYSE full-day holiday dates observed in ``year``.

    Derived from the holidays' defining rules (no hardcoded year list), so this
    is correct for any past or future year.
    """
    holidays: set[date] = set()

    # New Year's Day (Jan 1) — special observance: Sunday -> Monday, but a
    # Saturday New Year's is NOT observed on the preceding Friday.
    ny = date(year, 1, 1)
    if ny.weekday() == 6:  # Sunday -> observed Monday Jan 2
        holidays.add(ny + timedelta(days=1))
    elif ny.weekday() != 5:  # weekday; Saturday gets no observance
        holidays.add(ny)

    # Martin Luther King Jr. Day — 3rd Monday of January.
    holidays.add(_nth_weekday(year, 1, 0, 3))
    # Washington's Birthday / Presidents' Day — 3rd Monday of February.
    holidays.add(_nth_weekday(year, 2, 0, 3))
    # Good Friday — Friday before Easter Sunday.
    holidays.add(_easter_sunday(year) - timedelta(days=2))
    # Memorial Day — last Monday of May.
    holidays.add(_last_weekday(year, 5, 0))
    # Juneteenth — June 19 (from 2021), standard observance.
    if year >= _JUNETEENTH_FIRST_YEAR:
        holidays.add(_observed_std(date(year, 6, 19)))
    # Independence Day — July 4, standard observance.
    holidays.add(_observed_std(date(year, 7, 4)))
    # Labor Day — 1st Monday of September.
    holidays.add(_nth_weekday(year, 9, 0, 1))
    # Thanksgiving — 4th Thursday of November.
    holidays.add(_nth_weekday(year, 11, 3, 4))
    # Christmas Day — December 25, standard observance.
    holidays.add(_observed_std(date(year, 12, 25)))

    return frozenset(holidays)


def is_market_holiday(d: date) -> bool:
    """True if ``d`` is an observed NYSE full-day holiday."""
    return d in us_market_holidays(d.year)


def is_trading_day(d: date) -> bool:
    """True if ``d`` is a weekday that is not an observed market holiday."""
    return d.weekday() < 5 and not is_market_holiday(d)


def previous_trading_day(d: date) -> date:
    """The most recent trading day strictly before ``d``."""
    candidate = d - timedelta(days=1)
    for _ in range(15):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate


def adjust_to_trading_day(d: date) -> date:
    """Return ``d`` if it is a trading day, else the most recent prior trading day.

    Holiday-aware replacement for the old weekend-only ``_adjust_weekend_to_friday``.
    """
    candidate = d
    for _ in range(15):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate


def session_status(now: Optional[datetime] = None) -> str:
    """Classify the current US cash-equity session.

    Returns one of ``SESSION_REGULAR``, ``SESSION_PRE_MARKET``,
    ``SESSION_POST_MARKET``, ``SESSION_CLOSED_WEEKEND``, ``SESSION_CLOSED_HOLIDAY``.
    """
    et = now if now is not None else now_et()
    d = et.date()
    if d.weekday() >= 5:
        return SESSION_CLOSED_WEEKEND
    if is_market_holiday(d):
        return SESSION_CLOSED_HOLIDAY
    minutes = et.hour * 60 + et.minute
    if minutes < _REGULAR_OPEN_MIN:
        return SESSION_PRE_MARKET
    if minutes >= _REGULAR_CLOSE_MIN:
        return SESSION_POST_MARKET
    return SESSION_REGULAR


def is_market_open(now: Optional[datetime] = None) -> bool:
    """True only during the regular session (09:30-16:00 ET on a trading day)."""
    return session_status(now) == SESSION_REGULAR


def last_completed_session(today: Optional[date] = None) -> date:
    """The real last *completed* US cash-equity trading session.

    When ``today`` is omitted, uses Eastern Time: before the 16:00 ET cash close
    on a trading day, the last completed session is the previous trading day.
    When ``today`` is provided, the session for that date is assumed already
    closed (callers pass an explicit date when they mean an end-of-day context).
    """
    if today is None:
        et = now_et()
        today = et.date()
        before_close = (et.hour * 60 + et.minute) < (16 * 60)
    else:
        before_close = False

    candidate = today - timedelta(days=1) if before_close else today
    for _ in range(15):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate
