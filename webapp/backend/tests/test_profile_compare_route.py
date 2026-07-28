"""Tests profile-compare — route POST /api/profiles/compare.

Covers:
- structured OK response for 2 and 3 profiles
- with_curvature=True propagates the experimental badge
- 422 if <2 profiles, ti3_paths mismatch, non-absolute path
- 404 if file missing
- 422 if >8 profiles (anti-abuse hard cap)

Fixtures: synthetic non-HP printer profiles (`fixtures/synthetic_test_resident_*.icc`)
+ bundled sRGB profiles (`lib/z9_client/assets/`).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ASSETS = Path(__file__).resolve().parents[3] / "lib" / "z9_client" / "assets"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_RES_A = _FIXTURES / "synthetic_test_resident_A.icc"
_RES_B = _FIXTURES / "synthetic_test_resident_B.icc"


def _asset(name: str) -> Path:
    p = ASSETS / name
    if not p.exists():
        pytest.skip(f"asset manquant: {name}")
    return p


@pytest.fixture
def client():
    from webapp.backend.routes.profiles import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ─── 200 — normal structure ──────────────────────────────────────────


def test_compare_two_profiles_returns_200_and_structured_body(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_RES_B),
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["n_profiles"] == 2
    assert body["meta"]["with_curvature"] is False
    assert body["meta"]["with_profcheck"] is False
    assert len(body["profiles"]) == 2
    # ICC identity surfaced
    assert body["profiles"][0]["icc_version"]
    assert body["profiles"][0]["color_space"] == "RGB"
    # Experimental notice absent when flag off
    assert "curvature_notice" not in body


def test_compare_three_profiles_returns_200(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_asset("sRGB_lcms_v10.icc")),
            str(_asset("eciRGB_v2.icc")),
        ],
    })
    assert r.status_code == 200
    assert r.json()["meta"]["n_profiles"] == 3


def test_compare_with_curvature_carries_experimental_badge(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_RES_B),
        ],
        "with_curvature": True,
    })
    assert r.status_code == 200
    body = r.json()
    # Root notice present
    assert "curvature_notice" in body
    assert "EXPERIMENTAL" in body["curvature_notice"]
    # Badge in each profile entry
    for e in body["profiles"]:
        c = e["curvature"]
        assert c is not None
        assert c["experimental"] is True
        assert "EXPERIMENTAL" in c["notice"]


# ─── 422 — validation ─────────────────────────────────────────────────


def test_compare_rejects_single_profile_422(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [str(_RES_A)],
    })
    assert r.status_code == 422


def test_compare_rejects_non_absolute_path_422(client):
    r = client.post("/api/profiles/compare", json={
        "paths": ["foo.icc", "bar.icc"],
    })
    assert r.status_code == 422
    assert "must be absolute" in r.json()["detail"]


def test_compare_rejects_ti3_paths_count_mismatch_422(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_RES_B),
        ],
        "ti3_paths": ["/tmp/only-one.ti3"],
    })
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "ti3_paths" in detail


def test_compare_rejects_too_many_profiles_422(client):
    # 9 > 8 (hard cap)
    paths = [str(_RES_A)] * 9
    r = client.post("/api/profiles/compare", json={"paths": paths})
    assert r.status_code == 422


def test_compare_rejects_non_absolute_ti3_422(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_RES_B),
        ],
        "ti3_paths": ["relative.ti3", None],
    })
    assert r.status_code == 422


def test_compare_ti3_none_items_skip_profcheck_gracefully(client):
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_RES_B),
        ],
        "ti3_paths": [None, None],
    })
    assert r.status_code == 200
    body = r.json()
    # No active profcheck entry (None for both)
    assert body["profiles"][0]["profcheck"] is None
    assert body["profiles"][1]["profcheck"] is None
    # But the meta flag indicates a ti3_paths was provided
    assert body["meta"]["with_profcheck"] is False


# ─── 404 — missing file ─────────────────────────────────────────────


def test_compare_rejects_missing_file_404(client, tmp_path):
    ghost = tmp_path / "ghost.icc"  # does not exist
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(ghost),
        ],
    })
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


def test_compare_rejects_missing_ti3_404(client, tmp_path):
    ghost = tmp_path / "ghost.ti3"
    r = client.post("/api/profiles/compare", json={
        "paths": [
            str(_RES_A),
            str(_RES_B),
        ],
        "ti3_paths": [str(ghost), None],
    })
    assert r.status_code == 404
    assert "ti3" in r.json()["detail"]
