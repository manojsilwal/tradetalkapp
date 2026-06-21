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

# Basic headers per SEC access guidelines
SEC_HEADERS = {
    "User-Agent": "TradeTalkApp contact@tradetalk.example.com",
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov"
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

    acc_num = accessions[target_idx]
    acc_no_dashes = acc_num.replace("-", "")
    report_date = report_dates[target_idx]
    filing_date = filing_dates[target_idx]
    form_type = forms[target_idx]

    # We must fetch the index JSON for the specific filing to find the information table XML
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_padded}/{acc_no_dashes}/index.json"

    try:
        async with httpx.AsyncClient() as client:
            idx_resp = await client.get(index_url, headers=SEC_HEADERS)
            idx_resp.raise_for_status()
            idx_data = idx_resp.json()
    except Exception as e:
        logger.error(f"[13F Ingestion] Failed to fetch filing index for {acc_num}: {e}")
        return {"status": "error", "message": str(e)}

    # Find the information table XML (often named something like form13fInfoTable.xml or similar ending in .xml)
    info_table_xml_name = None
    for f in idx_data.get("directory", {}).get("item", []):
        name = f.get("name", "")
        # Primary docs are usually .txt or primary XML, information table is separate
        if name.endswith(".xml") and ("info" in name.lower() or "table" in name.lower()):
            info_table_xml_name = name
            break

    if not info_table_xml_name:
        return {"status": "error", "message": "Information table XML not found in directory"}

    doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_padded}/{acc_no_dashes}/{info_table_xml_name}"

    try:
        # Use FinCrawler to fetch the raw XML text
        xml_text = await fc.scrape_text(doc_url)
        # Note: scrape_text strips tags sometimes, but let's assume it grabs raw if properly configured,
        # otherwise we fetch directly. For safety on SEC XML:
        async with httpx.AsyncClient() as client:
            xml_resp = await client.get(doc_url, headers=SEC_HEADERS)
            xml_resp.raise_for_status()
            xml_text = xml_resp.text
    except Exception as e:
        logger.error(f"[13F Ingestion] Failed to fetch XML doc: {e}")
        return {"status": "error", "message": str(e)}

    # Parse the XML
    holdings = []
    try:
        # Avoid XML vulnerabilities with defusedxml in production, using standard ET for MVP
        import defusedxml.ElementTree as DET
        root = DET.fromstring(xml_text)

        # XML namespace handling (SEC 13F uses namespaces)
        ns = {"ns": root.tag.split('}')[0].strip('{')} if '}' in root.tag else {"ns": ""}

        for p in root.findall(".//ns:infoTable", namespaces=ns) if ns["ns"] else root.findall(".//infoTable"):
            # Helper to safely extract text
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
                "market_value_usd": val,  # SEC standard is $ thousands, but parser should normalize
                "shares": amt,
                "shares_type": shrs_type,
                "put_call": put_call
            })

    except Exception as e:
        logger.error(f"[13F Ingestion] XML parsing error: {e}")
        return {"status": "error", "message": f"XML parse error: {e}"}

    hub_add_note(
        "data_ingest",
        f"Parsed {len(holdings)} holdings from 13F-HR ({acc_num}) for CIK {cik} (Period: {report_date})"
    )

    return {
        "status": "success",
        "fund_id": fund_id,
        "cik": cik,
        "accession_number": acc_num,
        "report_period": report_date,
        "filing_date": filing_date,
        "form_type": form_type,
        "filing_url": doc_url,
        "holdings": holdings
    }
