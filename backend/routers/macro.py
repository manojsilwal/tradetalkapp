"""Macro, metrics, and gold advisor endpoints."""
from fastapi import APIRouter, Depends, Query
from ..schemas import MacroDataResponse, InvestorMetricsResponse, GoldAdvisorResponse
from ..auth import get_optional_user
from ..cron_auth import require_cron_secret
from ..rate_limiter import rate_limit
from ..deps import macro_connector, investor_metrics_connector, llm_client, up, knowledge_store

router = APIRouter(tags=["macro"])

_rl_expensive = rate_limit("expensive")

_ALLOWED_FLOW_IV = frozenset({"1d", "1w", "1m", "1y"})


def _flow_interval(interval: str) -> str:
    iv = (interval or "1w").strip().lower()
    return iv if iv in _ALLOWED_FLOW_IV else "1w"


async def _ensure_macro_flow_snapshot(interval: str) -> None:
    from ..macro_flow.store import latest_rrg_payload
    from ..macro_flow.orchestrator import run_macro_flow_pipeline_safe

    if latest_rrg_payload(interval):
        return
    await run_macro_flow_pipeline_safe(interval, knowledge_store=knowledge_store)


@router.get("/macro", response_model=MacroDataResponse)
async def get_macro_data():
    """Global Macro Analysis Endpoint."""
    data = await macro_connector.fetch_data()
    ind = data["indicators"]
    return MacroDataResponse(
        vix_level=ind["vix_level"],
        credit_stress_index=ind["credit_stress_index"],
        market_regime="BULL_NORMAL" if ind["credit_stress_index"] <= 1.1 else "BEAR_STRESS",
        sectors=data["sectors"],
        consumer_spending=data["consumer_spending"],
        capital_flows=data["capital_flows"],
        cash_reserves=data["cash_reserves"],
        usd_broad_index=ind.get("usd_broad_index"),
        usd_index_change_5d_pct=ind.get("usd_index_change_5d_pct"),
        usd_strength_label=ind.get("usd_strength_label") or "unknown",
        dxy_level=ind.get("dxy_level"),
        dxy_change_5d_pct=ind.get("dxy_change_5d_pct"),
        dxy_strength_label=ind.get("dxy_strength_label") or "unknown",
        treasury_2y=ind.get("treasury_2y"),
        treasury_10y=ind.get("treasury_10y"),
        yield_curve_spread_10y_2y=ind.get("yield_curve_spread_10y_2y"),
        fed_funds_rate=ind.get("fed_funds_rate"),
        cpi_yoy=ind.get("cpi_yoy"),
        unemployment_rate=ind.get("unemployment"),
        macro_narrative=ind.get("macro_narrative") or "",
        fred_fetched_at=ind.get("fred_fetched_at"),
    )


@router.get("/metrics/{ticker}", response_model=InvestorMetricsResponse)
async def get_investor_metrics(ticker: str):
    """Fetches live fundamental metrics."""
    data = await investor_metrics_connector.fetch_data(ticker=ticker)
    if "error" in data:
        return InvestorMetricsResponse(ticker=ticker.upper(), metrics={})
    return InvestorMetricsResponse(ticker=ticker.upper(), metrics=data["metrics"])


@router.get("/metrics/validate/{ticker}")
async def validate_ticker_fast(ticker: str):
    """
    Fast ticker existence probe backed by yfinance.
    Returns ``exists=false`` when no usable quote can be resolved.
    """
    import asyncio

    sym = (ticker or "").strip().upper()
    if not sym or len(sym) > 12:
        return {"ticker": sym, "exists": False, "reason": "invalid_format"}

    def _probe() -> tuple[bool, float | None]:
        # Use Yahoo chart endpoint directly with hard network timeout for a
        # fast existence check on newly-entered symbols.
        import requests

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        params = {"range": "1d", "interval": "1d"}
        r = requests.get(url, params=params, timeout=2.5)
        r.raise_for_status()
        data = r.json() or {}
        chart = (data.get("chart") or {}).get("result") or []
        if not chart:
            return False, None
        meta = chart[0].get("meta") or {}
        px = meta.get("regularMarketPrice")
        if px is None:
            return False, None
        try:
            v = float(px)
            return (v > 0), v
        except (TypeError, ValueError):
            return False, None

    try:
        ok, price = await asyncio.wait_for(asyncio.to_thread(_probe), timeout=3.0)
    except (asyncio.TimeoutError, TimeoutError):
        return {
            "ticker": sym,
            "exists": False,
            "reason": "probe_timeout",
        }
    except Exception:
        return {
            "ticker": sym,
            "exists": False,
            "reason": "probe_failed",
        }
    return {
        "ticker": sym,
        "exists": bool(ok),
        "last_price": price,
    }


@router.get("/advisor/gold", response_model=GoldAdvisorResponse, dependencies=[Depends(_rl_expensive)])
async def gold_advisor_snapshot(_auth_user=Depends(get_optional_user)):
    """Gold allocator snapshot: FRED real yields, VIX, DXY, gold futures, LLM briefing."""
    from ..gold_advisor_service import run_gold_advisor
    result = await run_gold_advisor(macro_connector, llm_client)
    if _auth_user:
        try:
            up.award_xp(_auth_user.id, "gold_advisor", note="gold_snapshot")
        except Exception:
            pass
    return GoldAdvisorResponse(context=result["context"], briefing=result["briefing"])


@router.get("/macro/flow/categories")
async def macro_flow_categories():
    from ..macro_flow.store import list_categories_from_db, taxonomy_fallback

    rows = list_categories_from_db()
    if not rows:
        rows = taxonomy_fallback()
    return {"categories": rows}


@router.get("/macro/flow/rrg")
async def macro_flow_rrg(interval: str = Query("1w")):
    from ..macro_flow.store import latest_rrg_payload

    iv = _flow_interval(interval)
    await _ensure_macro_flow_snapshot(iv)
    return {"interval": iv, "points": latest_rrg_payload(iv)}


@router.post("/macro/flow/refresh", dependencies=[Depends(_rl_expensive)])
async def macro_flow_refresh(interval: str = Query("1w")):
    from ..macro_flow.orchestrator import run_macro_flow_pipeline

    iv = _flow_interval(interval)
    out = await run_macro_flow_pipeline(iv, knowledge_store=knowledge_store)
    if out.get("error"):
        return {"ok": False, **out}
    return {"ok": True, **out}


@router.post("/macro/flow/cron-refresh", dependencies=[Depends(require_cron_secret)])
async def macro_flow_cron_refresh(interval: str = Query("1w")):
    """Scheduled refresh (GitHub Actions / Render) — same secret as ``/knowledge/pipeline-run``."""
    from ..macro_flow.orchestrator import run_macro_flow_pipeline

    iv = _flow_interval(interval)
    out = await run_macro_flow_pipeline(iv, knowledge_store=knowledge_store)
    if out.get("error"):
        return {"ok": False, **out}
    return {"ok": True, **out}


@router.get("/macro/flow/sankey")
async def macro_flow_sankey(interval: str = Query("1w")):
    from ..macro_flow.store import latest_rrg_payload, latest_edge_flows

    iv = _flow_interval(interval)
    await _ensure_macro_flow_snapshot(iv)
    pts = latest_rrg_payload(iv)
    edges = latest_edge_flows(iv)
    nodes = []
    seen = set()
    for p in pts:
        cid = p.get("category_id")
        if cid and cid not in seen:
            seen.add(cid)
            nodes.append(
                {
                    "id": cid,
                    "name": p.get("name") or cid,
                    "qa_verdict": p.get("qa_verdict"),
                    "flow_score": p.get("flow_score"),
                }
            )
    links = []
    for e in edges:
        mag = e.get("flow_magnitude")
        if mag is None:
            continue
        links.append(
            {
                "source": e.get("source_category"),
                "target": e.get("target_category"),
                "value": abs(float(mag)),
                "edge_id": e.get("edge_id"),
                "description": e.get("description"),
            }
        )
    for n in nodes:
        cid = n.get("id")
        for p in pts:
            if p.get("category_id") == cid and p.get("color_hex"):
                n["color_hex"] = p.get("color_hex")
                break
    return {"interval": iv, "nodes": nodes, "links": links}


@router.get("/macro/flow/stock-graph", dependencies=[Depends(_rl_expensive)])
async def macro_flow_stock_graph(interval: str = Query("1w")):
    """S&P 500 stock-level co-flow graph (correlation-weighted directed edges)."""
    from ..macro_flow.stock_graph import build_stock_flow_graph_async

    iv = _flow_interval(interval)
    return await build_stock_flow_graph_async(iv)


@router.get("/macro/flow/value-chain")
async def macro_flow_value_chain(
    theme: str = Query("ai-infra"),
    interval: str = Query("1w"),
):
    from ..macro_flow.store import value_chain_payload

    iv = _flow_interval(interval)
    await _ensure_macro_flow_snapshot(iv)
    return value_chain_payload(theme, iv)


@router.get("/macro/flow/timeline")
async def macro_flow_timeline(interval: str = Query("1w"), limit: int = Query(30, ge=1, le=120)):
    from ..macro_flow.store import flow_timeline

    iv = _flow_interval(interval)
    await _ensure_macro_flow_snapshot(iv)
    return {"interval": iv, "snapshots": flow_timeline(iv, limit=limit)}


# ── Supply chain capital flow (entity-level directed graph) ──────────────────

@router.get("/macro/supply-chain/graph")
async def supply_chain_graph(
    year: int | None = Query(None),
    root: str | None = Query(None),
):
    from ..supply_chain.store import get_graph
    return get_graph(year=year, root=root)


@router.get("/macro/supply-chain/nodes/{node_id}")
async def supply_chain_node_detail(node_id: str, year: int | None = Query(None)):
    from ..supply_chain.store import get_node_detail
    from fastapi import HTTPException as _HTTPExc

    detail = get_node_detail(node_id, year=year)
    if not detail:
        raise _HTTPExc(status_code=404, detail=f"Node {node_id!r} not found")
    return detail


@router.post("/macro/supply-chain/extract-preview")
async def supply_chain_extract_preview(
    ticker: str = Query(...),
    form: str = Query("10-K"),
):
    from ..supply_chain.extract_agent import extract_supply_chain_preview
    return await extract_supply_chain_preview(ticker, form=form)


@router.post("/macro/supply-chain/reseed")
async def supply_chain_reseed(_=Depends(require_cron_secret)):
    from ..supply_chain.seed_chains import seed_supply_chain_db, node_count
    seed_supply_chain_db()
    return {"ok": True, "nodes": node_count()}


@router.get("/macro/supply-chain/timeline")
async def supply_chain_timeline(
    year_from: int = Query(2020, alias="from"),
    year_to: int = Query(2026, alias="to"),
    root: str | None = Query(None),
):
    from ..supply_chain.temporal import get_snapshots
    snapshots = get_snapshots(year_from=year_from, year_to=year_to, root=root)
    return {"year_from": year_from, "year_to": year_to, "root": root, "snapshots": snapshots}


@router.get("/macro/supply-chain/sector-sankey")
async def supply_chain_sector_sankey(year: int = Query(2025)):
    from ..supply_chain.sector_rollup import sector_sankey
    return sector_sankey(year)


@router.get("/macro/supply-chain/sector-sankey/timeline")
async def supply_chain_sector_sankey_timeline(
    year_from: int = Query(2020, alias="from"),
    year_to: int = Query(2026, alias="to"),
):
    from ..supply_chain.sector_rollup import sector_sankey_timeline
    return {"year_from": year_from, "year_to": year_to, "snapshots": sector_sankey_timeline(year_from, year_to)}
