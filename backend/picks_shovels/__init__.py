"""
Picks & Shovels Momentum Finder.

A stock-discovery engine that ranks U.S.-listed companies benefiting from major
demand-cycle momentum — especially "picks-and-shovels" suppliers (memory, optical,
power, cooling, semiconductor equipment, grid, energy, cybersecurity, ...).

This package is intentionally additive: it clones the proven Actionable Companies
screener architecture (async batch scan -> SQLite snapshot -> ranked rows ->
Decision-Outcome Ledger emit) and layers on a picks-and-shovels theme taxonomy, a
cross-sectional 7-component momentum score, and deterministic anti-hallucination
explanations. Nothing here mutates existing scoring or data paths.
"""
from __future__ import annotations

__all__ = [
    "themes",
    "scoring",
    "data",
    "store",
    "explain",
    "engine",
    "ledger",
]
