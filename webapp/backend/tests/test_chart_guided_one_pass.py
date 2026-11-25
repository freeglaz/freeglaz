"""The one-pass wizard's guided form: the same parameters, bounds and composition as a final pass
of a profiling strategy, without being one (no run, no pass, no ``strategy`` block). The server
composes the targen line (the -c stays the wizard's menu) and records the colprof line the
profile step starts from. Its default values give the lines the wizard always started from.
targen is faked (its behaviour is not re-validated here); the printer is the mock of
test_charts_routes."""
import pytest

from lib.z9_client import profiling_strategies as ps
from webapp.backend.tests.test_charts_routes import MID, _ti1_text
from webapp.backend.tests.test_profiling_strategies import (  # noqa: F401  (fixtures)
    _desc, client, sent, targen)

BASE = {"media_key": "a3", "paper_mediaid": MID, "gloss_enhancer": "OFF", "paper_name": "Baryta test"}
RESIDENT = "resident:OFF"


def _guided(client, params=None, c_profile=RESIDENT, **extra):
    return client.post("/api/charts", json={**BASE, "c_profile": c_profile, "guided": {"params": params or {}},
                                            **extra})


# ── definitions and composition (pure) ───────────────────────────────────────
def test_defaults_give_the_lines_the_wizard_started_from():
    params = ps.normalize_one_pass(preconditioned=True)
    c = ps.compose_one_pass(params)
    assert c["targen_flags"] == "-G -N 0.5 -f 200"          # targen's own -N 0.5, written out
    assert c["colprof_flags"] == "-v -qh"                   # the profile step's default line
    assert c["source_gamut"] is None and c["viewing_conditions"] is None
    # without a -c, targen cannot place the neutral axis: -N / -n are left out
    bare = ps.normalize_one_pass({"chart": {"neutral_emphasis": 1.0, "neutral_steps": 8}}, preconditioned=False)
    assert "neutral_emphasis" not in bare["chart"] and "neutral_steps" not in bare["chart"]
    assert ps.compose_one_pass(bare)["targen_flags"] == "-G -f 200"


def test_params_are_bounded_and_only_those_of_the_form():
    p = ps.normalize_one_pass({"chart": {"patches": 836, "ofps": "Q", "grey_steps": 12, "neutral_emphasis": 0.75,
                                         "neutral_steps": 6},
                               "profile": {"quality": "u", "source_gamut": "AdobeRGB", "viewcond_out": "pc"}},
                              preconditioned=True)
    c = ps.compose_one_pass(p)
    assert c["targen_flags"] == "-Q -g 12 -N 0.75 -n 6 -f 836"
    assert c["colprof_flags"] == "-v -qu -S AdobeRGB -c mt -d pc"
    assert c["source_gamut"]["file"] == "ClayRGB-elle-V2-g22.icc"
    for bad, code in (({"chart": {"patches": 10}}, "param_out_of_range"),
                      ({"chart": {"ofps": "R"}}, "invalid_param"),
                      ({"profile": {"smoothing": 1.2}}, "unknown_param"),    # not a final-pass setting
                      ({"profile": {"fwa": True}}, "unknown_param"),         # decided at the build
                      ({"chart": {"extra": "-c x.icc"}}, "unknown_param"),
                      ({"pass1": {}}, "unknown_param")):
        with pytest.raises(ps.StrategyError) as e:
            ps.normalize_one_pass(bad, preconditioned=True)
        assert e.value.code == code


@pytest.mark.parametrize("table", [
    '[one_pass]\nchart = { patches = 200, ofps = "G", grey_steps = 0, neutral_emphasis = 0.5 }\n'
    'profile = { quality = "h", source_gamut = "none", viewcond_in = "mt", viewcond_out = "pp" }\n',
    '[one_pass]\nchart = { patches = 20, ofps = "G", grey_steps = 0, neutral_emphasis = 0.5, neutral_steps = 0 }\n'
    'profile = { quality = "h", source_gamut = "none", viewcond_in = "mt", viewcond_out = "pp" }\n',
])
def test_a_malformed_one_pass_table_is_refused(tmp_path, table):
    p = tmp_path / "s.toml"
    p.write_text(table)
    with pytest.raises(ps.StrategyError) as e:
        ps.one_pass_defaults(p)
    assert e.value.code in ("invalid_definition", "param_out_of_range")


def test_without_the_table_only_the_free_line_is_offered(tmp_path, client):
    p = tmp_path / "s.toml"
    p.write_text("")
    assert ps.one_pass_defaults(p) is None
    with pytest.raises(ps.StrategyError) as e:
        ps.normalize_one_pass(preconditioned=True, path=p)
    assert e.value.code == "one_pass_unavailable"


# ── routes ────────────────────────────────────────────────────────────────────
def test_the_definitions_carry_the_guided_form(client):
    d = client.get("/api/profiling-strategies").json()
    assert d["one_pass"]["default_params"]["chart"]["patches"] == 200
    assert d["one_pass"]["composed"]["targen_flags"] == "-G -N 0.5 -f 200"


def test_compose_one_pass_says_what_the_server_will_run(client):
    r = client.post("/api/profiling-strategies/compose-one-pass",
                    json={"params": {"chart": {"patches": 400}, "profile": {"quality": "m"}}, "preconditioned": False})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["targen_flags"], d["colprof_flags"], d["patches"]) == ("-G -f 400", "-v -qm", 400)
    assert "neutral_emphasis" not in d["params"]["chart"]
    r = client.post("/api/profiling-strategies/compose-one-pass", json={"params": {"chart": {"patches": 9}}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "param_out_of_range"


def test_a_guided_chart_runs_the_composed_line_and_records_its_profile_line(client, targen):
    r = _guided(client, {"chart": {"patches": 120, "neutral_emphasis": 0.75},
                         "profile": {"quality": "u", "source_gamut": "AdobeRGB"}})
    assert r.status_code == 200, r.text
    created = r.json()
    call = targen.calls[-1]
    assert call["flags"] == "-G -N 0.75 -f 120" and call["precond_bytes"]            # the resident -c
    assert call["argv"][call["argv"].index("-d2")] == "-d2"
    block = _desc(created["chart_id"])["guided"]
    assert block["lines"] == {"targen_flags": "-G -N 0.75 -f 120", "colprof_flags": "-v -qu -S AdobeRGB -c mt -d pp"}
    assert block["preconditioned"] is True and "strategy" not in _desc(created["chart_id"])
    assert created["guided"]["lines"] == block["lines"] and created["guided"]["targen_argv"] == call["argv"]
    assert "strategy" not in created
    # the Measurements detail and the print summary say it the same way
    ident = client.get(f"/api/charts/{created['chart_id']}").json()["identity"]
    assert ident["guided"]["lines"]["colprof_flags"] == "-v -qu -S AdobeRGB -c mt -d pp"
    summary = client.get(f"/api/charts/{created['chart_id']}/print-summary").json()
    assert summary["guided"] == created["guided"]


def test_a_guided_chart_from_scratch_leaves_the_neutral_settings_out(client, targen):
    r = _guided(client, {"chart": {"patches": 150, "neutral_emphasis": 1.0, "neutral_steps": 4}}, c_profile="none")
    assert r.status_code == 200, r.text
    assert targen.calls[-1]["flags"] == "-G -f 150" and targen.calls[-1]["precond_bytes"] is None
    g = _desc(r.json()["chart_id"])["guided"]
    assert g["preconditioned"] is False and "neutral_emphasis" not in g["params"]["chart"]


@pytest.mark.parametrize("extra, code", [
    ({"targen_flags": "-d2 -G -f 100"}, "guided_composes_the_chart"),
    ({"ti1_text": _ti1_text(60)}, "guided_composes_the_chart"),
    ({"purpose": "validation"}, "guided_not_for_validation"),
    ({"strategy": {"id": "two_pass_adaptive", "pass_id": "pass1"}}, "guided_is_not_a_strategy"),
])
def test_a_guided_chart_is_never_mixed_with_another_source(client, targen, extra, code):
    r = _guided(client, c_profile="none" if "strategy" in extra else RESIDENT, **extra)
    assert r.status_code == 422 and r.json()["detail"]["code"] == code, r.text
    assert targen.calls == []


def test_a_refused_parameter_costs_nothing(client, targen):
    r = _guided(client, {"chart": {"patches": 6000}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "param_out_of_range"
    assert targen.calls == []


def test_targen_failing_on_the_guided_line_offers_quasi_random_never_applies_it(client, targen):
    targen.state["fail"] = True
    r = _guided(client)
    d = r.json()["detail"]
    assert r.status_code == 422 and d["code"] == "targen_failed" and d["fallback"] == "Q"
    assert d["targen_flags"] == "-G -N 0.5 -f 200" and len(targen.calls) == 1
    r = _guided(client, {"chart": {"ofps": "Q"}})
    assert r.json()["detail"]["fallback"] is None


def test_a_free_line_chart_is_not_guided(client, targen):
    r = client.post("/api/charts", json={**BASE, "targen_flags": "-d2 -G -f 100", "c_profile": "none"})
    assert r.status_code == 200, r.text
    assert "guided" not in r.json()
    assert client.get(f"/api/charts/{r.json()['chart_id']}").json()["identity"]["guided"] is None
