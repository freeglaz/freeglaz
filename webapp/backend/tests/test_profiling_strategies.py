"""Profiling strategies (two-pass adaptive): definitions and composition, and the NEW links only —
pass 2 made from the exact intermediate profile of pass 1 (copy + sha256, never replaced
silently), the intermediate profile never ranged nor installable, conditions and instrument
inherited, print conditions checked before any paper is used, FWA gate, resume from the
charts. targen and colprof are faked (the ICC and targen behaviours are not re-validated
here); the printer is the mock of test_charts_routes."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import measurement_identity as mi
from lib.z9_client import profiling_strategies as ps
from lib.z9_client import targen_strategies as tg
from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from webapp.backend.tests.test_charts_routes import (MID, SERIAL, _RESIDENT, _fake_icc_build, _fake_z9,
                                                     _ti1_text, _write_ti3)

SID = "two_pass_adaptive"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    monkeypatch.setattr(charts_routes, "_clc_state", lambda z9, media_id: None)
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def targen(monkeypatch):
    """Fake targen: records the line, the -c and the bytes of the profile it was given."""
    calls = []
    state = {"fail": False}

    def _run(flags, out_basename, *, precond_icc=None, **kw):
        argv = tg.build_targen_argv(flags, out_basename, precond_icc=precond_icc)
        calls.append({"flags": flags, "argv": argv,
                      "precond_bytes": Path(precond_icc).read_bytes() if precond_icc else None})
        if state["fail"]:
            raise RuntimeError("targen failed (exit 1) : OFPS did not converge")
        ti1 = Path(f"{out_basename}.ti1")
        ti1.write_text(_ti1_text(tg.extract_f_count(flags) or 50))
        return ti1, {"argv": argv, "stdout": "", "returncode": 0}

    monkeypatch.setattr(charts_routes._tg, "run_targen", _run)
    return SimpleNamespace(calls=calls, state=state)


@pytest.fixture
def sent(monkeypatch):
    jobs = []

    def _send(self, job, **kw):
        jobs.append(job)
        return SimpleNamespace(duration_seconds=1.0, prn_size_bytes=1)

    monkeypatch.setattr("lib.z9_client.printing.PrintOps.send", _send)
    return jobs


def _icc_build(monkeypatch):
    """Fake colprof writing DIFFERENT bytes at each build (so versions differ)."""
    n = {"i": 0}

    def _fb(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        n["i"] += 1
        Path(output_icc_path).write_bytes(f"ICC#{n['i']} {descriptor} {' '.join(colprof_flags)}".encode() * 8)
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=64)
    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fb)


def _desc(cid):
    return json.loads((cache.locate_chart_dir(cid) / "chart.json").read_text())


def _create(client, pass_id="pass1", *, media="a3", ge="OFF", params=None, run=None, build_id=None, **extra):
    body = {"media_key": media, "paper_mediaid": MID, "gloss_enhancer": ge, "paper_name": "Baryta test",
            "strategy": {"id": SID, "pass_id": pass_id, "params": params or {}, "run": run,
                         "precondition_build_id": build_id}, **extra}
    return client.post("/api/charts", json=body)


def _pass1(client):
    r = _create(client, params={"pass1": {"chart": {"patches": 100}}, "pass2": {"chart": {"patches": 150}}})
    assert r.status_code == 200, r.text
    return r.json()["chart_id"]


def _measure(cid, name="20261125_100000.ti3", shift=0.0):
    _write_ti3(cache.locate_chart_dir(cid) / "measurements", name, shift=shift)


def _build_intermediate(client, cid):
    r = client.post(f"/api/charts/{cid}/profile", json={"colprof_flags": "-v -qm -r 2.0 -bl"})
    assert r.status_code == 200, r.text
    return r.json()


# ── definitions and composition (pure) ───────────────────────────────────────
def test_builtin_two_pass_lines_match_the_brief():
    sdef = ps.get_strategy(SID)
    assert [p.id for p in sdef.passes] == ["pass1", "pass2"]
    assert [p.role for p in sdef.passes] == ["preconditioning", "final"]
    c1, c2 = ps.compose_all(sdef, ps.normalize_params(sdef))
    assert c1["targen_flags"] == "-G -f 400" and c1["precondition"] == "none"
    assert c1["colprof_flags"] == "-v -qm -r 2.0 -bl"
    assert c2["targen_flags"] == "-G -N 0.75 -f 2000" and c2["precondition"] == "pass1"
    assert c2["colprof_flags"] == "-v -qh -S AdobeRGB -c mt -d pp"
    # the alias is said plainly: AdobeRGB is the bundled ClayRGB
    assert c2["source_gamut"]["alias"] == "AdobeRGB" and c2["source_gamut"]["file"] == "ClayRGB-elle-V2-g22.icc"
    assert c2["viewing_conditions"]["viewcond_in"]["description"] == "Monitor in typical work environment"


def test_params_are_bounded_and_only_those_of_the_pass():
    sdef = ps.get_strategy(SID)
    p = ps.normalize_params(sdef, {"pass1": {"profile": {"smoothing": 1.2}},
                                   "pass2": {"chart": {"ofps": "Q", "neutral_emphasis": 1.0}}})
    assert ps.compose(sdef, "pass1", p)["colprof_flags"] == "-v -qm -r 1.2 -bl"
    assert ps.compose(sdef, "pass2", p)["targen_flags"] == "-Q -N 1.0 -f 2000"
    for bad, code in (({"pass1": {"chart": {"patches": 10}}}, "param_out_of_range"),
                      ({"pass1": {"chart": {"neutral_emphasis": 0.5}}}, "unknown_param"),   # needs a -c
                      ({"pass2": {"profile": {"quality": "x"}}}, "invalid_param"),
                      ({"pass2": {"profile": {"source_gamut": "MyGamut"}}}, "invalid_param"),
                      ({"pass3": {}}, "unknown_pass"),
                      ({"pass1": {"chart": {"extra": "-c x.icc"}}}, "unknown_param")):
        with pytest.raises(ps.StrategyError) as e:
            ps.normalize_params(sdef, bad)
        assert e.value.code == code
    none = ps.normalize_params(sdef, {"pass2": {"profile": {"source_gamut": "none"}}})
    assert ps.compose(sdef, "pass2", none)["colprof_flags"] == "-v -qh"     # -c/-d only with -S


@pytest.mark.parametrize("toml, msg", [
    ('[strategy.x]\n[[strategy.x.pass]]\nid="p"\nchart={generator="targen",patches=100,ti1="a.ti1"}\n'
     'profile={role="final"}\n', "not both"),
    ('[strategy.x]\n[[strategy.x.pass]]\nid="p1"\nchart={generator="targen",patches=100}\n'
     'profile={role="preconditioning"}\n[[strategy.x.pass]]\nid="p2"\nchart={generator="targen",patches=100}\n'
     'profile={role="final"}\n', "no later pass uses"),
    ('[strategy.x]\n[[strategy.x.pass]]\nid="p"\nchart={generator="targen",patches=100,neutral_emphasis=0.5}\n'
     'profile={role="final"}\n', "preconditioning profile"),
    ('[strategy.x]\n[[strategy.x.pass]]\nid="p"\nchart={generator="targen",patches=100}\n'
     'profile={role="final"}\nvariants=[{id="a",default=true},{id="b",default=true}]\n', "exactly one default"),
])
def test_loader_refuses_malformed_definitions(tmp_path, toml, msg):
    f = tmp_path / "s.toml"
    f.write_text(toml)
    with pytest.raises(ps.StrategyError) as e:
        ps.load_strategies(f)
    assert e.value.code == "invalid_definition" and msg in e.value.message


def test_free_targen_line_cannot_bring_its_own_precondition():
    with pytest.raises(ps.StrategyError):
        ps.check_free_targen_line("-G -f 400 -c other.icc")
    ps.check_free_targen_line("-G -e 8 -f 400")


def _ident(instrument, condition, bands=(380.0, 390.0), method=mi.METHOD_EXTERNAL):
    return mi.MeasurementIdentity(method=method, instrument=instrument, condition=condition,
                                  spectral_bands=tuple(bands), calibration_standard="XRGA", origin="record")


def test_fwa_gate():
    assert ps.fwa_gate([_ident("i1pro3plus", "M0")]) == {"allowed": True, "reason": None}
    assert ps.fwa_gate([_ident("colormunki", mi.CONDITION_FIXED)])["reason"] == "instrument_without_uv"
    assert ps.fwa_gate([_ident("i1pro3plus", "M2")])["reason"] == "condition_not_m0"
    assert ps.fwa_gate([_ident("i1pro3plus", mi.CONDITION_UNKNOWN)])["reason"] == "condition_unknown"
    assert ps.fwa_gate([_ident("i1pro3plus", "M0", bands=())])["reason"] == "no_spectra"
    assert ps.fwa_gate([_ident(mi.SOL_MODEL, mi.CONDITION_FIXED, method=mi.METHOD_SOL)])["reason"] \
        == "instrument_not_supported"
    # the instrument rules FWA out before the file's content (a ColorMunki file without spectra)
    assert ps.fwa_gate([_ident("colormunki", mi.CONDITION_FIXED, bands=())])["reason"] == "instrument_without_uv"
    assert ps.fwa_gate([_ident("i1pro3plus", "M0"), None])["reason"] == "identity_unknown"
    assert ps.fwa_gate([])["reason"] == "no_measurements"


def test_next_step():
    f = {"printed": False}
    assert ps.next_step(f, []) == "pass1_print"
    f = {"printed": True, "n_included": 0}
    assert ps.next_step(f, []) == "pass1_measure"
    f = {"printed": True, "n_included": 1, "intermediate": {"built": False}}
    assert ps.next_step(f, []) == "intermediate_build"
    f["intermediate"] = {"built": True, "state": "stale"}
    assert ps.next_step(f, []) == "intermediate_build"
    f["intermediate"] = {"built": True, "state": "up_to_date"}
    assert ps.next_step(f, []) == "pass2_create"
    assert ps.next_step(f, [{"printed": False}]) == "pass2_print"
    assert ps.next_step(f, [{"printed": True, "n_included": 0}]) == "pass2_measure"
    assert ps.next_step(f, [{"printed": True, "n_included": 1, "final_built": False}]) == "final_build"
    assert ps.next_step(f, [{"printed": True, "n_included": 1, "final_built": True}]) == "done"


def test_strategies_route_and_compose(client):
    d = client.get("/api/profiling-strategies").json()
    s = next(x for x in d["strategies"] if x["id"] == SID)
    assert s["validated"] is False and s["passes_composed"][1]["colprof_flags"] == "-v -qh -S AdobeRGB -c mt -d pp"
    assert {"code": "pp", "description": "Practical Reflection Print (ISO-3664 P2)"} in d["choices"]["viewing_conditions"]
    r = client.post("/api/profiling-strategies/compose", json={"strategy": SID, "params": {"pass2": {"chart": {"patches": 1500}}}})
    assert r.status_code == 200 and r.json()["passes"][1]["targen_flags"] == "-G -N 0.75 -f 1500"
    r = client.post("/api/profiling-strategies/compose", json={"strategy": SID, "params": {"pass2": {"chart": {"patches": 99999}}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "param_out_of_range"


# ── pass 1 ───────────────────────────────────────────────────────────────────
def test_pass1_is_composed_by_the_server_and_starts_the_run(client, targen):
    cid = _pass1(client)
    st = _desc(cid)["strategy"]
    assert st["id"] == SID and st["pass"] == "pass1" and st["role"] == "preconditioning" and st["run"] == cid
    assert st["lines"] == {"targen_flags": "-G -f 100", "free_line": False, "colprof_flags": "-v -qm -r 2.0 -bl"}
    assert st["params"]["pass2"]["chart"]["patches"] == 150                 # frozen for pass 2
    assert st["resident_tag_sha256"] == charts_routes.hashlib_sha256(_RESIDENT.read_bytes())
    assert targen.calls[-1]["argv"][1:4] == ["-d2", "-G", "-f"] and "-c" not in targen.calls[-1]["argv"]
    assert _desc(cid)["targen"]["argv"] == targen.calls[-1]["argv"]       # the real argv, recorded


def test_pass1_refusals_cost_nothing(client, targen):
    for extra, code in (({"c_profile": "resident:OFF"}, "precondition_is_the_strategy"),
                        ({"purpose": "validation"}, "strategy_not_for_validation"),
                        ({"ti1_text": _ti1_text(40)}, "strategy_composes_the_chart")):
        r = _create(client, **extra)
        assert r.status_code == 422 and r.json()["detail"]["code"] == code, r.text
    r = _create(client, run="CHT-20261125-1000-AA")
    assert r.status_code == 422 and r.json()["detail"]["code"] == "strategy_run_not_expected"
    assert targen.calls == []                                               # refused before targen


def test_targen_failure_offers_the_quasi_random_fallback_explicitly(client, targen):
    targen.state["fail"] = True
    r = _create(client)
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "targen_failed" and d["ofps"] == "G" and d["fallback"] == "Q"
    targen.state["fail"] = False
    r = _create(client, params={"pass1": {"chart": {"ofps": "Q"}}})       # the user chose -Q
    assert r.status_code == 200 and _desc(r.json()["chart_id"])["strategy"]["lines"]["targen_flags"] == "-Q -f 400"


def test_print_records_the_parameters_actually_used(client, targen, sent):
    cid = _pass1(client)
    r = client.post(f"/api/charts/{cid}/print", json={"quality": "HIGH"})
    assert r.status_code == 200, r.text
    pp = _desc(cid)["print_params"]
    assert pp["quality"] == "HIGH" and pp["rendermode"] == "COLOR" and pp["gloss_enhancer"] == "OFF"
    assert pp["paper_media_id"] == MID and pp["printer_serial"] == SERIAL
    assert pp["resident_icc_sha256"] == charts_routes.hashlib_sha256(_RESIDENT.read_bytes())


# ── intermediate profile ─────────────────────────────────────────────────────
def test_intermediate_profile_is_never_ranged_nor_installable(client, targen, monkeypatch):
    _icc_build(monkeypatch)
    cid = _pass1(client)
    _measure(cid)
    res = _build_intermediate(client, cid)
    assert res["installable"] is False and res["ranged"] is None and res["ranged_icc_path"] is None
    assert res["strategy"]["role"] == "preconditioning"
    assert not any(cache.repo_z9_dir().rglob("*.icc")) if cache.repo_z9_dir().exists() else True
    d = cache.locate_chart_dir(cid)
    m = json.loads((d / "builds" / res["build_id"] / "manifest.json").read_text())
    assert m["request"]["strategy"] == {"id": SID, "run": cid, "pass": "pass1", "role": "preconditioning",
                                        "variant": None}
    assert m["result"]["label"] == f"Intermediate {cid} (targen -c only)"
    # even with a name asked for: no repo copy, no collision check against repo/z9
    r = client.post(f"/api/charts/{cid}/profile", json={"colprof_flags": "-v -qm -r 1.2 -bl", "name": "Mine"})
    assert r.status_code == 200 and r.json()["installable"] is False
    hist = client.get(f"/api/charts/{cid}/builds").json()["builds"]
    assert {b["role"] for b in hist} == {"preconditioning"}


# ── pass 2 ───────────────────────────────────────────────────────────────────
def _ready_for_pass2(client, monkeypatch):
    _icc_build(monkeypatch)
    cid = _pass1(client)
    _measure(cid)
    return cid, _build_intermediate(client, cid)["build_id"]


def test_pass2_uses_the_exact_intermediate_profile_and_keeps_a_copy(client, targen, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    inter = (cache.locate_chart_dir(p1) / "builds" / bid / "profile.icc").read_bytes()
    # another format (a4) than pass 1 (a3): the geometry is free between passes
    r = _create(client, "pass2", media="a4", run=p1, build_id=bid)
    assert r.status_code == 200, r.text
    p2 = r.json()["chart_id"]
    assert targen.calls[-1]["precond_bytes"] == inter                      # targen -c = that version
    assert targen.calls[-1]["argv"][-3] == "-c"
    assert targen.calls[-1]["flags"] == "-G -N 0.75 -f 150"
    d2 = cache.locate_chart_dir(p2)
    desc = _desc(p2)
    assert desc["strategy"]["run"] == p1 and desc["strategy"]["pass"] == "pass2"
    assert desc["precondition"]["build_id"] == bid and desc["precondition"]["chart_id"] == p1
    assert (d2 / "precondition.icc").read_bytes() == inter
    assert desc["precondition"]["icc_sha256"] == charts_routes.hashlib_sha256(inter)
    assert desc["strategy"]["inherited"]["measurement"]["consistent"] is True
    assert desc["strategy"]["inherited"]["measurement"]["instrument"] == mi.SOL_MODEL
    # the intermediate profile never converts the chart: the TIFF carries the live resident
    from PIL import Image
    with Image.open(d2 / desc["files"]["tiff"]) as im:
        embedded = im.info.get("icc_profile")
    assert embedded == _RESIDENT.read_bytes() and embedded != inter


def test_pass2_refusals(client, targen, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    n = len(targen.calls)
    cases = [
        (dict(run=p1), 422, "precondition_build_required"),
        (dict(run=p1, build_id="20990101T000000Z-abcdef"), 409, "precondition_build_unknown"),
        (dict(run=p1, build_id=bid, ge="FULLPAGE"), 409, "strategy_conditions_changed"),
        (dict(run=p1, build_id=bid, layout="printtarg", instrument="colormunki", media="roll24"), 409,
         "strategy_instrument_locked"),
        (dict(run=p1, build_id=bid, params={"pass1": {"chart": {"patches": 300}}}), 422, "earlier_pass_frozen"),
        (dict(run=None, build_id=bid), 422, "strategy_run_required"),
        (dict(run=p1, build_id=bid, strategy_line="-G -f 150 -c x.icc"), 422, "precondition_is_the_strategy"),
    ]
    for kw, status, code in cases:
        line = kw.pop("strategy_line", None)
        body = {"media_key": kw.pop("media", "a4"), "paper_mediaid": MID, "gloss_enhancer": kw.pop("ge", "OFF"),
                "strategy": {"id": SID, "pass_id": "pass2", "params": kw.pop("params", {}), "run": kw.pop("run"),
                             "precondition_build_id": kw.pop("build_id", None), "targen_flags": line}, **kw}
        r = client.post("/api/charts", json=body)
        assert r.status_code == status and r.json()["detail"]["code"] == code, (code, r.text)
    assert len(targen.calls) == n                                           # nothing generated


def test_a_stale_intermediate_is_refused_and_an_existing_pass2_keeps_its_own(client, targen, sent, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    assert client.post(f"/api/charts/{p1}/print", json={"quality": "HIGH"}).status_code == 200
    p2a = _create(client, "pass2", media="a4", run=p1, build_id=bid).json()["chart_id"]
    kept = (cache.locate_chart_dir(p2a) / "precondition.icc").read_bytes()
    _measure(p1, "20261126_100000.ti3", shift=1.0)                          # a new acquisition of pass 1
    r = _create(client, "pass2", media="a4", run=p1, build_id=bid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "precondition_stale"
    bid2 = _build_intermediate(client, p1)["build_id"]                     # "Préparer la passe 2" again
    assert bid2 != bid
    r = _create(client, "pass2", media="a4", run=p1, build_id=bid)         # the old one is no longer current
    assert r.status_code == 409 and r.json()["detail"]["code"] == "precondition_not_current"
    p2b = _create(client, "pass2", media="a4", run=p1, build_id=bid2).json()["chart_id"]
    # the first attempt still carries its own preconditioner, untouched
    assert (cache.locate_chart_dir(p2a) / "precondition.icc").read_bytes() == kept
    assert _desc(p2a)["precondition"]["build_id"] == bid and _desc(p2b)["precondition"]["build_id"] == bid2
    runs = client.get(f"/api/profiling-strategies/runs?paper={MID}").json()["runs"]
    assert len(runs) == 1 and {c["chart_id"] for c in runs[0]["later"]} == {p2a, p2b}
    assert runs[0]["next_step"] == "pass2_print"


def test_pass2_survives_the_deletion_of_pass1(client, targen, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    p2 = _create(client, "pass2", media="a4", run=p1, build_id=bid).json()["chart_id"]
    assert client.delete(f"/api/charts/{p1}").status_code == 200
    assert (cache.locate_chart_dir(p2) / "precondition.icc").is_file()
    run = client.get(f"/api/profiling-strategies/runs/{p1}").json()
    assert run["first_missing"] is True and run["variant_available"] is False


# ── print of pass 2 ──────────────────────────────────────────────────────────
def test_pass2_print_conditions_are_checked_before_any_paper(client, targen, sent, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    assert client.post(f"/api/charts/{p1}/print", json={"quality": "HIGH"}).status_code == 200
    p2 = _create(client, "pass2", media="a4", run=p1, build_id=bid).json()["chart_id"]
    n = len(sent)
    r = client.post(f"/api/charts/{p2}/print", json={"quality": "NORMAL"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "strategy_print_conditions_changed"
    assert r.json()["detail"]["differences"][0]["code"] == "quality_differs"
    # a resident changed since pass 1: a warning to confirm, never a silent print
    d1 = cache.locate_chart_dir(p1)
    desc1 = _desc(p1)
    desc1["print_params"]["resident_icc_sha256"] = "0" * 64
    (d1 / "chart.json").write_text(json.dumps(desc1))
    r = client.post(f"/api/charts/{p2}/print", json={"quality": "HIGH"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "strategy_print_warnings"
    assert [w["code"] for w in r.json()["detail"]["warnings"]] == ["resident_changed"]
    assert len(sent) == n                                                   # nothing sent so far
    r = client.post(f"/api/charts/{p2}/print", json={"quality": "HIGH", "confirm_condition_warnings": True})
    assert r.status_code == 200, r.text
    assert _desc(p2)["print_params"]["confirmed_warnings"][0]["code"] == "resident_changed"


# ── final profile, variant, FWA ──────────────────────────────────────────────
def test_final_profile_and_the_pass1_pass2_variant(client, targen, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    p2 = _create(client, "pass2", media="a4", run=p1, build_id=bid).json()["chart_id"]
    _measure(p2)
    flags = _desc(p2)["strategy"]["lines"]["colprof_flags"]
    assert flags == "-v -qh -S AdobeRGB -c mt -d pp"
    r = client.post(f"/api/charts/{p2}/profile", json={"colprof_flags": flags})
    assert r.status_code == 200, r.text
    assert r.json()["installable"] is True and r.json()["strategy"]["variant"] == "pass2"
    # the alias really went to colprof as the bundled ClayRGB
    m = client.get(f"/api/charts/{p2}/builds/{r.json()['build_id']}/manifest").json()
    assert any(f.endswith("ClayRGB-elle-V2-g22.icc") for f in m["request"]["colprof_flags_resolved"])
    r = client.post(f"/api/charts/{p2}/profile",
                    json={"colprof_flags": flags, "extra_chart_ids": [p1], "name": "Baryta_p1p2"})
    assert r.status_code == 200, r.text
    assert r.json()["strategy"]["variant"] == "pass1_pass2" and r.json()["installable"] is True
    hist = client.get(f"/api/charts/{p2}/builds").json()["builds"]
    assert sorted(b["strategy_variant"] for b in hist) == ["pass1_pass2", "pass2"]
    run = client.get(f"/api/profiling-strategies/runs/{p1}").json()
    assert run["next_step"] == "pass1_print"           # derived from the charts: nothing printed here
    assert run["later"][0]["final_built"] is True


def test_fwa_is_refused_on_measurements_argyll_cannot_compensate(client, targen, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    p2 = _create(client, "pass2", media="a4", run=p1, build_id=bid).json()["chart_id"]
    _measure(p2)
    pv = client.post(f"/api/charts/{p2}/profile/preview", json={"colprof_flags": "-v -qh", "policy": "apply_all"})
    assert pv.status_code == 200 and pv.json()["fwa"]["allowed"] is False
    assert pv.json()["fwa"]["reason"] == "instrument_not_supported"           # built-in spectrophotometer
    r = client.post(f"/api/charts/{p2}/profile", json={"colprof_flags": "-v -qh -f"})
    assert r.status_code == 422 and "FWA" in r.text
    run = client.get(f"/api/profiling-strategies/runs/{p1}").json()
    assert run["fwa"]["pass2"]["allowed"] is False


def test_one_pass_chart_is_unchanged(client, targen):
    r = client.post("/api/charts", json={"media_key": "a3", "paper_mediaid": MID, "gloss_enhancer": "OFF",
                                         "targen_flags": "-d2 -G -f 60", "c_profile": "none"})
    assert r.status_code == 200 and "strategy" not in r.json()
    assert "strategy" not in _desc(r.json()["chart_id"])
    assert client.get("/api/profiling-strategies/runs").json()["runs"] == []


# ── measurement condition inherited by the external session ──────────────────
def test_external_session_condition_is_locked_for_a_later_pass():
    from webapp.backend.services.external_measure import _strategy_filter_lock
    desc = {"strategy": {"pass": "pass2", "inherited": {"measurement": {
        "consistent": True, "instrument": "i1pro3plus", "condition": "M0", "filter_option": "n"}}}}
    assert _strategy_filter_lock(desc) == "n"
    assert _strategy_filter_lock({"strategy": {"pass": "pass1", "inherited": {"measurement": None}}}) is None
    assert _strategy_filter_lock({}) is None
