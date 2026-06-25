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


def test_high_growth_dcf_emits_ordered_tiers():
    snapshot = {
        "sharesOutstanding": 100_000_000,
        "marketCap": 1_000_000_000,
        "beta": 1.5,
        "totalDebt": 200_000_000,
        "totalCash": 500_000_000,
        "totalRevenue": 200_000_000,
        "revenueGrowth": 0.45,
        "grossMargins": 0.80,
        "freeCashflow": 30_000_000,
    }
    result = compute_high_growth_dcf_scenarios(snapshot)
    tiers = result["dcf_tiers"]
    order = ["bear", "conservative_base", "base", "bull", "extreme_bull"]
    assert list(tiers.keys()) == order
    vals = [tiers[k] for k in order]
    assert all(v is not None for v in vals)
    # Monotonically non-decreasing across the optimism ladder.
    assert vals == sorted(vals)


def test_high_growth_hold_then_fade_shape(monkeypatch):
    """The projection should hold initial growth ~3y then fade — verified by
    capturing the growth path passed to the engine's multi_stage_path."""
    from backend import dcf_engine
    captured = {}
    orig = dcf_engine.multi_stage_path

    def spy(anchor, terminal_growth, years, **kwargs):
        path = orig(anchor, terminal_growth, years, **kwargs)
        captured.setdefault("paths", []).append((anchor, path))
        return path

    monkeypatch.setattr("backend.valuation_inputs.dcf_engine.multi_stage_path", spy)
    snapshot = {
        "sharesOutstanding": 100_000_000, "marketCap": 1_000_000_000, "beta": 1.5,
        "totalDebt": 200_000_000, "totalCash": 500_000_000, "totalRevenue": 200_000_000,
        "revenueGrowth": 0.45, "grossMargins": 0.80, "freeCashflow": 30_000_000,
    }
    compute_high_growth_dcf_scenarios(snapshot)
    base_anchor, base_path = next(p for p in captured["paths"] if abs(p[0] - 0.45) < 1e-9)
    # Years 1..3 hold the anchor, later years fade below it.
    assert base_path[0] == pytest.approx(base_anchor)
    assert base_path[2] == pytest.approx(base_anchor)
    assert base_path[-1] < base_anchor
