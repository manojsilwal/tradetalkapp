"""
Theme taxonomy for the Narrative Rotation Radar.

Reuses the curated Picks & Shovels taxonomy (``backend/picks_shovels/themes.py``)
as the seed universe so the two surfaces stay consistent, and layers theme-level
metadata the lifecycle engine needs:

  - ``THEMES``           ordered theme descriptors (id, label, color, bottleneck)
  - ``theme_members``    theme_id -> [tickers] (the basket)
  - ``theme_universe``   union of every basket ticker (the MVP scan universe)
  - ``KEYWORDS``         theme_id -> narrative keyword dictionary (Plan §18)

The keyword dictionaries are used by the (later) narrative/media engine; they are
defined here so the taxonomy is the single source of truth.
"""
from __future__ import annotations

from typing import Dict, List

from ..picks_shovels import themes as _ps

# Re-export the Picks & Shovels theme descriptors and membership so the radar and
# the company screener share one taxonomy.
THEMES: List[Dict[str, object]] = _ps.THEMES
THEME_MEMBERS: Dict[str, List[str]] = _ps.THEME_MEMBERS
SEED_UNIVERSE: List[str] = _ps.SEED_UNIVERSE

theme_label = _ps.theme_label
theme_bottleneck = _ps.theme_bottleneck
theme_ids = _ps.theme_ids


# ── Narrative keyword dictionaries (Plan §18) ────────────────────────────────
# Used by the narrative/media engine (NR-7) to count theme mentions in news/filings.
KEYWORDS: Dict[str, List[str]] = {
    "ai_compute": [
        "artificial intelligence", "AI infrastructure", "GPU", "accelerated computing",
        "inference", "training workload", "AI accelerator", "custom silicon",
    ],
    "memory_hbm": [
        "high bandwidth memory", "HBM", "DRAM", "NAND", "memory capacity", "data center memory",
    ],
    "optical": [
        "optical interconnect", "transceiver", "800G", "1.6T", "silicon photonics", "optical networking",
    ],
    "ai_networking": [
        "switching fabric", "networking ASIC", "Ethernet fabric", "InfiniBand", "custom ASIC",
    ],
    "semi_equipment": [
        "wafer fab", "advanced packaging", "CoWoS", "lithography", "semiconductor equipment", "test capacity",
    ],
    "power_infra": [
        "data center power", "switchgear", "electrical distribution", "power capacity", "grid power",
    ],
    "cooling": [
        "liquid cooling", "thermal management", "immersion cooling", "rack cooling",
    ],
    "data_center_re": [
        "data center", "colocation", "hyperscaler capex", "compute hosting", "data center capacity",
    ],
    "grid_construction": [
        "grid build-out", "electrical construction", "transmission", "interconnection queue",
    ],
    "energy_utilities": [
        "nuclear", "baseload", "power generation", "utility", "data center electricity demand",
    ],
    "pcb_connectors": [
        "printed circuit board", "PCB", "connector", "high speed connector", "components",
    ],
    "cybersecurity": [
        "cybersecurity", "observability", "data infrastructure", "cloud security", "zero trust",
    ],
}


def theme_members(theme_id: str) -> List[str]:
    """Basket tickers for a theme (empty if unknown)."""
    return list(THEME_MEMBERS.get(theme_id, []))


def theme_universe() -> List[str]:
    """Union of every basket ticker across all themes (the scan universe)."""
    out: List[str] = []
    seen: set = set()
    for tid in theme_ids():
        for tk in theme_members(tid):
            u = tk.upper()
            if u not in seen:
                seen.add(u)
                out.append(u)
    return out


def theme_keywords(theme_id: str) -> List[str]:
    return list(KEYWORDS.get(theme_id, []))


def validate_taxonomy() -> None:
    """Raise if the radar taxonomy is internally inconsistent."""
    _ps.validate_taxonomy()
    ids = set(theme_ids())
    for tid in ids:
        if not theme_members(tid):
            raise ValueError(f"theme {tid!r} has no basket members")
    # KEYWORDS may be a subset, but every keyworded theme must be a real theme.
    for tid in KEYWORDS:
        if tid not in ids:
            raise ValueError(f"KEYWORDS references unknown theme {tid!r}")
