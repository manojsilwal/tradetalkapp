"""
CORAL Hub Skill: SEC 13F Ingestion Agent

Responsible for:
- Fetching SEC submission metadata (5-year history, paginated) via the shared
  rate-limited EDGAR client.
- Discovering 13F-HR / 13F-HR/A filings for tracked managers.
- Downloading + caching raw filing documents (index.json, primary doc, info table).
- Normalizing the XML information table to holdings structures (value-scale aware).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.coral_agents import hub_add_note
from backend.sec.edgar_client import edgar

logger = logging.getLogger(__name__)

DEFAULT_FORMS: Tuple[str, ...] = ("13F-HR", "13F-HR/A")

# SEC began reporting 13F VALUE in whole dollars on 2023-01-03; before that it was
# in thousands of dollars. Normalize everything to whole dollars.
_DOLLAR_CUTOFF = datetime(2023, 1, 3).date()


def resolve_raw_dir() -> Path:
    explicit = os.environ.get("SEC_RAW_DIR", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = os.environ.get("TRADETALK_DATA_DIR", "").strip()
    if data_dir:
        return Path(data_dir) / "sec_raw"
    return Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "sec_raw"


def _flatten_columnar(block: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a columnar filings dict ({form:[...], accessionNumber:[...]}) into rows."""
    keys = list(block.keys())
    if not keys:
        return []
    try:
        n = len(block[keys[0]])
    except TypeError:
        return []
    rows = []
    for i in range(n):
        rows.append({k: (block[k][i] if i < len(block[k]) else None) for k in keys})
    return rows


async def fetch_submissions_5y(
    cik: str,
    forms: Tuple[str, ...] = DEFAULT_FORMS,
    years: int = 5,
) -> Dict[str, Any]:
    """
    Fetch a CIK's submission metadata over the last ``years`` years, including the
    older paginated ``filings.files[]`` JSONs (not just ``filings.recent``).
    Returns deduped filings (latest filing per report period) for ``forms``.
    """
    cik_padded = str(cik).zfill(10)
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
    try:
        root = await edgar.get_json(submissions_url)
    except Exception as e:
        logger.error("[13F] submissions fetch failed CIK %s: %s", cik, e)
        return {"status": "error", "message": str(e), "filings": [], "entity_name": None}

    entity_name = root.get("name")
    all_rows: List[Dict[str, Any]] = _flatten_columnar(root.get("filings", {}).get("recent", {}))

    for file_ref in root.get("filings", {}).get("files", []) or []:
        name = file_ref.get("name")
        if not name:
            continue
        try:
            older = await edgar.get_json(f"https://data.sec.gov/submissions/{name}")
            all_rows.extend(_flatten_columnar(older))
        except Exception as e:
            logger.warning("[13F] older submissions file %s failed: %s", name, e)

    cutoff = datetime.utcnow().date() - timedelta(days=365 * years)
    # Keep latest filing per report period (so amendments supersede originals).
    by_period: Dict[str, Dict[str, str]] = {}
    for row in all_rows:
        form = row.get("form")
        filed = row.get("filingDate")
        if form not in forms or not filed:
            continue
        try:
            filed_date = datetime.strptime(filed, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        if filed_date < cutoff:
            continue
        period = row.get("reportDate") or filed
        rec = {
            "accession_number": row.get("accessionNumber"),
            "report_date": period,
            "filing_date": filed,
            "form_type": form,
            "primary_document": row.get("primaryDocument"),
        }
        prev = by_period.get(period)
        if prev is None or filed > prev["filing_date"]:
            by_period[period] = rec

    filings = sorted(by_period.values(), key=lambda r: r["report_date"], reverse=True)
    return {
        "status": "success",
        "cik": cik_padded,
        "entity_name": entity_name,
        "filings": filings,
    }


async def ingest_manager_13f(cik: str, fund_id: str) -> Dict[str, Any]:
    """Fetch + parse the most recent 13F-HR for a CIK (used for AUM probing)."""
    logger.info("[13F Ingestion] Starting ingestion for CIK: %s", cik)
    meta = await fetch_submissions_5y(cik)
    if meta.get("status") != "success" or not meta.get("filings"):
        return {"status": "skipped", "message": f"No recent 13F-HR for CIK {cik}"}
    latest = meta["filings"][0]
    return await _fetch_and_parse_filing(
        cik=cik,
        cik_padded=str(cik).zfill(10),
        fund_id=fund_id,
        accession_number=latest["accession_number"],
        report_date=latest["report_date"],
        filing_date=latest["filing_date"],
        form_type=latest["form_type"],
        primary_document=latest.get("primary_document"),
    )


async def ingest_manager_13f_history(
    cik: str,
    fund_id: str,
    max_quarters: int = 20,
    save_raw: bool = False,
) -> Dict[str, Any]:
    """
    Fetch up to ``max_quarters`` of 13F filings (5-year window, paginated),
    parsing holdings for each.
    """
    meta = await fetch_submissions_5y(cik)
    if meta.get("status") != "success":
        return {"status": "error", "message": meta.get("message"), "filings": []}

    selected = meta["filings"][:max_quarters]
    parsed_filings: List[Dict[str, Any]] = []
    for f in selected:
        result = await _fetch_and_parse_filing(
            cik=cik,
            cik_padded=str(cik).zfill(10),
            fund_id=fund_id,
            accession_number=f["accession_number"],
            report_date=f["report_date"],
            filing_date=f["filing_date"],
            form_type=f["form_type"],
            primary_document=f.get("primary_document"),
            save_raw=save_raw,
        )
        if result.get("status") == "success":
            parsed_filings.append(result)

    hub_add_note(
        "data_ingest",
        f"[13F History] CIK {cik}: parsed {len(parsed_filings)}/{len(selected)} quarterly filings",
    )
    return {
        "status": "success" if parsed_filings else "error",
        "fund_id": fund_id,
        "cik": cik,
        "entity_name": meta.get("entity_name"),
        "filings": parsed_filings,
    }


def _apply_value_scale(holdings: List[Dict[str, Any]], filing_date: str) -> None:
    """Scale market values to whole dollars (pre-2023 filings report in thousands)."""
    try:
        fd = datetime.strptime(filing_date, "%Y-%m-%d").date() if filing_date else None
    except (ValueError, TypeError):
        fd = None
    if fd is not None and fd < _DOLLAR_CUTOFF:
        for h in holdings:
            h["market_value_usd"] = (h.get("market_value_usd") or 0.0) * 1000.0


async def _fetch_and_parse_filing(
    cik: str,
    cik_padded: str,
    fund_id: str,
    accession_number: str,
    report_date: str,
    filing_date: str,
    form_type: str,
    primary_document: Optional[str] = None,
    save_raw: bool = False,
) -> Dict[str, Any]:
    """Fetch a single 13F filing's information-table XML and parse its holdings."""
    acc_no_dashes = accession_number.replace("-", "")
    # SEC Archives paths use the CIK WITHOUT leading zeros.
    cik_unpadded = str(int(cik_padded)) if str(cik_padded).isdigit() else str(cik_padded)
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_no_dashes}"

    try:
        idx_data = await edgar.get_json(f"{base}/index.json")
    except Exception as e:
        logger.error("[13F] index fetch failed %s: %s", accession_number, e)
        return {"status": "error", "message": str(e)}

    items = idx_data.get("directory", {}).get("item", [])
    xml_items = [f for f in items if str(f.get("name", "")).lower().endswith(".xml")]
    info_table_xml_name = None
    # 1) explicit type/description signal (most reliable across filers).
    for f in xml_items:
        meta = f"{f.get('type','')} {f.get('description','')}".lower()
        if "information table" in meta or "info table" in meta:
            info_table_xml_name = f.get("name")
            break
    # 2) filename heuristic.
    if not info_table_xml_name:
        for f in xml_items:
            n = str(f.get("name", "")).lower()
            if any(k in n for k in ("infotable", "info_table", "form13finfotable", "table", "info")):
                info_table_xml_name = f.get("name")
                break
    # 3) fallback: any XML that is not the cover page (primary_doc.xml). 13F-HR
    #    filings contain exactly the cover + the information table.
    if not info_table_xml_name:
        for f in xml_items:
            n = str(f.get("name", "")).lower()
            if n != "primary_doc.xml" and "primary" not in n:
                info_table_xml_name = f.get("name")
                break
    if not info_table_xml_name:
        logger.warning(
            "[13F] info table XML not found for %s (xmls=%s)",
            accession_number, [f.get("name") for f in xml_items],
        )
        return {"status": "error", "message": "Information table XML not found in directory"}

    doc_url = f"{base}/{info_table_xml_name}"
    try:
        xml_text = await edgar.get_text(doc_url)
    except Exception as e:
        logger.error("[13F] info table fetch failed %s: %s", accession_number, e)
        return {"status": "error", "message": str(e)}

    holdings = _parse_info_table(xml_text)
    if holdings is None:
        return {"status": "error", "message": "XML parse error"}
    _apply_value_scale(holdings, filing_date)

    local_raw_path: Optional[str] = None
    if save_raw:
        try:
            raw_dir = resolve_raw_dir() / cik_padded / acc_no_dashes
            raw_dir.mkdir(parents=True, exist_ok=True)
            import json as _json
            (raw_dir / "index.json").write_text(_json.dumps(idx_data), encoding="utf-8")
            (raw_dir / info_table_xml_name).write_text(xml_text, encoding="utf-8")
            if primary_document:
                try:
                    primary_bytes = await edgar.get_bytes(f"{base}/{primary_document}")
                    (raw_dir / primary_document).write_bytes(primary_bytes)
                except Exception as pe:
                    logger.warning("[13F] primary doc save failed %s: %s", accession_number, pe)
            local_raw_path = str(raw_dir)
        except Exception as e:
            logger.warning("[13F] raw save failed %s: %s", accession_number, e)

    hub_add_note(
        "data_ingest",
        f"Parsed {len(holdings)} holdings from {form_type} ({accession_number}) "
        f"for CIK {cik} (Period: {report_date})",
    )

    return {
        "status": "success",
        "fund_id": fund_id,
        "cik": cik,
        "accession_number": accession_number,
        "report_period": report_date,
        "filing_date": filing_date,
        "form_type": form_type,
        "primary_document": primary_document,
        "filing_url": doc_url,
        "local_raw_path": local_raw_path,
        "holdings": holdings,
    }


def _parse_info_table(xml_text: str) -> Optional[List[Dict[str, Any]]]:
    """Parse a 13F information-table XML string into holdings dicts."""
    try:
        import defusedxml.ElementTree as DET
        root = DET.fromstring(xml_text)

        ns = {"ns": root.tag.split('}')[0].strip('{')} if '}' in root.tag else {"ns": ""}

        holdings: List[Dict[str, Any]] = []
        info_tables = (
            root.findall(".//ns:infoTable", namespaces=ns)
            if ns["ns"] else root.findall(".//infoTable")
        )
        for p in info_tables:
            def _get_text(elem, path):
                node = elem.find(path, namespaces=ns) if ns["ns"] else elem.find(path)
                return node.text.strip() if node is not None and node.text else ""

            issuer = _get_text(p, "ns:nameOfIssuer") if ns["ns"] else _get_text(p, "nameOfIssuer")
            title_class = _get_text(p, "ns:titleOfClass") if ns["ns"] else _get_text(p, "titleOfClass")
            cusip = _get_text(p, "ns:cusip") if ns["ns"] else _get_text(p, "cusip")

            value_str = _get_text(p, "ns:value") if ns["ns"] else _get_text(p, "value")
            shrs_node = p.find(".//ns:shrsOrPrnAmt", namespaces=ns) if ns["ns"] else p.find(".//shrsOrPrnAmt")
            shrs_amt = ""
            shrs_type = ""
            if shrs_node is not None:
                shrs_amt = _get_text(shrs_node, "ns:sshPrnamt") if ns["ns"] else _get_text(shrs_node, "sshPrnamt")
                shrs_type = _get_text(shrs_node, "ns:sshPrnamtType") if ns["ns"] else _get_text(shrs_node, "sshPrnamtType")

            put_call = _get_text(p, "ns:putCall") if ns["ns"] else _get_text(p, "putCall")

            try:
                val = float(value_str) if value_str else 0.0
                amt = float(shrs_amt) if shrs_amt else 0.0
            except ValueError:
                val = 0.0
                amt = 0.0

            holdings.append({
                "issuer_name": issuer,
                "title_of_class": title_class,
                "cusip": cusip,
                "market_value_usd": val,
                "shares": amt,
                "shares_type": shrs_type,
                "put_call": put_call,
            })
        return holdings
    except Exception as e:
        logger.error("[13F Ingestion] XML parsing error: %s", e)
        return None
