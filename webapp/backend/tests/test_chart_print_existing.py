"""Printing a chart that already exists (never printed, or printed again): its summary has the
creation response's shape, so the wizard shows the same preview, checks and confirmation; a
pass of a two-pass profiling created but never printed is no longer a dead end; a new print
keeps the previous ones (and the delays of the measurements made after them); a later pass
printed again is checked against the first pass like its first print."""
import functools
import json
from datetime import datetime

import pytest

from lib.z9_client import cache
from lib.z9_client import printtarg_chart as P
from lib.z9_client.argyll import find_argyll_binary
from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from webapp.backend.tests.test_charts_routes import MID, _fake_z9, _ti1_text
from webapp.backend.tests.test_printtarg_routes import _body as _pt_body
from webapp.backend.tests.test_profiling_strategies import (  # noqa: F401  (fixtures)
    _create, _desc, _measure, _pass1, _ready_for_pass2, client, sent, targen)

ONE_PASS = {"media_key": "a3", "paper_mediaid": MID, "gloss_enhancer": "OFF", "paper_name": "Baryta test"}


def _summary(client, cid):
    r = client.get(f"/api/charts/{cid}/print-summary")
    assert r.status_code == 200, r.text
    return r.json()


def _same_as_creation(created, summary):
    for key, value in created.items():
        if key == "warnings":                          # request-level notes of the creation only
            continue
        assert summary[key] == value, key


def test_summary_of_a_new_chart_is_the_creation_response(client, targen):
    created = client.post("/api/charts", json={**ONE_PASS, "ti1_text": _ti1_text(60)}).json()
    s = _summary(client, created["chart_id"])
    _same_as_creation(created, s)
    assert s["media_key"] == "a3"
    assert s["existing"] == {"created_at": _desc(created["chart_id"])["created_at"], "printed_at": None,
                             "n_prints": 0, "n_scans": 0, "printable": True, "not_printable": None}


def test_a_chart_printed_again_keeps_its_previous_prints(client, targen, sent):
    cid = client.post("/api/charts", json={**ONE_PASS, "ti1_text": _ti1_text(60)}).json()["chart_id"]
    assert client.post(f"/api/charts/{cid}/print", json={"quality": "HIGH"}).status_code == 200
    first = _desc(cid)["print_params"]
    _measure(cid, name="20261125_100000.ti3")
    assert client.post(f"/api/charts/{cid}/print", json={"quality": "HIGH"}).status_code == 200
    desc = _desc(cid)
    assert len(sent) == 2 and desc["print_history"] == [first]
    assert desc["print_params"]["printed_at"] == desc["printed_at"]
    s = _summary(client, cid)["existing"]
    assert (s["n_prints"], s["n_scans"], s["printable"]) == (2, 1, True)
    # a measurement made after the first print counts its delay from it, not from the reprint
    desc["print_history"][0]["printed_at"] = "2026-11-25T09:00:00"
    desc["printed_at"] = desc["print_params"]["printed_at"] = "2026-11-25T11:00:00"
    (cache.locate_chart_dir(cid) / "chart.json").write_text(json.dumps(desc))
    scan = client.get(f"/api/charts/{cid}").json()["scans"][0]
    assert scan["delay_seconds"] == 3600


def test_a_pass_created_but_never_printed_is_printed_from_its_summary(client, targen, sent):
    cid = _pass1(client)
    runs = client.get(f"/api/profiling-strategies/runs?paper={MID}&ge=OFF").json()["runs"]
    assert runs[0]["next_step"] == "pass1_print"
    s = _summary(client, cid)
    assert s["strategy"]["pass"] == "pass1" and s["strategy"]["run"] == cid
    assert s["existing"]["printed_at"] is None and s["existing"]["printable"] is True
    assert client.post(f"/api/charts/{cid}/print", json={"quality": "HIGH"}).status_code == 200
    runs = client.get(f"/api/profiling-strategies/runs?paper={MID}&ge=OFF").json()["runs"]
    assert runs[0]["next_step"] == "pass1_measure"


def test_a_later_pass_printed_again_is_checked_against_the_first_pass(client, targen, sent, monkeypatch):
    p1, bid = _ready_for_pass2(client, monkeypatch)
    assert client.post(f"/api/charts/{p1}/print", json={"quality": "HIGH"}).status_code == 200
    p2 = _create(client, "pass2", media="a4", run=p1, build_id=bid).json()["chart_id"]
    assert client.post(f"/api/charts/{p2}/print", json={"quality": "HIGH"}).status_code == 200
    s = _summary(client, p2)
    assert s["strategy"]["pass"] == "pass2" and s["strategy"]["precondition"]["build_id"] == bid
    n, before = len(sent), _desc(p2)
    r = client.post(f"/api/charts/{p2}/print", json={"quality": "NORMAL"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "strategy_print_conditions_changed"
    assert len(sent) == n and _desc(p2) == before                       # nothing sent, nothing recorded
    assert client.post(f"/api/charts/{p2}/print", json={"quality": "HIGH"}).status_code == 200
    assert len(_desc(p2)["print_history"]) == 1


def test_a_lightened_chart_or_a_missing_image_is_not_printable(client, targen):
    cid = client.post("/api/charts", json={**ONE_PASS, "ti1_text": _ti1_text(60)}).json()["chart_id"]
    tiff = cache.locate_chart_dir(cid) / _desc(cid)["files"]["tiff"]
    tiff.rename(tiff.with_suffix(".bak"))
    assert _summary(client, cid)["existing"]["not_printable"] == "tiff_missing"
    tiff.with_suffix(".bak").rename(tiff)
    assert client.post(f"/api/charts/{cid}/lighten").status_code == 200
    s = _summary(client, cid)["existing"]
    assert (s["printable"], s["not_printable"]) == (False, "lightened")


def test_summary_of_an_unknown_chart_is_404(client):
    assert client.get("/api/charts/CHT-20261125-0000-ZZ/print-summary").status_code == 404


@pytest.mark.skipif(not (find_argyll_binary("printtarg") and find_argyll_binary("targen")),
                    reason="printtarg/targen (Argyll) not installed")
def test_summary_of_a_real_printtarg_chart_is_the_creation_response(client, monkeypatch):
    monkeypatch.setattr(charts_routes._ptc, "orchestrate_printtarg_chart",
                        functools.partial(P.orchestrate_printtarg_chart, dpi=100))
    app.dependency_overrides[charts_routes.get_chart_z9] = lambda: _fake_z9()
    created = client.post("/api/charts", json=_pt_body(ti1_text=None, targen_flags="-d2 -G -f 120",
                                                       instrument="colormunki", paper_name="Baryta")).json()
    s = _summary(client, created["chart_id"])
    _same_as_creation(created, s)
    assert s["existing"]["printable"] is True
    assert datetime.fromisoformat(s["existing"]["created_at"])
