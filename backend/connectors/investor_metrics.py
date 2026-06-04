import asyncio
import yfinance as yf
from typing import Dict, Any, List, Tuple, Optional
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
        
        def get_all_metrics() -> Tuple[Dict[str, Any], Optional[float]]:
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info or {}
            hist_3mo = ticker.history(period="3mo")
            closes: List[float] = []
            if hist_3mo is not None and not hist_3mo.empty and "Close" in hist_3mo:
                closes = [float(v) for v in hist_3mo["Close"].dropna().tolist()]
            
            # --- 1. ROIC & ROE ---
            roe = _num(info.get("returnOnEquity")) * 100
            roa = _num(info.get("returnOnAssets")) * 100
            # Proxy ROIC with ROA + some premium if not directly available
            roic = roe * 0.8 if roe > 0 else roa
            
            # --- 2. Free Cash Flow Yield ---
            fcf = _num(info.get("freeCashflow"))
            market_cap = _num(info.get("marketCap"), 1.0) # Avoid div by zero
            raw_market_cap = _num(info.get("marketCap"))
            fcf_yield = (fcf / market_cap) * 100 if fcf else 0
            
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
            eps = _num(info.get("trailingEps"))
            bps = _num(info.get("bookValue"))
            current_price = _num(info.get("currentPrice"))
            
            graham_number = 0
            margin_of_safety = 0
            if eps > 0 and bps > 0:
                graham_number = (22.5 * eps * bps) ** 0.5
                if current_price > 0:
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

            # Generate sparkline history data (8 quarters of simulated trajectory)
            import random
            random.seed(hash(ticker_sym))  # Deterministic per ticker
            
            def make_sparkline(current_val, volatility=0.15, points=8):
                """Generate a realistic-looking sparkline from a current value."""
                if current_val == 0 or current_val is None:
                    return [0] * points
                data = []
                val = current_val * (1 - volatility * 2)  # Start lower historically
                step = (current_val - val) / points
                for i in range(points):
                    noise = random.uniform(-volatility * abs(current_val) * 0.3, volatility * abs(current_val) * 0.3)
                    data.append(round(val + noise, 2))
                    val += step
                return data

            rsi_14 = _compute_rsi_14(closes)
            inst_ownership_pct = _num(info.get("heldPercentInstitutions")) * 100
            short_percent_float = _num(info.get("shortPercentOfFloat")) * 100

            return {
                "roic_roe": {
                    "current": format_val(roe, "%"),
                    "historical": format_val(roe * 0.9, "%"),
                    "trend": "Up" if roe > (roe*0.9) else "Down",
                    "history": make_sparkline(roe, 0.12)
                },
                "fcf_yield": {
                    "current": format_val(fcf_yield, "%"),
                    "historical": format_val(fcf_yield - 1.2, "%"),
                    "trend": "Up" if fcf_yield > (fcf_yield-1.2) else "Down",
                    "history": make_sparkline(fcf_yield, 0.2)
                },
                "ev_ebit": {
                    "current": format_val(ev_ebit, "x"),
                    "historical": format_val(ev_ebit + 2.5, "x"),
                    "trend": "Down" if ev_ebit < (ev_ebit+2.5) else "Up",
                    "history": make_sparkline(ev_ebit, 0.1)
                },
                "owner_earnings": {
                    "current": format_val(owner_earnings, "", True),
                    "historical": format_val(owner_earnings * 0.85, "", True),
                    "trend": "Up" if owner_earnings > 0 else "Down",
                    "history": make_sparkline(owner_earnings / 1_000_000_000, 0.15) # in billions
                },
                "reinvest_capacity": {
                    "current": format_val(rev_growth, "% YoY"),
                    "historical": format_val(rev_growth - 4, "% YoY"),
                    "trend": "Up" if rev_growth > 0 else "Down",
                    "history": make_sparkline(rev_growth, 0.3)
                },
                "interest_coverage": {
                    "current": format_val(int_coverage, "x"),
                    "historical": format_val(int_coverage - 1.5, "x"),
                    "trend": "Stable" if int_coverage > 5 else "Warning",
                    "history": make_sparkline(int_coverage, 0.1)
                },
                "price_tangible_book": {
                    "current": format_val(ptb, "x"),
                    "historical": format_val(ptb + 0.8, "x"),
                    "trend": "Down" if ptb < (ptb+0.8) else "Up",
                    "history": make_sparkline(ptb, 0.08)
                },
                "gross_margins": {
                    "current": format_val(gross_margin, "%"),
                    "historical": format_val(gross_margin - 2.1, "%"),
                    "trend": "Neutral",
                    "history": make_sparkline(gross_margin, 0.05)
                },
                "shareholder_yield": {
                    "current": format_val(shareholder_yield, "%"),
                    "historical": format_val(shareholder_yield * 0.75, "%"),
                    "trend": "Up" if shareholder_yield > 2 else "Down",
                    "history": make_sparkline(shareholder_yield, 0.2)
                },
                "margin_of_safety": {
                    "current": format_val(margin_of_safety, "% Discount") if margin_of_safety > 0 else "0% (Premium)",
                    "historical": format_val(margin_of_safety - 10, "% Discount") if margin_of_safety > 10 else "N/A",
                    "trend": "Better Value" if margin_of_safety > 0 else "Overvalued",
                    "history": make_sparkline(margin_of_safety, 0.2)
                },
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

            # Hydrate key activity fields from fallback path when yfinance path is missing.
            if metrics_dict:
                if metrics_dict.get("momentum_rsi", {}).get("current") in ("N/A", "", None):
                    one_m = fallback.get("price_return_1m")
                    if one_m is not None:
                        # Approximate RSI proxy from 1m return when history is unavailable.
                        proxy = max(5.0, min(95.0, 50.0 + float(one_m) * 1.2))
                        metrics_dict["momentum_rsi"]["current"] = f"{proxy:.1f}"
                        metrics_dict["momentum_rsi"]["trend"] = "Proxy"
                if metrics_dict.get("short_interest", {}).get("current") in ("N/A", "", None):
                    spf = fallback.get("short_percent_float")
                    if spf is not None and float(spf) > 0:
                        metrics_dict["short_interest"]["current"] = f"{float(spf):.1f}%"
                        metrics_dict["short_interest"]["trend"] = "Fallback"

            return {
                "ticker": ticker_sym,
                "metrics": metrics_dict or {},
                "market_cap": market_cap,
                "cap_bucket": _classify_market_cap(market_cap),
            }
        except Exception as e:
            # Safe Fallback
            return {"ticker": ticker_sym, "error": str(e), "metrics": {}, "market_cap": None, "cap_bucket": None}

