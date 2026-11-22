"""Measurement identity in the profiling engine and the chart API. Store isolated, colprof
faked, no printer, no instrument, no binary run.

Combining readings (average, additional charts) requires one measurement identity;
historical SOL scans keep combining as before; incompatible readings kept but EXCLUDED
never block; validation with another instrument is allowed and reported."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import build_inputs as bi
from lib.z9_client import cache
from lib.z9_client import measurement_identity as mi
from lib.z9_client import measurement_publish as mp
from lib.z9_client import printtarg_chart as P
from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from webapp.backend.tests.test_build_inputs import _chart, _fake_colprof, grid_rows, make_chart, ti3_text
from webapp.backend.tests.test_charts_routes import MID, SERIAL
from webapp.backend.tests.test_measurement_identity import colormunki_ti3, grid, i1pro3_ti3, ti3

IDS = [r[0] for r in grid()]


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


def _no_printer():
    raise AssertionError("this route must not need the printer")


def _printtarg_chart(cid, instrument="colormunki", purpose="profiling") -> Path:
    """A printtarg chart folder as the generator leaves it (no TIFF needed here)."""
    d = cache.charts_dir(SERIAL) / cid
    d.mkdir(parents=True)
    spec = P.INSTRUMENTS[instrument]
    (d / "chart.json").write_text(json.dumps({
        "schema_version": 1, "chart_id": cid, "created_at": "2026-11-22T15:00:00",
        "layout": "printtarg", "roll_layout": "transverse", "purpose": purpose,
        "instrument": {"key": spec.key, "name": spec.name, "printtarg_selector": spec.selector},
        "media": {"format_key": "roll24", "source": "roll", "width_mm": 610.0, "height_mm": None},
        "paper": {"name": "Baryta test", "media_id": MID, "gloss_enhancer": "OFF", "serial": SERIAL},
        "geometry": {"patch_count": len(IDS), "total_strips": 2, "n_pages": 1},
        "files": {"tiff": "chart.tif"}}))
    return d


_SECOND = iter(range(10, 60))


def _measure(d, data, argv=("chartread", "-v", "chart"), model="colormunki"):
    """An external reading published through the import contract (record first)."""
    from datetime import datetime
    stage = d / "external"
    stage.mkdir(exist_ok=True)
    (stage / "s.ti3").write_bytes(data)
    rec = {"method": "external", "instrument": {"model": model, "serial": None},
           "acquisition": {"tool": "chartread", "argv": list(argv), "measured_at": None},
           "chart": {"chart_id": d.name}, "raw_files": []}
    out = mp.publish_measurement(d, stage / "s.ti3", rec, IDS,
                                 now=datetime(2026, 11, 22, 16, 0, next(_SECOND)))
    return out.ti3_path.name


def _m0(d):
    return _measure(d, i1pro3_ti3(), ("chartread", "-F", "n", "c"), "i1pro3plus")


def _m2(d, rows=None):
    return _measure(d, i1pro3_ti3(rows, instrument_filter="UVCUT"), ("chartread", "-F", "u", "c"), "i1pro3plus")


# ─── historical SOL behaviour ───────────────────────────────────────────────
def test_historical_sol_scans_still_average(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())})
    app.dependency_overrides[charts_routes.get_chart_z9] = _no_printer
    r = client.post(f"/api/charts/{cid}/profile/preview", json={"profile_base": "average"})
    assert r.status_code == 200, r.text
    comp = r.json()["measurement_compatibility"]
    assert comp["status"] == "compatible" and comp["combined"]
    assert all(m["identity"]["historical"] and m["identity"]["instrument"] == "hp_z9_embedded"
               for m in comp["members"])
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert r.status_code == 200 and r.json()["averaged"], r.text
    assert fed["ti3_path"].name == f"{cid}_avg.ti3"
    man = json.loads((d / "profile_manifest.json").read_text())
    assert [s["measurement_identity"]["origin"] for s in man["plan"]["current_scans"]] == ["sol_header"] * 2
    assert man["plan"]["measurement_compatibility"]["status"] == "compatible"


def test_sol_fingerprint_has_no_record_part(tmp_path):
    """Scans without a record: the inputs fingerprint is computed exactly as before."""
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())})
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(base="average"))
    import hashlib
    core = {"v": 1, "chart_id": "CHT-A", "base_effective": "average",
            "scans": [["CHT-A", "current", s.scan, s.sha256, []] for s in plan.scans],
            "profiles": [], "flags": [], "flag_files": []}
    assert plan.fingerprint == "sha256:" + hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()


# ─── refusals ───────────────────────────────────────────────────────────────
def test_m0_and_m2_are_refused_at_preview_and_at_build(client, monkeypatch):
    cid = "CHT-20261122-1600-AA"
    d = _printtarg_chart(cid, "i1pro3plus")
    m0, m2 = _m0(d), _m2(d)
    app.dependency_overrides[charts_routes.get_chart_z9] = _no_printer
    r = client.post(f"/api/charts/{cid}/profile/preview", json={"profile_base": "average"})
    assert r.status_code == 409, r.text
    det = r.json()["detail"]
    assert det["code"] == "measurement_identity_incompatible"
    assert {s["scan"] for s in det["scans"]} == {m0, m2}
    diffs = det["decision"]["conflicts"][0]["differences"]
    assert [x["code"] for x in diffs] == ["condition_differs"]
    assert {diffs[0]["a"], diffs[0]["b"]} == {"M0", "M2"}
    assert "exclude_scan" in det["remedies"]
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "measurement_identity_incompatible"
    assert fed == {} and not (d / f"{cid}.icc").exists() and not (d / ".build").exists()


def test_build_job_refuses_even_when_the_route_is_bypassed(client, monkeypatch):
    cid = "CHT-20261122-1600-AB"
    d = _printtarg_chart(cid, "i1pro3plus")
    _m0(d), _m2(d)
    fed = _fake_colprof(monkeypatch)
    body = charts_routes.ProfileChartBody(profile_base="average")
    with pytest.raises(bi.MeasurementIdentityError) as e:
        charts_routes._build_profile_now(cid, body, lambda phase: None)
    assert e.value.code == "measurement_identity_incompatible" and fed == {}
    assert not (d / f"{cid}.icc").exists()


def test_materialize_refuses_a_hand_made_plan(tmp_path):
    """Defence in depth: a plan built without plan_inputs is checked again."""
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3().decode(), "b.ti3": colormunki_ti3().decode()})
    scans = tuple(bi.PlannedScan("CHT-A", "current", s, snap.scan_path(s),
                                 bi._sha256_file(snap.scan_path(s)), ()) for s in ("a.ti3", "b.ti3"))
    plan = bi.InputPlan(chart_id="CHT-A", policy="legacy", base_requested="average",
                        base_effective="average", current=scans, extras=(), profiles=(),
                        rejections_not_applied=(), rejections_unmatched=(), colprof_flags=(), flag_files=())
    with pytest.raises(bi.MeasurementIdentityError):
        bi.materialize(plan, tmp_path / "w")
    assert not (tmp_path / "w" / "CHT-A_avg.ti3").exists()


def test_excluding_the_incompatible_scan_allows_the_build(client, monkeypatch):
    cid = "CHT-20261122-1600-AC"
    d = _printtarg_chart(cid, "i1pro3plus")
    m0 = _m0(d)
    _m2(d), _m2(d, grid(3)[::-1])                               # two M2 readings
    r = client.post(f"/api/charts/{cid}/scans/{m0}/role", json={"role": "excluded"})
    assert r.status_code == 200, r.text
    assert (d / "measurements" / m0).exists()                    # kept, only excluded
    r = client.post(f"/api/charts/{cid}/profile/preview", json={"profile_base": "average"})
    assert r.status_code == 200, r.text
    comp = r.json()["measurement_compatibility"]
    assert comp["status"] == "compatible" and len(comp["members"]) == 2
    assert {m["identity"]["condition"] for m in comp["members"]} == {"M2"}
    _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert r.status_code == 200 and r.json()["n_scans_averaged"] == 2, r.text


def test_base_last_uses_one_scan_and_is_never_refused(client):
    cid = "CHT-20261122-1600-AD"
    d = _printtarg_chart(cid, "i1pro3plus")
    _m0(d), _m2(d)
    r = client.post(f"/api/charts/{cid}/profile/preview", json={})
    assert r.status_code == 200, r.text
    assert r.json()["base_effective"] == "last"
    assert r.json()["measurement_compatibility"]["combined"] is False


def test_extra_chart_of_another_instrument_is_refused(client):
    cur, other = "CHT-20261122-1600-BA", "CHT-20261122-1600-BB"
    _measure(_printtarg_chart(cur, "colormunki"), colormunki_ti3())
    _m2(_printtarg_chart(other, "i1pro3plus"))
    r = client.post(f"/api/charts/{cur}/profile/preview", json={"extra_chart_ids": [other]})
    assert r.status_code == 409, r.text
    det = r.json()["detail"]
    assert "instrument_differs" in {x["code"] for x in det["decision"]["conflicts"][0]["differences"]}
    assert det["remedies"] == ["remove_extra_chart"]
    src = client.get(f"/api/charts/{cur}/build-sources").json()["extra_charts"]
    assert [(x["chart_id"], x["measurement_compatibility"]) for x in src] == [(other, "incompatible")]


def test_extra_chart_sol_and_external_are_refused(client):
    cid, _ = _chart(client, {"a.ti3": ti3_text(grid_rows())})          # honeycomb, SOL scan
    other = "CHT-20261122-1600-BC"
    _measure(_printtarg_chart(other, "colormunki"), colormunki_ti3())
    r = client.post(f"/api/charts/{cid}/profile/preview", json={"extra_chart_ids": [other]})
    assert r.status_code == 409
    codes = {x["code"] for x in r.json()["detail"]["decision"]["conflicts"][0]["differences"]}
    assert {"method_differs", "instrument_differs"} <= codes


def test_extra_chart_same_instrument_and_condition_is_combined(client, monkeypatch):
    cur, other = "CHT-20261122-1600-BD", "CHT-20261122-1600-BE"
    _measure(_printtarg_chart(cur, "colormunki"), colormunki_ti3())
    _measure(_printtarg_chart(other, "colormunki"), colormunki_ti3(grid()[::-1]))
    r = client.post(f"/api/charts/{cur}/profile/preview", json={"extra_chart_ids": [other]})
    assert r.status_code == 200, r.text
    assert r.json()["measurement_compatibility"]["status"] == "compatible"
    _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cur}/profile", json={"extra_chart_ids": [other]})
    assert r.status_code == 200, r.text


def test_source_profiles_keep_their_historical_rule(tmp_path):
    """Measurements extracted from an ICC are relabelled by the extraction: not tracked,
    never checked by identity (the historical band check of the concat still applies)."""
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows())})
    icc = tmp_path / "src.icc"
    icc.write_bytes(b"ICC")
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(source_profiles=(str(icc),)))
    d = plan.to_dict()
    assert d["source_profiles"][0]["identity_tracked"] is False
    assert d["source_profiles"][0]["measurement_identity"] is None
    assert d["measurement_compatibility"]["combined"] is False


def test_invalid_record_refuses_the_build_even_for_one_scan(client):
    cid = "CHT-20261122-1600-BF"
    d = _printtarg_chart(cid)
    name = _measure(d, colormunki_ti3())
    mi.record_path(d / "measurements" / name).write_text('{"schema": "x"}')
    r = client.post(f"/api/charts/{cid}/profile/preview", json={})
    assert r.status_code == 409, r.text
    det = r.json()["detail"]
    assert det["code"] == "measurement_record_invalid"
    assert det["record_error"]["code"] == "measurement_record_malformed"


def test_record_changed_between_plan_and_materialize(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    cid = "CHT-20261122-1600-BG"
    d = _printtarg_chart(cid)
    a = _measure(d, colormunki_ti3())
    _measure(d, colormunki_ti3(grid()[::-1]))
    snap = bi.ChartSnapshot(cid, d, tuple(sorted(p.name for p in (d / "measurements").glob("*.ti3"))))
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(base="average"))
    assert all(s.record_sha256 for s in plan.scans)
    rec = mi.record_path(d / "measurements" / a)
    rec.write_text(rec.read_text() + "\n")                     # same facts, other bytes
    assert bi.plan_inputs(snap, [], bi.BuildRequest(base="average")).fingerprint != plan.fingerprint
    with pytest.raises(bi.SourceChangedError):
        bi.materialize(plan, tmp_path / "w")


# ─── comparison of scans (ΔE, QC) ───────────────────────────────────────────
def test_concordance_requires_the_same_identity(client):
    cid = "CHT-20261122-1600-CA"
    d = _printtarg_chart(cid, "i1pro3plus")
    _m0(d), _m2(d)
    for route in ("scan/delta", "scan/qc"):
        r = client.get(f"/api/charts/{cid}/{route}")
        assert r.status_code == 409, (route, r.text)
        assert r.json()["detail"]["code"] == "scans_not_comparable"
    cid2, _ = _chart(client, {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())})
    for route in ("scan/delta", "scan/qc"):
        r = client.get(f"/api/charts/{cid2}/{route}")
        assert r.status_code == 200, (route, r.text)
        assert r.json()["measurement_compatibility"]["status"] == "compatible"


def test_exclusion_impact_skips_a_base_that_cannot_be_built(client):
    cid = "CHT-20261122-1600-CB"
    d = _printtarg_chart(cid, "i1pro3plus")
    m0 = _m0(d)
    _m2(d)
    r = client.post(f"/api/charts/{cid}/scans/{m0}/readings/5/exclusion", json={"dry_run": True})
    assert r.status_code == 200, r.text
    imp = r.json()["impact"]
    assert imp["bases_unavailable"] == {"average": "measurement_identity_incompatible"}
    assert "last" in imp["bases"]


# ─── chart library and detail ───────────────────────────────────────────────
def test_list_mixes_historical_honeycomb_and_printtarg(client):
    hc, d_hc = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    assert "layout" not in json.loads((d_hc / "chart.json").read_text())      # historical chart
    pt = "CHT-20261122-1600-DA"
    _measure(_printtarg_chart(pt, "i1pro3plus"), i1pro3_ti3(instrument_filter="UVCUT"),
             ("chartread", "-F", "u", "c"), "i1pro3plus")
    app.dependency_overrides[charts_routes.get_chart_z9] = _no_printer
    charts = {c["chart_id"]: c for c in client.get("/api/charts").json()["charts"]}
    h, p = charts[hc], charts[pt]
    assert (h["layout"], h["measurement_method"], h["instrument"], h["roll_layout"], h["n_strips"]) == \
        ("honeycomb", "sol", None, None, None)
    assert (p["layout"], p["measurement_method"], p["instrument"], p["roll_layout"], p["n_strips"]) == \
        ("printtarg", "external", "i1pro3plus", "transverse", 2)
    for c in (h, p):                                         # historical fields still there
        assert {"patch_count", "printed", "scanned", "n_scans", "purpose", "paper_media_id",
                "gloss_enhancer", "profiled"} <= set(c)
        assert c["scanned"] and c["n_scans"] == 1


def test_detail_exposes_the_expected_method_and_each_identity(client):
    hc, _ = _chart(client, {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())})
    pt = "CHT-20261122-1600-DB"
    d = _printtarg_chart(pt, "i1pro3plus")
    _m0(d), _m2(d)
    app.dependency_overrides[charts_routes.get_chart_z9] = _no_printer
    h = client.get(f"/api/charts/{hc}").json()
    assert h["identity"]["layout"] == "honeycomb" and h["identity"]["measurement"]["method"] == "sol"
    assert all(s["measurement_identity"]["historical"] for s in h["scans"])
    assert h["included_scans_compatibility"]["status"] == "compatible"
    p = client.get(f"/api/charts/{pt}").json()
    m = p["identity"]["measurement"]
    assert (p["identity"]["instrument"], m["method"], m["instruments"], m["requires_printer"]) == \
        ("i1pro3plus", "external", ["i1pro3plus"], False)
    ids = [s["measurement_identity"] for s in p["scans"]]
    assert [i["condition"] for i in ids] == ["M0", "M2"]
    assert all(i["has_record"] and i["provenance"]["argv"] for i in ids)
    assert p["included_scans_compatibility"]["status"] == "incompatible"
    assert "external_session" not in p and "session" not in p          # no fake session state


def test_delete_scan_removes_its_record(client):
    cid = "CHT-20261122-1600-DC"
    d = _printtarg_chart(cid)
    name = _measure(d, colormunki_ti3())
    r = client.delete(f"/api/charts/{cid}/scans/{name}")
    assert r.status_code == 200, r.text
    assert sorted(p.name for p in (d / "measurements").iterdir() if not p.name.startswith(".")) == []


def test_printtarg_options_come_from_the_whitelist(client, monkeypatch):
    import subprocess
    import lib.z9_client.argyll as argyll

    def _no_run(*a, **kw):
        raise AssertionError("no binary may be run")
    monkeypatch.setattr(subprocess, "run", _no_run)
    monkeypatch.setattr(subprocess, "Popen", _no_run)
    monkeypatch.setattr(argyll, "find_argyll_binary",
                        lambda name: None if name == "chartread" else f"/opt/argyll/{name}")
    app.dependency_overrides[charts_routes.get_chart_z9] = _no_printer
    r = client.get("/api/charts/printtarg-options")
    assert r.status_code == 200, r.text
    o = r.json()
    assert [i["key"] for i in o["instruments"]] == list(P.INSTRUMENTS)
    assert o["media"] == list(P.SUPPORTED_MEDIA)
    for i in o["instruments"]:
        assert i["printtarg_selector"] == P.INSTRUMENTS[i["key"]].selector
        (preset,) = i["presets"]
        ref = P.PRESETS[(i["key"], "roll24")]
        assert (preset["page_w_mm"], preset["page_h_mm"], preset["unlimited_strip"]) == \
            (ref.page_w_mm, ref.page_h_mm, ref.unlimited_strip)
        assert i["measurement"]["method"] == "external"
    assert o["roll_layout"]["default"] == "transverse"
    assert o["binaries"] == {"printtarg": {"available": True}, "chartread": {"available": False}}
    assert o["requires_printer_for_measurement"] is False


# ─── validation with another instrument ─────────────────────────────────────
def _build_profile(client, monkeypatch, cid):
    _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200, r.text
    return cache.charts_dir(SERIAL) / cid / f"{cid}.icc"


def _validate(client, monkeypatch, cid, icc):
    monkeypatch.setattr("lib.z9_client.profile_compare.verify_profile_independent",
                        lambda prof, ti3: {"status": "ok"})
    r = client.post(f"/api/charts/{cid}/validate",
                    json={"profile_path": str(icc), "media_id": MID, "gloss_enhancer": "OFF"})
    assert r.status_code == 200, r.text
    return r.json()["measurement_identity"]


def test_validation_with_another_instrument_is_allowed_with_a_warning(client, monkeypatch):
    built = "CHT-20261122-1600-EA"
    _measure(_printtarg_chart(built, "colormunki"), colormunki_ti3())
    icc = _build_profile(client, monkeypatch, built)
    other = "CHT-20261122-1600-EB"
    _m2(_printtarg_chart(other, "i1pro3plus", purpose="validation"))
    app.dependency_overrides[charts_routes.get_chart_z9] = _no_printer
    rep = _validate(client, monkeypatch, other, icc)
    assert rep["comparison"]["status"] == "differs"
    assert [w["code"] for w in rep["warnings"]] == ["validation_identity_differs"]
    assert rep["build"]["status"] == "recorded"
    assert [s["identity"]["instrument"] for s in rep["build"]["scans"]] == ["colormunki"]
    assert rep["validation"]["identity"]["instrument"] == "i1pro3plus"
    same = "CHT-20261122-1600-EC"
    _measure(_printtarg_chart(same, "colormunki", purpose="validation"), colormunki_ti3(grid()[::-1]))
    rep = _validate(client, monkeypatch, same, icc)
    assert rep["comparison"]["status"] == "same" and rep["warnings"] == []


def test_validation_reconstructs_identities_of_an_older_manifest(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    icc = _build_profile(client, monkeypatch, cid)
    ptr = json.loads((d / "profile_manifest.json").read_text())
    for s in ptr["plan"]["current_scans"]:
        s.pop("measurement_identity")                         # manifest written before this field
    (d / "profile_manifest.json").write_text(json.dumps(ptr))
    val, _ = _chart(client, {"v.ti3": ti3_text(grid_rows())})
    rep = _validate(client, monkeypatch, val, icc)
    assert rep["build"]["status"] == "reconstructed" and rep["comparison"]["status"] == "same"


# ─── exclusion guards: a reading of another identity is no replacement ──────
WHITE = next(sid for sid, r, g, b in grid() if (r, g, b) == (100.0, 100.0, 100.0))


def test_a_reading_of_another_condition_is_not_a_replacement(client):
    """M0 and M2 included together: the M2 white cannot stand in for the M0 one (the build
    would never combine them), so excluding the M0 white is the last-reading refusal."""
    cid = "CHT-20261122-1600-FA"
    d = _printtarg_chart(cid, "i1pro3plus")
    m0 = _m0(d)
    _m2(d)
    r = client.post(f"/api/charts/{cid}/scans/{m0}/readings/{WHITE}/exclusion", json={"dry_run": True})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "WHITE_LAST_READING"
    assert r.json()["detail"]["impact"]["other_compatible_readings_in_chart"] == 0
    white = next(x for x in client.get(f"/api/charts/{cid}/scans/{m0}/patches").json()["patches"] if x["id"] == WHITE)
    assert white["exclusion_risk"]["other_compatible_readings"] == 0 and white["exclusion_risk"]["level"] == "refused"
    r = client.post(f"/api/charts/{cid}/scans/{m0}/readings/5/reject", json={"rejected": True})
    assert r.status_code == 409, r.text                     # last valid reading of patch 5 at M0
    assert not json.loads((d / "scan_state.json").read_text() if (d / "scan_state.json").exists() else "{}") \
        .get("rejected_readings")


def test_readings_of_the_same_condition_still_replace_each_other(client):
    cid = "CHT-20261122-1600-FB"
    d = _printtarg_chart(cid, "i1pro3plus")
    a = _m2(d)
    _m2(d, grid()[::-1])
    r = client.post(f"/api/charts/{cid}/scans/{a}/readings/{WHITE}/exclusion", json={"dry_run": True})
    assert r.status_code == 200, r.text
    assert r.json()["impact"]["other_compatible_readings_in_chart"] == 1
    r = client.post(f"/api/charts/{cid}/scans/{a}/readings/5/reject", json={"rejected": True})
    assert r.status_code == 200, r.text
