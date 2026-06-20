"""
CORAL Hub Skill: Leaderboard Scoring & Data Confidence Engine

Responsible for:
- Computing the composite Data Confidence Score.
- Ranking funds based on the composite Leaderboard Score.
"""
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

def calculate_data_confidence(
    valid_quarters: int,
    expected_quarters: int,
    mapped_market_value: float,
    total_market_value: float,
    priced_market_value: float,
    amendment_penalty: int = 0,
    strategy_fit_score: int = 100,
    reconciliation_score: int = 100
) -> Dict[str, Any]:
    """
    Computes a 0-100 data confidence score based on the weighted components defined in the PRD.
    """
    if expected_quarters <= 0 or total_market_value <= 0:
        return {"score": 0, "label": "Not reliable"}

    filing_completeness = (valid_quarters / expected_quarters) * 100
    mapping_score = (mapped_market_value / total_market_value) * 100
    price_score = (priced_market_value / total_market_value) * 100

    # Require 32 quarters for full track record length score
    track_record_score = min(valid_quarters / 32.0, 1.0) * 100

    amendment_stability = max(0, 100 - amendment_penalty)

    confidence = (
        0.20 * filing_completeness +
        0.20 * mapping_score +
        0.20 * price_score +
        0.15 * track_record_score +
        0.10 * amendment_stability +
        0.10 * strategy_fit_score +
        0.05 * reconciliation_score
    )

    score = int(round(confidence))

    if score >= 90: label = "High"
    elif score >= 75: label = "Good"
    elif score >= 60: label = "Medium"
    elif score >= 40: label = "Low"
    else: label = "Not reliable"

    return {
        "score": score,
        "label": label,
        "components": {
            "filing_completeness": filing_completeness,
            "mapping_score": mapping_score,
            "price_score": price_score,
            "track_record_score": track_record_score
        }
    }

def rank_leaderboard(funds: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Given a list of funds with pre-calculated metrics, computes percentile rankings
    and generates the final leaderboard score for ranking.
    """
    if not funds:
        return []

    logger.info(f"[Leaderboard Scoring] Ranking {len(funds)} funds")

    # Helper to calculate percentiles
    def assign_percentiles(key: str, default_val: float = 0.0):
        # Sort by the specific metric
        sorted_funds = sorted(funds, key=lambda f: f.get("metrics", {}).get(key, default_val))
        n = len(sorted_funds)
        for i, f in enumerate(sorted_funds):
            pct = i / (n - 1) if n > 1 else 1.0
            if "percentiles" not in f:
                f["percentiles"] = {}
            f["percentiles"][key] = pct

    # Assign percentiles for positive factors
    assign_percentiles("cagr")
    assign_percentiles("alphaVsBenchmark")
    assign_percentiles("sharpe")
    assign_percentiles("sortino")
    assign_percentiles("positiveQuarterRate")

    # For drawdown, a lower (more negative) drawdown is worse, so the raw percentile is backward.
    # The default key access sorts from most negative to least negative, so higher percentile = less drawdown.
    assign_percentiles("maxDrawdown")

    # Confidence and completeness percentiles
    sorted_conf = sorted(funds, key=lambda f: f.get("confidence", {}).get("score", 0))
    n = len(sorted_conf)
    for i, f in enumerate(sorted_conf):
        f["percentiles"]["dataConfidenceScore"] = i / (n - 1) if n > 1 else 1.0
        # Simplistic track record completeness proxy using the confidence score component
        f["percentiles"]["trackRecordCompleteness"] = f.get("confidence", {}).get("components", {}).get("track_record_score", 0) / 100.0

    # Calculate final composite score
    for f in funds:
        p = f["percentiles"]
        score = (
            0.25 * p.get("cagr", 0) +
            0.20 * p.get("alphaVsBenchmark", 0) +
            0.15 * p.get("sharpe", 0) +
            0.10 * p.get("sortino", 0) +
            0.10 * p.get("maxDrawdown", 0) +
            0.08 * p.get("positiveQuarterRate", 0) +
            0.07 * p.get("dataConfidenceScore", 0) +
            0.05 * p.get("trackRecordCompleteness", 0)
        )
        f["leaderboard_score"] = score

    # Sort descending by composite score
    ranked_funds = sorted(funds, key=lambda f: f["leaderboard_score"], reverse=True)

    # Assign rank
    for i, f in enumerate(ranked_funds):
        f["rank"] = i + 1

    return ranked_funds
