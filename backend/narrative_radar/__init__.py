"""
Narrative Rotation Radar — theme-lifecycle intelligence.

Generalizes the company-level Picks & Shovels Momentum Finder
(``backend/picks_shovels``) from *companies within one theme* to
*themes within the market*: it classifies each market theme into a
lifecycle phase (seeding → accumulation → acceleration → mainstream →
saturation → distribution → exit → dormant) from observable market,
breadth, and (later) flow/narrative/13F signals.

See ``docs/NARRATIVE_ROTATION_RADAR_PLAN.md``. MVP scope = NR-1..NR-4
(taxonomy + market/breadth features + phase scoring + ledger emit + API)
using only data already in the repo (no new external dependencies).
"""
from __future__ import annotations

__all__ = [
    "themes",
    "features",
    "scoring",
    "lifecycle",
    "explain",
    "store",
    "ledger",
    "engine",
    "data",
]
