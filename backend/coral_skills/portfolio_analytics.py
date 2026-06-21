"""
CORAL Hub Skill: Portfolio Analytics Engine

Responsible for:
- Computing portfolio weights for individual holdings.
- Aggregating sector allocations.
- Calculating Top 10 Concentration and HHI (Herfindahl-Hirschman Index).
- Identifying quarter-over-quarter position changes (NEW BUY, SOLD OUT, INCREASED, REDUCED).
"""
import logging
from typing import Dict, Any, List

from backend.coral_agents import hub_add_note

logger = logging.getLogger(__name__)

def compute_portfolio_analytics(current_holdings: List[Dict[str, Any]], previous_holdings: List[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Computes holding weights, sector allocations, and position changes.
    """
    logger.info("[Portfolio Analytics] Computing analytics for latest 13F")

    # 1. Total Market Value
    total_market_value = sum(h.get("market_value_usd", 0.0) for h in current_holdings)

    if total_market_value == 0:
        return {"error": "Total market value is zero"}

    # 2. Holding Weights & Top 10
    for h in current_holdings:
        h["weight"] = h.get("market_value_usd", 0.0) / total_market_value

    # Sort by weight descending
    current_holdings.sort(key=lambda x: x["weight"], reverse=True)
    top_10_weight = sum(h["weight"] for h in current_holdings[:10])

    # HHI (Herfindahl-Hirschman Index)
    hhi = sum((h["weight"] * 100) ** 2 for h in current_holdings)

    # 3. Sector Allocation
    sector_map = {}
    for h in current_holdings:
        sector = h.get("sector", "Unknown")
        if sector not in sector_map:
            sector_map[sector] = {"market_value_usd": 0.0, "holdings_count": 0}
        sector_map[sector]["market_value_usd"] += h.get("market_value_usd", 0.0)
        sector_map[sector]["holdings_count"] += 1

    sector_allocation = []
    for sector, data in sector_map.items():
        weight = data["market_value_usd"] / total_market_value
        sector_allocation.append({
            "sector": sector,
            "weight": weight,
            "market_value_usd": data["market_value_usd"],
            "holdings_count": data["holdings_count"]
        })

    # Sort sectors by weight
    sector_allocation.sort(key=lambda x: x["weight"], reverse=True)
    top_sector = sector_allocation[0]["sector"] if sector_allocation else None
    top_sector_weight = sector_allocation[0]["weight"] if sector_allocation else None

    # 4. Quarter-over-Quarter Position Changes
    position_changes = {
        "new_buys": 0,
        "sold_out": 0,
        "increased": 0,
        "reduced": 0,
        "unchanged": 0
    }

    if previous_holdings:
        prev_map = {h["cusip"]: h for h in previous_holdings if "cusip" in h}

        for h in current_holdings:
            cusip = h.get("cusip")
            curr_shares = h.get("shares", 0.0)

            if cusip not in prev_map:
                h["position_status"] = "NEW_BUY"
                position_changes["new_buys"] += 1
            else:
                prev_shares = prev_map[cusip].get("shares", 0.0)
                if curr_shares > prev_shares:
                    h["position_status"] = "INCREASED"
                    position_changes["increased"] += 1
                elif curr_shares < prev_shares:
                    h["position_status"] = "REDUCED"
                    position_changes["reduced"] += 1
                else:
                    h["position_status"] = "UNCHANGED"
                    position_changes["unchanged"] += 1

                prev_weight = prev_map[cusip].get("weight", 0.0)
                h["qoq_weight_change"] = h["weight"] - prev_weight

        # Check for Sold Out
        curr_map = {h["cusip"]: h for h in current_holdings if "cusip" in h}
        for h in previous_holdings:
            cusip = h.get("cusip")
            if cusip not in curr_map:
                position_changes["sold_out"] += 1
    else:
        for h in current_holdings:
            h["position_status"] = "UNKNOWN"
            h["qoq_weight_change"] = 0.0

    formatted_top_sector_weight = f"{top_sector_weight:.2%}" if top_sector_weight is not None else "0%"
    hub_add_note(
        "technical",
        f"Computed portfolio analytics: Top 10 Weight={top_10_weight:.2%}, Top Sector={top_sector} ({formatted_top_sector_weight})"
    )

    return {
        "total_market_value_usd": total_market_value,
        "holdings_count": len(current_holdings),
        "top_sector": top_sector,
        "top_sector_weight": top_sector_weight,
        "top_10_weight": top_10_weight,
        "hhi": hhi,
        "sector_allocation": sector_allocation,
        "position_changes_summary": position_changes,
        "holdings": current_holdings
    }
