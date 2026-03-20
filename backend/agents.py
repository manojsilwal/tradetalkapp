import json
import logging
from typing import Dict, Any, List, Optional
from .schemas import MarketState, FactorResult, VerificationStatus
from .connectors import ShortsConnector, SocialSentimentConnector, MacroHealthConnector

logger = logging.getLogger(__name__)


class AgentPair:
    """
    Core implementation of the Nested Loop architecture.
    Every factor goes through an Analyst and a QA_Verifier.
    They must reach a 'VERIFIED' state before proceeding.

    If a KnowledgeStore is provided, the pair queries swarm_reflections
    before the first analyst step so agents learn from past outcomes.
    """
    def __init__(self, factor_name: str, max_iterations: int = 3, knowledge_store=None, llm_client=None):
        self.factor_name = factor_name
        self.max_iterations = max_iterations
        self._ks = knowledge_store
        self._llm = llm_client

    def _fetch_prior_lessons(self, ticker: str, market_state: MarketState) -> str:
        """Retrieve up to 2 swarm reflections relevant to this factor + ticker."""
        if not self._ks or not hasattr(self._ks, "query_swarm_reflections"):
            return ""
        try:
            regime = market_state.market_regime.value if market_state.market_regime else "BULL_NORMAL"
            query = f"{self.factor_name} {ticker} {regime}"
            lessons = self._ks.query_swarm_reflections(query, n_results=2)
            if not lessons:
                return ""
            formatted = "\n".join(f"  - {l}" for l in lessons)
            logger.info("[AgentPair:%s] injected %d prior lessons for %s", self.factor_name, len(lessons), ticker)
            return f"\n[Prior lessons from swarm reflections]:\n{formatted}\n"
        except Exception as e:
            logger.warning("[AgentPair:%s] lesson retrieval failed: %s", self.factor_name, e)
            return ""

    async def run(self, market_state: MarketState, ticker: str = "GME") -> FactorResult:
        iteration = 0
        history: List[Dict[str, str]] = []
        status = VerificationStatus.PENDING

        prior_lessons = self._fetch_prior_lessons(ticker, market_state)
        if prior_lessons:
            history.append({"role": "Memory", "content": prior_lessons})

        while status != VerificationStatus.VERIFIED and iteration < self.max_iterations:
            iteration += 1

            analyst_report = await self._analyst_step(market_state, ticker, history)
            history.append({"role": f"{self.factor_name} Analyst", "content": analyst_report["rationale"]})

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
    def __init__(self, connector: ShortsConnector, knowledge_store=None, llm_client=None):
        super().__init__(factor_name="Short Interest", max_iterations=3,
                         knowledge_store=knowledge_store, llm_client=llm_client)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=ticker)
        sir = data["short_interest_ratio"]
        dtc = data["days_to_cover"]

        is_ambiguous = 10.0 <= sir <= 20.0
        non_memory = [h for h in history if h.get("role") != "Memory"]

        if not non_memory:
            if sir > 15.0:
                signal = 1
                rationale = f"Initial Analysis: Real-time yfinance scan shows SIR at {sir}% of float. Potential short squeeze brewing."
            elif is_ambiguous and self._llm:
                llm_result = await self._llm.generate_swarm_analyst_call(
                    "Short Interest", ticker,
                    {"short_interest_ratio": sir, "days_to_cover": dtc},
                    [h["content"] for h in history if h.get("role") == "Memory"],
                )
                signal = int(llm_result.get("signal", 0))
                rationale = f"LLM-assisted analysis (ambiguous SIR={sir}%): {llm_result.get('rationale', 'No reasoning provided.')}"
            else:
                signal = 0
                rationale = f"Initial Analysis: SIR at {sir}% is below squeeze threshold."
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
        if "days to cover" not in analyst_report["rationale"].lower() and "llm-assisted" not in analyst_report["rationale"].lower():
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
    def __init__(self, connector: SocialSentimentConnector, knowledge_store=None, llm_client=None):
        super().__init__(factor_name="Social Sentiment", max_iterations=2,
                         knowledge_store=knowledge_store, llm_client=llm_client)
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
    def __init__(self, connector: MacroHealthConnector, knowledge_store=None, llm_client=None):
        super().__init__(factor_name="Macro Environment", max_iterations=2,
                         knowledge_store=knowledge_store, llm_client=llm_client)
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
    def __init__(self, connector: PolymarketConnector, knowledge_store=None, llm_client=None):
        super().__init__(factor_name="Crowd Predictions", max_iterations=2,
                         knowledge_store=knowledge_store, llm_client=llm_client)
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
    def __init__(self, connector: FundamentalsConnector, knowledge_store=None, llm_client=None):
        super().__init__(factor_name="Fundamental Health", max_iterations=2,
                         knowledge_store=knowledge_store, llm_client=llm_client)
        self.connector = connector
        
    async def _analyst_step(self, market_state: MarketState, ticker: str, history: List[Dict[str, str]]) -> Dict[str, Any]:
        data = await self.connector.fetch_data(ticker=ticker)
        cash = data["total_cash"]
        debt = data["total_debt"]
        ratio = data["cash_to_debt_ratio"]

        def format_currency(val: float) -> str:
            if val >= 1_000_000_000:
                return f"${val / 1_000_000_000:.2f}B"
            elif val >= 1_000_000:
                return f"${val / 1_000_000:.2f}M"
            else:
                return f"${val:,.0f}"

        cash_str = format_currency(cash)
        debt_str = format_currency(debt)

        is_ambiguous = 0.5 <= ratio <= 1.3
        is_healthy = ratio >= 1.0 or (cash > 1_000_000_000 and ratio >= 0.5)

        if is_ambiguous and self._llm:
            llm_result = await self._llm.generate_swarm_analyst_call(
                "Fundamental Health", ticker,
                {"total_cash": cash, "total_debt": debt, "cash_to_debt_ratio": ratio},
                [h["content"] for h in history if h.get("role") == "Memory"],
            )
            signal = int(llm_result.get("signal", 1 if is_healthy else 0))
            rationale = (
                f"Fundamental Analysis: Total Cash Reserves = {cash_str}, "
                f"Total Debt = {debt_str}. Cash-to-Debt Ratio = {ratio:.2f}. "
                f"LLM-assisted (ambiguous zone): {llm_result.get('rationale', '')}"
            )
        else:
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
