"""Phase 4C: explicit exclusions, structural guards, coverage of the effective set, preview,
policy apply_all (explicit + fingerprint), reversibility and the new profile status.
colprof is faked (deterministic: the ICC is a hash of the ti3 it receives)."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import build_inputs as bi
from lib.z9_client import profile_versions as pv
from webapp.backend.main import app
from webapp.backend.tests.test_build_inputs import _chart, _fake_colprof, grid_rows, make_chart, ti3_text, tree_digest

G5 = grid_rows(5)                                       # 125 points, levels 0/25/50/75/100


def sid_of(rows, rgb):
    return next(s for s, r, g, b in rows if (r, g, b) == tuple(float(x) for x in rgb))


def plan_cov(tmp_path, scans, rejected=None, base=None, policy="apply_all", extras=(), roles=None, name="CHT-A"):
    snap = make_chart(tmp_path, name, scans, roles=roles, rejected=rejected)
    plan = bi.plan_inputs(snap, list(extras), bi.BuildRequest(base=base, policy=policy))
    return plan, bi.analyze_coverage(plan)


# ── coverage & guards (pure) ────────────────────────────────────────────────
@pytest.mark.parametrize("rgb,position,level", [
    ((50, 50, 50), "interior", "info"), ((100, 50, 50), "face", "info"),
    ((100, 50, 0), "edge", "warning"), ((100, 0, 0), "corner", "strong_warning"),
    ((100, 100, 0), "corner", "strong_warning")])
def test_single_scan_exclusion_levels(tmp_path, rgb, position, level):
    plan, cov = plan_cov(tmp_path, {"a.ti3": ti3_text(G5)}, rejected={"a.ti3": [sid_of(G5, rgb)]})
    assert plan.current[0].rejections_applied == (sid_of(G5, rgb),)
    (h,) = cov["holes"]
    assert h["position"] == position and h["rgb"] == [float(x) for x in rgb]
    assert [g["level"] for g in cov["guards"]] == [level] and cov["refusals"] == []
    assert cov["design_points"] == 125 and cov["effective_points"] == 124


@pytest.mark.parametrize("name,rgb", [("WHITE", (100, 100, 100)), ("BLACK", (0, 0, 0))])
def test_unique_white_or_black_is_refused(tmp_path, name, rgb):
    _, cov = plan_cov(tmp_path, {"a.ti3": ti3_text(G5)}, rejected={"a.ti3": [sid_of(G5, rgb)]})
    assert cov["refusals"] == [f"{name}_LAST_READING"] and "STRUCTURAL_POINT_LOST" in cov["reasons"]


def test_duplicated_white_can_lose_one_reading(tmp_path):
    rows = G5 + [("W2", 100.0, 100.0, 100.0)]          # a second white, other SAMPLE_ID
    _, cov = plan_cov(tmp_path, {"a.ti3": ti3_text(rows)}, rejected={"a.ti3": [sid_of(G5, (100, 100, 100))]})
    assert cov["refusals"] == [] and cov["holes"] == [] and cov["white"]["compatible_readings"] == 1


def test_white_from_another_chart_is_not_a_compatible_replacement(tmp_path):
    x = make_chart(tmp_path, "CHT-X", {"x.ti3": ti3_text(G5)})
    _, cov = plan_cov(tmp_path, {"a.ti3": ti3_text(G5)}, rejected={"a.ti3": [sid_of(G5, (100, 100, 100))]},
                      extras=[x])
    assert cov["refusals"] == ["WHITE_LAST_READING"] and cov["white"]["readings_other_sources"] == 1
    assert cov["holes"] == [] and cov["source_profiles_counted"] is False


def test_last_vs_average_with_white_rejected_on_the_last_scan(tmp_path):
    scans = {"a.ti3": ti3_text(G5), "b.ti3": ti3_text(G5)}
    rej = {"b.ti3": [sid_of(G5, (100, 100, 100))]}
    _, last = plan_cov(tmp_path, scans, rejected=rej, base="last")
    _, avg = plan_cov(tmp_path, scans, rejected=rej, base="average", name="CHT-B")
    _, legacy = plan_cov(tmp_path, scans, rejected=rej, base="last", policy="legacy", name="CHT-C")
    assert last["refusals"] == ["WHITE_LAST_READING"]                  # older scan not used by last
    assert avg["refusals"] == [] and avg["white"]["compatible_readings"] == 1
    assert legacy["refusals"] == [] and legacy["holes"] == []          # legacy: not applied → nothing lost


def test_corner_edge_cluster_and_gap_indicators(tmp_path):
    corners = [sid_of(G5, (100, 0, 0)), sid_of(G5, (0, 100, 0))]
    edge = [sid_of(G5, (100, 25, 0)), sid_of(G5, (100, 50, 0))]               # adjacent on an edge
    cluster = [sid_of(G5, rgb) for rgb in ((50, 50, 50), (50, 50, 75), (50, 75, 50), (75, 50, 50))]
    _, cov = plan_cov(tmp_path, {"a.ti3": ti3_text(G5)}, rejected={"a.ti3": corners + edge + cluster})
    assert sorted(cov["missing_corners"]) == ["green", "red"]
    assert {"CORNERS_MISSING", "EDGE_COVERAGE", "HOLE_CLUSTER", "TOO_MANY_HOLES"} <= set(cov["reasons"])
    assert cov["recommend_remeasure"] and cov["refusals"] == []             # warnings only
    assert cov["indicators"]["nominal_spacing"] == 25.0
    assert any(len(c) >= 4 for c in cov["indicators"]["clusters"])


def test_qc_findings_turn_into_warnings(tmp_path):
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(G5)}, rejected={"a.ti3": ["1"]})
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(policy="apply_all"))
    cov = bi.analyze_coverage(plan, {("CHT-A", "a.ti3"): ["SET_INCOHERENT", "ZONE_INCOHERENT"]})
    assert {"QC_SET_INCOHERENT_ISOLATED_EXCLUSIONS", "QC_ZONE_INCOHERENT"} <= set(cov["reasons"])


# ── routes ──────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


def excl(client, cid, scan, sid, **body):
    return client.post(f"/api/charts/{cid}/scans/{scan}/readings/{sid}/exclusion", json=body)


def state(d):
    return json.loads((d / "scan_state.json").read_text()) if (d / "scan_state.json").exists() else {}


def test_exclusion_route_dry_run_write_and_reintegration(client):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    sid = sid_of(G5, (100, 50, 0))
    before = tree_digest(d)
    r = excl(client, cid, "a.ti3", sid, dry_run=True)
    assert r.status_code == 200 and r.json()["applied"] is False and tree_digest(d) == before
    imp = r.json()["impact"]
    assert imp["reading"]["position"] == "edge" and imp["level"] == "warning"
    assert imp["bases"]["last"] == {**imp["bases"]["last"], "reading_used": True, "hole": True}
    assert "average" not in imp["bases"]                                    # one scan only
    r = excl(client, cid, "a.ti3", sid, reason="smudge seen on the print")
    assert r.status_code == 200 and r.json()["applied"] is True
    st = state(d)
    assert st["rejected_readings"] == {"a.ti3": [sid]}
    assert st["rejection_meta"]["a.ti3"][sid]["reason"] == "smudge seen on the print"
    assert st["rejection_meta"]["a.ti3"][sid]["api"] == "exclusion/v1"
    assert excl(client, cid, "a.ti3", sid, excluded=False).status_code == 200
    st = state(d)
    assert st["rejected_readings"] == {} and st.get("rejection_meta", {}) == {}   # meta follows the registry


def test_exclusion_route_refuses_the_unique_white_and_accepts_a_duplicate(client):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    w = sid_of(G5, (100, 100, 100))
    r = excl(client, cid, "a.ti3", w)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "WHITE_LAST_READING"
    assert state(d).get("rejected_readings", {}) == {}                      # nothing written
    cid2, d2 = _chart(client, {"a.ti3": ti3_text(G5 + [("W2", 100.0, 100.0, 100.0)])})
    r = excl(client, cid2, "a.ti3", w)
    assert r.status_code == 200 and r.json()["impact"]["other_compatible_readings_in_chart"] == 1
    assert excl(client, cid2, "a.ti3", "W2").status_code == 409             # now the last white


def test_legacy_reject_route_is_unchanged(client):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    r = client.post(f"/api/charts/{cid}/scans/a.ti3/readings/{sid_of(G5, (50, 50, 50))}/reject", json={"rejected": True})
    assert r.status_code == 409 and "last valid reading" in r.text           # the old rule still applies


def test_preview_writes_nothing_and_matches_the_build(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    sid = sid_of(G5, (50, 50, 50))
    excl(client, cid, "a.ti3", sid)
    before = tree_digest(d)
    p = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all"}).json()
    assert tree_digest(d) == before and not (d / ".build").exists()
    assert p["policy"] == "apply_all" and p["rejections_applied"] == [{"chart_id": cid, "scan": "a.ti3", "sids": [sid]}]
    assert [h["rgb"] for h in p["holes"]] == [[50.0, 50.0, 50.0]] and p["would_be_refused"] is False
    legacy = client.post(f"/api/charts/{cid}/profile/preview", json={}).json()
    assert legacy["rejections_applied"] == [] and legacy["rejections_not_applied"][0]["reason"] == bi.REASON_LEGACY_LAST
    assert legacy["inputs_fingerprint"] != p["inputs_fingerprint"]
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "expected_fingerprint": p["inputs_fingerprint"]})
    assert r.status_code == 200, r.text
    m = pv.read_version_manifest(d, r.json()["build_id"])
    assert m["plan"]["inputs_fingerprint"] == p["inputs_fingerprint"] and m["request"]["policy"] == "apply_all"
    assert m["coverage"]["holes"][0]["rgb"] == [50.0, 50.0, 50.0]
    sids_fed = {ln.split()[0] for ln in fed["ti3_bytes"].decode().split("BEGIN_DATA\n")[1].splitlines()[:-1]}
    assert sid not in sids_fed and len(sids_fed) == 124


def test_apply_all_requires_the_previewed_fingerprint(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    _fake_colprof(monkeypatch)
    assert client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all"}).status_code == 422
    assert client.post(f"/api/charts/{cid}/profile", json={"policy": "sometimes"}).status_code == 422
    fp = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all"}).json()["inputs_fingerprint"]
    excl(client, cid, "a.ti3", sid_of(G5, (25, 25, 25)))                    # data changed after the preview
    r = client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "expected_fingerprint": fp})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "inputs_changed"
    assert not (d / "builds").exists()


def test_apply_all_build_is_refused_when_white_is_lost_on_last(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5), "b.ti3": ti3_text(G5)})
    w = sid_of(G5, (100, 100, 100))
    assert excl(client, cid, "b.ti3", w).json()["impact"]["bases"]["last"]["level"] == "refused"   # warned
    _fake_colprof(monkeypatch)
    p = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all"}).json()
    assert p["would_be_refused"] and p["refusals"] == ["WHITE_LAST_READING"]
    r = client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "expected_fingerprint": p["inputs_fingerprint"]})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "coverage_refused"
    pa = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all", "profile_base": "average"}).json()
    assert pa["refusals"] == []                                             # the older scan keeps a white
    assert client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "profile_base": "average",
                                                           "expected_fingerprint": pa["inputs_fingerprint"]}).status_code == 200


def test_historical_calls_are_unchanged_by_new_exclusions(client, monkeypatch):
    raw = ti3_text(G5)
    cid, d = _chart(client, {"a.ti3": raw})
    excl(client, cid, "a.ti3", sid_of(G5, (50, 50, 50)))                    # made with the new API
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={})                  # old interface call
    assert r.status_code == 200 and fed["ti3_bytes"] == raw.encode()        # rejection NOT applied (legacy)
    m = pv.read_version_manifest(d, r.json()["build_id"])
    assert m["plan"]["policy"] == "legacy" and m["plan"]["rejections_not_applied"][0]["reason"] == bi.REASON_LEGACY_LAST


def test_reversibility_a_b_c(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    _fake_colprof(monkeypatch)

    def build(name):
        fp = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all"}).json()["inputs_fingerprint"]
        r = client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "expected_fingerprint": fp, "name": name})
        assert r.status_code == 200, r.text
        return r.json()["build_id"], (d / f"{cid}.icc").read_bytes(), fp

    a_id, a, fa = build("prof-a")
    sid = sid_of(G5, (75, 25, 50))
    excl(client, cid, "a.ti3", sid)
    b_id, b, fb = build("prof-b")
    excl(client, cid, "a.ti3", sid, excluded=False)
    c_id, c, fc = build("prof-c")
    assert c == a and b != a and fc == fa != fb                             # back to the initial profile
    vs = {v["build_id"]: v for v in pv.list_versions(d)}
    assert {a_id, b_id, c_id} <= set(vs) and all(vs[x]["intact"] for x in (a_id, b_id, c_id))
    assert (d / "builds" / a_id / "profile.icc").read_bytes() == a and (d / "builds" / b_id / "profile.icc").read_bytes() == b


def test_profile_status_new_semantics(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5), "b.ti3": ti3_text(G5)})
    _fake_colprof(monkeypatch)
    status = lambda: client.get(f"/api/charts/{cid}").json()["profile"]["status"]  # noqa: E731
    (d / f"{cid}.icc").write_bytes(b"OLD PROFILE")
    assert status()["state"] == "unknown"                                   # no reliable manifest
    fp = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all"}).json()["inputs_fingerprint"]
    assert client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "expected_fingerprint": fp}).status_code == 200
    assert status()["state"] == "up_to_date"
    excl(client, cid, "a.ti3", "1")                                         # scan NOT used by last
    assert status()["state"] == "up_to_date"
    excl(client, cid, "b.ti3", "2")                                         # used reading excluded
    assert status()["state"] == "stale"
    excl(client, cid, "b.ti3", "2", excluded=False)
    assert status()["state"] == "up_to_date"
    monkeypatch.setattr(bi, "software_versions", lambda: {"colprof": "9.9.9"})
    assert status()["toolchain_changed"] is True
    (d / "measurements" / "b.ti3").unlink()
    s = status()
    assert s["state"] == "missing_input" and s["missing_input"][0]["scan"] == "b.ti3"


def test_multichart_apply_all_independent_exclusions(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5)})
    xid, xd = _chart(client, {"x.ti3": ti3_text(G5)})
    excl(client, cid, "a.ti3", "10")
    excl(client, xid, "x.ti3", "10")
    excl(client, xid, "x.ti3", "20")
    fed = _fake_colprof(monkeypatch)
    p = client.post(f"/api/charts/{cid}/profile/preview", json={"policy": "apply_all", "extra_chart_ids": [xid]}).json()
    r = client.post(f"/api/charts/{cid}/profile", json={"policy": "apply_all", "extra_chart_ids": [xid],
                                                         "expected_fingerprint": p["inputs_fingerprint"]})
    assert r.status_code == 200, r.text
    m = pv.read_version_manifest(d, r.json()["build_id"])
    prov = bi.reconstruct_provenance(m, lambda c, s: (d if c == cid else xd) / "measurements" / s)
    assert len(prov) == 124 + 123 and [p_["out_sid"] for p_ in prov] == [str(i) for i in range(1, 248)]
    assert ("10" not in {p_["sample_id"] for p_ in prov if p_["chart_id"] == cid})
    assert {"10", "20"}.isdisjoint({p_["sample_id"] for p_ in prov if p_["chart_id"] == xid})
    # SID 10 removed in BOTH charts → one hole (both origins listed); SID 20 only in x → covered by a
    (hole,) = m["coverage"]["holes"]
    assert sorted((e["chart_id"], e["sid"]) for e in hole["excluded_from"]) == sorted([(cid, "10"), (xid, "10")])


def test_scan_state_keys_survive_role_changes(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(G5), "b.ti3": ti3_text(G5)})
    _fake_colprof(monkeypatch)
    bid = client.post(f"/api/charts/{cid}/profile", json={}).json()["build_id"]
    excl(client, cid, "a.ti3", "5", reason="r")
    client.post(f"/api/charts/{cid}/scans/b.ti3/role", json={"role": "excluded"})
    st = state(d)
    assert st["last_build_id"] == bid and st["rejection_meta"]["a.ti3"]["5"]["reason"] == "r"
