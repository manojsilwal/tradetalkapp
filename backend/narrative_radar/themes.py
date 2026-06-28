"""
Theme taxonomy for the Narrative Rotation Radar.

Reuses the curated Picks & Shovels taxonomy (``backend/picks_shovels/themes.py``)
for AI-infrastructure themes and appends radar-local **sector** and **precious metals**
groups (ETF-primary baskets).

Exports
-------
THEMES            ordered theme descriptors (id, label, color, bottleneck, group)
THEME_MEMBERS     theme_id -> [tickers]
theme_universe    union of every basket ticker (scan universe)
KEYWORDS          theme_id -> narrative keyword dictionary
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from ..picks_shovels import themes as _ps
from ..sp500_ingestion_pipeline import SECTOR_ETFS

# Theme groups for cross-sectional ranking and UI filtering.
GROUP_AI_THEME = "ai_theme"
GROUP_SECTOR = "sector"
GROUP_PRECIOUS_METALS = "precious_metals"

# AI themes from Picks & Shovels (group = ai_theme).
_AI_THEMES: List[Dict[str, object]] = [
    {**t, "group": GROUP_AI_THEME} for t in _ps.THEMES
]

# GICS sector themes — ETF-primary basket (one liquid sector ETF each).
_SECTOR_COLOR = "#64748b"
_SECTOR_THEMES: List[Dict[str, object]] = []
_SECTOR_MEMBERS: Dict[str, List[str]] = {}
for _label, _etf in SECTOR_ETFS.items():
    _slug = "sector_" + _label.lower().replace(" ", "_").replace("&", "and")
    _SECTOR_THEMES.append({
        "id": _slug,
        "label": _label,
        "color": _SECTOR_COLOR,
        "capex_directness": 50,
        "bottleneck": f"GICS { _label } sector rotation and relative strength vs SPY.",
        "group": GROUP_SECTOR,
        "sector_etf": _etf,
    })
    _SECTOR_MEMBERS[_slug] = [_etf]

# Precious metals — two themes, one ranking group.
_PM_THEMES: List[Dict[str, object]] = [
    {
        "id": "pm_gold",
        "label": "Gold",
        "color": "#eab308",
        "capex_directness": 40,
        "bottleneck": "Gold bullion and miners — safe-haven / real-yield sensitivity.",
        "group": GROUP_PRECIOUS_METALS,
    },
    {
        "id": "pm_silver",
        "label": "Silver",
        "color": "#cbd5e1",
        "capex_directness": 35,
        "bottleneck": "Silver bullion and miners — industrial + precious-metal dual narrative.",
        "group": GROUP_PRECIOUS_METALS,
    },
]
_PM_MEMBERS: Dict[str, List[str]] = {
    "pm_gold": ["GLD", "GDX"],
    "pm_silver": ["SLV", "SIL"],
}

_AI_MEMBERS: Dict[str, List[str]] = dict(_ps.THEME_MEMBERS)

# Narrative keyword dictionaries (Plan §18) — AI themes.
_KEYWORDS_AI: Dict[str, List[str]] = {
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

_KEYWORDS_SECTOR: Dict[str, List[str]] = {
    "sector_technology": ["technology sector", "tech stocks", "software", "semiconductors", "XLK"],
    "sector_financials": ["financial sector", "banks", "insurance", "XLF"],
    "sector_healthcare": ["healthcare sector", "biotech", "pharma", "XLV"],
    "sector_consumer_discretionary": ["consumer discretionary", "retail spending", "XLY"],
    "sector_consumer_staples": ["consumer staples", "defensive staples", "XLP"],
    "sector_industrials": ["industrials sector", "manufacturing", "XLI"],
    "sector_energy": ["energy sector", "oil", "gas", "XLE"],
    "sector_materials": ["materials sector", "commodities", "mining", "XLB"],
    "sector_real_estate": ["real estate sector", "REITs", "XLRE"],
    "sector_utilities": ["utilities sector", "dividend utilities", "XLU"],
    "sector_communication_services": ["communication services", "media", "telecom", "XLC"],
}

_KEYWORDS_PM: Dict[str, List[str]] = {
    "pm_gold": ["gold", "bullion", "safe haven", "real yields", "GLD", "gold miners", "GDX"],
    "pm_silver": ["silver", "industrial metal", "solar demand", "SLV", "silver miners", "SIL"],
}


def _sectors_enabled() -> bool:
    return os.environ.get("NARRATIVE_RADAR_SECTORS", "1").strip() != "0"


def _build_taxonomy() -> tuple[List[Dict[str, object]], Dict[str, List[str]], Dict[str, List[str]]]:
    themes = list(_AI_THEMES)
    members = dict(_AI_MEMBERS)
    keywords = dict(_KEYWORDS_AI)
    if _sectors_enabled():
        themes.extend(_SECTOR_THEMES)
        themes.extend(_PM_THEMES)
        members.update(_SECTOR_MEMBERS)
        members.update(_PM_MEMBERS)
        keywords.update(_KEYWORDS_SECTOR)
        keywords.update(_KEYWORDS_PM)
    return themes, members, keywords


_THEMES, _THEME_MEMBERS, _KEYWORDS = _build_taxonomy()

THEMES: List[Dict[str, object]] = _THEMES
THEME_MEMBERS: Dict[str, List[str]] = _THEME_MEMBERS
KEYWORDS: Dict[str, List[str]] = _KEYWORDS
SEED_UNIVERSE: List[str] = _ps.SEED_UNIVERSE

_THEME_BY_ID: Dict[str, Dict[str, object]] = {str(t["id"]): t for t in THEMES}


def theme_label(theme_id: str) -> str:
    t = _THEME_BY_ID.get(theme_id)
    if t:
        return str(t["label"])
    return _ps.theme_label(theme_id)


def theme_bottleneck(theme_id: str) -> str:
    t = _THEME_BY_ID.get(theme_id)
    if t:
        return str(t.get("bottleneck") or "")
    return _ps.theme_bottleneck(theme_id)


def theme_ids() -> List[str]:
    return [str(t["id"]) for t in THEMES]


def theme_group(theme_id: str) -> str:
    t = _THEME_BY_ID.get(theme_id)
    if t:
        return str(t.get("group") or GROUP_AI_THEME)
    return GROUP_AI_THEME


def groups() -> List[str]:
    seen: List[str] = []
    for t in THEMES:
        g = str(t.get("group") or GROUP_AI_THEME)
        if g not in seen:
            seen.append(g)
    return seen


def themes_for_group(group: str) -> List[str]:
    return [str(t["id"]) for t in THEMES if str(t.get("group") or GROUP_AI_THEME) == group]


def sector_etf_for(theme_id: str) -> Optional[str]:
    t = _THEME_BY_ID.get(theme_id)
    if not t:
        return None
    etf = t.get("sector_etf")
    if etf:
        return str(etf)
    members = theme_members(theme_id)
    return members[0] if members else None


def theme_members(theme_id: str) -> List[str]:
    return list(THEME_MEMBERS.get(theme_id, []))


def theme_universe() -> List[str]:
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
    for tid in KEYWORDS:
        if tid not in ids:
            raise ValueError(f"KEYWORDS references unknown theme {tid!r}")
    for tid in ids:
        g = theme_group(tid)
        if g not in (GROUP_AI_THEME, GROUP_SECTOR, GROUP_PRECIOUS_METALS):
            raise ValueError(f"theme {tid!r} has unknown group {g!r}")
