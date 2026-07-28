"""profcheck (b) Mechanism 1 — INDEPENDENT validation (forward A2B).

`verify_profile_independent` (lib) = profcheck detailed on the ti3 of an
INDEPENDENT chart + cross-check of non-overlap vs the creation patches.
Route `POST /api/charts/{id}/validate {profile_path}`: LOCAL, no Z9 action
(printing/scan already done). Testable without Z9 or Argyll (profcheck/extract mocked).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import profile_compare as pc
from lib.z9_client.profiling import ProfilingOps

SERIAL = "CNXXXXXXXX"
MEDIA = "355783D031D38CD4F1D8B8426FE3C51A"
CHART_ID = "CHT-20260608-0001-AA"


def _minimal_icc() -> bytes:
    head = bytearray(128)
    head[36:40] = b"acsp"
    return bytes(head) + b"\x00" * 64


def _ti3(path: Path, rows):
    body = ['CTI3', 'COLOR_REP "RGB_XYZ"', 'BEGIN_DATA_FORMAT',
            'SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z', 'END_DATA_FORMAT',
            f'NUMBER_OF_SETS {len(rows)}', 'BEGIN_DATA']
    body += [f'{i} {r} {g} {b} 50 50 50' for i, (r, g, b) in enumerate(rows, 1)]
    body += ['END_DATA', '']
    path.write_text("\n".join(body))


# ─── lib: _independence_vs_creation (verifiable cross-check) ──────────────────
def test_independence_overlap(tmp_path, monkeypatch):
    val = tmp_path / "val.ti3"; cre = tmp_path / "cre.ti3"
    _ti3(val, [(0, 0, 0), (50, 50, 50), (100, 100, 100), (20, 40, 60)])
    _ti3(cre, [(50, 50, 50), (0, 0, 0), (33, 33, 33)])
    monkeypatch.setattr(ProfilingOps, "extract_cgats_from_icc",
                        lambda self, icc, output_path=None, **k: Path(output_path).write_text(cre.read_text()))
    r = pc._independence_vs_creation(val, tmp_path / "p.icc")
    assert r["creation_known"] is True
    assert r["n_validation"] == 4 and r["n_overlap"] == 2 and r["pct_overlap"] == 50.0
    assert r["compromised"] is True            # >20 % → (a) in disguise


def test_independence_no_creation_measurements(tmp_path, monkeypatch):
    val = tmp_path / "val.ti3"; _ti3(val, [(0, 0, 0), (10, 20, 30)])

    def _raise(self, icc, output_path=None, **k):
        raise ValueError("pas de mesures")
    monkeypatch.setattr(ProfilingOps, "extract_cgats_from_icc", _raise)
    r = pc._independence_vs_creation(val, tmp_path / "p.icc")
    assert r["creation_known"] is False and r["n_validation"] == 2


def test_verify_independent_ok(tmp_path, monkeypatch):
    icc = tmp_path / "p.icc"; icc.write_bytes(_minimal_icc())
    ti3 = tmp_path / "val.ti3"; _ti3(ti3, [(0, 0, 0), (100, 100, 100)])
    monkeypatch.setattr(pc, "_profcheck_run",
                        lambda i, t, timeout=120, detailed=False: {
                            "status": "ok", "avg_de2000": 0.8, "peak_de2000": 2.0,
                            "rms_de2000": 1.0, "quality": "Good", "patches": [], "n_patches": 0})
    monkeypatch.setattr(pc, "_white_point_lab", lambda i: None)
    monkeypatch.setattr(pc, "_independence_vs_creation",
                        lambda v, p: {"creation_known": True, "n_overlap": 0, "pct_overlap": 0.0,
                                      "compromised": False})
    r = pc.verify_profile_independent(icc, ti3)
    assert r["status"] == "ok" and r["scope"] == "independent"
    assert r["caveat"] == "independent-validation"
    assert r["independence"]["compromised"] is False
    assert r["profcheck"]["quality"] == "Good"


# ─── route: POST /{id}/validate ──────────────────────────────────────────────
@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    cache.ensure_store()
    return tmp_path


@pytest.fixture
def client(env):
    from webapp.backend.routes.charts import router
    a = FastAPI(); a.include_router(router)
    return TestClient(a)


def _make_chart(ge="OFF", with_ti3=True):
    d = cache.charts_dir(SERIAL) / CHART_ID
    (d / "measurements").mkdir(parents=True, exist_ok=True)
    desc = {"paper": {"name": "Baryta test", "media_id": MEDIA, "gloss_enhancer": ge}}
    (d / "chart.json").write_text(json.dumps(desc), encoding="utf-8")
    if with_ti3:
        _ti3(d / "measurements" / "scan.ti3", [(0, 0, 0), (100, 100, 100)])
    return d


def _make_profile(media_id=MEDIA, ge="OFF"):
    path, _ = cache.save_repo_z9_profile(
        icc_bytes=_minimal_icc(), serial=SERIAL, media_id=media_id,
        paper_name="Baryta test", label="Profil X", gloss_slot=ge,
        method="argyll", origin="free_chart_argyll")
    return path


def test_validate_route_happy(client, monkeypatch):
    _make_chart(ge="OFF"); prof = _make_profile(ge="OFF")
    monkeypatch.setattr(
        "lib.z9_client.profile_compare.verify_profile_independent",
        lambda icc, ti3, **k: {"status": "ok", "scope": "independent",
                               "caveat": "independent-validation",
                               "profcheck": {"status": "ok", "quality": "Good", "avg_de2000": 0.8},
                               "independence": {"creation_known": True, "pct_overlap": 0.0,
                                                "compromised": False}})
    r = client.post(f"/api/charts/{CHART_ID}/validate", json={"profile_path": str(prof)})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["scope"] == "independent" and b["label"] == "Profil X"
    assert b["independence"]["compromised"] is False


def test_validate_route_no_ti3(client):
    _make_chart(with_ti3=False); prof = _make_profile()
    r = client.post(f"/api/charts/{CHART_ID}/validate", json={"profile_path": str(prof)})
    assert r.status_code == 409


def test_validate_route_profile_missing(client):
    _make_chart()
    missing = cache.repo_z9_paper_dir(SERIAL, MEDIA) / "absent.icc"
    r = client.post(f"/api/charts/{CHART_ID}/validate", json={"profile_path": str(missing)})
    assert r.status_code == 404


def test_validate_route_outside_store(client, tmp_path):
    _make_chart()
    outside = tmp_path.parent / "evil.icc"; outside.write_bytes(_minimal_icc())
    r = client.post(f"/api/charts/{CHART_ID}/validate", json={"profile_path": str(outside)})
    assert r.status_code == 403


def test_validate_route_ge_mismatch(client, monkeypatch):
    """GE passed by the UI ≠ chart GE → real mismatch rejected."""
    _make_chart(ge="OFF"); prof = _make_profile(ge="OFF")
    monkeypatch.setattr("lib.z9_client.profile_compare.verify_profile_independent",
                        lambda icc, ti3, **k: {"status": "ok"})
    r = client.post(f"/api/charts/{CHART_ID}/validate",
                    json={"profile_path": str(prof), "media_id": MEDIA, "gloss_enhancer": "FULLPAGE"})
    assert r.status_code == 422 and "GE" in r.json()["detail"]


def test_validate_route_paper_mismatch(client):
    """media_id passed by the UI ≠ chart media_id → real mismatch rejected."""
    _make_chart(ge="OFF"); prof = _make_profile(ge="OFF")
    r = client.post(f"/api/charts/{CHART_ID}/validate",
                    json={"profile_path": str(prof), "media_id": "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
                          "gloss_enhancer": "OFF"})
    assert r.status_code == 422 and "paper" in r.json()["detail"].lower()


def test_list_charts_scanned_validation_only(client):
    """RESUME: ?scanned=true&purpose=validation returns ONLY validation charts
    already scanned (not the profiling charts, not the non-scanned ones)."""
    import json as _j
    # scanned validation chart (CHART_ID)
    d = _make_chart(ge="OFF", with_ti3=True)
    desc = _j.loads((d / "chart.json").read_text()); desc["purpose"] = "validation"
    desc["chart_id"] = CHART_ID
    (d / "chart.json").write_text(_j.dumps(desc))
    # scanned profiling chart (to exclude)
    d2 = cache.charts_dir(SERIAL) / "CHT-20260608-0002-BB"
    (d2 / "measurements").mkdir(parents=True, exist_ok=True)
    (d2 / "chart.json").write_text(_j.dumps(
        {"chart_id": "CHT-20260608-0002-BB", "purpose": "profiling",
         "paper": {"media_id": MEDIA, "name": "x"}}))
    _ti3(d2 / "measurements" / "s.ti3", [(0, 0, 0)])
    r = client.get("/api/charts", params={"scanned": "true", "purpose": "validation"})
    assert r.status_code == 200
    ids = [c["chart_id"] for c in r.json()["charts"]]
    assert CHART_ID in ids and "CHT-20260608-0002-BB" not in ids


def test_delete_chart_removes_dir(client):
    d = _make_chart(with_ti3=True)
    (d / "chart.tif").write_bytes(b"X" * 1000)
    assert d.is_dir()
    r = client.delete(f"/api/charts/{CHART_ID}")
    assert r.status_code == 200 and r.json()["deleted"] == "all"
    assert not d.is_dir()                       # everything removed


def test_lighten_chart_keeps_ti3_drops_tiff(client):
    import json as _j
    d = _make_chart(with_ti3=True)
    (d / "chart.tif").write_bytes(b"X" * 5000)
    desc = _j.loads((d / "chart.json").read_text()); desc["files"] = {"tiff": "chart.tif"}
    (d / "chart.json").write_text(_j.dumps(desc))
    r = client.post(f"/api/charts/{CHART_ID}/lighten")
    assert r.status_code == 200 and r.json()["freed_bytes"] == 5000
    assert not (d / "chart.tif").exists()       # chart TIFF purged
    assert (d / "measurements" / "scan.ti3").exists()   # ti3 KEPT
    assert _j.loads((d / "chart.json").read_text())["lightened"] is True
    # already lightened → 409
    r2 = client.post(f"/api/charts/{CHART_ID}/lighten")
    assert r2.status_code == 409


def test_validate_route_mirror_no_false_positive(client, env, monkeypatch):
    """REGRESSION: MIRROR profile (stored by paper_name, name without GE-<slot>__) — the
    guard must NOT re-derive from the file (false positive "paper"). The UI
    passes the authentic identity (hashed media_id + GE) → matches the chart."""
    _make_chart(ge="FULLPAGE")
    mp = cache.mirror_paper_dir(SERIAL, "cansonphotolustrerc0526")
    mp.mkdir(parents=True, exist_ok=True)
    icc = mp / "GE-FULLPAGE_cansonphotolustrerc0526-2026-06-07.icc"   # plain '_', no sidecar
    icc.write_bytes(_minimal_icc())
    monkeypatch.setattr("lib.z9_client.profile_compare.verify_profile_independent",
                        lambda i, t, **k: {"status": "ok", "scope": "independent"})
    r = client.post(f"/api/charts/{CHART_ID}/validate",
                    json={"profile_path": str(icc), "media_id": MEDIA, "gloss_enhancer": "FULLPAGE"})
    assert r.status_code == 200, r.text          # no more false positive
    assert r.json()["scope"] == "independent"
