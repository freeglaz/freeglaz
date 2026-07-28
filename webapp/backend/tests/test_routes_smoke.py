"""Smoke tests for the webapp backend routes."""
from fastapi.testclient import TestClient

from webapp.backend.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "argyll" in body  # Argyll availability carried for diagnostics
