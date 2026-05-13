"""
Curated macro categories and ticker weights for thematic flow.
Weights per category must sum to 1.0 (validated by validate_taxonomy).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

# (category_id, name, color_hex, description)
CATEGORIES: List[Tuple[str, str, str, str]] = [
    ("ai_infra", "AI / Semiconductors", "#22c55e", "Compute, memory, networking for AI workloads"),
    ("cloud_software", "Cloud & Enterprise Software", "#3b82f6", "SaaS and hyperscaler-adjacent software"),
    ("financials", "Financials & Credit", "#eab308", "Banks, payments, market infrastructure"),
    ("energy_materials", "Energy & Materials", "#f97316", "Commodities, cyclicals, industrials"),
    ("consumer_health", "Consumer & Healthcare", "#ec4899", "Discretionary staples mix and healthcare"),
    ("defensive", "Defensive / Quality", "#94a3b8", "Utilities, staples, low-beta quality"),
]

# category_id -> list of (ticker, weight)
TAXONOMY: Dict[str, List[Tuple[str, float]]] = {
    "ai_infra": [
        ("NVDA", 0.22),
        ("AMD", 0.12),
        ("AVGO", 0.12),
        ("TSM", 0.10),
        ("ASML", 0.08),
        ("MU", 0.08),
        ("MRVL", 0.06),
        ("SMCI", 0.05),
        ("ARM", 0.05),
        ("LRCX", 0.06),
        ("KLAC", 0.06),
    ],
    "cloud_software": [
        ("MSFT", 0.28),
        ("CRM", 0.12),
        ("NOW", 0.10),
        ("SNOW", 0.08),
        ("DDOG", 0.08),
        ("MDB", 0.06),
        ("PANW", 0.10),
        ("ZS", 0.08),
        ("NET", 0.10),
    ],
    "financials": [
        ("JPM", 0.20),
        ("BAC", 0.12),
        ("GS", 0.12),
        ("MS", 0.10),
        ("V", 0.18),
        ("MA", 0.16),
        ("BLK", 0.12),
    ],
    "energy_materials": [
        ("XOM", 0.18),
        ("CVX", 0.16),
        ("COP", 0.10),
        ("FCX", 0.12),
        ("NEM", 0.10),
        ("CAT", 0.14),
        ("LIN", 0.12),
        ("APD", 0.08),
    ],
    "consumer_health": [
        ("AMZN", 0.18),
        ("TSLA", 0.08),
        ("UNH", 0.14),
        ("JNJ", 0.12),
        ("LLY", 0.12),
        ("PFE", 0.08),
        ("PG", 0.10),
        ("KO", 0.10),
        ("PEP", 0.08),
    ],
    "defensive": [
        ("XLU", 0.35),
        ("NEE", 0.15),
        ("DUK", 0.12),
        ("WMT", 0.20),
        ("COST", 0.18),
    ],
}


def validate_taxonomy() -> None:
    """Raise ValueError if any category weights do not sum to ~1.0."""
    for cid, rows in TAXONOMY.items():
        s = sum(w for _, w in rows)
        if abs(s - 1.0) > 1e-4:
            raise ValueError(f"category {cid} weights sum to {s}, expected 1.0")
