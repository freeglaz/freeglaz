"""The general -c menu also offers the profiles freeglaz built for the same paper and gloss
enhancer (note 129 §7.2): straight from their build to targen -c, never through the printer.
Copies of other profiles, other papers or GE, and intermediate profiles are not offered."""
import json
from pathlib import Path

from lib.z9_client import cache
from webapp.backend.tests.test_charts_routes import MID, _ti1_text
from webapp.backend.tests.test_profiling_strategies import (  # noqa: F401  (fixtures)
    _build_intermediate, _desc, _icc_build, _measure, _pass1, client, targen)


def _one_pass_chart_with_profile(client, monkeypatch, name="Baryta_one_pass"):
    _icc_build(monkeypatch)
    r = client.post("/api/charts", json={"media_key": "a3", "paper_mediaid": MID, "gloss_enhancer": "OFF",
                                         "paper_name": "Baryta test", "ti1_text": _ti1_text(60)})
    cid = r.json()["chart_id"]
    _measure(cid)
    res = client.post(f"/api/charts/{cid}/profile", json={"name": name}).json()
    assert res["installable"] is True
    return cid, res["ranged_icc_path"]


def _menu(client, ge="OFF"):
    return client.get(f"/api/charts/precondition-profiles?paper={MID}&print_ge={ge}").json()["profiles"]


def test_built_profiles_are_offered_for_the_same_paper_and_ge(client, targen, monkeypatch):
    cid, path = _one_pass_chart_with_profile(client, monkeypatch)
    built = [p for p in _menu(client) if p["kind"] == "freeglaz"]
    assert [p["path"] for p in built] == [path]
    assert built[0]["id"] == f"path:{path}" and built[0]["chart_id"] == cid and built[0]["name"] == "Baryta_one_pass"
    kinds = [p["kind"] for p in _menu(client)]
    assert kinds[0] == "resident" and kinds[-1] == "none"            # residents first, "none" last (unchanged)
    assert not [p for p in _menu(client, ge="FULLPAGE") if p["kind"] == "freeglaz"]   # another GE: not offered


def test_copies_and_intermediate_profiles_are_not_offered(client, targen, monkeypatch):
    _, path = _one_pass_chart_with_profile(client, monkeypatch)
    meta_file = cache._repo_z9_meta_path(Path(path))
    meta = json.loads(meta_file.read_text())
    meta["origin"] = "mirror_copy"                                   # a copy of an HP profile
    meta_file.write_text(json.dumps(meta))
    assert not [p for p in _menu(client) if p["kind"] == "freeglaz"]
    p1 = _pass1(client)                                              # a two-pass run: its intermediate
    _measure(p1)
    _build_intermediate(client, p1)
    assert not [p for p in _menu(client) if p["kind"] == "freeglaz"]


def test_a_built_profile_preconditions_a_one_pass_chart(client, targen, monkeypatch):
    _, path = _one_pass_chart_with_profile(client, monkeypatch)
    r = client.post("/api/charts", json={"media_key": "a3", "paper_mediaid": MID, "gloss_enhancer": "OFF",
                                         "targen_flags": "-d2 -G -N 0.75 -f 80", "c_profile": f"path:{path}"})
    assert r.status_code == 200, r.text
    assert targen.calls[-1]["argv"][-3:-1] == ["-c", path]
    assert targen.calls[-1]["precond_bytes"] == Path(path).read_bytes()
    assert "strategy" not in _desc(r.json()["chart_id"])             # the one-pass path, unchanged
