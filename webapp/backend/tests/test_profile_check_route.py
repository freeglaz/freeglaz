"""Increment (a) — SELF-COHERENCE check of a stored profile.

`verify_profile_self` (lib) = profcheck -k on the self-sourced creation ti3 +
white Lab test, with the "best case / optimistic" warning. Generic route
`POST /api/profiles/check {path}` (covers mirror / repo z9 / store, by path).
Testable WITHOUT Z9 or Argyll (extract + profcheck mocked).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import profile_compare as pc
from lib.z9_client.profiling import ProfilingOps

SERIAL = "CNXXXXXXXX"
MEDIA = "355783D031D38CD4F1D8B8426FE3C51A"


def _minimal_icc() -> bytes:
    head = bytearray(128)
    head[36:40] = b"acsp"
    return bytes(head) + b"\x00" * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    cache.ensure_store()
    return tmp_path


@pytest.fixture
def client(env):
    from webapp.backend.routes.profiles import router
    a = FastAPI()
    a.include_router(router)
    return TestClient(a)


def _range_profile():
    """Store a minimal repo/z9 profile → (path, meta)."""
    path, meta = cache.save_repo_z9_profile(
        icc_bytes=_minimal_icc(), serial=SERIAL, media_id=MEDIA,
        paper_name="Baryta test", label="Mon profil", gloss_slot="OFF",
        method="argyll", origin="free_chart_argyll")
    return path, meta


# ─── lib: parse per-patch (c.1) ─────────────────────────────────────────────
_PROFCHECK_SAMPLE = """No of test patches = 3
[1.004616] 115: 0.33333300 0.33333300 0.33333300 -> 27.348508 0.599822 -1.048515 should be 27.133034 -0.060822 -1.185008
[0.007131] 28: 0.00000000 0.50196100 1.00000000 -> 38.259444 3.088663 -69.151352 should be 38.257930 3.112561 -69.189820
[0.692174] 212: 0.66666700 0.33333300 0.16862700 -> 40.875271 18.565098 36.713375 should be 41.497320 18.388337 37.456034
Profile check complete, errors(CIEDE2000): max. = 1.004616, avg. = 0.567974, RMS = 0.652000
"""


def test_parse_patches_sorted_and_scaled():
    patches = pc._parse_profcheck_patches(_PROFCHECK_SAMPLE)
    assert len(patches) == 3
    # sort worst→best
    assert [p["de2000"] for p in patches] == [1.0046, 0.6922, 0.0071]
    worst = patches[0]
    assert worst["id"] == 115
    assert worst["rgb"] == [85, 85, 85]                  # 0.3333 ×255 = 8-bit canonical
    assert worst["lab_target"] == [27.133, -0.061, -1.185]
    assert worst["lab_achieved"] == [27.349, 0.6, -1.049]
    # extreme channel: 0.50196 ×255 = 128, 1.0 ×255 = 255
    assert patches[2]["rgb"] == [0, 128, 255]


def test_profcheck_run_detailed(monkeypatch, tmp_path):
    """detailed=True → adds patches/n_patches; the summary stays identical."""
    ti3 = tmp_path / "x.ti3"; ti3.write_text("CTI3\n")
    icc = tmp_path / "x.icc"; icc.write_bytes(_minimal_icc())
    monkeypatch.setattr("lib.z9_client.argyll.find_argyll_binary", lambda _: "/usr/bin/profcheck")

    class _R:
        stdout = _PROFCHECK_SAMPLE
        stderr = ""
    monkeypatch.setattr(pc.subprocess, "run", lambda *a, **k: _R())
    r = pc._profcheck_run(icc, ti3, detailed=True)
    assert r["status"] == "ok"
    assert r["avg_de2000"] == 0.567974 and r["peak_de2000"] == 1.004616
    assert r["n_patches"] == 3 and len(r["patches"]) == 3
    # non-detailed mode: no patches (compare regression guard)
    r2 = pc._profcheck_run(icc, ti3)
    assert "patches" not in r2 and r2["status"] == "ok"


# ─── lib: verify_profile_self ───────────────────────────────────────────────
def test_verify_self_ok(env, monkeypatch):
    icc = env / "p.icc"
    icc.write_bytes(_minimal_icc())
    # extract_cgats_from_icc mocked → writes a ti3; profcheck + white-point mocked.
    monkeypatch.setattr(ProfilingOps, "extract_cgats_from_icc",
                        lambda self, p, output_path=None, **k: Path(output_path).write_text("CTI3\n"))
    monkeypatch.setattr(pc, "_profcheck_run",
                        lambda icc_path, ti3_path, timeout=120, detailed=False: {
                            "status": "ok", "peak_de2000": 2.1, "avg_de2000": 0.4,
                            "rms_de2000": 0.5, "quality": "Excellent"})
    monkeypatch.setattr(pc, "_white_point_lab",
                        lambda icc_path: {"L": 95.0, "a": 0.3, "b": 1.2, "suspect": False})
    r = pc.verify_profile_self(icc)
    assert r["status"] == "ok"
    assert r["scope"] == "self" and r["caveat"] == "auto-coherence"   # scope honesty
    assert r["profcheck"]["quality"] == "Excellent"
    assert r["white_point_lab"]["suspect"] is False


def test_verify_self_no_measurements(env, monkeypatch):
    """ICC with no embedded measurements (extract fails) → no_measurements (points to b)."""
    icc = env / "p.icc"
    icc.write_bytes(_minimal_icc())

    def _raise(self, p, output_path=None, **k):
        raise ValueError("pas de tag CIED/targ")
    monkeypatch.setattr(ProfilingOps, "extract_cgats_from_icc", _raise)
    monkeypatch.setattr(pc, "_white_point_lab", lambda icc_path: None)
    r = pc.verify_profile_self(icc)
    assert r["status"] == "no_measurements" and r["scope"] == "self"


def test_verify_self_icc_missing(env):
    r = pc.verify_profile_self(env / "nope.icc")
    assert r["status"] == "error"


# ─── IN-HOUSE grid: renamed labels (Excellent/Good/Fair/Poor) ──────────
def test_quality_labels_renamed():
    assert pc._quality_label(0.4, 2.0) == "Excellent"
    assert pc._quality_label(0.8, 4.0) == "Good"
    assert pc._quality_label(1.5, 6.0) == "Fair"
    assert pc._quality_label(3.0, 9.0) == "Poor"


# ─── generic route: POST /check {path} ───────────────────────────────────────
def test_check_route_happy(client, monkeypatch):
    path, _ = _range_profile()
    monkeypatch.setattr(
        "lib.z9_client.profile_compare.verify_profile_self",
        lambda icc, **k: {"status": "ok", "scope": "self", "caveat": "auto-coherence",
                          "profcheck": {"status": "ok", "avg_de2000": 0.4, "peak_de2000": 2.1,
                                        "rms_de2000": 0.5, "quality": "Excellent"},
                          "white_point_lab": {"L": 95.0, "a": 0.3, "b": 1.2, "suspect": False}})
    r = client.post("/api/profiles/check", json={"path": str(path)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["caveat"] == "auto-coherence"
    assert body["label"] == "Mon profil" and body["paper_name"] == "Baryta test"
    assert body["profcheck"]["quality"] == "Excellent"


def test_check_route_mirror_path(client, env, monkeypatch):
    """Profile INSTALLED via the mirror (no repo sidecar) → checked by path,
    label = file stem. Covers the "check the installed one without Z9" use case."""
    mp = cache.mirror_paper_dir(SERIAL, "Baryta test")
    mp.mkdir(parents=True, exist_ok=True)
    icc = mp / "GE-OFF__installed.icc"
    icc.write_bytes(_minimal_icc())
    monkeypatch.setattr(
        "lib.z9_client.profile_compare.verify_profile_self",
        lambda p, **k: {"status": "no_measurements", "scope": "self"})
    r = client.post("/api/profiles/check", json={"path": str(icc)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "no_measurements"
    assert body["label"] == "GE-OFF__installed"   # fallback stem (no mirror sidecar)


def test_check_route_unknown_profile(client):
    path = cache.repo_z9_paper_dir(SERIAL, MEDIA) / "absent.icc"
    r = client.post("/api/profiles/check", json={"path": str(path)})
    assert r.status_code == 404


def test_check_route_outside_store_rejected(client, tmp_path):
    """Anti path-traversal: a path outside the store is rejected (403)."""
    outside = tmp_path.parent / "evil.icc"
    outside.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/check", json={"path": str(outside)})
    assert r.status_code == 403
