import pytest
from unittest.mock import patch, MagicMock

import pandas as pd
from backend.coral_skills.portfolio_analytics import compute_portfolio_analytics
from backend.coral_skills.leaderboard_scoring import calculate_data_confidence, rank_leaderboard
from backend.coral_skills.return_reconstruction import calculate_clone_returns

def test_portfolio_analytics():
    current_holdings = [
        {"cusip": "123", "market_value_usd": 100, "sector": "Tech"},
        {"cusip": "456", "market_value_usd": 50, "sector": "Fin"}
    ]
    prev_holdings = [
        {"cusip": "123", "shares": 50, "weight": 0.5},
        {"cusip": "789", "shares": 10, "weight": 0.5} # Sold out
    ]

    # Needs shares in current to measure increased/reduced
    current_holdings[0]["shares"] = 100
    current_holdings[1]["shares"] = 10 # New buy

    with patch("backend.coral_skills.portfolio_analytics.hub_add_note"):
        result = compute_portfolio_analytics(current_holdings, prev_holdings)

    assert result["total_market_value_usd"] == 150
    assert result["top_sector"] == "Tech"
    assert result["position_changes_summary"]["new_buys"] == 1
    assert result["position_changes_summary"]["sold_out"] == 1
    assert result["position_changes_summary"]["increased"] == 1

def test_leaderboard_scoring():
    funds = [
        {"fund_id": "1", "metrics": {"cagr": 0.20, "sharpe": 1.5, "maxDrawdown": -0.1}},
        {"fund_id": "2", "metrics": {"cagr": 0.10, "sharpe": 0.8, "maxDrawdown": -0.3}}
    ]
    ranked = rank_leaderboard(funds)
    assert ranked[0]["fund_id"] == "1"
    assert ranked[1]["fund_id"] == "2"
    assert ranked[0]["rank"] == 1

def test_calculate_data_confidence():
    conf = calculate_data_confidence(32, 32, 100, 100, 100)
    assert conf["score"] == 100
    assert conf["label"] == "High"
