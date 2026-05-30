"""
Investor-friendly capex value chain: stage scores + sector-to-sector flows.

Stage rotation scores come from persisted macro_flow snapshots.
CapEx dollars come from yfinance cash-flow statements (TTM, USD-normalized).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .capex_data import build_flows_from_stage_capex, fetch_stage_capex_payload
from .store import latest_rrg_payload

# (stage_id, display_name, color_hex, [(source_category_id, blend_weight), ...])
VALUE_CHAIN_STAGES: Tuple[Tuple[str, str, str, Tuple[Tuple[str, float], ...]], ...] = (
    (
        "retail_industry",
        "Retail / Industry",
        "#ec4899",
        (("consumer_health", 0.55), ("energy_materials", 0.45)),
    ),
    (
        "hyperscaler",
        "Hyperscaler",
        "#3b82f6",
        (("cloud_software", 1.0),),
    ),
    (
        "semiconductor",
        "Semiconductor",
        "#22c55e",
        (("ai_infra", 0.85),),
    ),
    (
        "foundry_infra",
        "Foundry / Equipment",
        "#a855f7",
        (("ai_infra", 0.15), ("energy_materials", 0.35)),
    ),
    (
        "materials",
        "Materials / Minerals",
        "#f97316",
        (("energy_materials", 0.65),),
    ),
)

# Ordered capex stack (downstream demand → upstream inputs)
CHAIN_EDGES: Tuple[Tuple[str, str, str], ...] = (
    ("retail_industry", "hyperscaler", "Enterprise & cloud capex demand"),
    ("hyperscaler", "semiconductor", "GPU / accelerator orders"),
    ("semiconductor", "foundry_infra", "Fab capacity, lithography & packaging"),
    ("foundry_infra", "materials", "Wafers, chemicals & rare-earth inputs"),
)

# Edge groups backed by backend/data/supply_chains.json estimates.
# These are approximate vendor-spend relationships, not audited market totals.
CHAIN_SPEND_GROUPS: Tuple[Tuple[str, str, Tuple[Tuple[str, str], ...]], ...] = (
    (
        "retail_industry",
        "hyperscaler",
        (
            ("LLY", "OPENAI"),
            ("OPENAI", "MSFT"),
            ("OPENAI", "AMZN"),
            ("OPENAI", "GOOGL"),
        ),
    ),
    (
        "hyperscaler",
        "semiconductor",
        (
            ("MSFT", "NVDA"),
            ("MSFT", "AVGO"),
            ("AMZN", "NVDA"),
            ("AMZN", "AVGO"),
            ("GOOGL", "NVDA"),
        ),
    ),
    (
        "semiconductor",
        "foundry_infra",
        (
            ("NVDA", "TSM"),
            ("AVGO", "TSM"),
            ("AAPL", "TSM"),
        ),
    ),
    (
        "foundry_infra",
        "materials",
        (
            ("TSM", "ASML"),
            ("TSM", "LRCX"),
            ("TSM", "KLAC"),
            ("CATL", "ALB"),
            ("CATL", "SQM"),
        ),
    ),
)


def _supply_chain_edges() -> List[Dict[str, Any]]:
    data_path = Path(__file__).resolve().parents[1] / "data" / "supply_chains.json"
    try:
        with data_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    return [e for e in data.get("edges") or [] if isinstance(e, dict)]


def _year_keys(edges: List[Dict[str, Any]]) -> List[str]:
    years = set()
    for e in edges:
        years.update(str(y) for y in (e.get("years") or {}).keys())
    return sorted(years)


def _build_spend_payload_legacy(stages: List[Dict[str, Any]], flows: List[Dict[str, Any]]) -> Dict[str, Any]:
    edges = _supply_chain_edges()
    edge_by_pair = {(e.get("source"), e.get("target")): e for e in edges}
    years = _year_keys(edges)
    if not years:
        return {"available": False, "unit": "USD", "years": [], "flows": [], "stage_totals": []}

    latest_year = years[-1]
    stage_ids = [str(s.get("id") or "") for s in stages]
    stage_names = {str(s.get("id") or ""): str(s.get("name") or s.get("id") or "") for s in stages}
    stage_totals = {sid: {year: 0.0 for year in years} for sid in stage_ids}
    spend_flows: List[Dict[str, Any]] = []

    for src_stage, tgt_stage, pairs in CHAIN_SPEND_GROUPS:
        by_year = {year: 0.0 for year in years}
        confidence_weight = 0.0
        confidence_total = 0.0
        citations: List[str] = []
        pair_count = 0
        for source, target in pairs:
            edge = edge_by_pair.get((source, target))
            if not edge:
                continue
            pair_count += 1
            years_map = edge.get("years") or {}
            conf = float(edge.get("confidence") or 0.0)
            citation = edge.get("citation")
            if citation:
                citations.append(str(citation))
            for year in years:
                value = float(years_map.get(year) or 0.0)
                by_year[year] += value
                confidence_weight += value * conf
                confidence_total += value

        latest_usd = by_year.get(latest_year, 0.0)
        for year, value in by_year.items():
            # Spending by source stage; terminal stage gets incoming total below.
            stage_totals.setdefault(src_stage, {y: 0.0 for y in years})[year] += value
        if tgt_stage == stage_ids[-1]:
            for year, value in by_year.items():
                stage_totals.setdefault(tgt_stage, {y: 0.0 for y in years})[year] += value

        spend_flows.append(
            {
                "from_id": src_stage,
                "from_name": stage_names.get(src_stage, src_stage),
                "to_id": tgt_stage,
                "to_name": stage_names.get(tgt_stage, tgt_stage),
                "latest_year": latest_year,
                "latest_usd": round(latest_usd, 2),
                "timeline": [{"year": year, "usd": round(by_year[year], 2)} for year in years],
                "confidence": round(confidence_weight / confidence_total, 2) if confidence_total else None,
                "source_pairs": pair_count,
                "citations": sorted(set(citations))[:4],
            }
        )

    stage_spend = [
        {
            "id": sid,
            "name": stage_names.get(sid, sid),
            "latest_year": latest_year,
            "latest_usd": round(stage_totals.get(sid, {}).get(latest_year, 0.0), 2),
            "timeline": [
                {"year": year, "usd": round(stage_totals.get(sid, {}).get(year, 0.0), 2)}
                for year in years
            ],
        }
        for sid in stage_ids
    ]

    flow_by_pair = {(f["from_id"], f["to_id"]): f for f in spend_flows}
    for f in flows:
        spend = flow_by_pair.get((f.get("from_id"), f.get("to_id")))
        if spend:
            f["spend_latest_usd"] = spend["latest_usd"]
            f["spend_latest_year"] = spend["latest_year"]

    return {
        "available": True,
        "unit": "USD",
        "metric": "relationship_estimate",
        "basis": "Approximate yearly relationship spend from curated public-company supply-chain estimates.",
        "source": "supply_chains.json",
        "years": years,
        "latest_year": latest_year,
        "latest_label": f"Estimated spend ({latest_year}e)",
        "flows": spend_flows,
        "stage_totals": stage_spend,
    }


async def _build_spend_payload(stages: List[Dict[str, Any]], flows: List[Dict[str, Any]]) -> Dict[str, Any]:
    stage_ids = [str(s.get("id") or "") for s in stages]
    stage_names = {str(s.get("id") or ""): str(s.get("name") or s.get("id") or "") for s in stages}

    try:
        capex = await fetch_stage_capex_payload()
    except Exception:
        capex = {"available": False}

    if capex.get("available"):
        spend_flows = build_flows_from_stage_capex(capex.get("stage_totals") or [], CHAIN_EDGES)
        for f in spend_flows:
            f["from_name"] = stage_names.get(f["from_id"], f.get("from_name") or f["from_id"])
            f["to_name"] = stage_names.get(f["to_id"], f.get("to_name") or f["to_id"])

        stage_spend = []
        for row in capex.get("stage_totals") or []:
            sid = str(row.get("id") or "")
            stage_spend.append(
                {
                    "id": sid,
                    "name": stage_names.get(sid, row.get("name") or sid),
                    "latest_usd": row.get("latest_usd"),
                    "ticker_count": row.get("ticker_count"),
                    "timeline": row.get("timeline") or [],
                }
            )

        flow_by_pair = {(f["from_id"], f["to_id"]): f for f in spend_flows}
        for f in flows:
            spend = flow_by_pair.get((f.get("from_id"), f.get("to_id")))
            if spend:
                f["spend_latest_usd"] = spend["latest_usd"]

        return {
            "available": True,
            "unit": "USD",
            "metric": capex.get("metric") or "capex_ttm",
            "basis": capex.get("basis"),
            "source": capex.get("source") or "yfinance",
            "as_of": capex.get("as_of"),
            "years": capex.get("years") or [],
            "latest_label": capex.get("latest_label") or "TTM reported CapEx",
            "flows": spend_flows,
            "stage_totals": stage_spend,
            "tickers": capex.get("tickers") or [],
        }

    return _build_spend_payload_legacy(stages, flows)


def _score_by_category(interval: str) -> Dict[str, float]:
    pts = latest_rrg_payload(interval)
    return {str(p.get("category_id") or ""): float(p.get("flow_score") or 0.0) for p in pts}


async def build_value_chain_payload(interval: str) -> Dict[str, Any]:
    cat_scores = _score_by_category(interval)
    has_data = bool(cat_scores)

    stages: List[Dict[str, Any]] = []
    for sid, name, color, blends in VALUE_CHAIN_STAGES:
        score = 0.0
        wsum = 0.0
        for cid, w in blends:
            if cid in cat_scores:
                score += cat_scores[cid] * w
                wsum += w
        if wsum > 0:
            score /= wsum
        stages.append(
            {
                "id": sid,
                "name": name,
                "color_hex": color,
                "flow_score": round(score, 4),
            }
        )

    max_abs = max((abs(s["flow_score"]) for s in stages), default=0.0) or 1.0
    for s in stages:
        s["flow_pct"] = round(abs(s["flow_score"]) / max_abs * 100.0, 1)

    flows: List[Dict[str, Any]] = []
    stage_by_id = {s["id"]: s for s in stages}
    for src_id, tgt_id, desc in CHAIN_EDGES:
        src = stage_by_id.get(src_id) or {}
        tgt = stage_by_id.get(tgt_id) or {}
        src_score = float(src.get("flow_score") or 0.0)
        # Propagate capital pressure downstream (positive = stronger forward flow)
        raw = max(abs(src_score), 0.01) * (0.92 if src_score >= 0 else 0.75)
        flows.append(
            {
                "from_id": src_id,
                "from_name": src.get("name") or src_id,
                "to_id": tgt_id,
                "to_name": tgt.get("name") or tgt_id,
                "value": round(raw, 4),
                "description": desc,
            }
        )

    max_flow = max((f["value"] for f in flows), default=1.0) or 1.0
    for f in flows:
        f["pct_of_peak"] = round(f["value"] / max_flow * 100.0, 1)

    spend = await _build_spend_payload(stages, flows)

    return {
        "interval": interval,
        "has_data": has_data,
        "stages": stages,
        "flows": flows,
        "spend": spend,
        "note": (
            "CapEx totals are trailing-twelve-month reported capital expenditure from yfinance "
            "for a representative public ticker basket per stage (USD-normalized). "
            "Industry headlines about $700B+ often include forward guidance, private cloud spend, "
            "and a broader hyperscaler universe than this basket. Educational only — not investment advice."
        ),
    }
