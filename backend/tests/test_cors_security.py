from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)

def test_cors_allowed_methods():
    # Preflight request
    headers = {
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type",
    }
    response = client.options("/auth/me", headers=headers)
    assert response.status_code == 200

    allowed_methods = response.headers.get("access-control-allow-methods", "")
    methods_list = [m.strip() for m in allowed_methods.split(",")]

    # Check that expected methods are present
    expected_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"]
    for method in expected_methods:
        assert method in methods_list

    # Check that unauthorized methods are NOT present (if CORS middleware filters them in Access-Control-Allow-Methods)
    # FastAPI's CORSMiddleware by default returns the allowed_methods list in response to preflight
    assert "PATCH" not in methods_list
    assert "TRACE" not in methods_list

def test_cors_origin_validation():
    # Valid origin
    headers = {"Origin": "http://localhost:5173"}
    response = client.get("/auth/me", headers=headers)
    # We don't care about auth status here, just CORS headers
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    # Invalid origin
    headers = {"Origin": "http://malicious.com"}
    response = client.get("/auth/me", headers=headers)
    assert "access-control-allow-origin" not in response.headers
