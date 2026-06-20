"""Basic health and tool validation tests."""
import os


def test_health_endpoint():
    """Server health endpoint returns 200 with expected fields."""
    import httpx
    url = os.environ.get("SERVER_URL", "http://localhost:8080")
    r = httpx.get(f"{url}/health", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok" or "status" in data


def test_well_known_mcp():
    """Discovery endpoint returns valid JSON."""
    import httpx
    url = os.environ.get("SERVER_URL", "http://localhost:8080")
    r = httpx.get(f"{url}/.well-known/mcp.json", timeout=10)
    assert r.status_code == 200
    data = r.json()
    assert "name" in data
    assert "tools" in data or "capabilities" in data


if __name__ == "__main__":
    test_health_endpoint()
    test_well_known_mcp()
    print("All tests passed.")
