"""
Picks-and-shovels theme / bottleneck taxonomy (static seed map — Plan §9).

Modeled after ``backend/macro_flow/taxonomy/seed_taxonomy.py`` and the curated
``backend/brain/business_classifier.py`` ``AI_ACCELERATOR_TICKERS`` list. This is a
hand-maintained v1 because "is a picks-and-shovels supplier to bottleneck X" is not
reliably derivable from yfinance fundamentals alone.

Exports
-------
THEMES            ordered list of theme descriptors (id, label, color, bottleneck)
THEME_MEMBERS     theme_id -> [tickers]
SEED_UNIVERSE     union of every ticker referenced (the MVP scan universe)
THEME_MAP         ticker -> ThemeMembership (themes, bottleneck, reason, hiddenness, capex)
theme_label / theme_bottleneck / membership_for  convenience lookups
validate_taxonomy()  raises if the taxonomy is internally inconsistent
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ── Theme descriptors ────────────────────────────────────────────────────────
# (theme_id, label, color_hex, customer_capex_directness 0-100, bottleneck text)
_THEME_ROWS = [
    ("ai_compute", "AI Compute", "#22c55e", 95,
     "AI training/inference compute capacity (GPUs, accelerators, custom silicon)."),
    ("memory_hbm", "Memory / HBM", "#06b6d4", 90,
     "High-bandwidth memory and data-center DRAM/NAND capacity feeding AI accelerators."),
    ("optical", "Optical Networking", "#3b82f6", 88,
     "High-speed optical interconnect (800G/1.6T transceivers, photonics) for AI clusters."),
    ("ai_networking", "AI Networking / Custom Silicon", "#6366f1", 90,
     "Switching fabric and custom ASICs that move data between accelerators."),
    ("semi_equipment", "Semiconductor Equipment / Packaging", "#8b5cf6", 85,
     "Wafer-fab tools, advanced packaging (CoWoS) and test capacity gating chip supply."),
    ("power_infra", "Power Infrastructure", "#f59e0b", 92,
     "Electrical distribution, switchgear and power gear for high-density data centers."),
    ("cooling", "Cooling / Thermal", "#14b8a6", 88,
     "Liquid cooling and thermal management for high-density AI rack power."),
    ("data_center_re", "Data Center Real Estate", "#ec4899", 80,
     "Physical data-center capacity, colocation and compute hosting."),
    ("grid_construction", "Grid / Construction / Engineering", "#f97316", 78,
     "Grid build-out and electrical construction connecting new load to the grid."),
    ("energy_utilities", "Energy / Utilities / Nuclear", "#eab308", 70,
     "Baseload and incremental generation (nuclear, gas) to power data-center demand."),
    ("pcb_connectors", "PCB / Connectors / Components", "#94a3b8", 72,
     "Advanced PCBs, connectors and components inside AI servers and infrastructure."),
    ("cybersecurity", "Cybersecurity / Data Infra", "#ef4444", 60,
     "Security, observability and data infrastructure scaling with cloud/AI workloads."),
]

THEMES: List[Dict[str, object]] = [
    {"id": tid, "label": label, "color": color, "capex_directness": capex, "bottleneck": bottleneck}
    for (tid, label, color, capex, bottleneck) in _THEME_ROWS
]

_THEME_BY_ID: Dict[str, Dict[str, object]] = {t["id"]: t for t in THEMES}

# theme_id -> tickers (Plan §9 seed mapping)
THEME_MEMBERS: Dict[str, List[str]] = {
    "ai_compute": ["NVDA", "AMD", "AVGO", "MRVL", "INTC", "ARM"],
    "memory_hbm": ["MU", "WDC", "STX", "PSTG", "NTAP", "DELL", "HPE"],
    "optical": ["COHR", "LITE", "CIEN", "FN", "AAOI", "GLW", "APH", "TEL"],
    "ai_networking": ["AVGO", "MRVL", "ANET", "ALAB", "MTSI", "CSCO"],
    "semi_equipment": ["ASML", "AMAT", "LRCX", "KLAC", "TER", "AMKR", "CAMT", "ONTO", "COHU", "AEHR"],
    "power_infra": ["ETN", "VRT", "GEV", "NVT", "HUBB", "POWL", "ATKR"],
    "cooling": ["VRT", "MOD", "AAON", "FIX", "EME", "WTS", "ITT"],
    "data_center_re": ["EQIX", "DLR", "IRM", "CORZ", "APLD", "IREN"],
    "grid_construction": ["PWR", "MTZ", "MYRG", "PRIM", "STRL", "GVA", "EME", "FIX", "ACM", "J"],
    "energy_utilities": ["CEG", "VST", "NRG", "TLN", "PCG", "SO", "DUK", "NEE", "AEP", "EXC", "GEV", "BE"],
    "pcb_connectors": ["TTMI", "BELFB", "APH", "TEL", "CTS", "JBL", "FLEX", "CLS", "SANM"],
    "cybersecurity": ["CRWD", "PANW", "ZS", "FTNT", "DDOG", "DT", "ESTC", "SNOW", "MDB", "CFLT", "GTLB"],
}

# High-signal seed universe (Plan §4) — kept explicit so the union below is auditable.
_CORE_UNIVERSE = [
    # Mega / Core
    "NVDA", "AVGO", "AMD", "MU", "TSM", "ASML", "AMAT", "LRCX", "KLAC", "ANET",
    "VRT", "ETN", "GEV", "EQIX", "DLR", "CEG", "VST", "PWR",
    # Strong Secondary
    "MRVL", "COHR", "LITE", "CIEN", "FN", "STX", "WDC", "SMCI", "DELL", "HPE",
    "NVT", "HUBB", "POWL", "MOD", "FIX", "EME", "JBL", "FLEX", "CLS",
    # Hidden / Underfollowed
    "TTMI", "BELFB", "CAMT", "AMKR", "ONTO", "COHU", "AEHR", "AAOI", "APH", "TEL",
    "GLW", "MYRG", "MTZ", "PRIM", "STRL", "GVA", "ATKR", "WTS", "ITT", "BE", "TLN",
    "APLD", "CORZ", "IREN", "IRM",
]

# Hiddenness seed hints (Plan §8 named examples). Anything not listed is classified
# from live market cap at scoring time. SMCI/SMCI-like names default to market-cap rule.
BIG_PLAYERS = {"NVDA", "AVGO", "AMD", "MU", "TSM", "ASML", "ETN", "AMAT", "LRCX", "KLAC", "CEG"}
SECONDARY_PLAYERS = {
    "MRVL", "ANET", "VRT", "COHR", "CIEN", "NVT", "HUBB", "STX", "EQIX", "DLR",
    "VST", "PWR", "GEV", "DELL", "HPE", "GLW", "APH", "TEL", "PANW", "CRWD", "SNOW",
}
HIDDEN_PLAYERS = {
    "TTMI", "BELFB", "CAMT", "AMKR", "POWL", "MOD", "MYRG", "FN", "AAOI", "ONTO",
    "COHU", "AEHR", "ATKR", "WTS", "ITT", "BE", "TLN", "APLD", "CORZ", "IREN",
    "MTZ", "PRIM", "STRL", "GVA",
}


# ── Per-ticker membership ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ThemeMembership:
    ticker: str
    themes: List[str] = field(default_factory=list)  # ordered theme_ids
    bottleneck_solved: str = ""
    exposure_reason: str = ""
    hiddenness_seed: str = ""  # "Big Player" | "Secondary Player" | "Hidden Player" | ""
    customer_capex_seed: float = 60.0  # 0-100 directness hint (Plan §7.5)

    @property
    def primary_theme(self) -> str:
        return self.themes[0] if self.themes else ""


def _hiddenness_seed(ticker: str) -> str:
    if ticker in BIG_PLAYERS:
        return "Big Player"
    if ticker in SECONDARY_PLAYERS:
        return "Secondary Player"
    if ticker in HIDDEN_PLAYERS:
        return "Hidden Player"
    return ""


def _build_theme_map() -> Dict[str, ThemeMembership]:
    # Invert THEME_MEMBERS into ticker -> ordered theme list (preserve THEMES order).
    ticker_themes: Dict[str, List[str]] = {}
    for theme in THEMES:
        tid = str(theme["id"])
        for tk in THEME_MEMBERS.get(tid, []):
            ticker_themes.setdefault(tk, [])
            if tid not in ticker_themes[tk]:
                ticker_themes[tk].append(tid)

    # Ensure every core-universe ticker has an entry (some core names like SMCI are
    # not in §9 lists; tag them by their nearest theme so they are never themeless).
    _CORE_FALLBACK = {"SMCI": ["ai_compute"]}
    for tk in _CORE_UNIVERSE:
        if tk not in ticker_themes:
            ticker_themes[tk] = list(_CORE_FALLBACK.get(tk, ["ai_compute"]))

    out: Dict[str, ThemeMembership] = {}
    for tk, tids in ticker_themes.items():
        primary = tids[0]
        prim = _THEME_BY_ID[primary]
        capex = max((float(_THEME_BY_ID[t]["capex_directness"]) for t in tids), default=60.0)
        labels = ", ".join(str(_THEME_BY_ID[t]["label"]) for t in tids)
        out[tk] = ThemeMembership(
            ticker=tk,
            themes=tids,
            bottleneck_solved=str(prim["bottleneck"]),
            exposure_reason=f"Supplies the {labels} value chain.",
            hiddenness_seed=_hiddenness_seed(tk),
            customer_capex_seed=capex,
        )
    return out


THEME_MAP: Dict[str, ThemeMembership] = _build_theme_map()

SEED_UNIVERSE: List[str] = sorted(set(_CORE_UNIVERSE) | set(THEME_MAP.keys()))


# ── Convenience lookups ──────────────────────────────────────────────────────


def membership_for(ticker: str) -> Optional[ThemeMembership]:
    return THEME_MAP.get((ticker or "").upper())


def theme_label(theme_id: str) -> str:
    t = _THEME_BY_ID.get(theme_id)
    return str(t["label"]) if t else theme_id


def theme_bottleneck(theme_id: str) -> str:
    t = _THEME_BY_ID.get(theme_id)
    return str(t["bottleneck"]) if t else ""


def theme_ids() -> List[str]:
    return [str(t["id"]) for t in THEMES]


def validate_taxonomy() -> None:
    """Raise ValueError if the taxonomy is internally inconsistent."""
    ids = theme_ids()
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate theme ids in THEMES")
    for tid in THEME_MEMBERS:
        if tid not in _THEME_BY_ID:
            raise ValueError(f"THEME_MEMBERS references unknown theme id {tid!r}")
    for tk, m in THEME_MAP.items():
        if not m.themes:
            raise ValueError(f"ticker {tk} has no themes")
        for t in m.themes:
            if t not in _THEME_BY_ID:
                raise ValueError(f"ticker {tk} references unknown theme {t!r}")
    if not SEED_UNIVERSE:
        raise ValueError("SEED_UNIVERSE is empty")
