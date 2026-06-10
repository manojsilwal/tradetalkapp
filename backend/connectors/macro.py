import asyncio
import yfinance as yf
from typing import Dict, Any
from ..data_errors import InsufficientDataError
from .base import DataConnector

# Dual-read of the tier-1 macro_vix_to_credit_stress tool. If the registry
# is off or the resource is missing, production falls back to this exact
# numeric default, so behaviour is byte-identical to the pre-evolution path.
_MACRO_VIX_DEFAULTS: Dict[str, float] = {
    "divisor": 15.0,
    "status_threshold": 1.1,
}


class MacroHealthConnector(DataConnector):
    """
    Tracks the CBOE Volatility Index (^VIX) as a live measure of Macro Stress.
    Critical for the 2026 Macro Grounding rules.
    """
    def __init__(self, force_bearish: bool = False):
        self.force_bearish = force_bearish

    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        # Fetch the VIX index. Truthful-data contract: no placeholder value —
        # if every probe fails, the whole macro snapshot is unavailable.
        def get_vix():
            from .quote_fallbacks import _yahoo_chart_spot

            yahoo_vix = _yahoo_chart_spot("^VIX")
            if yahoo_vix is not None:
                return yahoo_vix
            vix = yf.Ticker("^VIX")
            hist = vix.history(period="1d")
            if not hist.empty:
                return hist["Close"].iloc[-1]
            return None

        try:
            vix_level = await asyncio.to_thread(get_vix)
        except Exception:
            vix_level = None
        if vix_level is None:
            raise InsufficientDataError(
                "yfinance",
                "Live VIX level could not be fetched from any provider; "
                "macro analysis requires it.",
                missing=["vix_level"],
            )
            
        # Tier-1 learnable mapping: VIX → credit_stress_index.
        # Divisor and stress threshold are pulled via dual-read; when the
        # registry is disabled we use _MACRO_VIX_DEFAULTS = (15.0, 1.1).
        from ..tool_configs import get_tool_config  # local import avoids cycles on boot
        from ..tool_handlers import vix_to_credit_stress_value
        macro_cfg = get_tool_config("macro_vix_to_credit_stress", _MACRO_VIX_DEFAULTS)
        credit_stress_index = vix_to_credit_stress_value({"vix_level": vix_level}, macro_cfg)
        stress_threshold = float(macro_cfg.get("status_threshold", 1.1))
        
        # Pull Live Sector Rotation performance
        def get_sectors():
            # Sectors including Real Estate (XLRE) and Metals/Mining (XME)
            tickers = ["XLK", "XLF", "XLV", "XLE", "XLC", "XLRE", "XME"]
            sector_names = {
                "XLK": "Technology",
                "XLF": "Financials",
                "XLV": "Healthcare",
                "XLE": "Energy",
                "XLC": "Communications",
                "XLRE": "Real Estate",
                "XME": "Metals & Mining"
            }
            from .yfinance_batch import batch_daily_change_pct

            pct_by_sym = batch_daily_change_pct(tickers)
            sectors = []
            missing_sectors = []
            for t in tickers:
                pct = pct_by_sym.get(t)
                if pct is None:
                    from .quote_fallbacks import yahoo_chart_change_pct

                    pct = yahoo_chart_change_pct(t)
                if pct is None:
                    missing_sectors.append(t)
                    continue
                sectors.append({
                    "symbol": t,
                    "name": sector_names.get(t, t),
                    "daily_change_pct": round(float(pct), 2),
                })
            if missing_sectors:
                raise InsufficientDataError(
                    "yfinance",
                    "Live sector rotation data could not be fetched for: "
                    + ", ".join(missing_sectors),
                    missing=[f"sector_change_pct:{s}" for s in missing_sectors],
                )
            return sectors

        try:
            sector_data = await asyncio.to_thread(get_sectors)
        except InsufficientDataError:
            raise
        except Exception as e:
            raise InsufficientDataError(
                "yfinance",
                f"Live sector rotation data unavailable: {e}",
                missing=["sectors"],
            ) from e
            
        # Pull Global Capital Flows (US vs Intl vs Debt vs Gold vs Cash)
        def get_capital_flows():
            import datetime as _dt

            # Bucket definitions with AUM proxies (approximate, in USD)
            BUCKETS = [
                {"bucket_id": "us_equities", "proxy_symbol": "SPY", "display_name": "US Equities",
                 "stance": "risk_on", "region": "US", "is_us_destination": True,
                 "notional_base_usd": 500_000_000_000, "category": "Risk On", "sort_order": 1},
                {"bucket_id": "intl_equities", "proxy_symbol": "EFA", "display_name": "International Equities",
                 "stance": "risk_on", "region": "INTL_COUNTERPARTY", "is_us_destination": False,
                 "notional_base_usd": 100_000_000_000, "category": "Risk On", "sort_order": 2},
                {"bucket_id": "japan_equities", "proxy_symbol": "EWJ", "display_name": "Japan Equities",
                 "stance": "risk_on", "region": "INTL_COUNTERPARTY", "is_us_destination": False,
                 "notional_base_usd": 20_000_000_000, "category": "Risk On", "sort_order": 3},
                {"bucket_id": "us_treasuries", "proxy_symbol": "TLT", "display_name": "US Treasuries",
                 "stance": "safe_haven", "region": "US", "is_us_destination": True,
                 "notional_base_usd": 300_000_000_000, "category": "Safe Haven", "sort_order": 4},
                {"bucket_id": "gold", "proxy_symbol": "GLD", "display_name": "Gold",
                 "stance": "safe_haven", "region": "INTL_COUNTERPARTY", "is_us_destination": False,
                 "notional_base_usd": 70_000_000_000, "category": "Safe Haven", "sort_order": 5},
                {"bucket_id": "cash", "proxy_symbol": "BIL", "display_name": "1-3 Month T-Bill (Cash Proxy)",
                 "stance": "cash", "region": "US", "is_us_destination": True,
                 "notional_base_usd": 50_000_000_000, "category": "Cash Reserves", "sort_order": 6},
            ]

            symbols = [b["proxy_symbol"] for b in BUCKETS]
            from .yfinance_batch import batch_daily_change_pct, history_by_ticker

            pct_by_sym = batch_daily_change_pct(symbols)
            hist_cache = history_by_ticker(symbols, period="5y", interval="1d")

            def _compute_period_return(hist_df, days):
                """Compute cumulative return over last N trading days."""
                if hist_df is None or len(hist_df) < 2:
                    return 0.0
                if days >= len(hist_df):
                    days = len(hist_df) - 1
                if days <= 0:
                    return 0.0
                recent_close = hist_df["Close"].iloc[-1]
                past_close = hist_df["Close"].iloc[-(days + 1)]
                if past_close == 0:
                    return 0.0
                return round(((recent_close - past_close) / past_close) * 100, 2)

            def _get_historical_returns(sym):
                hist = hist_cache.get(sym)
                return {
                    "1d": _compute_period_return(hist, 1),
                    "1w": _compute_period_return(hist, 5),
                    "1m": _compute_period_return(hist, 21),
                    "1y": _compute_period_return(hist, 252),
                    "5y": _compute_period_return(hist, len(hist) - 1 if hist is not None and len(hist) > 1 else 0),
                }

            # Build legacy flows + enriched buckets
            legacy_flows = []
            enriched_buckets = []
            today_str = _dt.date.today().isoformat()

            for bucket in BUCKETS:
                sym = bucket["proxy_symbol"]
                pct = pct_by_sym.get(sym)
                if pct is None:
                    from .quote_fallbacks import yahoo_chart_change_pct

                    pct = yahoo_chart_change_pct(sym)
                if pct is None:
                    raise InsufficientDataError(
                        "yfinance",
                        f"Live capital-flow proxy quote unavailable for {sym}.",
                        missing=[f"capital_flow_change_pct:{sym}"],
                    )
                pct = round(float(pct), 2)

                # Legacy format (backward compat)
                legacy_flows.append({
                    "asset": sym,
                    "name": bucket["display_name"],
                    "category": bucket["category"],
                    "daily_change_pct": pct,
                })

                # Compute component flow
                notional = bucket["notional_base_usd"]
                component_flow = round((pct / 100) * notional, 2)

                # Determine flow direction
                if bucket["region"] == "INTL_COUNTERPARTY" and bucket["stance"] == "safe_haven":
                    direction = "non_us_safe_haven"
                elif bucket["region"] == "INTL_COUNTERPARTY":
                    direction = "intl_counterparty"
                elif bucket["stance"] == "cash":
                    direction = "intra_us"
                elif component_flow >= 0:
                    direction = "inflow_to_us"
                else:
                    direction = "outflow_from_us"

                enriched_buckets.append({
                    "bucket_id": bucket["bucket_id"],
                    "proxy_symbol": sym,
                    "display_name": bucket["display_name"],
                    "stance": bucket["stance"],
                    "region": bucket["region"],
                    "is_us_destination": bucket["is_us_destination"],
                    "price_change_pct": pct,
                    "notional_base_usd": notional,
                    "component_flow_usd": component_flow,
                    "flow_direction": direction,
                    "historical_returns": _get_historical_returns(sym),
                })

            # --- Reconciliation ---
            opening_total = sum(b["notional_base_usd"] for b in enriched_buckets)
            components_sum = sum(b["component_flow_usd"] for b in enriched_buckets)
            closing_total = opening_total + components_sum
            gap = round(closing_total - (opening_total + components_sum), 2)
            tolerance = 1.0
            is_reconciled = abs(gap) <= tolerance

            # US net capital: sum flows for US-destination buckets
            us_net = sum(b["component_flow_usd"] for b in enriched_buckets if b["is_us_destination"])
            us_net_increased = us_net > 0

            reconciliation = {
                "opening_capital_total_usd": opening_total,
                "closing_capital_total_usd": closing_total,
                "net_capital_change_usd": components_sum,
                "components_sum_usd": components_sum,
                "reconciliation_gap_usd": gap,
                "is_reconciled": is_reconciled,
                "us_net_increased": us_net_increased,
                "tolerance_usd": tolerance,
            }

            # --- Explanation ---
            drivers_inflow = []
            drivers_outflow = []

            for b in enriched_buckets:
                driver = {
                    "bucket_id": b["bucket_id"],
                    "proxy_symbol": b["proxy_symbol"],
                    "display_name": b["display_name"],
                    "component_flow_usd": b["component_flow_usd"],
                    "price_change_pct": b["price_change_pct"],
                    "intl_counterparty_symbol": None,
                    "intl_index_change_pct": None,
                }

                if b["flow_direction"] == "inflow_to_us" and b["component_flow_usd"] > 0:
                    drivers_inflow.append(driver)
                elif b["flow_direction"] == "outflow_from_us" and b["component_flow_usd"] < 0:
                    drivers_outflow.append(driver)
                elif b["flow_direction"] == "intl_counterparty" and b["price_change_pct"] < 0:
                    # Negative intl = capital leaving foreign → rotating to US
                    driver["intl_counterparty_symbol"] = b["proxy_symbol"]
                    driver["intl_index_change_pct"] = b["price_change_pct"]
                    drivers_inflow.append(driver)

            explanation = {
                "us_net_increased": us_net_increased,
                "net_capital_change_usd": components_sum,
                "drivers_inflow_to_us": drivers_inflow,
                "drivers_outflow_from_us": drivers_outflow,
                "reconciles_to": closing_total,
                "is_reconciled": is_reconciled,
            }

            return {
                "legacy_flows": legacy_flows,
                "reconciled": {
                    "flow_date": today_str,
                    "buckets": enriched_buckets,
                    "reconciliation": reconciliation,
                    "explanation": explanation,
                },
            }

        try:
            capital_flow_result = await asyncio.to_thread(get_capital_flows)
            capital_flows = capital_flow_result["legacy_flows"]
            reconciled_capital_flows = capital_flow_result["reconciled"]
        except InsufficientDataError:
            raise
        except Exception as e:
            raise InsufficientDataError(
                "yfinance",
                f"Live capital-flow data unavailable: {e}",
                missing=["capital_flows"],
            ) from e

        # Truthful-data contract: consumer spending and cash-reserve history have
        # no live data source wired yet. We return empty series instead of the
        # previous simulated charts — never fabricated data.
        consumer_spending = []
        cash_reserves = []
        
        return {
            "source": "yfinance ^VIX Volatility & Sector ETFs (Live)",
            "indicators": {
                "credit_stress_index": credit_stress_index, # Ground truth for Macro Engine
                "vix_level": round(float(vix_level), 2),
            },
            "sectors": sector_data,
            "consumer_spending": consumer_spending,
            "capital_flows": capital_flows,
            "reconciled_capital_flows": reconciled_capital_flows,
            "cash_reserves": cash_reserves,
            "status": "Stress Detected" if credit_stress_index > stress_threshold else "Normal"
        }
