"""
Fund leaderboard universe builder (3 ranking modes).

build_universe(ranking_mode, top_n) returns a ranked list of filer dicts shaped
for the orchestrator::

    {"cik": "1067983", "name": "Berkshire Hathaway", "aum_usd": <ranking value>,
     "ranking_method": "SEC_13F_VALUE", "source": "sec_bulk_2025q1",
     "latest_13f_value_usd": <usd or None>, "external_aum_usd": <usd or None>}

Ranking modes:
- SEC_13F_VALUE (default): download the latest SEC Form 13F bulk dataset ZIP and
  rank every filer by total reported 13F value for the latest period.
- EXTERNAL_AUM_CURATED: load backend/data/fund_universe.yml and rank by curated AUM.
- CUSTOM_WATCHLIST: same YAML shape from a watchlist file/env (FUND_LB_WATCHLIST_PATH).

All filers are upserted into fund_master with ranking metadata.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import fund_leaderboard_store as store
from .sec.edgar_client import edgar

logger = logging.getLogger(__name__)

RANKING_SEC_13F_VALUE = "SEC_13F_VALUE"
RANKING_EXTERNAL_AUM = "EXTERNAL_AUM_CURATED"
RANKING_WATCHLIST = "CUSTOM_WATCHLIST"
VALID_MODES = {RANKING_SEC_13F_VALUE, RANKING_EXTERNAL_AUM, RANKING_WATCHLIST}

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
# SEC began reporting 13F VALUE in whole dollars on 2023-01-03; before that, thousands.
_DOLLAR_CUTOFF = date(2023, 1, 3)


# ── Curated / watchlist (YAML) ────────────────────────────────────────────────

def _default_universe_yaml() -> str:
    return os.path.join(_BACKEND_DIR, "data", "fund_universe.yml")


def _load_yaml_managers(path: str) -> List[Dict[str, Any]]:
    import yaml

    if not os.path.exists(path):
        logger.warning("[Universe] YAML not found: %s", path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    managers = data.get("managers", []) if isinstance(data, dict) else []
    # Dedupe by CIK (first occurrence wins).
    seen: Dict[str, Dict[str, Any]] = {}
    for m in managers:
        cik = str(m.get("cik", "")).strip()
        if not cik or cik in seen:
            continue
        seen[cik] = m
    return list(seen.values())


def _universe_from_yaml(path: str, ranking_method: str, source: str, top_n: int) -> List[Dict[str, Any]]:
    managers = _load_yaml_managers(path)
    rows: List[Dict[str, Any]] = []
    for m in managers:
        cik = str(m.get("cik", "")).strip()
        aum = float(m.get("external_aum_usd") or 0.0)
        rows.append({
            "cik": cik,
            "name": m.get("name") or f"CIK {cik}",
            "aum_usd": aum,
            "external_aum_usd": aum,
            "latest_13f_value_usd": None,
            "ranking_method": ranking_method,
            "source": source,
            "manager_type": m.get("manager_type"),
            "strategy_tags": m.get("strategy_tags") or [],
            "philosophy": m.get("philosophy"),
        })
    rows.sort(key=lambda r: r["aum_usd"], reverse=True)
    return rows[:top_n]


# ── SEC bulk dataset (default) ────────────────────────────────────────────────

def _scale_value(value: float, period: Optional[str]) -> float:
    """Scale to whole dollars; pre-2023 datasets report VALUE in thousands."""
    try:
        pd = datetime.strptime(period, "%Y-%m-%d").date() if period else None
    except (ValueError, TypeError):
        pd = None
    if pd is not None and pd < _DOLLAR_CUTOFF:
        return value * 1000.0
    return value


async def _resolve_latest_zip_url() -> Optional[str]:
    """Scrape the data-sets page for the most recent *_form13f.zip link."""
    try:
        html = await edgar.get_text(_DATASETS_PAGE)
    except Exception as e:
        logger.warning("[Universe] datasets page fetch failed: %s", e)
        return None
    # Hrefs like /files/dera/data/form-13f-data-sets/2025q1_form13f.zip
    hrefs = re.findall(r'href="([^"]*?(\d{4})q([1-4])_form13f\.zip)"', html, flags=re.IGNORECASE)
    if not hrefs:
        return None
    # Pick the most recent by (year, quarter).
    best = max(hrefs, key=lambda h: (int(h[1]), int(h[2])))
    url = best[0]
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://www.sec.gov" + url
    return url


def _read_tsv(zf: zipfile.ZipFile, name: str) -> List[Dict[str, str]]:
    candidates = [n for n in zf.namelist() if n.upper().endswith(name.upper())]
    if not candidates:
        return []
    with zf.open(candidates[0]) as fh:
        text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        reader = csv.DictReader(text, delimiter="\t")
        return [dict(r) for r in reader]


def _parse_bulk_zip(zip_path: Path) -> List[Dict[str, Any]]:
    """Aggregate per-CIK total 13F value for the latest period in the dataset."""
    with zipfile.ZipFile(zip_path) as zf:
        submissions = _read_tsv(zf, "SUBMISSION.tsv")
        coverpages = _read_tsv(zf, "COVERPAGE.tsv")
        infotables = _read_tsv(zf, "INFOTABLE.tsv")

    if not submissions:
        return []

    def _norm_date(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        s = s.strip()
        for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                continue
        return s

    # accession -> (cik, period, submission_type)
    acc_meta: Dict[str, Dict[str, Any]] = {}
    latest_period: Optional[str] = None
    for r in submissions:
        acc = (r.get("ACCESSION_NUMBER") or "").strip()
        if not acc:
            continue
        cik = (r.get("CIK") or "").strip()
        period = _norm_date(r.get("PERIODOFREPORT"))
        stype = (r.get("SUBMISSIONTYPE") or "").strip()
        acc_meta[acc] = {"cik": cik, "period": period, "type": stype}
        if period and (latest_period is None or period > latest_period):
            latest_period = period

    # accession -> filing manager name
    acc_name: Dict[str, str] = {}
    for r in coverpages:
        acc = (r.get("ACCESSION_NUMBER") or "").strip()
        if acc:
            acc_name[acc] = (r.get("FILINGMANAGER_NAME") or "").strip()

    # Accessions belonging to the latest period (13F-HR variants only).
    target_accs = {
        acc for acc, m in acc_meta.items()
        if m["period"] == latest_period and (m["type"] or "").startswith("13F-HR")
    }

    # Sum INFOTABLE VALUE per accession (scaled), then aggregate per CIK.
    acc_value: Dict[str, float] = {}
    for r in infotables:
        acc = (r.get("ACCESSION_NUMBER") or "").strip()
        if acc not in target_accs:
            continue
        try:
            val = float((r.get("VALUE") or "0").replace(",", "") or 0)
        except ValueError:
            val = 0.0
        acc_value[acc] = acc_value.get(acc, 0.0) + val

    by_cik: Dict[str, Dict[str, Any]] = {}
    for acc in target_accs:
        meta = acc_meta[acc]
        cik = meta["cik"]
        if not cik:
            continue
        scaled = _scale_value(acc_value.get(acc, 0.0), meta["period"])
        entry = by_cik.setdefault(cik, {"cik": cik, "name": acc_name.get(acc) or f"CIK {cik}", "value": 0.0, "period": meta["period"]})
        entry["value"] += scaled
        if acc_name.get(acc):
            entry["name"] = acc_name[acc]

    rows = list(by_cik.values())
    rows.sort(key=lambda r: r["value"], reverse=True)
    return rows


async def _universe_from_bulk(top_n: int) -> List[Dict[str, Any]]:
    zip_url = await _resolve_latest_zip_url()
    if not zip_url:
        raise RuntimeError("could not resolve latest 13F bulk dataset URL")
    quarter_tag = re.search(r"(\d{4}q[1-4])", zip_url, flags=re.IGNORECASE)
    source = f"sec_bulk_{quarter_tag.group(1).lower()}" if quarter_tag else "sec_bulk"
    logger.info("[Universe] downloading 13F bulk dataset: %s", zip_url)

    with tempfile.TemporaryDirectory() as td:
        zip_path = Path(td) / "form13f.zip"
        await edgar.download_to(zip_url, zip_path)
        aggregated = _parse_bulk_zip(zip_path)

    rows: List[Dict[str, Any]] = []
    for a in aggregated[:top_n]:
        rows.append({
            "cik": str(int(a["cik"])) if str(a["cik"]).isdigit() else str(a["cik"]),
            "name": a["name"],
            "aum_usd": a["value"],
            "latest_13f_value_usd": a["value"],
            "external_aum_usd": None,
            "ranking_method": RANKING_SEC_13F_VALUE,
            "source": source,
            "manager_type": "institutional",
            "strategy_tags": [],
        })
    return rows


# ── Public API ────────────────────────────────────────────────────────────────

async def build_universe(
    ranking_mode: str = RANKING_SEC_13F_VALUE,
    top_n: int = 50,
    watchlist_path: Optional[str] = None,
    persist: bool = True,
) -> List[Dict[str, Any]]:
    """Build and (optionally) persist the ranked manager universe."""
    mode = (ranking_mode or RANKING_SEC_13F_VALUE).upper()
    if mode not in VALID_MODES:
        logger.warning("[Universe] unknown ranking_mode %s -> SEC_13F_VALUE", ranking_mode)
        mode = RANKING_SEC_13F_VALUE

    rows: List[Dict[str, Any]] = []
    if mode == RANKING_EXTERNAL_AUM:
        rows = _universe_from_yaml(_default_universe_yaml(), RANKING_EXTERNAL_AUM, "fund_universe.yml", top_n)
    elif mode == RANKING_WATCHLIST:
        path = watchlist_path or os.environ.get("FUND_LB_WATCHLIST_PATH") or _default_universe_yaml()
        rows = _universe_from_yaml(path, RANKING_WATCHLIST, os.path.basename(path), top_n)
    else:
        try:
            rows = await _universe_from_bulk(top_n)
        except Exception as e:
            logger.warning("[Universe] bulk dataset failed (%s); falling back to curated YAML", e)
            rows = _universe_from_yaml(_default_universe_yaml(), RANKING_SEC_13F_VALUE, "fund_universe.yml(fallback)", top_n)

    if persist:
        for r in rows:
            try:
                store.upsert_fund(
                    cik=r["cik"],
                    display_name=r["name"],
                    manager_type=r.get("manager_type", "institutional"),
                    strategy_tags=r.get("strategy_tags") or [],
                    latest_13f_value_usd=r.get("latest_13f_value_usd"),
                    external_aum_usd=r.get("external_aum_usd"),
                    ranking_method=r.get("ranking_method"),
                    source=r.get("source"),
                    entity_name=r["name"],
                )
            except Exception as e:
                logger.warning("[Universe] upsert failed for CIK %s: %s", r.get("cik"), e)

    logger.info("[Universe] mode=%s built %d filers", mode, len(rows))
    return rows
