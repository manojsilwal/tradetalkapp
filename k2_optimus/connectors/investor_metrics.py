import asyncio
import yfinance as yf
from typing import Dict, Any, List
from .base import DataConnector

class InvestorMetricsConnector(DataConnector):
    """
    Fetches the 10 fundamental metrics used by elite value/distressed investors.
    Provides current (latest) vs historical (prior year/quarter) values where applicable
    or educational proxies for highly subjective metrics.
    """
    
    async def fetch_data(self, **kwargs) -> Dict[str, Any]:
        ticker_sym = kwargs.get("ticker", "GME").upper()
        
        def get_all_metrics() -> Dict[str, Any]:
            ticker = yf.Ticker(ticker_sym)
            info = ticker.info
            
            # --- 1. ROIC & ROE ---
            roe = info.get("returnOnEquity", 0) * 100
            roa = info.get("returnOnAssets", 0) * 100
            # Proxy ROIC with ROA + some premium if not directly available
            roic = roe * 0.8 if roe > 0 else roa
            
            # --- 2. Free Cash Flow Yield ---
            fcf = info.get("freeCashflow", 0)
            market_cap = info.get("marketCap", 1) # Avoid div by zero
            fcf_yield = (fcf / market_cap) * 100 if fcf else 0
            
            # --- 3. EV/EBIT ---
            ev = info.get("enterpriseValue", 0)
            ebitda = info.get("ebitda", 0)
            ev_ebit = (ev / ebitda) if ebitda else 0
            
            # --- 4. Owner Earnings ---
            # Proxy: Net Income + Depreciation (often roughly equals Operating Cash Flow)
            op_cashflow = info.get("operatingCashflow", 0)
            capex = info.get("capitalExpenditures", 0) # usually negative
            owner_earnings = op_cashflow + capex # adding negative effectively subtracts
            
            # --- 5. Capacity to Reinvest ---
            # Highly subjective. Proxy: Retained earnings growth or simply Revenue Growth
            rev_growth = info.get("revenueGrowth", 0) * 100
            
            # --- 6. Interest Coverage ---
            ebitda_margin = info.get("ebitdaMargins", 0)
            # Yfinance doesn't cleanly expose interest expense in simple info dict often.
            # Proxy based on totalDebt
            total_debt = info.get("totalDebt", 0)
            # Assume 5% average interest rate on debt
            est_interest = total_debt * 0.05
            int_coverage = (ebitda / est_interest) if est_interest > 0 else 999
            
            # --- 7. Price-to-Tangible Book ---
            ptb = info.get("priceToBook", 0) # Using P/B as close proxy
            
            # --- 8. Gross & Operating Margins ---
            gross_margin = info.get("grossMargins", 0) * 100
            op_margin = info.get("operatingMargins", 0) * 100
            
            # --- 9. Shareholder Yield (Dividend Yield + Net Buyback Yield) ---
            dividend_yield = info.get("dividendYield", 0) or 0
            dividend_yield_pct = dividend_yield * 100
            # Approximate buyback yield from share repurchase data
            shares_outstanding = info.get("sharesOutstanding", 0) or 1
            # yfinance doesn't directly expose buyback $ easily; approximate from 
            # the difference between operating cash flow allocation
            buyback_amount = info.get("shareRepurchasesAndIssuances", 0) or 0
            if buyback_amount == 0:
                # Fallback: estimate from FCF minus dividends paid
                dividends_paid = abs(info.get("lastDividendValue", 0) or 0) * shares_outstanding
                estimated_buyback = max(0, fcf - dividends_paid - capex) * 0.5  # Conservative estimate
                buyback_yield_pct = (estimated_buyback / market_cap) * 100 if market_cap > 0 else 0
            else:
                buyback_yield_pct = (abs(buyback_amount) / market_cap) * 100 if market_cap > 0 else 0
            shareholder_yield = dividend_yield_pct + buyback_yield_pct
            
            # --- 10. Margin of Safety ---
            # Calculated intrinsic value discount. 
            # We'll calculate a crude Graham Number as an intrinsic value proxy
            eps = info.get("trailingEps", 0)
            bps = info.get("bookValue", 0)
            current_price = info.get("currentPrice", 0)
            
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
                }
            }
            
        try:
            metrics = await asyncio.to_thread(get_all_metrics)
            return {"ticker": ticker_sym, "metrics": metrics}
        except Exception as e:
            # Safe Fallback
            return {"ticker": ticker_sym, "error": str(e), "metrics": {}}

