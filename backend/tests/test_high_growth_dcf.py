import pytest
from backend.valuation_inputs import compute_high_growth_dcf_scenarios

def test_high_growth_dcf_basic():
    snapshot = {
        "sharesOutstanding": 100_000_000,
        "marketCap": 1_000_000_000,
        "beta": 1.5,
        "totalDebt": 200_000_000,
        "totalCash": 500_000_000,
        "totalRevenue": 200_000_000,
        "revenueGrowth": 0.45,
        "grossMargins": 0.80,
        "freeCashflow": -50_000_000,
    }

    result = compute_high_growth_dcf_scenarios(snapshot)

    assert result["available"] is True
    assert result["scenarios"]["base"] is not None
    assert result["scenarios"]["bear"] is not None
    assert result["scenarios"]["bull"] is not None
    assert result["current_fcf_margin"] == -0.25
    assert result["revenue_growth"] == 0.45
    assert result["target_fcf_margin_base"] > 0
    assert "high_growth_sensitivity" in result["valuation_warning_flags"]

def test_high_growth_dcf_insufficient_inputs():
    snapshot = {
        "sharesOutstanding": 100_000_000,
        "totalRevenue": 0, # Missing revenue
        "revenueGrowth": 0.45,
    }
    result = compute_high_growth_dcf_scenarios(snapshot)
    assert result["available"] is False
    assert "Insufficient revenue" in result["missing_reason"]
