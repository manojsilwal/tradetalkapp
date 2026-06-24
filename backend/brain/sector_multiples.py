"""Sector peer multiple helpers for valuation routing.

The production version can persist nightly sector medians via ``StoragePort``.
This pure module keeps v1 deterministic and offline-testable: pass feature rows
with ``sector`` and valuation multiples and receive robust medians.
"""
from __future__ import annotations

from statistics import median
from typing import Dict, Iterable, List, Optional


MULTIPLE_KEYS = ("pe_ratio", "ev_ebitda", "ev_sales")


def _num(row: Dict, key: str) -> Optional[float]:
    v = row.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def build_sector_medians(rows: Iterable[Dict]) -> Dict[str, Dict[str, float]]:
    """Return ``{sector: {multiple: median}}`` from point-in-time peer rows."""
    buckets: Dict[str, Dict[str, List[float]]] = {}
    for row in rows:
        sector = str(row.get("sector") or row.get("gics_sector") or "unknown").strip() or "unknown"
        buckets.setdefault(sector, {k: [] for k in MULTIPLE_KEYS})
        for key in MULTIPLE_KEYS:
            v = _num(row, key)
            if v is not None:
                buckets[sector][key].append(v)
    out: Dict[str, Dict[str, float]] = {}
    for sector, values in buckets.items():
        med = {k: round(float(median(vs)), 4) for k, vs in values.items() if vs}
        if med:
            out[sector] = med
    return out


def sector_median(sector_medians: Optional[Dict[str, Dict[str, float]]],
                  sector: Optional[str], key: str) -> Optional[float]:
    if not sector_medians or not sector:
        return None
    sec = sector_medians.get(sector) or sector_medians.get(str(sector).strip())
    if not sec:
        return None
    v = sec.get(key)
    return float(v) if v is not None else None
