"""Filing intelligence contract, cache, extraction, and agent fetch."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_MEMORY_CACHE: Dict[str, Dict[str, Any]] = {}


def enabled() -> bool:
    return os.environ.get("FILING_INTELLIGENCE_ENABLE", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def ttl_days() -> int:
    try:
        return max(1, int(os.environ.get("FILING_INTELLIGENCE_TTL_DAYS", "7")))
    except (TypeError, ValueError):
        return 7


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json_field(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return None


def _record_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    rec = dict(row)
    rec["end_market_exposure"] = _parse_json_field(
        rec.pop("end_market_exposure_json", None) or rec.get("end_market_exposure")
    ) or {}
    rec["thematic_tags"] = _parse_json_field(
        rec.pop("thematic_tags_json", None) or rec.get("thematic_tags")
    ) or []
    rec["citations"] = _parse_json_field(
        rec.pop("citations_json", None) or rec.get("citations")
    ) or []
    rec.pop("raw_extract_json", None)
    if rec.get("as_of_date") is not None:
        rec["as_of_date"] = str(rec["as_of_date"])
    return rec


def is_stale(record: Dict[str, Any], *, ttl: Optional[int] = None) -> bool:
    if not record:
        return True
    extracted = record.get("extracted_at_utc")
    if not extracted:
        return True
    try:
        if isinstance(extracted, (int, float)):
            age_days = (time.time() - float(extracted)) / 86400.0
        else:
            dt = datetime.fromisoformat(str(extracted).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except (TypeError, ValueError):
        return True
    limit = ttl if ttl is not None else int(record.get("ttl_days") or ttl_days())
    return age_days > limit


def get_filing_intelligence(ticker: str) -> Optional[Dict[str, Any]]:
    sym = (ticker or "").upper().strip()
    if not sym:
        return None
    if sym in _MEMORY_CACHE:
        return _MEMORY_CACHE[sym]
    try:
        from ..paper_portfolio import get_filing_intelligence_record

        row = get_filing_intelligence_record(sym)
        if row:
            rec = _record_from_row(row)
            _MEMORY_CACHE[sym] = rec
            return rec
    except Exception as exc:  # noqa: BLE001
        logger.debug("[filing_intelligence] cache read failed for %s: %s", sym, exc)
    return None


def get_filing_intelligence_bulk(tickers: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for t in tickers:
        sym = (t or "").upper().strip()
        if not sym:
            continue
        rec = get_filing_intelligence(sym)
        if rec and not is_stale(rec):
            out[sym] = rec
    return out


def upsert_filing_intelligence(record: Dict[str, Any]) -> None:
    sym = (record.get("ticker") or "").upper().strip()
    if not sym:
        return
    record = dict(record)
    record["ticker"] = sym
    if "extracted_at_utc" not in record:
        record["extracted_at_utc"] = _now_iso()
    _MEMORY_CACHE[sym] = _record_from_row(record)
    try:
        from ..paper_portfolio import upsert_filing_intelligence_record

        upsert_filing_intelligence_record(record)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[filing_intelligence] persist failed for %s: %s", sym, exc)


def format_filing_narrative(record: Dict[str, Any]) -> str:
    """Natural-language summary for RAG sp500_fundamentals_narratives."""
    sym = (record.get("ticker") or "").upper()
    form = record.get("filing_form") or "10-K"
    parts = [f"{sym} filing intelligence ({form}):"]
    if record.get("demand_visibility_summary"):
        parts.append(f"Demand visibility: {record['demand_visibility_summary']}.")
    backlog = record.get("order_backlog_usd")
    if backlog is not None:
        parts.append(f"Order backlog ${backlog:,.0f}.")
    btb = record.get("book_to_bill_ratio")
    if btb is not None:
        parts.append(f"Book-to-bill {btb:.2f}.")
    recur = record.get("recurring_revenue_pct")
    if recur is not None:
        parts.append(f"Recurring revenue ~{recur:.0f}%.")
    moat = record.get("primary_moat_driver")
    if moat:
        parts.append(f"Moat driver: {moat}.")
    tags = record.get("thematic_tags") or []
    if tags:
        parts.append(f"Thematic exposure: {', '.join(str(t) for t in tags)}.")
    conc = record.get("top_customer_concentration_pct")
    if conc is not None:
        parts.append(f"Top customer concentration ~{conc}%.")
    return " ".join(parts)


def index_filing_narrative(record: Dict[str, Any]) -> None:
    """Upsert filing summary into sp500_fundamentals_narratives for agent RAG."""
    sym = (record.get("ticker") or "").upper().strip()
    if not sym:
        return
    try:
        from ..knowledge_store import get_knowledge_store

        narrative = format_filing_narrative(record)
        get_knowledge_store().upsert_sp500_fundamental(
            ticker=sym,
            sector=str(record.get("sector") or "Unknown"),
            narrative=narrative,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[filing_intelligence] RAG index failed for %s: %s", sym, exc)


def format_filing_intelligence_for_chat(payload: Dict[str, Any]) -> str:
    """Compact text for chat tool responses."""
    if not payload.get("available"):
        sym = payload.get("ticker") or "?"
        reason = payload.get("reason") or "unavailable"
        spot = payload.get("spot_price_usd")
        lines = [f"Filing intelligence for {sym}: not available ({reason})."]
        if spot is not None:
            lines.append(f"Live spot: ${spot:.2f}")
        return "\n".join(lines)

    rec = payload.get("record") or {}
    sym = payload.get("ticker") or rec.get("ticker") or "?"
    lines = [f"Filing intelligence for {sym}:"]
    if payload.get("stale"):
        lines.append("(Cache stale — batch refresh pending.)")
    if rec.get("demand_visibility_summary"):
        lines.append(f"Demand visibility: {rec['demand_visibility_summary']}")
    for label, key, fmt in (
        ("Backlog USD", "order_backlog_usd", "${:,.0f}"),
        ("Backlog YoY %", "backlog_growth_yoy_pct", "{:.1f}%"),
        ("Book-to-bill", "book_to_bill_ratio", "{:.2f}"),
        ("Recurring rev %", "recurring_revenue_pct", "{:.0f}%"),
    ):
        val = rec.get(key)
        if val is not None:
            lines.append(f"{label}: {fmt.format(val)}")
    if rec.get("primary_moat_driver"):
        lines.append(f"Moat driver: {rec['primary_moat_driver']}")
    tags = rec.get("thematic_tags") or []
    if tags:
        lines.append(f"Thematic tags: {', '.join(str(t) for t in tags)}")
    rm = payload.get("risk_matrix") or {}
    if rm:
        lines.append(
            "Risk matrix: "
            + ", ".join(f"{k}={v}" for k, v in rm.items())
        )
    if payload.get("spot_price_usd") is not None:
        lines.append(f"Live spot: ${payload['spot_price_usd']:.2f}")
    return "\n".join(lines)


def to_brain_fundamentals(record: Dict[str, Any]) -> Dict[str, float]:
    from ..brain.filing_intelligence_scorer import brain_features_from_record

    return brain_features_from_record(record)


def _find_usd_amount(text: str, pattern: str) -> Optional[float]:
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    mult = 1.0
    if raw.upper().endswith("B"):
        mult = 1e9
        raw = raw[:-1]
    elif raw.upper().endswith("M"):
        mult = 1e6
        raw = raw[:-1]
    try:
        return float(raw) * mult
    except ValueError:
        return None


def extract_heuristic_from_text(
    ticker: str,
    text: str,
    *,
    filing_form: str = "10-K",
) -> Dict[str, Any]:
    """Offline-safe keyword extraction when LLM/FinCrawler unavailable."""
    low = (text or "").lower()
    risk_hits = sum(
        1 for kw in ("going concern", "material weakness", "restatement", "investigation", "litigation")
        if kw in low
    )
    tone_hits = sum(
        1 for kw in ("record", "strong demand", "raised guidance", "backlog", "momentum", "growth")
        if kw in low
    )
    expansion_hits = sum(
        1 for kw in ("new product", "design win", "capacity expansion", "platform", "ecosystem")
        if kw in low
    )
    conc_hits = sum(
        1 for kw in ("concentration", "top customer", "largest customer", "single customer")
        if kw in low
    )
    backlog_usd = _find_usd_amount(
        text, r"(?:backlog|order backlog|remaining performance obligations)[^\$]{0,40}\$?\s*([\d.,]+[BM]?)"
    )
    book_to_bill = None
    btb_m = re.search(r"book[- ]to[- ]bill[^\d]{0,20}([\d.]+)", low)
    if btb_m:
        try:
            book_to_bill = float(btb_m.group(1))
        except ValueError:
            pass
    dc_pct = None
    dc_m = re.search(r"data center[s]?[^\d%]{0,30}([\d.]+)\s*%", low)
    if dc_m:
        try:
            dc_pct = float(dc_m.group(1)) / 100.0
        except ValueError:
            pass
    thematic: List[str] = []
    if dc_pct and dc_pct >= 0.20:
        thematic.append("AI Infrastructure — Direct")
    elif any(k in low for k in ("data center", "hyperscale", "ai ", "artificial intelligence")):
        thematic.append("AI Infrastructure — Indirect Enabler")
    moat = None
    if "switching cost" in low or "specification" in low or "spec-in" in low:
        moat = "High switching costs / spec-lock-in"
    elif "platform" in low or "ecosystem" in low:
        moat = "Platform ecosystem lock-in"
    elif "patent" in low or "proprietary" in low:
        moat = "IP / technology leadership"

    filing_risk = min(1.0, 0.2 + risk_hits * 0.15)
    management_tone = min(1.0, 0.35 + tone_hits * 0.12)
    expansion = min(1.0, 0.3 + expansion_hits * 0.12)
    concentration = min(1.0, 0.15 + conc_hits * 0.2)
    if conc_hits and dc_pct and dc_pct > 0.5:
        concentration = min(1.0, concentration + 0.2)

    rec: Dict[str, Any] = {
        "ticker": ticker.upper(),
        "as_of_date": str(datetime.now(timezone.utc).date()),
        "filing_form": filing_form,
        "source": "heuristic",
        "filing_risk_score": filing_risk,
        "management_tone_score": management_tone,
        "new_product_expansion_score": expansion,
        "customer_concentration_score": concentration,
        "order_backlog_usd": backlog_usd,
        "book_to_bill_ratio": book_to_bill,
        "end_market_exposure": {"data_center": dc_pct} if dc_pct is not None else {},
        "primary_moat_driver": moat,
        "thematic_tags": thematic,
        "demand_visibility_summary": (
            f"Backlog ${backlog_usd / 1e9:.1f}B" if backlog_usd else "Demand signals from filing text."
        ),
        "citations": [text[:240]] if text else [],
        "extracted_at_utc": _now_iso(),
        "ttl_days": ttl_days(),
    }
    from ..brain.filing_intelligence_scorer import compute_demand_visibility_score

    dvs = compute_demand_visibility_score(rec)
    if dvs is not None:
        rec["demand_visibility_score"] = dvs
    return rec


async def extract_from_filing_async(
    ticker: str,
    text: str,
    *,
    filing_form: str = "10-K",
    use_llm: bool = True,
) -> Dict[str, Any]:
    if not text or len(text) < 100:
        return {}
    if use_llm:
        try:
            from ..deps import llm_client

            out = await llm_client.extract_filing_intelligence(ticker, text, filing_form=filing_form)
            if out and out.get("ticker"):
                out.setdefault("extracted_at_utc", _now_iso())
                out.setdefault("source", "llm_extract")
                out.setdefault("ttl_days", ttl_days())
                from ..brain.filing_intelligence_scorer import compute_demand_visibility_score

                if out.get("demand_visibility_score") is None:
                    dvs = compute_demand_visibility_score(out)
                    if dvs is not None:
                        out["demand_visibility_score"] = dvs
                return out
        except Exception as exc:  # noqa: BLE001
            logger.debug("[filing_intelligence] LLM extract failed for %s: %s", ticker, exc)
    return extract_heuristic_from_text(ticker, text, filing_form=filing_form)


def build_risk_matrix(
    record: Optional[Dict[str, Any]],
    *,
    pe_ratio: Optional[float] = None,
    debt_to_equity: Optional[float] = None,
) -> Dict[str, str]:
    """Six-category risk grades for Decision Terminal."""
    levels = ("Low", "Moderate", "High", "Extreme")

    def _grade(score: float) -> str:
        if score >= 0.75:
            return "High"
        if score >= 0.45:
            return "Moderate"
        return "Low"

    valuation = "Moderate"
    if pe_ratio is not None:
        if pe_ratio > 80:
            valuation = "Extreme"
        elif pe_ratio > 40:
            valuation = "High"
        elif pe_ratio < 18:
            valuation = "Low"

    execution = "Moderate"
    cyclical = "Moderate"
    competitive = "Low"
    balance_sheet = "Moderate"
    regulatory = "Low"

    if record:
        fr = record.get("filing_risk_score")
        if fr is not None and float(fr) > 0.55:
            regulatory = _grade(float(fr))
            execution = _grade(float(fr))
        conc = record.get("customer_concentration_score")
        if conc is not None and float(conc) > 0.5:
            cyclical = "High"
        tags = record.get("thematic_tags") or []
        if any("Direct" in str(t) for t in tags):
            cyclical = "High"
        moat = (record.get("primary_moat_driver") or "").lower()
        if "ecosystem" in moat or "switching" in moat:
            competitive = "Low"
        elif moat:
            competitive = "Moderate"

    if debt_to_equity is not None:
        de = float(debt_to_equity)
        if de > 200:
            balance_sheet = "High"
        elif de > 100:
            balance_sheet = "Moderate"
        else:
            balance_sheet = "Low"

    out = {
        "valuation": valuation,
        "execution": execution,
        "cyclical": cyclical,
        "competitive": competitive,
        "balance_sheet": balance_sheet,
        "regulatory": regulatory,
    }
    for k, v in out.items():
        if v not in levels:
            out[k] = "Moderate"
    return out


def build_narrative_scenarios(record: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Bull/base/bear narrative blocks (price anchors filled by DT roadmap)."""
    summary = (record or {}).get("demand_visibility_summary") or ""
    moat = (record or {}).get("primary_moat_driver") or "fundamental execution"
    tags = ", ".join((record or {}).get("thematic_tags") or []) or "core markets"
    return {
        "bull": {
            "thesis": f"Demand visibility sustains with {moat}; thematic tailwind from {tags}.",
            "key_assumption": summary or "Backlog and orders remain above plan.",
            "price_implied_usd": None,
        },
        "base": {
            "thesis": "Execution tracks guidance with stable margins and manageable leverage.",
            "key_assumption": "No material customer loss or regulatory shock.",
            "price_implied_usd": None,
        },
        "bear": {
            "thesis": "Cyclical slowdown or concentration risk materializes; multiples compress.",
            "key_assumption": "Hyperscaler capex or end-market demand decelerates.",
            "price_implied_usd": None,
        },
    }


async def fetch_for_agent(ticker: str, *, force_refresh: bool = False) -> Dict[str, Any]:
    """Structured filing intelligence + live spot for tool_registry / chat."""
    sym = (ticker or "").upper().strip()
    if not sym:
        return {"available": False, "reason": "empty_ticker"}

    record = None if force_refresh else get_filing_intelligence(sym)
    if record is None or is_stale(record):
        if enabled() and os.environ.get("FINCRAWLER_URL"):
            try:
                from ..fincrawler_client import fc

                if fc.enabled:
                    text = await fc.get_sec_filing(sym, form="10-K", max_chars=12000)
                    if text and not text.startswith("SEC filing unavailable"):
                        record = await extract_from_filing_async(sym, text, filing_form="10-K")
                        if record:
                            upsert_filing_intelligence(record)
            except Exception as exc:  # noqa: BLE001
                logger.debug("[filing_intelligence] live fetch failed for %s: %s", sym, exc)

    spot = None
    spot_source = None
    try:
        from ..connectors.spot import resolve_spot

        q = resolve_spot(sym)
        if q and q.price:
            spot = float(q.price)
            spot_source = q.source
    except Exception:  # noqa: BLE001
        pass

    if not record:
        return {
            "available": False,
            "ticker": sym,
            "spot_price_usd": spot,
            "spot_source": spot_source,
            "reason": "no_filing_intelligence_cached",
        }

    return {
        "available": True,
        "ticker": sym,
        "spot_price_usd": spot,
        "spot_source": spot_source,
        "record": record,
        "risk_matrix": build_risk_matrix(record),
        "scenarios": build_narrative_scenarios(record),
        "brain_features": to_brain_fundamentals(record),
        "stale": is_stale(record),
    }
