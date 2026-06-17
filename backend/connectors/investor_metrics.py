import asyncio
import yfinance as yf
from typing import Dict, Any, List, Tuple, Optional
from ..data_errors import InsufficientDataError
from .base import DataConnector, clean_dividend_yield
from .debate_data import fetch_debate_data
from ..paper_portfolio import _classify_market_cap

class InvestorMetricsConnector(DataConnector):
    """
    Fetches the 10 fundamental metrics used by elite value/distressed investors.
    Provides current (latest) vs historical (prior year/quarter) values where applicable
    or educational proxies for highly subjective metrics.
    """
    
    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        ticker_sym = kwargs.get("ticker", "GME").upper()

        def _num(v: Any, default: float = 0.0) -> float:
            try:
                if v is None:
                    return float(default)
                return float(v)
            except (TypeError, ValueError):
                return float(default)

        def _compute_rsi_14(prices: List[float]) -> float | None:
            if len(prices) < 15:
                return None
            gains: List[float] = []
            losses: List[float] = []
            for i in range(1, len(prices)):
                d = prices[i] - prices[i - 1]
                gains.append(max(d, 0.0))
                losses.append(abs(min(d, 0.0)))
            avg_gain = sum(gains[-14:]) / 14.0
            avg_loss = sum(losses[-14:]) / 14.0
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return 100.0 - (100.0 / (1.0 + rs))
        
        def _blank_key_activity() -> Dict[str, Dict[str, Any]]:
            empty = {"current": "N/A", "historical": "N/A", "trend": "N/A", "history": []}
            return {
                "momentum_rsi": dict(empty),
                "institutional_ownership": dict(empty),
                "short_interest": dict(empty),
            }

        def _hydrate_key_activity(
            metrics_dict: Dict[str, Any], fallback: Dict[str, Any]
        ) -> None:
            """Fill ownership / short interest from debate_data when Yahoo is thin.

            Only real values are hydrated — no proxies. A metric that cannot be
            sourced stays "N/A" (truthful-data contract).
            """
            for key in ("momentum_rsi", "institutional_ownership", "short_interest"):
                if key not in metrics_dict or not isinstance(metrics_dict[key], dict):
                    metrics_dict[key] = {
                        "current": "N/A",
                        "historical": "N/A",
                        "trend": "N/A",
                        "history": [],
                    }
            inst = metrics_dict["institutional_ownership"]
            if inst.get("current") in ("N/A", "", None):
                inst_pct = fallback.get("held_percent_institutions")
                if inst_pct is not None and float(inst_pct) > 0:
                    inst["current"] = f"{float(inst_pct):.1f}%"
                    inst["trend"] = "Fallback"
            short = metrics_dict["short_interest"]
            if short.get("current") in ("N/A", "", None):
                spf = fallback.get("short_percent_float")
                if spf is not None and float(spf) > 0:
                    short["current"] = f"{float(spf):.1f}%"
                    short["trend"] = "Fallback"

        def get_all_metrics() -> Tuple[Dict[str, Any], Optional[float]]:
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info or {}
            hist_3mo = ticker.history(period="3mo")
            closes: List[float] = []
            if hist_3mo is not None and not hist_3mo.empty and "Close" in hist_3mo:
                closes = [float(v) for v in hist_3mo["Close"].dropna().tolist()]
            
            # --- 1. ROIC & ROE ---
            from backend.metric_primitives import fcf_yield_percent, roic_proxy

            roe = _num(info.get("returnOnEquity")) * 100
            roa = _num(info.get("returnOnAssets")) * 100
            roic_proxy_pct = roic_proxy(roe) if roe > 0 else roa
            
            # --- 2. Free Cash Flow Yield ---
            fcf = _num(info.get("freeCashflow"))
            market_cap = _num(info.get("marketCap"), 1.0) # Avoid div by zero
            raw_market_cap = _num(info.get("marketCap"))
            fcf_yield = fcf_yield_percent(fcf, raw_market_cap) or 0
            
            # --- 3. EV/EBIT ---
            ev = _num(info.get("enterpriseValue"))
            ebitda = _num(info.get("ebitda"))
            ev_ebit = (ev / ebitda) if ebitda else 0
            
            # --- 4. Owner Earnings ---
            # Proxy: Net Income + Depreciation (often roughly equals Operating Cash Flow)
            op_cashflow = _num(info.get("operatingCashflow"))
            capex = _num(info.get("capitalExpenditures")) # usually negative
            owner_earnings = op_cashflow + capex # adding negative effectively subtracts
            
            # --- 5. Capacity to Reinvest ---
            # Highly subjective. Proxy: Retained earnings growth or simply Revenue Growth
            rev_growth = _num(info.get("revenueGrowth")) * 100
            
            # --- 6. Interest Coverage ---
            ebitda_margin = _num(info.get("ebitdaMargins"))
            # Yfinance doesn't cleanly expose interest expense in simple info dict often.
            # Proxy based on totalDebt
            total_debt = _num(info.get("totalDebt"))
            # Assume 5% average interest rate on debt
            est_interest = total_debt * 0.05
            int_coverage = (ebitda / est_interest) if est_interest > 0 else 999
            
            # --- 7. Price-to-Tangible Book ---
            ptb = _num(info.get("priceToBook")) # Using P/B as close proxy
            
            # --- 8. Gross & Operating Margins ---
            gross_margin = _num(info.get("grossMargins")) * 100
            op_margin = _num(info.get("operatingMargins")) * 100
            
            dividend_yield_pct = clean_dividend_yield(info.get("dividendYield"))
            # Approximate buyback yield from share repurchase data
            shares_outstanding = _num(info.get("sharesOutstanding"), 1.0)
            # yfinance doesn't directly expose buyback $ easily; approximate from 
            # the difference between operating cash flow allocation
            buyback_amount = _num(info.get("shareRepurchasesAndIssuances"))
            if buyback_amount == 0:
                # Fallback: estimate from FCF minus dividends paid
                dividends_paid = abs(_num(info.get("lastDividendValue"))) * shares_outstanding
                estimated_buyback = max(0, fcf - dividends_paid - capex) * 0.5  # Conservative estimate
                buyback_yield_pct = (estimated_buyback / market_cap) * 100 if market_cap > 0 else 0
            else:
                buyback_yield_pct = (abs(buyback_amount) / market_cap) * 100 if market_cap > 0 else 0
            shareholder_yield = dividend_yield_pct + buyback_yield_pct
            
            # --- 10. Margin of Safety ---
            # Calculated intrinsic value discount. 
            # We'll calculate a crude Graham Number as an intrinsic value proxy
            from backend.metric_primitives import graham_fair_value as _graham
            from backend.connectors.spot import resolve_spot

            eps = _num(info.get("trailingEps"))
            bps = _num(info.get("bookValue"))
            spot_q = resolve_spot(ticker_sym)
            current_price = spot_q.price if spot_q else _num(info.get("currentPrice"))
            
            graham_number = 0
            margin_of_safety = 0
            if eps > 0 and bps > 0:
                g_num = _graham(eps, bps)
                graham_number = g_num or 0
                if current_price and current_price > 0 and graham_number:
                    discount = (graham_number - current_price) / graham_number
                    margin_of_safety = discount * 100
                    
            def format_val(val, suffix="", is_currency=False):
                if val == 0 or val is None: return "N/A"
                if is_currency:
                    if abs(val) >= 1_000_000_000:
                        return f"${val/1_000_000_000:.1f}B"
                    elif abs(val) >= 1_000_000:
                        return f"${val/1_000_000:.1f}M"
                    return f"${val:.2f}"
                return f"{val:.1f}{suffix}"

            # Truthful-data contract: no simulated sparklines and no fabricated
            # "historical" values. History is empty until a real time-series
            # source is wired; historical/trend stay "N/A".
            rsi_14 = _compute_rsi_14(closes)
            inst_ownership_pct = _num(info.get("heldPercentInstitutions")) * 100
            short_percent_float = _num(info.get("shortPercentOfFloat")) * 100

            def metric_entry(current_formatted: str) -> Dict[str, Any]:
                return {
                    "current": current_formatted,
                    "historical": "N/A",
                    "trend": "N/A",
                    "history": [],
                }

            return {
                "roic_roe": metric_entry(format_val(roe, "%")),
                "roe": metric_entry(format_val(roe, "%")),
                "roic_proxy_pct": metric_entry(format_val(roic_proxy_pct, "%")),
                "fcf_yield": metric_entry(format_val(fcf_yield, "%")),
                "ev_ebit": metric_entry(format_val(ev_ebit, "x")),
                "owner_earnings": metric_entry(format_val(owner_earnings, "", True)),
                "reinvest_capacity": metric_entry(format_val(rev_growth, "% YoY")),
                "interest_coverage": metric_entry(format_val(int_coverage, "x")),
                "price_tangible_book": metric_entry(format_val(ptb, "x")),
                "gross_margins": metric_entry(format_val(gross_margin, "%")),
                "shareholder_yield": metric_entry(format_val(shareholder_yield, "%")),
                "margin_of_safety": metric_entry(
                    format_val(margin_of_safety, "% Discount") if margin_of_safety > 0 else "0% (Premium)"
                ),
                # Explicit keys used by UnifiedDashboardUI "Key Metrics Activity"
                "momentum_rsi": {
                    "current": format_val(rsi_14) if rsi_14 is not None else "N/A",
                    "historical": "N/A",
                    "trend": "N/A",
                },
                "institutional_ownership": {
                    "current": format_val(inst_ownership_pct, "%") if inst_ownership_pct > 0 else "N/A",
                    "historical": "N/A",
                    "trend": "N/A",
                },
                "short_interest": {
                    "current": format_val(short_percent_float, "%") if short_percent_float > 0 else "N/A",
                    "historical": "N/A",
                    "trend": "N/A",
                },
            }, raw_market_cap if raw_market_cap and raw_market_cap > 0 else None
            
        try:
            # Parallel acquisition: primary yfinance metrics + fallback debate_data
            yf_task = asyncio.to_thread(get_all_metrics)
            fallback_task = fetch_debate_data(ticker_sym)
            metrics, fallback = await asyncio.gather(yf_task, fallback_task, return_exceptions=True)

            market_cap: Optional[float] = None
            metrics_dict: Dict[str, Any] = {}

            if isinstance(metrics, Exception):
                metrics_dict = {}
            elif isinstance(metrics, tuple) and len(metrics) == 2:
                metrics_dict, market_cap = metrics
            elif isinstance(metrics, dict):
                metrics_dict = metrics

            if isinstance(fallback, Exception):
                fallback = {}
            if not isinstance(fallback, dict):
                fallback = {}

            if not metrics_dict and not fallback:
                raise InsufficientDataError(
                    "yfinance",
                    f"Live fundamental metrics unavailable for {ticker_sym}: "
                    "both primary and fallback fetches failed.",
                    ticker=ticker_sym,
                    missing=["metrics"],
                )

            if not metrics_dict and fallback:
                metrics_dict = _blank_key_activity()
                market_cap = fallback.get("market_cap") or market_cap

            _hydrate_key_activity(metrics_dict, fallback)

            return {
                "ticker": ticker_sym,
                "metrics": metrics_dict,
                "market_cap": market_cap,
                "cap_bucket": _classify_market_cap(market_cap),
            }
        except InsufficientDataError:
            raise
        except Exception as e:
            try:
                fallback = await fetch_debate_data(ticker_sym)
            except Exception:
                fallback = {}
            if isinstance(fallback, dict) and fallback:
                metrics_dict = _blank_key_activity()
                _hydrate_key_activity(metrics_dict, fallback)
                mc = fallback.get("market_cap")
                return {
                    "ticker": ticker_sym,
                    "metrics": metrics_dict,
                    "market_cap": mc,
                    "cap_bucket": _classify_market_cap(mc),
                }
            raise InsufficientDataError(
                "yfinance",
                f"Live fundamental metrics unavailable for {ticker_sym}: {e}",
                ticker=ticker_sym,
                missing=["metrics"],
            ) from e

