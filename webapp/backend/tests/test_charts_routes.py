"""Free chart routes: formats, -f<=max guard, targen-help,
user presets, ordered -c listing, creation (targen + .ti1). Z9 mocked."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from lib.z9_client import cache
from lib.z9_client.exceptions import Z9Error

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_RESIDENT = _FIXTURES / "synthetic_test_resident_A.icc"   # synthetic resident stand-in (mluc)
MID = "9E489F02AE027F9DD93191D872728C1D"
SERIAL = "CNXXXXXXXX"


def _fake_z9(only_ge=None, loaded=True):
    """Z9 mock: export_icc writes an ICC from the assets. only_ge=set -> only those GE
    exist. loaded=False -> device.status() with no paper loaded (-> 409)."""
    paper = MagicMock()

    def _export(*, ref, output_path, gloss_enhancer, quality, color_space):
        if only_ge is not None and gloss_enhancer not in only_ge:
            raise Z9Error(f"pas de profil GE={gloss_enhancer}")
        Path(output_path).write_bytes(_RESIDENT.read_bytes())
        return {"output_path": output_path}

    paper.export_icc.side_effect = _export
    paper.get.return_value = {"name": "Baryta test", "category_id": "custom",
                              "is_factory": False}
    paper.capabilities.return_value = {}
    device = MagicMock()
    device.status.return_value = ({
        "loaded_paper_id": MID, "loaded_paper_source": "MANUAL_FEED",
        "loaded_paper_width_mm": 297.0, "loaded_paper_length_mm": 420.0,
        "loaded_paper_name": "Baryta test",
        "identification": {"SerialNumber": SERIAL},
    } if loaded else {})
    # print_chart refetches the resident FRESH at the go (fetch_resident_icc →
    # z9.soap.get_profile) and embeds it via job.icc_override.
    soap = MagicMock()
    soap.get_profile.return_value = {"icc_bytes": _RESIDENT.read_bytes()}
    # store.get_serial(z9) (per-serial bridge) reads z9.identification() — not device.status().
    return SimpleNamespace(paper=paper, device=device, soap=soap, host="127.0.0.1",
                           identification=lambda: {"SerialNumber": SERIAL})


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_scan_job():
    """`chart_scan_job` has module-level globals (cooldown/anti-concurrent) NOT isolated by
    tmp_path -> reset between tests to avoid pollution (phantom 429/409)."""
    from webapp.backend.services import chart_scan_job
    chart_scan_job.reset()
    yield
    chart_scan_job.reset()


def _scan_and_wait(client, cid, *, timeout=5.0):
    """NON-BLOCKING scan: POST /scan starts the job, then poll /scan/status until
    done/error. Returns the final job (ti3/n_patches/bands filled at done). Reset
    the singleton first (no residual cooldown from a previous scan of the same test)."""
    import time
    from webapp.backend.services import chart_scan_job
    chart_scan_job.reset()
    r = client.post(f"/api/charts/{cid}/scan", json={})
    assert r.status_code == 200, r.text
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = (client.get(f"/api/charts/{cid}/scan/status").json() or {}).get("job") or {}
        if job.get("state") in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("scan job inachevé (timeout)")


# --- formats + guard ---------------------------------------------------------
def test_formats(client):
    r = client.get("/api/charts/formats")
    assert r.status_code == 200
    fmts = {f["key"]: f for f in r.json()["formats"]}
    assert fmts["a4"]["max_patches"] == 204
    assert fmts["a3"]["max_patches"] == 468
    assert fmts["roll24"]["is_roll"] and fmts["roll24"]["max_patches"] is None


def test_create_rejects_f_over_max(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json={
        "media_key": "a4", "columns": 11, "paper_mediaid": MID,
        "gloss_enhancer": "OFF", "targen_flags": "-v -d2 -G -f 9999"})
    assert r.status_code == 422
    assert "max" in r.json()["detail"]


# --- targen help + user presets ----------------------------------------------
def test_targen_help(client):
    r = client.get("/api/charts/targen-help")
    if r.status_code == 503:
        pytest.skip("targen (Argyll) absent")
    assert r.status_code == 200 and "targen" in r.json()["help"].lower()


def test_targen_user_presets_roundtrip(client, tmp_path, monkeypatch):
    # redirect the user presets file to tmp
    p = tmp_path / "targen_strategies.toml"
    monkeypatch.setattr(charts_routes._tg, "_user_presets_path", lambda: p)
    assert client.get("/api/charts/targen-presets").json()["presets"] == []
    r = client.post("/api/charts/targen-presets", json={
        "key": "mon_base", "flags": "-v -d2 -G -f 200", "description": "ma base"})
    assert r.status_code == 200
    presets = client.get("/api/charts/targen-presets").json()["presets"]
    assert any(x["key"] == "mon_base" and "-f 200" in x["flags"] for x in presets)


# --- colprof: presets + help + agreed default (targen symmetry) --------------
def test_colprof_presets_and_default(client):
    d = client.get("/api/charts/colprof-presets").json()
    names = {p["name"] for p in d["presets"]}
    assert "default" in names and "faithful" in names      # builtins strategies.py
    assert d["default_flags"].startswith("-v -qh")          # AGREED recipe
    assert "Rec2020" not in d["default_flags"]              # Rec2020 excluded from default


def test_colprof_help(client):
    r = client.get("/api/charts/colprof-help")
    if r.status_code == 503:
        pytest.skip("colprof (Argyll) absent")
    assert r.status_code == 200 and "colprof" in r.json()["help"].lower()


def test_colprof_user_preset_roundtrip(client, tmp_path, monkeypatch):
    monkeypatch.setattr("lib.z9_client.strategies._user_strategies_path",
                        lambda: tmp_path / "colprof_strategies.toml")
    r = client.post("/api/charts/colprof-presets", json={
        "key": "mon_colprof", "flags": "-v -qm -r 0.3", "description": "test"})
    assert r.status_code == 200
    assert "mon_colprof" in {x["name"] for x in r.json()["presets"]}


def test_profile_colprof_flags_override(client, monkeypatch):
    """The wizard's colprof line is passed as-is to build_profile."""
    cid = _create_a3(client)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    cap = {}

    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        cap["flags"] = colprof_flags
        Path(output_icc_path).write_bytes(b"\0" * 1024)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=1024)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={"colprof_flags": "-v -qu -r 0.2"})
    assert r.status_code == 200
    assert cap["flags"] == ["-v", "-qu", "-r", "0.2"]


# --- precondition-profiles (ordered -c menu) ---------------------------------
def test_precondition_profiles_ordered(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.get(f"/api/charts/precondition-profiles?paper={MID}&print_ge=OFF")
    assert r.status_code == 200
    profs = r.json()["profiles"]
    kinds = [p["kind"] for p in profs]
    # residents first (OFF then ON), "none" last
    assert profs[0]["kind"] == "resident" and profs[0]["ge"] == "OFF"
    assert profs[0]["default"] is True            # default = same GE as the print
    assert any(p["kind"] == "resident" and p["ge"] == "FULLPAGE" for p in profs)
    assert kinds[-1] == "none"
    assert profs[0]["name"] and "srgb" not in profs[0]["name"].lower()  # mluc name


def test_precondition_profiles_only_one_ge(client):
    """Slot with only GE=OFF -> a single resident listed."""
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9(only_ge={"OFF"})
    r = client.get(f"/api/charts/precondition-profiles?paper={MID}")
    profs = r.json()["profiles"]
    residents = [p for p in profs if p["kind"] == "resident"]
    assert len(residents) == 1 and residents[0]["ge"] == "OFF"


# --- creation (path a targen, path b .ti1) -----------------------------------
def _ti1_text(n):
    lines = ['CGATS.17', 'COLOR_REP "RGB"', 'BEGIN_DATA_FORMAT',
             'SAMPLE_ID RGB_R RGB_G RGB_B', 'END_DATA_FORMAT',
             f'NUMBER_OF_SETS {n}', 'BEGIN_DATA']
    for i in range(n):
        v = round(100.0 * i / max(1, n - 1), 4)
        lines.append(f'{i+1} {v} {v} {v}')
    lines += ['END_DATA', '']
    return '\n'.join(lines)


def test_create_via_ti1_upload(client):
    """Path b: .ti1 provided -> bypass targen -> orchestrate (resident tag mocked)."""
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json={
        "media_key": "a3", "columns": 10, "paper_mediaid": MID,
        "gloss_enhancer": "OFF", "ti1_text": _ti1_text(60),
        "paper_name": "Baryta test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["chart_id"].startswith("CHT-") and body["n_patches"] == 60
    # the chart appears in the library (tmp store)
    listing = client.get("/api/charts").json()["charts"]
    assert any(c["chart_id"] == body["chart_id"] for c in listing)


def test_create_via_targen(client):
    """Path a: targen line -> run targen -> orchestrate. Skip if targen absent."""
    if shutil.which("targen") is None:
        pytest.skip("targen (Argyll) absent")
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json={
        "media_key": "a4", "columns": 11, "paper_mediaid": MID,
        "gloss_enhancer": "OFF", "targen_flags": "-v -d2 -G -f 60"})
    assert r.status_code == 200, r.text
    assert r.json()["n_patches"] >= 60   # -f 60 + extras (-e/-B)


def test_create_requires_targen_or_ti1(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json={
        "media_key": "a3", "columns": 10, "paper_mediaid": MID, "gloss_enhancer": "OFF"})
    assert r.status_code == 422


# --- guided path: print (hardware action) ------------------------------------
def _create_a3(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts", json={
        "media_key": "a3", "columns": 10, "paper_mediaid": MID,
        "gloss_enhancer": "OFF", "ti1_text": _ti1_text(60), "paper_name": "Baryta test"})
    assert r.status_code == 200, r.text
    return r.json()["chart_id"]


def test_print_invalid_chart_id(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    assert client.post("/api/charts/PASBON/print", json={}).status_code == 422


def test_print_unknown_chart(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    r = client.post("/api/charts/CHT-20260606-1200-AA/print", json={})
    assert r.status_code == 404


def test_print_no_paper_loaded(client):
    cid = _create_a3(client)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9(loaded=False)
    r = client.post(f"/api/charts/{cid}/print", json={})
    assert r.status_code == 409


def test_print_happy(client, monkeypatch):
    cid = _create_a3(client)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    # PrintOps.send mocked; we capture the EFFECTIVE placement (= print pipeline).
    sent = {}

    def _fake_send(self, job, **kw):
        sent.update(offset_x=job.offset_x_mm, offset_y=job.offset_y_mm,
                    sheet_h=job.sheet_h_mm, image_h=job.image_h_mm, gloss=job.gloss)
        return SimpleNamespace(duration_seconds=1.4, prn_size_bytes=1000)

    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _fake_send)
    r = client.post(f"/api/charts/{cid}/print", json={"quality": "HIGH"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["gloss"] == "OFF"
    # placement = SINGLE print pipeline (CENTERED compute_geometry) — no more
    # reinvented inline top-anchoring (cause of the GE overflow).
    assert abs(sent["offset_y"] - (sent["sheet_h"] - sent["image_h"]) / 2) < 0.5
    # CRITICAL scan<->print CONSISTENCY: the scanLayout reflects the EFFECTIVE placement.
    desc = _descriptor(cid)
    assert desc["scanLayout"]["placement"]["mode"] == "print_pipeline"
    assert abs(desc["scanLayout"]["placement"]["offset_y_mm"] - sent["offset_y"]) < 0.01
    assert abs(desc["scanLayout"]["placement"]["offset_x_mm"] - sent["offset_x"]) < 0.01
    # printed_at set -> enters the library (printed only, paper filter)
    lib = client.get(f"/api/charts?paper={MID}&printed=true").json()["charts"]
    assert any(c["chart_id"] == cid and c["printed"] for c in lib)
    # unknown paper filter -> empty
    other = client.get("/api/charts?paper=" + ("A" * 32) + "&printed=true").json()["charts"]
    assert all(c["chart_id"] != cid for c in other)


def test_print_blocks_when_resident_unavailable(client, monkeypatch):
    """Chart print refetches the resident FRESH at the go; if it can't be read,
    block franc (HTTP 502), send NOT called — no fallback to the stale tag."""
    cid = _create_a3(client)
    z9 = _fake_z9()
    z9.soap.get_profile.side_effect = Z9Error("SOAP getProfile failed")
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: z9
    send_called = {"flag": False}

    def _fake_send(self, job, **kw):
        send_called["flag"] = True
        return SimpleNamespace(duration_seconds=1.0, prn_size_bytes=1)

    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _fake_send)
    r = client.post(f"/api/charts/{cid}/print", json={"quality": "HIGH"})
    assert r.status_code == 502, r.text
    assert "resident" in r.text.lower()
    assert send_called["flag"] is False


# --- mode B: scan -> profile (guided path) -----------------------------------
def _descriptor(cid):
    import json
    from lib.z9_client import cache
    return json.loads((cache.charts_dir(SERIAL) / cid / "chart.json").read_text())


def _native_cgats_for(desc):
    patches = desc["patches_in_layout_order"]
    bands = [400 + 20 * i for i in range(16)]
    cols = ["PATCH_ROW", "PATCH_COL"] + [f"SPECTRAL_{w}" for w in bands]
    rows = ["  " + "  ".join([str(p["row"] + 1), str(p["col"] + 1)] + ["0.5"] * 16)
            for p in patches]
    return (f"CGATS.17\nNUMBER_OF_SETS\t{len(patches)}\nBEGIN_DATA_FORMAT\n"
            f"{' '.join(cols)}\nEND_DATA_FORMAT\nBEGIN_DATA\n"
            + "\n".join(rows) + "\nEND_DATA\n").encode("ascii")


def test_scan_unknown_chart(client):
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    assert client.post("/api/charts/CHT-20260606-1200-AA/scan", json={}).status_code == 404


def test_scan_happy_then_profile_no_ti3_first(client, monkeypatch):
    cid = _create_a3(client)
    # profile before scan -> 409
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 409
    # scan: measure mocked -> synthetic CGATS matched to the descriptor
    desc = _descriptor(cid)
    cgats = _native_cgats_for(desc)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: cgats)
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    job = _scan_and_wait(client, cid)   # NON-BLOCKING: POST starts the job, we poll until done
    assert job["state"] == "done", job
    assert job["n_patches"] == 60 and job["bands"] == 16
    # the chart is now "scanned" in the library
    lib = client.get(f"/api/charts?paper={MID}").json()["charts"]
    assert any(c["chart_id"] == cid and c["scanned"] for c in lib)


def test_scan_status_resume_flag_and_abandon(client, monkeypatch):
    """status exposes the resumable session + "confirmation required" flag; abandon = lock released."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    st = client.get(f"/api/charts/{cid}/scan/status").json()
    assert st["session"]["chart_id"] == cid and st["session"]["stage"] == "awaiting_next"
    assert st["resume_confirmation_required"] is True       # >=1 scan, awaiting next
    assert st["n_scans"] == 1                               # DISK count (measurements/*.ti3)
    assert client.post(f"/api/charts/{cid}/scan/abandon").status_code == 200
    st2 = client.get(f"/api/charts/{cid}/scan/status").json()
    assert st2["session"] is None and st2["resume_confirmation_required"] is False
    # the finished session does NOT make scans disappear -> n_scans stays the disk truth
    # (the wizard always sees the right count -> resume possible, keeps "chart moved" guard).
    assert st2["n_scans"] == 1


def test_scan_status_scoped_to_chart(client, monkeypatch):
    """SCOPING: an active session on ANOTHER chart does NOT leak into a chart's status
    (before the fix, scan_chart_status ignored its chart_id param -> returned the global session)."""
    from webapp.backend.services import scan_session
    cid = _create_a3(client)
    # active session on a DIFFERENT chart (global "one at a time" lock)
    scan_session.create_session(
        serial=SERIAL, chart_id="CHT-OTHER-0000-ZZ", stage=scan_session.STAGE_SCANNING)
    st = client.get(f"/api/charts/{cid}/scan/status").json()
    assert st["session"] is None                               # not another chart's session
    assert st["resume_confirmation_required"] is False


def test_scan_cooldown_429_at_api(client, monkeypatch):
    """HARD server guard: scan < 30 s after the previous one -> 429, BEFORE any session (no phantom)."""
    import time
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    from webapp.backend.services import chart_scan_job, scan_session
    chart_scan_job.reset()
    chart_scan_job._last_end_monotonic = time.monotonic()   # a scan just finished
    r = client.post(f"/api/charts/{cid}/scan", json={})
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert scan_session.active_session() is None        # refused BEFORE session


def test_scan_delta_endpoint(client, monkeypatch):
    """GET /scan/delta: <2 scans -> 409; >=2 -> mini-report (n_scans, summary, patches)."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    assert client.get(f"/api/charts/{cid}/scan/delta").status_code == 409   # 1 scan
    _scan_and_wait(client, cid)                                             # 2nd scan (same session)
    r = client.get(f"/api/charts/{cid}/scan/delta")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_scans"] == 2 and len(body["patches"]) == body["n_patches"]
    # 2 identical scans (mock) -> concordant, no outlier
    assert body["summary"]["isolated_outlier"] is False and body["summary"]["max"] < 0.01


def test_list_charts_exposes_n_scans(client, monkeypatch):
    """The list exposes n_scans (-> "Concordance" button in the chart view if >=2)."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid); _scan_and_wait(client, cid)        # 2 scans
    charts = client.get(f"/api/charts?paper={MID}").json()["charts"]
    c = next(x for x in charts if x["chart_id"] == cid)
    assert c["n_scans"] == 2
    assert c["gloss_enhancer"] == "OFF"      # GE exposed in list (ON/OFF subgroup on the front)


def test_scan_delta_survives_session_close(client, monkeypatch):
    """DECOUPLING: the report stays viewable from the chart even after the session
    is closed (the ti3 persist) — no need to re-scan."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid); _scan_and_wait(client, cid)
    assert client.post(f"/api/charts/{cid}/scan/abandon").status_code == 200   # close the session
    r = client.get(f"/api/charts/{cid}/scan/delta")
    assert r.status_code == 200 and r.json()["n_scans"] == 2   # report survives the close


def test_chart_detail_aggregate(client, monkeypatch):
    """GET /{id} (Measurements tab): identity + scans (A3) + derived profile even orphan (A4)."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    # 0 scan / 0 profile
    b0 = client.get(f"/api/charts/{cid}").json()
    assert b0["identity"]["chart_id"] == cid
    assert b0["identity"]["gloss_enhancer"] == "OFF"      # GE exposed in detail (shown via geLabel)
    assert b0["scans"] == [] and b0["profile"]["built"] is False
    # 1 scan -> A3
    _scan_and_wait(client, cid)
    b1 = client.get(f"/api/charts/{cid}").json()
    assert len(b1["scans"]) == 1 and b1["scans"][0]["kept"] is True and b1["scans"][0]["ti3"].endswith(".ti3")
    # profile built -> A4 (built + icc_path, even if not filed)
    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * 2048)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=2048)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    client.post(f"/api/charts/{cid}/profile", json={})
    b2 = client.get(f"/api/charts/{cid}").json()
    assert b2["profile"]["built"] is True and b2["profile"]["icc_path"].endswith(f"{cid}.icc")


def test_chart_detail_unknown_404(client):
    assert client.get("/api/charts/CHT-20260101-0000-ZZ").status_code == 404


def test_chart_detail_does_not_shadow_literal_routes(client):
    """GET /{id} must NOT capture the literal GET routes (/formats)."""
    assert client.get("/api/charts/formats").status_code == 200   # still the formats


def test_list_charts_exposes_profiled(client, monkeypatch):
    cid = _create_a3(client)
    c = next(x for x in client.get(f"/api/charts?paper={MID}").json()["charts"] if x["chart_id"] == cid)
    assert c["profiled"] is False
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * 1024)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=1024)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    client.post(f"/api/charts/{cid}/profile", json={})
    c2 = next(x for x in client.get(f"/api/charts?paper={MID}").json()["charts"] if x["chart_id"] == cid)
    assert c2["profiled"] is True


def test_pre_session_chart_anchored_in_measurements(client):
    """FIX "(?)" badge: a chart WITHOUT a session (pre-session) but with ti3 in
    measurements/ shows its REAL count + concordance — anchored to measurements, not session."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    def _ti3(name, shift=0.0):
        rows = "\n".join(f"{i} 50 50 50 {50.0 + shift} 1 -2" for i in range(20))
        (meas / name).write_text(
            "CTI3\nNUMBER_OF_FIELDS 7\nBEGIN_DATA_FORMAT\n"
            "SAMPLE_ID RGB_R RGB_G RGB_B LAB_L LAB_A LAB_B\nEND_DATA_FORMAT\n"
            f"NUMBER_OF_SETS 20\nBEGIN_DATA\n{rows}\nEND_DATA\n")
    _ti3("20260101_120000.ti3"); _ti3("20260101_130000.ti3", shift=0.3)   # 2 ti3, NO session
    # list: counted from measurements/ (not session) -> n_scans=2, no more (?)
    c = next(x for x in client.get(f"/api/charts?paper={MID}").json()["charts"] if x["chart_id"] == cid)
    assert c["scanned"] is True and c["n_scans"] == 2
    # detail: 2 scans, n_patches DERIVED from the ti3 (no session meta)
    det = client.get(f"/api/charts/{cid}").json()
    assert len(det["scans"]) == 2 and det["scans"][0]["n_patches"] == 20
    # concordance: computed WITHOUT session (anchored to measurements)
    r = client.get(f"/api/charts/{cid}/scan/delta")
    assert r.status_code == 200 and r.json()["n_scans"] == 2


def _write_ti3(meas, name, shift=0.0):
    """Minimal ti3 (LAB) directly in measurements/ — simulates an out-of-session scan."""
    meas.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"{i} 50 50 50 {50.0 + shift} 1 -2" for i in range(20))
    (meas / name).write_text(
        "CTI3\nNUMBER_OF_FIELDS 7\nBEGIN_DATA_FORMAT\n"
        "SAMPLE_ID RGB_R RGB_G RGB_B LAB_L LAB_A LAB_B\nEND_DATA_FORMAT\n"
        f"NUMBER_OF_SETS 20\nBEGIN_DATA\n{rows}\nEND_DATA\n")


def _fake_icc_build(monkeypatch, size=1024):
    def _fb(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * size)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=size)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fb)


def test_scan_role_included_excluded(client):
    """Per-scan role (BATCH 1, 2-state): included/excluded. Excluded OUT OF
    concordance; ti3 NEVER deleted; reversible; role exposed in detail."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3); _write_ti3(meas, "c.ti3", shift=5.0)
    assert client.get(f"/api/charts/{cid}/scan/delta").json()["n_scans"] == 3   # 3 included by default
    # c -> excluded: out of concordance (2 included), but kept + shown
    r = client.post(f"/api/charts/{cid}/scans/c.ti3/role", json={"role": "excluded"})
    assert r.status_code == 200 and r.json()["n_included"] == 2
    assert client.get(f"/api/charts/{cid}/scan/delta").json()["n_scans"] == 2
    det = client.get(f"/api/charts/{cid}").json()
    roles = {s["ti3"]: s["role"] for s in det["scans"]}
    assert roles == {"a.ti3": "included", "b.ti3": "included", "c.ti3": "excluded"}
    assert {s["ti3"]: s["kept"] for s in det["scans"]}["c.ti3"] is False   # kept backward-compat = included
    # reversible
    assert client.post(f"/api/charts/{cid}/scans/c.ti3/role", json={"role": "included"}).json()["n_included"] == 3
    assert all((meas / n).is_file() for n in ("a.ti3", "b.ti3", "c.ti3"))   # ti3 NEVER deleted


def test_scan_role_invalid_and_unknown(client):
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3")
    assert client.post(f"/api/charts/{cid}/scans/a.ti3/role", json={"role": "bogus"}).status_code == 422
    assert client.post(f"/api/charts/{cid}/scans/nope.ti3/role", json={"role": "included"}).status_code == 404


def test_scan_scanned_at_and_delay(client, monkeypatch):
    """Timestamp: scanned_at read from the ti3 filename (YYYYMMDD_HHMMSS); delay_seconds =
    scanned_at - printed_at (naive local). Without printed_at -> delay None (never invented)."""
    cid = _create_a3(client)
    d = cache.charts_dir(SERIAL) / cid
    meas = d / "measurements"
    _write_ti3(meas, "20260612_150000.ti3")          # name = timestamp
    # without printed_at: scanned_at present, delay None
    s0 = client.get(f"/api/charts/{cid}").json()["scans"][0]
    assert s0["scanned_at"] == "2026-06-12T15:00:00" and s0["delay_seconds"] is None
    # printed_at set 6 min earlier -> delay = 360 s
    import json as _json
    desc = _json.loads((d / "chart.json").read_text())
    desc["printed_at"] = "2026-06-12T14:54:00"
    (d / "chart.json").write_text(_json.dumps(desc))
    s1 = client.get(f"/api/charts/{cid}").json()["scans"][0]
    assert s1["delay_seconds"] == 360


def test_profile_needs_at_least_one_included(client, monkeypatch):
    """Guard: >=1 included scan to build (all excluded -> 409)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3")
    _fake_icc_build(monkeypatch)
    client.post(f"/api/charts/{cid}/scans/a.ti3/role", json={"role": "excluded"})
    client.post(f"/api/charts/{cid}/scans/b.ti3/role", json={"role": "excluded"})
    assert client.post(f"/api/charts/{cid}/profile", json={}).status_code == 409   # 0 included


def test_scan_state_migration_excluded(client):
    """Backward-compat migration: old scan_state {excluded:[...]} -> role 'excluded'."""
    import json as _json
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3")
    (cache.charts_dir(SERIAL) / cid / "scan_state.json").write_text(
        _json.dumps({"excluded": ["b.ti3"], "profile_built_from": ["a.ti3"]}))
    roles = {s["ti3"]: s["role"] for s in client.get(f"/api/charts/{cid}").json()["scans"]}
    assert roles == {"a.ti3": "included", "b.ti3": "excluded"}   # migrated without loss


def test_profile_built_from_and_stale(client, monkeypatch):
    """Profile STALE after the INCLUDED set changes (never auto-rebuilt)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=1.0)
    _fake_icc_build(monkeypatch)
    client.post(f"/api/charts/{cid}/profile", json={})               # base last (included set = a+b)
    det = client.get(f"/api/charts/{cid}").json()
    assert det["profile"]["built"] is True and det["profile"]["stale"] is False
    assert sorted(det["profile"]["built_from"]) == ["a.ti3", "b.ti3"]
    # b -> excluded -> included set changes -> STALE
    client.post(f"/api/charts/{cid}/scans/b.ti3/role", json={"role": "excluded"})
    assert client.get(f"/api/charts/{cid}").json()["profile"]["stale"] is True
    # rebuild explicitly (same name -> we REPLACE this chart's profile) -> re-trace
    client.post(f"/api/charts/{cid}/profile", json={"on_conflict": "replace"})
    assert client.get(f"/api/charts/{cid}").json()["profile"]["stale"] is False


# --- BATCH 2 — rejection by patch x scan (the scalpel) + QC view -------------
def _write_ti3_rows(meas, name, rows):
    """Minimal ti3 with rows = [(sid, R, G, B, L, a, b)...] in the GIVEN ORDER (used to test
    SAMPLE_ID anchoring independent of the line position)."""
    meas.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{sid} {r} {g} {b} {L} {a} {bb}" for (sid, r, g, b, L, a, bb) in rows)
    (meas / name).write_text(
        "CTI3\nNUMBER_OF_FIELDS 7\nBEGIN_DATA_FORMAT\n"
        "SAMPLE_ID RGB_R RGB_G RGB_B LAB_L LAB_A LAB_B\nEND_DATA_FORMAT\n"
        f"NUMBER_OF_SETS {len(rows)}\nBEGIN_DATA\n{body}\nEND_DATA\n")


def test_reject_reading_by_sample_id(client):
    """Reject a reading (patch x scan), anchored by SAMPLE_ID, exposed in detail + QC view,
    reversible, ti3 never deleted."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3)
    r = client.post(f"/api/charts/{cid}/scans/a.ti3/readings/5/reject", json={"rejected": True})
    assert r.status_code == 200 and r.json()["n_rejected"] == 1
    # detail: the rejected reading is exposed on the right scan
    det = client.get(f"/api/charts/{cid}").json()
    rej = {s["ti3"]: s["rejected_readings"] for s in det["scans"]}
    assert rej["a.ti3"] == ["5"] and rej["b.ti3"] == []
    # QC view: patch 5 has a reading marked rejected on a.ti3
    qc = client.get(f"/api/charts/{cid}/scan/qc").json()
    p5 = next(p for p in qc["patches"] if p["id"] == "5")
    flags = {rd["ti3"]: rd["rejected"] for rd in p5["readings"]}
    assert flags == {"a.ti3": True, "b.ti3": False} and p5["n_valid"] == 1
    # reversible + ti3 intact
    assert client.post(f"/api/charts/{cid}/scans/a.ti3/readings/5/reject",
                       json={"rejected": False}).json()["n_rejected"] == 0
    assert all((meas / n).is_file() for n in ("a.ti3", "b.ti3"))


def test_reject_anchored_by_sample_id_not_position(client):
    """SAMPLE_ID anchoring: two scans in reversed ORDER -> the rejection targets the right patch (not the line)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3_rows(meas, "a.ti3", [("1", 10, 10, 10, 20, 0, 0), ("2", 90, 90, 90, 80, 0, 0)])
    _write_ti3_rows(meas, "b.ti3", [("2", 90, 90, 90, 81, 0, 0), ("1", 10, 10, 10, 21, 0, 0)])  # reversed order
    client.post(f"/api/charts/{cid}/scans/a.ti3/readings/2/reject", json={"rejected": True})
    qc = client.get(f"/api/charts/{cid}/scan/qc").json()
    p2 = next(p for p in qc["patches"] if p["id"] == "2")
    assert {rd["ti3"]: rd["rejected"] for rd in p2["readings"]}["a.ti3"] is True
    p1 = next(p for p in qc["patches"] if p["id"] == "1")
    assert all(rd["rejected"] is False for rd in p1["readings"])   # patch 1 intact despite the order


def test_reject_blocks_last_valid_reading(client):
    """Guard: forbidden to reject the last valid reading of a patch (409)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3)
    assert client.post(f"/api/charts/{cid}/scans/a.ti3/readings/5/reject",
                       json={"rejected": True}).status_code == 200
    # b is the last valid reading of patch 5 -> rejection blocked
    assert client.post(f"/api/charts/{cid}/scans/b.ti3/readings/5/reject",
                       json={"rejected": True}).status_code == 409


def test_reject_guards_unknown_and_excluded(client):
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3")
    assert client.post(f"/api/charts/{cid}/scans/nope.ti3/readings/5/reject",
                       json={"rejected": True}).status_code == 404
    assert client.post(f"/api/charts/{cid}/scans/a.ti3/readings/999/reject",
                       json={"rejected": True}).status_code == 404
    client.post(f"/api/charts/{cid}/scans/a.ti3/role", json={"role": "excluded"})
    assert client.post(f"/api/charts/{cid}/scans/a.ti3/readings/5/reject",
                       json={"rejected": True}).status_code == 409   # excluded scan


def test_scan_qc_matrix_and_outlier(client):
    """QC view: one L/a/b reading per included scan, disagreement per patch, outlier detected at >=3."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    # 3 concordant scans except patch 5 which deviates STRONGLY in c.ti3 (outlier)
    base = [(str(i), 50, 50, 50, 50.0, 1.0, -2.0) for i in range(8)]
    _write_ti3_rows(meas, "a.ti3", base)
    _write_ti3_rows(meas, "b.ti3", base)
    cc = [(str(i), 50, 50, 50, (70.0 if i == 5 else 50.0), 1.0, -2.0) for i in range(8)]
    _write_ti3_rows(meas, "c.ti3", cc)
    qc = client.get(f"/api/charts/{cid}/scan/qc").json()
    assert qc["n_scans"] == 3 and qc["scans"] == ["a.ti3", "b.ti3", "c.ti3"]
    p5 = next(p for p in qc["patches"] if p["id"] == "5")
    assert p5["disagreement"] > 5.0                       # clear deviation
    assert next(rd for rd in p5["readings"] if rd["outlier"])["ti3"] == "c.ti3"   # outlier = c
    p0 = next(p for p in qc["patches"] if p["id"] == "0")
    assert p0["disagreement"] < 0.5 and not any(rd["outlier"] for rd in p0["readings"])


def test_scan_qc_needs_two_included(client):
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3")
    assert client.get(f"/api/charts/{cid}/scan/qc").status_code == 409   # 1 included


# --- view the measurements of ONE scan (>=1) + delete a set ------------------
def test_scan_patches_single_scan(client):
    """View measurements from 1 scan (DECOUPLED from the >=2 comparison): id + RGB + Lab per patch."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3")
    r = client.get(f"/api/charts/{cid}/scans/a.ti3/patches")
    assert r.status_code == 200
    body = r.json()
    assert body["n_patches"] == 20 and body["ti3"] == "a.ti3"
    p0 = body["patches"][0]
    assert set(p0) == {"id", "rgb", "lab"} and len(p0["rgb"]) == 3 and len(p0["lab"]) == 3
    assert client.get(f"/api/charts/{cid}/scans/nope.ti3/patches").status_code == 404


def test_delete_scan_set(client):
    """PERMANENTLY delete a measurement set (ti3 + cgats) + clean the sidecar."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3)
    (meas / "a.cgats").write_text("x")                    # twin cgats
    client.post(f"/api/charts/{cid}/scans/a.ti3/role", json={"role": "excluded"})
    r = client.delete(f"/api/charts/{cid}/scans/a.ti3")
    assert r.status_code == 200 and r.json()["remaining_scans"] == 1
    assert not (meas / "a.ti3").exists() and not (meas / "a.cgats").exists()   # ti3 + cgats deleted
    assert (meas / "b.ti3").exists()                       # the other intact
    # sidecar cleaned (no more orphan role for a.ti3)
    det = client.get(f"/api/charts/{cid}").json()
    assert [s["ti3"] for s in det["scans"]] == ["b.ti3"]
    assert client.delete(f"/api/charts/{cid}/scans/nope.ti3").status_code == 404


def test_delete_scan_makes_profile_stale(client, monkeypatch):
    """Dependency handled: deleting a ti3 from the built set -> profile "stale", never a crash."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3)
    _fake_icc_build(monkeypatch)
    client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert client.get(f"/api/charts/{cid}").json()["profile"]["stale"] is False
    client.delete(f"/api/charts/{cid}/scans/a.ti3")        # built set = {a,b} -> current = {b}
    det = client.get(f"/api/charts/{cid}").json()
    assert det["profile"]["built"] is True and det["profile"]["stale"] is True   # stale, no crash


def test_profile_excludes_rejected_reading_from_average(client, monkeypatch):
    """Base "average": the rejected reading is REMOVED from the concat (patch averaged over the rest).
    concat_ti3 dedup="keep" keeps all readings -> 2x20 = 40, minus 1 rejection = 39."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3)
    _fake_icc_build(monkeypatch)
    # control without rejection: 40 readings
    assert client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"}).status_code == 200
    n_sets = lambda: int(next(ln.split()[1] for ln in (meas / f"{cid}_avg.ti3").read_text().splitlines()
                              if ln.strip().startswith("NUMBER_OF_SETS")))
    assert n_sets() == 40
    # with 1 rejected reading: 39 (the rejected row removed from the concat). Rebuild same chart =
    # REPLACES the existing profile (same auto name).
    client.post(f"/api/charts/{cid}/scans/a.ti3/readings/5/reject", json={"rejected": True})
    client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average", "on_conflict": "replace"})
    assert n_sets() == 39
    assert not (meas / "a_qcfilt.ti3").exists()          # temporary filtered copy cleaned


def test_profile_stale_after_rejection_change(client, monkeypatch):
    """Profile stale if the REJECTIONS change after build (generalizes the Batch 1 stale)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"
    _write_ti3(meas, "a.ti3"); _write_ti3(meas, "b.ti3", shift=0.3)
    _fake_icc_build(monkeypatch)
    client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert client.get(f"/api/charts/{cid}").json()["profile"]["stale"] is False
    client.post(f"/api/charts/{cid}/scans/a.ti3/readings/5/reject", json={"rejected": True})
    assert client.get(f"/api/charts/{cid}").json()["profile"]["stale"] is True   # rejections changed
    client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average", "on_conflict": "replace"})
    assert client.get(f"/api/charts/{cid}").json()["profile"]["stale"] is False


# --- Profile build = BACKGROUND JOB (non-blocking) ---------------------------
def test_profile_returns_job_envelope_and_status(client, monkeypatch):
    """POST /profile returns a {job} envelope (+ flat result in sync mode); the job
    status is queryable; idle for a chart without a build."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3")
    # idle before any build
    assert client.get(f"/api/charts/{cid}/profile/status").json()["state"] == "idle"
    _fake_icc_build(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["job"]["state"] == "done" and body["job"]["result"]["ok"] is True   # envelope
    assert body["ok"] is True                                # flat result (backward-compat)
    # status reflects the latest build of THIS chart
    st = client.get(f"/api/charts/{cid}/profile/status").json()
    assert st["state"] == "done" and st["chart_id"] == cid


def test_profile_build_error_surfaces(client, monkeypatch):
    """colprof failure -> 422 with message (sync mode); the job goes to error (visible echo)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3")
    def _boom(self, *a, **k):
        raise RuntimeError("colprof boom")
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _boom)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 422 and "boom" in r.json()["detail"]
    assert client.get(f"/api/charts/{cid}/profile/status").json()["state"] == "error"
    # no final ICC left (failed build)
    assert not (cache.charts_dir(SERIAL) / cid / f"{cid}.icc").exists()


def test_profile_atomic_cleanup_partial_icc(client, monkeypatch):
    """Atomic write: a PARTIAL <base>.icc left by an interrupted colprof is cleaned
    (never a file resembling a valid ICC; out_icc stays absent)."""
    cid = _create_a3(client)
    meas = cache.charts_dir(SERIAL) / cid / "measurements"; _write_ti3(meas, "a.ti3")
    def _partial(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(str(ti3_base) + ".icc").write_bytes(b"")   # 0 bytes, then crash -> residual partial
        raise RuntimeError("interrompu")
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _partial)
    assert client.post(f"/api/charts/{cid}/profile", json={}).status_code == 422
    leftovers = list((meas).glob("*.icc")) + list((cache.charts_dir(SERIAL) / cid).glob("*.icc"))
    assert leftovers == []                              # partial cleaned, out_icc never created


def test_profile_happy(client, monkeypatch):
    # The route ORCHESTRATES colprof; the colprof correction itself is validated
    # live (a real colprof on 60 synthetic flat-reflectance patches diverges
    # / exceeds the timeout — degenerate data, out of scope for the route test).
    # We mock build_profile to test ONLY the route's orchestration.
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    assert _scan_and_wait(client, cid)["state"] == "done"

    captured = {}

    class _FakeResult:
        def __init__(self, p):
            self.output_icc_path = str(p)
            self.output_icc_size_bytes = 4096

    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        captured["ti3_base"] = str(ti3_base)
        captured["flags"] = colprof_flags
        Path(output_icc_path).write_bytes(b"\0" * 4096)
        return _FakeResult(output_icc_path)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["icc"].endswith(".icc") and body["icc_size_bytes"] > 0
    # agreed default = -v -qh (+ -S AdobeRGB if the source gamut is present on the machine)
    assert captured["flags"][:2] == ["-v", "-qh"]
    if "-S" in captured["flags"]:
        assert "AdobeRGB" in captured["flags"][captured["flags"].index("-S") + 1]
    # FILING by paper: copy into repo/z9/<serial>/papers/<media_id>/ + installable
    assert body["installable"] is True
    rp = Path(body["ranged_icc_path"])
    assert rp.exists() and rp.read_bytes() == b"\0" * 4096          # COPY (same bytes)
    # New naming: HPZ9_<paper>_GE-<slot>_<date> (printer + GE after paper)
    assert SERIAL in rp.parts and MID in rp.parts and rp.name.startswith("HPZ9_") and "_GE-OFF_" in rp.name
    # ORIGINAL in the chart folder intact (copy, not move)
    assert (cache.charts_dir(SERIAL) / cid / f"{cid}.icc").exists()
    # listed by the Profiles space (browsable by paper)
    listed = cache.list_repo_z9_profiles(SERIAL)
    assert any(e["media_id"] == MID and e["origin"] == "free_chart_argyll"
               and e["notes"] == f"chart {cid}" for e in listed)


def test_profile_colprof_flags_resolve_source_gamut_alias(client, monkeypatch):
    # -S <alias> typed in the webapp "colprof options" field must resolve to our
    # bundled assets/ ICC (absolute path), exactly like the CLI. A -s <percentage>
    # stays literal (colprof -s/-S accept src.icc OR a percentage).
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    assert _scan_and_wait(client, cid)["state"] == "done"

    captured = {}

    class _FakeResult:
        def __init__(self, p):
            self.output_icc_path = str(p)
            self.output_icc_size_bytes = 4096

    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        captured["flags"] = colprof_flags
        Path(output_icc_path).write_bytes(b"\0" * 4096)
        return _FakeResult(output_icc_path)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile",
                    json={"colprof_flags": "-v -qh -S AdobeRGB -s 90"})
    assert r.status_code == 200, r.text
    flags = captured["flags"]
    # -S AdobeRGB → absolute bundled assets path (ClayRGB-elle-V2-g22.icc)
    s_val = flags[flags.index("-S") + 1]
    assert s_val.startswith("/") and s_val.endswith("assets/ClayRGB-elle-V2-g22.icc")
    # -s 90 (percentage) stays literal
    assert flags[flags.index("-s") + 1] == "90"


def test_profile_no_serial_retrocompat(client, monkeypatch, tmp_path):
    """Chart WITHOUT a serial (created before it was added) + empty z9 repo -> filing skipped
    cleanly (ranged None, installable False), profiling NOT broken (200)."""
    cid = _create_a3(client)
    # simulate an older chart: remove the serial from the descriptor
    dpath = cache.charts_dir(SERIAL) / cid / "chart.json"
    desc = json.loads(dpath.read_text())
    desc["paper"].pop("serial", None)
    dpath.write_text(json.dumps(desc))
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)

    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * 2048)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=2048)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200, r.text                  # NO crash
    assert r.json()["ranged_icc_path"] is None and r.json()["installable"] is False


def test_profile_no_serial_fallback_to_known(client, monkeypatch):
    """Chart without a serial BUT a single Z9 known on disk (mirror, populated by sync) ->
    filed anyway (fallback "last known serial"). Covers existing charts."""
    cid = _create_a3(client)
    dpath = cache.charts_dir(SERIAL) / cid / "chart.json"
    desc = json.loads(dpath.read_text())
    desc["paper"].pop("serial", None)
    dpath.write_text(json.dumps(desc))
    # a single serial known via the mirror (repo/z9 empty at this stage)
    (cache.mirror_dir() / SERIAL).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)

    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * 2048)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=2048)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200, r.text
    assert r.json()["installable"] is True
    assert SERIAL in Path(r.json()["ranged_icc_path"]).parts


def test_profile_base_average_concats_kept_scans(client, monkeypatch):
    """base='average': >=2 stable scans from the session -> concat 'keep' -> colprof
    averages the repeated measurements. Reuses concat_ti3 (real here)."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)                 # scan #1 -> session awaiting_next
    _scan_and_wait(client, cid)                 # scan #2 (same chart -> SAME session)
    st = client.get(f"/api/charts/{cid}/scan/status").json()
    assert len(st["session"]["scans"]) == 2     # 2 "kept" scans

    captured = {}
    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        captured["ti3_base"] = str(ti3_base)
        Path(output_icc_path).write_bytes(b"\0" * 2048)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=2048)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["averaged"] is True and body["n_scans_averaged"] == 2
    assert captured["ti3_base"].endswith("_avg")    # the AVERAGED ti3 (not a raw scan)
    avg = cache.charts_dir(SERIAL) / cid / "measurements" / f"{cid}_avg.ti3"
    assert avg.is_file()
    # FIX B: n_patches = NUMBER_OF_SETS of the FINAL ti3 fed to colprof (_avg, dedup keep = 2xN),
    # NOT the chart's patch_count (N). Single source of truth = the file feeding colprof.
    from webapp.backend.routes.charts import _ti3_n_sets
    n_avg = _ti3_n_sets(avg)
    n_chart = len(desc["patches_in_layout_order"])
    assert n_avg == 2 * n_chart                                  # keep -> 2 concatenated scans
    ranged = [e for e in cache.list_repo_z9_profiles(SERIAL) if cid in (e.get("notes") or "")]
    assert ranged and ranged[0]["n_patches"] == n_avg            # meta = REAL count, not patch_count


def test_profile_base_last_uses_latest_unchanged(client, monkeypatch):
    """base='last'/None: historic behavior (latest ti3), averaged=False."""
    cid = _create_a3(client)
    desc = _descriptor(cid)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(desc))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * 1024)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=1024)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={"profile_base": "last"})
    assert r.status_code == 200, r.text
    assert r.json()["averaged"] is False


# --- Single-chart single-pass build (default) --------------------------------
def test_profile_simple_pass(client, monkeypatch):
    """Single-chart build -> SINGLE-PASS colprof (1 source, passthrough), no
    concat, origin free_chart_argyll."""
    cid = _create_a3(client)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)

    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        assert not str(ti3_base).endswith("_enriched")     # no enrich concat
        Path(output_icc_path).write_bytes(b"\0" * 2048)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=2048)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200
    assert any(e["origin"] == "free_chart_argyll" and e["media_id"] == MID
               for e in cache.list_repo_z9_profiles(SERIAL))


def _stub_build(monkeypatch):
    def _fake_build(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        Path(output_icc_path).write_bytes(b"\0" * 2048)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=2048)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fake_build)


def test_profile_custom_name(client, monkeypatch):
    # CHOSEN name -> filename = slugify(name) (without auto HPZ9/GE/date).
    cid = _create_a3(client)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    _stub_build(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"name": "Mon Profil Perso"})
    assert r.status_code == 200, r.text
    assert any(e["filename"] == "mon-profil-perso.icc" and e["media_id"] == MID
               for e in cache.list_repo_z9_profiles(SERIAL))


def test_profile_custom_name_collision_409(client, monkeypatch):
    # 2nd build same name -> 409 + suggestion -2 (never a silent -2).
    cid = _create_a3(client)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    _stub_build(monkeypatch)
    assert client.post(f"/api/charts/{cid}/profile", json={"name": "MyProf"}).status_code == 200
    r2 = client.post(f"/api/charts/{cid}/profile", json={"name": "MyProf"})
    assert r2.status_code == 409
    assert r2.json()["detail"]["suggestion"] == "myprof-2"


def test_profile_auto_name_collision_409_then_replace(client, monkeypatch):
    # AUTO name: 2nd build WITHOUT intent -> 409 (no more silent -N); with on_conflict=replace
    # -> 200 (rebuild "same profile" of the chart). The trigger (silent -2) is closed.
    cid = _create_a3(client)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    _stub_build(monkeypatch)
    assert client.post(f"/api/charts/{cid}/profile", json={}).status_code == 200
    r2 = client.post(f"/api/charts/{cid}/profile", json={})
    assert r2.status_code == 409 and r2.json()["detail"]["error"] == "name_conflict"
    assert r2.json()["detail"]["suggestion"].endswith("-2")
    r3 = client.post(f"/api/charts/{cid}/profile", json={"on_conflict": "replace"})
    assert r3.status_code == 200, r3.text


def test_profile_invalid_on_conflict_422(client):
    cid = _create_a3(client)
    r = client.post(f"/api/charts/{cid}/profile", json={"on_conflict": "smash"})
    assert r.status_code == 422


def test_profile_custom_name_non_ascii_refused(client, monkeypatch):
    cid = _create_a3(client)
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, **kw: _native_cgats_for(_descriptor(cid)))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    _scan_and_wait(client, cid)
    _stub_build(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"name": "Café"})
    assert r.status_code == 400


def test_profile_custom_name_too_long_refused(client):
    # >63 -> refused (422 Pydantic max_length, ICC V2 desc). Invalid body -> before the logic.
    cid = _create_a3(client)
    r = client.post(f"/api/charts/{cid}/profile", json={"name": "x" * 70})
    assert r.status_code == 422
