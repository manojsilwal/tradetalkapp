from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_leaderboard_endpoint():
    response = client.get("/api/funds/leaderboard")
    assert response.status_code == 200
    data = response.json()
    assert "rows" in data
    assert len(data["rows"]) == 1
    assert data["rows"][0]["fundName"] == "Example Capital Management"

def test_fund_portfolio_endpoint():
    response = client.get("/api/funds/123/portfolio/latest")
    assert response.status_code == 200
    data = response.json()
    assert data["fundId"] == "123"
    assert "holdings" in data

def test_fund_returns_endpoint():
    response = client.get("/api/funds/123/returns")
    assert response.status_code == 200
    data = response.json()
    assert data["fundId"] == "123"
    assert "metrics" in data
    assert data["metrics"]["cagr"] == 0.184

def test_fund_quarterly_report_endpoint():
    response = client.get("/api/funds/123/quarterly-report")
    assert response.status_code == 200
    data = response.json()
    assert data["fundId"] == "123"
    assert data["numberOfHoldings"] == 87
