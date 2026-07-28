"""Tests for serving the built frontend.

The frontend dist/ is conditional: if present, the backend serves the SPA
bundle; otherwise it serves a minimal install HTML page. Tests that depend
on the dist/ bundle are skipped if not built locally.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import FRONTEND_DIST, app

_DIST_INDEX = FRONTEND_DIST / "index.html"


@pytest.fixture
def client():
    return TestClient(app)


def test_api_health_still_works(client):
    """Non-regression: /api/health keeps working even with the SPA
    catch-all registered behind it."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_root_serves_index_html_when_dist_present(client):
    """If dist/index.html exists, GET / returns HTML (200)."""
    if not _DIST_INDEX.exists():
        pytest.skip("Frontend non buildé — run: cd webapp/frontend && npm run build")
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # Verify we got index.html (presence of the React root tag)
    assert "<div id=\"root\"" in r.text or "id='root'" in r.text


def test_unknown_route_returns_index_html_spa_fallback(client):
    """SPA fallback: unknown non-API route returns index.html (200).

    Would let a future React Router handle sub-routes on the frontend
    side without backend intervention."""
    if not _DIST_INDEX.exists():
        pytest.skip("Frontend non buildé")
    r = client.get("/some/random/path")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_unknown_api_route_returns_404_not_index(client):
    """Guard: /api/unknown must NOT return index.html. Otherwise a typo
    in an API path would give 200 HTML, masking the bug.

    This test passes whether dist/ is present or not:
      - dist absent: no catch-all -> natural 404 from the FastAPI router
      - dist present: explicit catch-all with `startswith("api/")` guard
    """
    r = client.get("/api/this-route-does-not-exist")
    assert r.status_code == 404
