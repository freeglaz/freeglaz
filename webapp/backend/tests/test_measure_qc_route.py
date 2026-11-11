"""GET /api/charts/{id}/scans/{ti3}/qc — intrinsic measurement QC, read-only + cache.

No printer: the Z9 dependency, the SOL measure entry point and outgoing sockets are
replaced by fakes that fail on ANY use while the QC route runs."""
import hashlib
import json
import socket
import threading

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import measure_qc as mq
from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from webapp.backend.services import measure_qc as qc_service
from webapp.backend.tests.synthetic_ti3 import grid, lab_to_xyz, make_ti3, measure, nearest, paper_lab
from webapp.backend.tests.test_charts_routes import SERIAL, _create_a3

TI3 = "20260101_120000.ti3"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def no_printer(monkeypatch):
    """Any hardware / network attempt fails immediately."""
    def _boom(*a, **k):
        raise AssertionError("printer / network access attempted by the QC feature")
    app.dependency_overrides[charts_routes.get_chart_z9] = _boom
    monkeypatch.setattr("lib.z9_client.sol_native.measure", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)


def _chart_with_scan(client, text=None, name=TI3):
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    if text is None:
        rgb = grid(6)
        xyz = measure(rgb)
        i = nearest(rgb, (0.4, 0.6, 0.4))
        xyz[i] = lab_to_xyz(paper_lab(xyz))[0]                 # one missed patch
        text = make_ti3(rgb, xyz)
    (meas / name).write_text(text)
    return cid, meas


def test_single_scan_report_then_cache_hit(client, no_printer):
    cid, meas = _chart_with_scan(client)
    before = (meas / TI3).read_bytes()
    r = client.get(f"/api/charts/{cid}/scans/{TI3}/qc")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cached"] is False and body["ti3"] == TI3
    assert body["ti3_sha256"] == hashlib.sha256(before).hexdigest()
    rep = body["report"]
    assert rep["algo_version"] == mq.QC_ALGO_VERSION and rep["counts"]["strong"] >= 1
    assert list((meas / ".qc").glob("*.json"))                         # derived cache written
    r2 = client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()
    assert r2["cached"] is True and r2["report"] == rep
    assert (meas / TI3).read_bytes() == before                         # measurements untouched


def test_cache_invalidated_when_the_ti3_changes(client, no_printer):
    cid, meas = _chart_with_scan(client)
    first = client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()
    rgb = grid(6)
    (meas / TI3).write_text(make_ti3(rgb, measure(rgb)))               # scan file replaced
    second = client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()
    assert second["cached"] is False and second["ti3_sha256"] != first["ti3_sha256"]
    assert second["report"]["counts"]["strong"] == 0
    assert len(list((meas / ".qc").glob("*.json"))) == 1               # stale report pruned


def test_stale_algorithm_version_or_tampered_cache_is_not_reused(client, no_printer, monkeypatch):
    cid, meas = _chart_with_scan(client)
    client.get(f"/api/charts/{cid}/scans/{TI3}/qc")
    cf = next((meas / ".qc").glob("*.json"))
    blob = json.loads(cf.read_text())
    blob["ti3_sha256"] = "0" * 64                                       # tampered / inconsistent
    cf.write_text(json.dumps(blob))
    assert client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()["cached"] is False
    monkeypatch.setattr(mq, "QC_ALGO_VERSION", "999")                   # algorithm changed
    r = client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()
    assert r["cached"] is False and r["report"]["algo_version"] == "999"


def test_detail_exposes_summary_only_when_already_cached(client, no_printer):
    cid, meas = _chart_with_scan(client)
    det = client.get(f"/api/charts/{cid}").json()
    assert det["scans"][0]["qc_summary"] is None
    assert not (meas / ".qc").exists()                                  # detail never computes
    client.get(f"/api/charts/{cid}/scans/{TI3}/qc")
    s = client.get(f"/api/charts/{cid}").json()["scans"][0]["qc_summary"]
    assert s["algo_version"] == mq.QC_ALGO_VERSION and s["counts"]["strong"] >= 1


def test_unknown_derived_and_traversal_names_are_refused(client, no_printer):
    cid, meas = _chart_with_scan(client)
    (meas / f"{cid}_avg.ti3").write_text((meas / TI3).read_text())      # derivative, not a scan
    for name in ("nope.ti3", f"{cid}_avg.ti3", "..%2Fchart.json", "chart.json"):
        r = client.get(f"/api/charts/{cid}/scans/{name}/qc")
        assert r.status_code == 404, (name, r.status_code)
    assert client.get("/api/charts/PASBON/scans/x.ti3/qc").status_code == 422


@pytest.mark.parametrize("text,code", [
    ("CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B\nEND_DATA_FORMAT\nBEGIN_DATA\n1 0 0 0\nEND_DATA\n",
     "INVALID_COLORIMETRY"),
    ("CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\nBEGIN_DATA\n1 0 0 0 1 1 1\n",
     "TRUNCATED"),
    (make_ti3(grid(4), measure(grid(4)), originator="freeglaz multipass (lib/z9_client/multipass.py)"), "MERGED_FILE"),
])
def test_unanalysable_scan_returns_coded_422(client, no_printer, text, code):
    cid, meas = _chart_with_scan(client, text=text)
    r = client.get(f"/api/charts/{cid}/scans/{TI3}/qc")
    assert r.status_code == 422 and r.json()["detail"]["code"] == code
    assert not list((meas / ".qc").glob("*.json"))                     # nothing cached on failure


def test_deleting_a_scan_purges_its_qc_cache(client, no_printer):
    cid, meas = _chart_with_scan(client)
    client.get(f"/api/charts/{cid}/scans/{TI3}/qc")
    assert list((meas / ".qc").glob("*.json"))
    assert client.delete(f"/api/charts/{cid}/scans/{TI3}").status_code == 200
    assert not list((meas / ".qc").glob("*.json"))


def test_concurrent_requests_compute_once(tmp_path, monkeypatch):
    p = tmp_path / TI3
    rgb = grid(6)
    p.write_text(make_ti3(rgb, measure(rgb)))
    calls = []
    real = mq.analyze_ti3

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)
    monkeypatch.setattr(mq, "analyze_ti3", counting)
    results = []
    threads = [threading.Thread(target=lambda: results.append(qc_service.get_report(p))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(results) == 6
    assert sum(1 for r in results if r["cached"]) == 5
    assert len({json.dumps(r["report"], sort_keys=True) for r in results}) == 1


def test_unwritable_cache_still_returns_the_report(tmp_path, monkeypatch):
    p = tmp_path / TI3
    rgb = grid(5)
    p.write_text(make_ti3(rgb, measure(rgb)))

    def ro(*a, **k):
        raise OSError("read-only store")
    monkeypatch.setattr(qc_service, "_write_atomic", ro)
    out = qc_service.get_report(p)
    assert out["cached"] is False and out["report"]["n_patches"] == len(rgb)
    assert qc_service.cached_summary(p) is None


def test_service_refuses_derived_files_directly(tmp_path):
    for name in ("x_avg.ti3", "x_qcfilt.ti3", "x_multisource.ti3", "x.txt"):
        f = tmp_path / name
        f.write_text("CTI3\n")
        with pytest.raises(qc_service.ScanQCError) as e:
            qc_service.get_report(f)
        assert e.value.code == "NOT_A_RAW_SCAN"
    assert qc_service.cached_summary(tmp_path / "x_avg.ti3") is None


def test_old_report_format_in_cache_is_recomputed(client, no_printer, monkeypatch):
    cid, meas = _chart_with_scan(client)
    client.get(f"/api/charts/{cid}/scans/{TI3}/qc")
    monkeypatch.setattr(mq, "REPORT_FORMAT", mq.REPORT_FORMAT + 1)       # structure changed
    r = client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()
    assert r["cached"] is False and r["report"]["report_format"] == mq.REPORT_FORMAT


def test_qc_consultation_never_touches_decisions_or_measurements(client, no_printer):
    """Reading the QC must not change rejected_readings / roles, nor any ti3."""
    cid, meas = _chart_with_scan(client)
    chart = meas.parent
    (chart / "scan_state.json").write_text(json.dumps(
        {"roles": {TI3: "included"}, "rejected_readings": {TI3: ["5"]}}))
    snap = {f: f.read_bytes() for f in [chart / "scan_state.json", *meas.glob("*.ti3")]}
    for _ in range(2):
        assert client.get(f"/api/charts/{cid}/scans/{TI3}/qc").status_code == 200
        assert client.get(f"/api/charts/{cid}").status_code == 200
        assert client.get(f"/api/charts/{cid}/scans/{TI3}/patches").status_code == 200
    assert {f: f.read_bytes() for f in snap} == snap


def test_displayed_measurements_match_the_qc_report(client, no_printer):
    """The table shows /patches (Lab 2 decimals, RGB 0-255) next to the QC report (Lab 3 decimals,
    rgb8): both must come from the SAME colorimetry (Lab recomputed from XYZ), up to rounding."""
    cid, meas = _chart_with_scan(client)
    pt = {p["id"]: p for p in client.get(f"/api/charts/{cid}/scans/{TI3}/patches").json()["patches"]}
    rep = client.get(f"/api/charts/{cid}/scans/{TI3}/qc").json()["report"]
    assert set(pt) == {p["sample_id"] for p in rep["patches"]}
    for q in rep["patches"]:
        p = pt[q["sample_id"]]
        assert p["rgb"] == q["rgb8"]
        assert max(abs(a - b) for a, b in zip(p["lab"], q["lab"])) <= 0.0051
