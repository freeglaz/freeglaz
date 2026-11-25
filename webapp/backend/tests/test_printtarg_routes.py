"""Chart routes for the printtarg layout (external instrument). Z9 mocked, no hardware.

Creation validates the layout and the instrument whitelist; printing goes through the
same pipeline (and the same guards, ROLL2 included) without touching any scanLayout;
the built-in scan refuses such a chart before any guard, session or SOL act.
"""
import functools
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import printtarg_chart as P
from lib.z9_client.argyll import find_argyll_binary
from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from webapp.backend.tests.test_charts_routes import (MID, SERIAL, _RESIDENT, _fake_z9,
                                                     _forbid_hardware, _ti1_text)

CID = "CHT-20261122-1500-AB"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    from webapp.backend.services import chart_scan_job
    chart_scan_job.reset()
    yield TestClient(app)
    app.dependency_overrides.clear()
    chart_scan_job.reset()
    for tif in tmp_path.rglob("*.tif"):     # pytest keeps temp folders (RAM-backed /tmp)
        tif.unlink(missing_ok=True)


def _targen_like_ti1(n=12) -> str:
    """Three-table .ti1 shaped like targen's (patches, density extremes, combinations)."""
    head = ['CTI1', 'COLOR_REP "iRGB"', 'NUMBER_OF_FIELDS 4', 'BEGIN_DATA_FORMAT',
            'SAMPLE_ID RGB_R RGB_G RGB_B', 'END_DATA_FORMAT', f'NUMBER_OF_SETS {n}', 'BEGIN_DATA']
    rows = [f'{i + 1} {100.0 * i / n:.4f} {50.0:.4f} {100.0 - 100.0 * i / n:.4f}' for i in range(n)]
    extra = []
    for kw in ("DENSITY_EXTREME_VALUES", "DEVICE_COMBINATION_VALUES"):
        extra += ['CTI1', f'{kw} "2"', 'NUMBER_OF_FIELDS 4', 'BEGIN_DATA_FORMAT',
                  'INDEX RGB_R RGB_G RGB_B', 'END_DATA_FORMAT', 'NUMBER_OF_SETS 2',
                  'BEGIN_DATA', '0 100.0 100.0 100.0', '1 0.0 0.0 0.0', 'END_DATA']
    return "\n".join(head + rows + ['END_DATA'] + extra + [''])


def _body(**kw):
    b = {"media_key": "roll24", "paper_mediaid": MID, "gloss_enhancer": "OFF",
         "ti1_text": _targen_like_ti1(), "layout": "printtarg", "instrument": "i1pro3plus"}
    b.update(kw)
    return b


def _charts_on_disk() -> list:
    base = cache.charts_root()
    return [d for s in base.iterdir() for d in s.iterdir()] if base.exists() else []


# ─── creation ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw", [
    {"layout": "hexagon"},
    {"instrument": None},
    {"instrument": "spyder"},
    {"media_key": "a3"},
    {"layout": "honeycomb", "instrument": "i1pro3plus"},
])
def test_create_validation(client, kw):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body(**kw))
    assert r.status_code == 422, r.text
    assert _charts_on_disk() == []


def test_create_refuses_a_single_table_ti1(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body(ti1_text=_ti1_text(20)))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "ti1_missing_targen_tables"
    assert _charts_on_disk() == []


def test_create_without_printtarg_is_503(client, monkeypatch):
    import lib.z9_client.argyll as argyll
    real = argyll.find_argyll_binary
    monkeypatch.setattr(argyll, "find_argyll_binary",
                        lambda name: None if name == "printtarg" else real(name))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body())
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["code"] == "printtarg_not_found"
    assert _charts_on_disk() == []


def test_honeycomb_create_unchanged_by_default(client):
    """No `layout` → the honeycomb path, same response keys as before."""
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json={
        "media_key": "a3", "columns": 10, "paper_mediaid": MID,
        "gloss_enhancer": "OFF", "ti1_text": _ti1_text(60), "paper_name": "Baryta test"})
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"chart_id", "chart_dir", "n_patches", "cols", "nrows",
                             "purpose", "feasibility", "tag", "warnings"}
    desc = json.loads((Path(r.json()["chart_dir"]) / "chart.json").read_text())
    assert "layout" not in desc and desc["scanLayout"]["fields"]


@pytest.mark.skipif(not (find_argyll_binary("printtarg") and find_argyll_binary("targen")),
                    reason="printtarg/targen (Argyll) not installed")
def test_create_with_real_targen_and_printtarg(client, monkeypatch):
    monkeypatch.setattr(charts_routes._ptc, "orchestrate_printtarg_chart",
                        functools.partial(P.orchestrate_printtarg_chart, dpi=100))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body(ti1_text=None, targen_flags="-d2 -G -f 120",
                                              instrument="colormunki", paper_name="Baryta"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["layout"] == "printtarg" and body["instrument"] == "colormunki"
    assert body["roll_layout"] == "transverse"
    assert abs(body["tiff_w_mm"] - 580) < 0.3                      # strips cross the roll
    # what the UI shows before printing: roll consumed and printed orientation (generator values)
    assert body["media_key"] == "roll24" and body["roll_length_mm"] > body["tiff_h_mm"]
    assert body["composition"] == {"rotation": "cw", "strips_cross_the_roll": True,
                                   "strip_order": "top_to_bottom", "patch_1_side": "right"}
    pm = json.loads((Path(body["chart_dir"]) / "chart.json").read_text())["patch_map"]
    expected = ([{"code": "patches_not_located", "count": pm["patch_count"] - pm["located"]}]
                if pm["located"] < pm["patch_count"] else [])
    assert body["generator_warnings"] == expected                  # codes, worded by the UI
    assert body["n_patches"] == 120 and body["steps_in_pass"] == 34 and body["n_pages"] == 1
    assert (body["patch_scale"], body["high_density"]) == (1.0, False)          # the defaults
    assert body["patch_geometry"]["nominal"]["patch_width_mm"] == 28.0
    desc = json.loads((Path(body["chart_dir"]) / "chart.json").read_text())
    assert desc["layout"] == "printtarg" and "scanLayout" not in desc
    assert desc["composition"]["rotation"] == "cw"
    assert desc["purpose"] == "profiling"
    assert desc["targen"]["argv"][1] == "-d2"                     # real targen argv kept
    assert desc["paper"]["serial"] == SERIAL
    assert Path(body["chart_dir"]).parent.name == SERIAL          # charts/<serial>/<id>/
    listing = client.get("/api/charts").json()["charts"]
    assert any(c["chart_id"] == body["chart_id"] and c["patch_count"] == 120 for c in listing)


# ─── a printtarg chart on disk (synthetic, no Argyll needed) ─────────────────
def _install_chart(width_mm=100.0, height_mm=60.0, dpi=300) -> Path:
    d = cache.charts_dir(SERIAL) / CID
    d.mkdir(parents=True)
    w, h = round(width_mm / 25.4 * dpi), round(height_mm / 25.4 * dpi)
    icc = _RESIDENT.read_bytes()
    tifffile.imwrite(d / "chart.tif", np.full((h, w, 3), 30000, np.uint16), photometric="rgb",
                     resolution=(dpi, dpi), resolutionunit="INCH",
                     extratags=[(34675, "B", len(icc), icc, False)])
    desc = {
        "schema_version": 1, "chart_id": CID, "created_at": "2026-11-22T15:00:00",
        "layout": "printtarg",
        "instrument": {"key": "i1pro3plus", "printtarg_selector": "3p"},
        "media": {"format_key": "roll24", "source": "roll", "width_mm": 610.0,
                  "height_mm": None, "dpi": dpi},
        "paper": {"name": "Baryta test", "media_id": MID, "gloss_enhancer": "OFF",
                  "serial": SERIAL},
        "geometry": {"patch_count": 10, "n_pages": 1},
        "color_management": {"tag_source": "resident-live", "gloss_enhancer": "OFF"},
        "files": {"tiff": "chart.tif", "ti2": f"printtarg/{CID}.ti2"},
    }
    (d / "chart.json").write_text(json.dumps(desc))
    return d


def test_print_printtarg_chart_without_scanlayout(client, monkeypatch):
    d = _install_chart()
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9(source="ROLL")
    sent = []

    def _fake_send(self, job, **kw):
        sent.append(job)
        return SimpleNamespace(duration_seconds=2.0, prn_size_bytes=1)
    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _fake_send)
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 200, r.text
    assert len(sent) == 1 and sent[0].media_source == "ROLL"
    assert sent[0].icc_override == _RESIDENT.read_bytes()          # fresh resident, as before
    desc = json.loads((d / "chart.json").read_text())
    assert "scanLayout" not in desc and desc["printed_at"]
    assert desc["print_placement"]["media_source"] == "ROLL"


def test_print_printtarg_chart_keeps_the_roll2_guard(client, monkeypatch):
    _install_chart()
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9(source="ROLL2")

    def _boom(self, job, **kw):
        raise AssertionError("nothing must be sent")
    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _boom)
    r = client.post(f"/api/charts/{CID}/print", json={"quality": "HIGH"})
    assert r.status_code == 409 and "ROLL2" in r.json()["detail"]


@pytest.mark.parametrize("model", ["HP DesignJet Z9 24in", "HP DesignJet Z9 Pro 64in PS"])
def test_builtin_scan_refuses_a_printtarg_chart(client, monkeypatch, model):
    from webapp.backend.services import scan_session
    _install_chart()
    _forbid_hardware(monkeypatch)
    fake = _fake_z9(model=model)

    def _no_identification():
        raise AssertionError("refused before reading the printer")
    fake.identification = _no_identification
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: fake
    r = client.post(f"/api/charts/{CID}/scan", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "chart_for_external_instrument"
    assert scan_session.active_session() is None


def test_chart_detail_and_listing_read_a_printtarg_chart(client):
    _install_chart()
    det = client.get(f"/api/charts/{CID}")
    assert det.status_code == 200, det.text
    assert det.json()["identity"]["patch_count"] == 10
    assert any(c["chart_id"] == CID for c in client.get("/api/charts").json()["charts"])
    shutil.rmtree(cache.charts_dir(SERIAL) / CID)


# ─── printtarg -a / -h and the estimate (test_printtarg_layout_options.py: the generator) ──
@pytest.mark.parametrize("kw, code", [
    ({"instrument": "colormunki", "patch_scale": 0.9, "high_density": True}, "unsupported_option_combination"),
    ({"instrument": "colormunki", "patch_scale": 0.7}, "unsupported_patch_scale"),
    ({"instrument": "i1pro3plus", "high_density": True}, "unsupported_high_density"),
    ({"instrument": "i1pro3plus", "patch_scale": 0.9}, "unsupported_patch_scale"),
])
def test_refused_layout_options_cost_nothing(client, monkeypatch, kw, code):
    """Refused before targen, the printer or printtarg: no chart folder."""
    def boom(*a, **k):
        raise AssertionError("printtarg must not run")
    monkeypatch.setattr(charts_routes._ptc, "orchestrate_printtarg_chart", boom)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body(**kw))
    assert r.status_code == 422 and r.json()["detail"]["code"] == code, r.text
    assert _charts_on_disk() == []


def test_layout_options_are_printtarg_only(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body(layout="honeycomb", instrument=None, patch_scale=0.9))
    assert r.status_code == 422 and "printtarg" in r.text


@pytest.mark.skipif(not (find_argyll_binary("printtarg") and find_argyll_binary("targen")),
                    reason="printtarg/targen (Argyll) not installed")
def test_create_a_high_density_colormunki_chart(client, monkeypatch):
    monkeypatch.setattr(charts_routes._ptc, "orchestrate_printtarg_chart",
                        functools.partial(P.orchestrate_printtarg_chart, dpi=100))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json=_body(ti1_text=None, targen_flags="-d2 -G -f 120",
                                              instrument="colormunki", high_density=True))
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["patch_scale"], body["high_density"], body["steps_in_pass"]) == (1.0, True, 34)
    geo = body["patch_geometry"]
    assert geo["nominal"]["patch_width_mm"] == 13.7 and geo["measured"]["staggered"]
    assert geo["physically_validated"] is False
    desc = json.loads((Path(body["chart_dir"]) / "chart.json").read_text())
    assert desc["printtarg"]["high_density"] is True and "-h" in desc["printtarg"]["argv"]


@pytest.mark.skipif(not find_argyll_binary("printtarg"), reason="printtarg (Argyll) not installed")
def test_estimate_route(client, tmp_path):
    r = client.post("/api/charts/printtarg-estimate",
                    json={"instrument": "colormunki", "media_key": "roll24", "n_patches": 500,
                          "patch_scale": 0.8})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["estimate"] and (e["patches_per_strip"], e["patch_scale"]) == (43, 0.8)
    assert e["roll_length_mm"] > e["chart_h_mm"] and e["patch_geometry"]["patch_width_mm"] == 22.4
    P._write_layout_ti1(tmp_path / "t.ti1", 40)
    r = client.post("/api/charts/printtarg-estimate",
                    json={"instrument": "colormunki", "media_key": "roll24",
                          "ti1_text": (tmp_path / "t.ti1").read_text()})
    assert r.status_code == 200 and r.json()["n_patches"] == 40
    for body, code in (({"instrument": "colormunki", "media_key": "roll24"}, "estimate_needs_count"),
                       ({"instrument": "colormunki", "media_key": "a4", "n_patches": 300,
                         "high_density": True}, "too_many_strips_for_sheet"),
                       ({"instrument": "i1pro3plus", "media_key": "roll24", "n_patches": 50,
                         "high_density": True}, "unsupported_high_density")):
        r = client.post("/api/charts/printtarg-estimate", json=body)
        assert r.status_code == 422 and r.json()["detail"]["code"] == code, r.text
    assert _charts_on_disk() == []                     # an estimate writes no chart
