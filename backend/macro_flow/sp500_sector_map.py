"""Ticker → thematic sector label (macro flow categories + broad bucket)."""
from __future__ import annotations

from typing import Dict

from .taxonomy.seed_taxonomy import CATEGORIES, TAXONOMY

_CATEGORY_NAMES = {cid: name for cid, name, _, _ in CATEGORIES}


def ticker_sector_map() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for cid, rows in TAXONOMY.items():
        label = _CATEGORY_NAMES.get(cid, cid)
        for sym, _ in rows:
            out[str(sym).upper()] = label
    return out


def sector_for_ticker(ticker: str, mapping: Dict[str, str] | None = None) -> str:
    m = mapping or ticker_sector_map()
    return m.get(str(ticker).upper(), "Broad market")
