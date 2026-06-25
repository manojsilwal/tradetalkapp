"""Offline DCF / reverse-DCF — produces the intrinsic-value anchors the Reflex
layer needs.

The intrinsic value is computed ONCE by the nightly brain and stored as an
anchor. The Reflex layer then derives live valuation attractiveness as
``intrinsic / live_price - 1`` (the headline NVDA example) without re-running a
DCF per request. Pure-NumPy/stdlib, deterministic, no I/O.

CRITICAL (analyst flaw #3): the intrinsic anchor itself depends on fundamentals
AND the discount rate. When those inputs move (guidance change, rate move), the
anchor is stale and the Reflex layer must invalidate — not nudge a score.
"""
from __future__ import annotations

from typing import Dict, Optional

from backend import dcf_engine


def dcf_value(fcf0: float, growth: float, years: int, terminal_growth: float,
              discount_rate: float) -> float:
    """Two-stage DCF present value (per-share if ``fcf0`` is per-share FCF).

    Stage 1: ``years`` of explicit growth at ``growth``.
    Stage 2: Gordon terminal value at ``terminal_growth``.
    Requires ``discount_rate > terminal_growth`` (else the terminal value is
    undefined / negative — a classic DCF foot-gun we refuse to fabricate).

    Delegates to the shared :mod:`backend.dcf_engine` so the Brain and the
    Decision Terminal share one cash-flow core (the flat-growth case is just a
    constant ``growth_path``).
    """
    return dcf_engine.constant_growth_value(
        fcf0, growth, years, terminal_growth, discount_rate
    )


def intrinsic_range(fcf0: float, growth: float, years: int = 5,
                    terminal_growth: float = 0.025, discount_rate: float = 0.09,
                    growth_band: float = 0.02, discount_band: float = 0.01) -> Dict[str, float]:
    """Bear/base/bull intrinsic value via small bands on growth & discount rate.

    Returns sorted low <= mid <= high so downstream code never sees an inverted
    range.
    """
    mid = dcf_value(fcf0, growth, years, terminal_growth, discount_rate)
    # Bear: lower growth, higher discount. Bull: higher growth, lower discount.
    low = dcf_value(fcf0, growth - growth_band, years, terminal_growth,
                    discount_rate + discount_band)
    high = dcf_value(fcf0, growth + growth_band, years, terminal_growth,
                     max(terminal_growth + 1e-4, discount_rate - discount_band))
    lo, hi = sorted((low, high))
    mid = min(max(mid, lo), hi)
    return {"intrinsic_value_low": round(lo, 4),
            "intrinsic_value_mid": round(mid, 4),
            "intrinsic_value_high": round(hi, 4)}


def reverse_dcf(target_value: float, fcf0: float, years: int = 5,
                terminal_growth: float = 0.025, discount_rate: float = 0.09,
                lo: float = -0.5, hi: float = 1.0, iters: int = 100) -> Optional[float]:
    """Solve for the growth rate the market price implies (bisection).

    ``dcf_value`` is monotonically increasing in growth, so bisection converges.
    Returns the implied growth, or None if ``target_value`` is outside the
    bracket's reachable range. Delegates to the shared engine.
    """
    return dcf_engine.reverse_dcf_growth(
        target_value, fcf0,
        years=years, terminal_growth=terminal_growth, discount_rate=discount_rate,
        lo=lo, hi=hi, iters=iters,
    )


def dcf_upside(intrinsic_value: float, price: float) -> Optional[float]:
    """Upside to intrinsic value: intrinsic / price - 1. None if price <= 0."""
    if price is None or price <= 0 or intrinsic_value is None:
        return None
    return float(intrinsic_value) / float(price) - 1.0


def equity_to_ev(market_cap: float, total_debt: float, cash: float) -> float:
    """Equity share of enterprise value, used to dampen how EV/EBITDA scales with
    price (a price move only changes the equity portion of EV, not net debt)."""
    ev = market_cap + total_debt - cash
    if ev <= 0:
        return 1.0
    return float(market_cap) / float(ev)
