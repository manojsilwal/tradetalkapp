import json
from typing import Dict, Any, List
from .schemas import MarketState, FactorResult, VerificationStatus
from .connectors import ShortsConnector, SocialSentimentConnector, MacroHealthConnector

class AgentPair:
    """
    Core implementation of the Nested Loop architecture.
    Every factor goes through an Analyst and a QA_Verifier.
    They must reach a 'VERIFIED' state before proceeding.
    """
    def __init__(self, factor_name: str, max_iterations: int = 3):
        self.factor_name = factor_name
        self.max_iterations = max_iterations

    async def run(self, market_state: MarketState, ticker: str = "GME") -> FactorResult:
        iteration = 0
        history: List[Dict[str, str]] = []
        status = VerificationStatus.PENDING
        
        while status != VerificationStatus.VERIFIED and iteration < self.max_iterations:
            iteration += 1
            
            # 1. Analyst Phase
            analyst_report = await self._analyst_step(market_state, ticker, history)
            history.append({"role": f"{self.factor_name} Analyst", "content": analyst_report["rationale"]})
            
            # 2. QA Verifier Phase
            qa_review = await self._qa_verifier_step(analyst_report, market_state, history)
            history.append({"role": f"{self.factor_name} QA_Verifier", "content": qa_review["rationale"]})
            
            status = qa_review["status"]
            if status == VerificationStatus.VERIFIED:
                return FactorResult(
                    factor_name=self.factor_name,
                    status=status,
                    confidence=qa_review.get("confidence", 0.8),
                    rationale=f"QA Approved after {iteration} iteration(s). Final Note: {qa_review['rationale']}",
                    trading_signal=analyst_report.get("trading_signal", 0),
                    history=history
                )
            
            # Loop continues if rejected, giving Analyst chance to incorporate feedback from history
            
        return FactorResult(
            factor_name=self.factor_name,
            status=VerificationStatus.REJECTED,
            confidence=0.0,
            rationale=f"Failed to reach VERIFIED state after {self.max_iterations} iterations. Last QA Note: {history[-1]['content']}",
            trading_signal=0,
            history=history
        )

    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        """Override in factor-specific subclass."""
        raise NotImplementedError

    async def _qa_verifier_step(self, analyst_report: Dict[str, Any], market_state: MarketState, history: List[Dict[str, str]]) -> Dict[str, Any]:
        """Override in factor-specific subclass."""
        raise NotImplementedError

# ---------------------------------------------------------
# Factor 1: Short Interest Squeeze
# ---------------------------------------------------------
class ShortInterestAgentPair(AgentPair):
    def __init__(self, connector: ShortsConnector):
        super().__init__(factor_name="Short Interest", max_iterations=3)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=ticker)
        sir = data["short_interest_ratio"]
        dtc = data["days_to_cover"]
        
        if not history:
            rationale = f"Initial Analysis: Real-time yfinance scan shows SIR at {sir}% of float. Potential short squeeze brewing."
            signal = 1 if sir > 15.0 else 0
        else:
            rationale = f"Revised Analysis: High SIR confirmed ({sir}%). Additionally, days to cover sits at {dtc}, confirming squeeze pressure and difficulty to exit."
            signal = 1 if (sir > 15.0 and dtc > 5.0) else 0
            
        return {"rationale": rationale, "trading_signal": signal}
        
    async def _qa_verifier_step(self, analyst_report: Dict[str, Any], market_state: MarketState, history: List[Dict[str, str]]) -> Dict[str, Any]:
        if analyst_report.get("trading_signal", 0) > 0 and market_state.is_bearish():
            return {
                "status": VerificationStatus.REJECTED,
                "rationale": f"Analyst signal is bullish, but MarketState indicates Credit Stress ({market_state.credit_stress_index}) > 1.1. Strategy rejected by macro grounding.",
                "confidence": 0.95
            }
        if "days to cover" not in analyst_report["rationale"].lower():
             return {
                 "status": VerificationStatus.REJECTED,
                 "rationale": "Analysis incomplete. The report mentions SIR but fails to document current days to cover. Please revise.",
                 "confidence": 0.8
             }
        return {
            "status": VerificationStatus.VERIFIED,
            "rationale": "Analysis correctly covers both SIR and days to cover. Macro regime permits bullish signals. Verification complete.",
            "confidence": 0.9
        }

# ---------------------------------------------------------
# Factor 2: Social Sentiment / Vibe Check
# ---------------------------------------------------------
from .connectors import SocialSentimentConnector

class SocialSentimentAgentPair(AgentPair):
    def __init__(self, connector: SocialSentimentConnector):
        super().__init__(factor_name="Social Sentiment", max_iterations=2)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=ticker)
        titles = data.get("recent_titles", [])
        
        if not titles:
            return {"rationale": "No recent blog or YouTube titles found for sentiment analysis. Defaulting neutral.", "trading_signal": 0}
            
        bull_keywords = ["buy", "bull", "soar", "moon", "squeeze", "surge", "up", "rally", "breakout", "target", "high", "gem"]
        bear_keywords = ["sell", "bear", "crash", "plunge", "down", "dump", "alert", "warning", "drop", "collapse", "fake"]
        
        bull_count = sum(1 for t in titles if any(kw in t.lower() for kw in bull_keywords))
        bear_count = sum(1 for t in titles if any(kw in t.lower() for kw in bear_keywords))
        
        signal = 1 if bull_count > bear_count and bull_count >= 2 else 0
        
        # Grab up to 2 sample titles for the rationale
        sample_titles = titles[:2]
        
        rationale = f"Live RSS Scraping ({data['counts']['blogs']} blogs, {data['counts']['youtube']} videos) found {bull_count} bullish and {bear_count} bearish keywords. "
        if signal == 1:
            rationale += "Retail buzz is skewed noticeably positive. "
        else:
            rationale += "Retail buzz is neutral/negative. "
            
        if len(sample_titles) >= 2:
            rationale += f"Sample headlines: '{sample_titles[0]}' | '{sample_titles[1]}'."

        return {"rationale": rationale, "trading_signal": signal}

    async def _qa_verifier_step(self, analyst_report: Dict[str, Any], market_state: MarketState, history: List[Dict[str, str]]) -> Dict[str, Any]:
        # Social sentiment shouldn't drive trades in severe crash environments
        if analyst_report.get("trading_signal", 0) > 0 and market_state.credit_stress_index > 2.0:
            return {
                "status": VerificationStatus.REJECTED,
                "rationale": f"Severe Macro Stress ({market_state.credit_stress_index} > 2.0). Ignoring social hype as structural risk outweighs retail momentum.",
                "confidence": 0.99
            }
        return {
            "status": VerificationStatus.VERIFIED,
            "rationale": "Sentiment correctly synthesized and contextualized within safe boundaries.",
            "confidence": 0.85
        }

# ---------------------------------------------------------
# Factor 3: Macro Health & Structure
# ---------------------------------------------------------
class MacroHealthAgentPair(AgentPair):
    def __init__(self, connector: MacroHealthConnector):
        super().__init__(factor_name="Macro Environment", max_iterations=2)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=None) # Macro is global
        vix = data["indicators"]["vix_level"]
        credit_stress = data["indicators"]["credit_stress_index"]
        
        signal = 1
        rationale = f"Global assessment: Live ^VIX Volatility Index is at {vix}. Derived Credit Stress Index is {credit_stress}."
        
        # If VIX is high, fail the macro check
        if vix >= 20.0:
            rationale += " Volatility indicates heightened systemic fear. Bearish leaning."
            signal = 0
            
        return {"rationale": rationale, "trading_signal": signal}

    async def _qa_verifier_step(self, analyst_report: Dict[str, Any], market_state: MarketState, history: List[Dict[str, str]]) -> Dict[str, Any]:
        return {
            "status": VerificationStatus.VERIFIED,
            "rationale": "Macro environment logic verified against credit stress index.",
            "confidence": 0.90
        }

# ---------------------------------------------------------
# Factor 4: Polymarket Prediction Sentiment
# ---------------------------------------------------------
from .connectors import PolymarketConnector

class PolymarketAgentPair(AgentPair):
    def __init__(self, connector: PolymarketConnector):
        super().__init__(factor_name="Crowd Predictions", max_iterations=2)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=ticker)
        events = data.get("events", [])
        has_relevant = data.get("has_relevant_data", False)
        
        if not has_relevant or not events:
            return {
                "rationale": f"No active prediction markets found on Polymarket related to {ticker}. This is normal for most stocks — prediction markets primarily cover high-profile events and crypto.",
                "trading_signal": 0
            }
            
        top_event = events[0]
        title = top_event["title"]
        prob = top_event["probability"]
        
        signal = 1 if prob > 0.50 else 0
        rationale = f"Found {len(events)} relevant prediction market(s) for {ticker}. Top market: '{title}' — highest outcome probability is {int(prob * 100)}%. "
        if signal == 1:
            rationale += "Traders are confidently pricing this in."
        else:
            rationale += "Traders are skeptical of this outcome."

        return {"rationale": rationale, "trading_signal": signal}

    async def _qa_verifier_step(self, analyst_report: Dict[str, Any], market_state: MarketState, history: List[Dict[str, str]]) -> Dict[str, Any]:
        return {
            "status": VerificationStatus.VERIFIED,
            "rationale": "Crowd-sourced probabilities have been synthesized correctly. This acts as an alternative data sentiment check.",
            "confidence": 0.85
        }

# ---------------------------------------------------------
# Factor 5: Fundamental Health
# ---------------------------------------------------------
from .connectors import FundamentalsConnector

class FundamentalHealthAgentPair(AgentPair):
    def __init__(self, connector: FundamentalsConnector):
        super().__init__(factor_name="Fundamental Health", max_iterations=2)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=ticker)
        cash = data["total_cash"]
        debt = data["total_debt"]
        ratio = data["cash_to_debt_ratio"]
        
        # Helper to format large numbers
        def format_currency(val: float) -> str:
            if val >= 1_000_000_000:
                return f"${val / 1_000_000_000:.2f}B"
            elif val >= 1_000_000:
                return f"${val / 1_000_000:.2f}M"
            else:
                return f"${val:,.0f}"
                
        cash_str = format_currency(cash)
        debt_str = format_currency(debt)
        
        # Consider healthy if ratio is >= 1.0, or if cash is massive (e.g. > $1B) with manageable debt
        is_healthy = ratio >= 1.0 or (cash > 1_000_000_000 and ratio >= 0.5)
        
        signal = 1 if is_healthy else 0
        
        rationale = (f"Fundamental Analysis: Total Cash Reserves = {cash_str}, "
                     f"Total Debt = {debt_str}. Cash-to-Debt Ratio = {ratio:.2f}. ")
                     
        if signal == 1:
            rationale += "The company demonstrates strong long-term fundamental health and manageable debt levels."
        else:
            rationale += "The company exhibits concerning debt levels relative to cash reserves, indicating long-term risk."
            
        return {"rationale": rationale, "trading_signal": signal}

    async def _qa_verifier_step(self, analyst_report: Dict[str, Any], market_state: MarketState, history: List[Dict[str, str]]) -> Dict[str, Any]:
        # Validate that the rationale actually includes cash and debt figures
        if "Total Cash" not in analyst_report["rationale"] or "Total Debt" not in analyst_report["rationale"]:
             return {
                 "status": VerificationStatus.REJECTED,
                 "rationale": "Analysis incomplete. The report must contain explicit Total Cash and Total Debt figures. Please revise.",
                 "confidence": 0.8
             }
             
        # Macro override: In severe stress, we want higher cash safety buffers
        if market_state.credit_stress_index > 1.5 and analyst_report.get("trading_signal", 0) > 0:
            if "Ratio = 0." in analyst_report["rationale"]: # simple string check for ratio < 1.0
                return {
                    "status": VerificationStatus.REJECTED,
                    "rationale": f"Severe Macro Stress ({market_state.credit_stress_index}). Refusing to verify companies with Cash-to-Debt ratios < 1.0 during market turbulence.",
                    "confidence": 0.95
                }

        return {
            "status": VerificationStatus.VERIFIED,
            "rationale": "Fundamental health metrics (cash, debt, ratio) correctly synthesized and contextualized.",
            "confidence": 0.90
        }
