"""
CORAL Hub Skill: SEC 13F Ingestion Agent

Responsible for:
- Fetching SEC submission JSON using the FinCrawler client.
- Discovering latest 13F-HR filings for tracked managers.
- Fetching raw SEC documents.
- Normalizing XML tables to database structures.
"""
from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
import httpx
from datetime import datetime

from backend.coral_agents import hub_add_note
from backend.fincrawler_client import fc

logger = logging.getLogger(__name__)

# Basic headers per SEC access guidelines. Do NOT hardcode Host — httpx derives
# it per-request (data.sec.gov for submissions vs www.sec.gov for Archives).
import os as _os
SEC_HEADERS = {
    "User-Agent": _os.environ.get("SEC_USER_AGENT", "TradeTalkApp contact@tradetalk.example.com"),
    "Accept-Encoding": "gzip, deflate",
}


async def ingest_manager_13f(cik: str, fund_id: str) -> Dict[str, Any]:
    """
    Given a zero-padded 10-digit CIK, fetch their submissions history,
    find the most recent 13F-HR, and parse the holdings.
    """
    logger.info(f"[13F Ingestion] Starting ingestion for CIK: {cik}")
    cik_padded = str(cik).zfill(10)
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    try:
        # Use httpx directly for SEC JSON to bypass crawler abstractions if needed,
        # but using fincrawler's rate limiting would be better if exposed.
        async with httpx.AsyncClient() as client:
            resp = await client.get(submissions_url, headers=SEC_HEADERS)
            resp.raise_for_status()
            sub_data = resp.json()
    except Exception as e:
        logger.error(f"[13F Ingestion] Failed to fetch submissions for CIK {cik}: {e}")
        return {"status": "error", "message": str(e)}

    filings = sub_data.get("filings", {}).get("recent", {})
    if not filings:
        return {"status": "error", "message": "No recent filings found"}

    # Find latest 13F-HR
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    report_dates = filings.get("reportDate", [])
    primary_docs = filings.get("primaryDocument", [])
    filing_dates = filings.get("filingDate", [])

    target_idx = -1
    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            target_idx = i
            break

    if target_idx == -1:
        msg = f"No 13F-HR found for CIK {cik}"
        logger.info(f"[13F Ingestion] {msg}")
        hub_add_note("data_ingest", msg)
        return {"status": "skipped", "message": msg}

    return await _fetch_and_parse_filing(
        cik=cik,
        cik_padded=cik_padded,
        fund_id=fund_id,
        accession_number=accessions[target_idx],
        report_date=report_dates[target_idx],
        filing_date=filing_dates[target_idx],
        form_type=forms[target_idx],
    )


async def ingest_manager_13f_history(
    cik: str,
    fund_id: str,
    max_quarters: int = 20,
) -> Dict[str, Any]:
    """
    Fetch up to ``max_quarters`` of 13F-HR filings for a CIK (most recent first),
    parsing holdings for each. Used to build a multi-quarter return series.
    """
    cik_padded = str(cik).zfill(10)
    submissions_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(submissions_url, headers=SEC_HEADERS)
            resp.raise_for_status()
            sub_data = resp.json()
    except Exception as e:
        logger.error(f"[13F History] Failed to fetch submissions for CIK {cik}: {e}")
        return {"status": "error", "message": str(e), "filings": []}

    filings = sub_data.get("filings", {}).get("recent", {})
    forms = filings.get("form", [])
    accessions = filings.get("accessionNumber", [])
    report_dates = filings.get("reportDate", [])
    filing_dates = filings.get("filingDate", [])

    # Collect 13F-HR indices, de-duplicating by report period (prefer first/newest).
    selected: List[Dict[str, str]] = []
    seen_periods = set()
    for i, form in enumerate(forms):
        if form not in ("13F-HR", "13F-HR/A"):
            continue
        period = report_dates[i] if i < len(report_dates) else ""
        if period in seen_periods:
            continue
        seen_periods.add(period)
        selected.append({
            "accession_number": accessions[i],
            "report_date": period,
            "filing_date": filing_dates[i] if i < len(filing_dates) else "",
            "form_type": form,
        })
        if len(selected) >= max_quarters:
            break

    parsed_filings: List[Dict[str, Any]] = []
    for f in selected:
        result = await _fetch_and_parse_filing(
            cik=cik,
            cik_padded=cik_padded,
            fund_id=fund_id,
            accession_number=f["accession_number"],
            report_date=f["report_date"],
            filing_date=f["filing_date"],
            form_type=f["form_type"],
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
        "filings": parsed_filings,
    }


async def _fetch_and_parse_filing(
    cik: str,
    cik_padded: str,
    fund_id: str,
    accession_number: str,
    report_date: str,
    filing_date: str,
    form_type: str,
) -> Dict[str, Any]:
    """Fetch a single 13F filing's information-table XML and parse its holdings."""
    acc_no_dashes = accession_number.replace("-", "")
    # SEC Archives paths use the CIK WITHOUT leading zeros (padded CIK 301-redirects).
    cik_unpadded = str(int(cik_padded)) if str(cik_padded).isdigit() else str(cik_padded)

    # Fetch the index JSON for the specific filing to find the information table XML
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_no_dashes}/index.json"
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            idx_resp = await client.get(index_url, headers=SEC_HEADERS)
            idx_resp.raise_for_status()
            idx_data = idx_resp.json()
    except Exception as e:
        logger.error(f"[13F Ingestion] Failed to fetch filing index for {accession_number}: {e}")
        return {"status": "error", "message": str(e)}

    info_table_xml_name = None
    for f in idx_data.get("directory", {}).get("item", []):
        name = f.get("name", "")
        if name.endswith(".xml") and ("info" in name.lower() or "table" in name.lower()):
            info_table_xml_name = name
            break

    if not info_table_xml_name:
        return {"status": "error", "message": "Information table XML not found in directory"}

    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_unpadded}/{acc_no_dashes}/{info_table_xml_name}"
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            xml_resp = await client.get(doc_url, headers=SEC_HEADERS)
            xml_resp.raise_for_status()
            xml_text = xml_resp.text
    except Exception as e:
        logger.error(f"[13F Ingestion] Failed to fetch XML doc: {e}")
        return {"status": "error", "message": str(e)}

    holdings = _parse_info_table(xml_text)
    if holdings is None:
        return {"status": "error", "message": "XML parse error"}

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
        "filing_url": doc_url,
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
        logger.error(f"[13F Ingestion] XML parsing error: {e}")
        return None
