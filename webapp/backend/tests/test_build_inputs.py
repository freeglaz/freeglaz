"""Unified preparation of the colprof inputs (lib/z9_client/build_inputs.py) and its use by
the profile build route, policy "legacy" (phase 4B: no change of result).

Unit tests (plan / materialize / provenance) use small synthetic ti3; route tests fake
colprof (no Argyll needed) and check what it RECEIVES, what is published and what is left on
disk. Real-colprof equivalence old/new pipeline: offline bench, outside the repository."""
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import build_inputs as bi
from lib.z9_client import cache
from lib.z9_client.multipass import concat_ti3, parse_ti3
from webapp.backend.main import app
from webapp.backend.tests.test_charts_routes import SERIAL, _create_a3


# ── synthetic ti3 ───────────────────────────────────────────────────────────
def ti3_text(rows, extra_cols=True):
    """rows = [(sid, r, g, b)] → ti3 with RGB, XYZ and 3 spectral bands (values derived from rgb)."""
    fmt = "SAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z" + (" SPEC_400 SPEC_550 SPEC_700" if extra_cols else "")
    lines = []
    for sid, r, g, b in rows:
        x, y, z = 5 + r * 0.4, 5 + g * 0.5, 5 + b * 0.6
        lines.append(f"{sid} {r:.4f} {g:.4f} {b:.4f} {x:.5f} {y:.5f} {z:.5f}"
                     + (f" {0.1 + b / 200:.5f} {0.1 + g / 200:.5f} {0.1 + r / 200:.5f}" if extra_cols else ""))
    head = ['CTI3', '', 'DESCRIPTOR "synthetic"', 'ORIGINATOR "test"', 'KEYWORD "SPECTRAL_BANDS"',
            'SPECTRAL_BANDS "3"', 'SPECTRAL_START_NM "400.0"', 'SPECTRAL_END_NM "700.0"',
            f"NUMBER_OF_FIELDS {len(fmt.split())}", "BEGIN_DATA_FORMAT", fmt, "END_DATA_FORMAT",
            f"NUMBER_OF_SETS {len(rows)}", "BEGIN_DATA", *lines, "END_DATA"]
    return "\n".join(head) + "\n"


def grid_rows(n=3, sid=lambda i: str(i + 1)):
    vals = [round(100 * k / (n - 1), 4) for k in range(n)]
    return [(sid(i), r, g, b) for i, (r, g, b) in enumerate((r, g, b) for r in vals for g in vals for b in vals)]


def make_chart(root, chart_id, scans, roles=None, rejected=None):
    """scans = {name: text}. Returns a ChartSnapshot of a chart folder under root."""
    d = Path(root) / chart_id
    (d / "measurements").mkdir(parents=True, exist_ok=True)
    for name, text in scans.items():
        (d / "measurements" / name).write_text(text, encoding="ascii")
    return bi.ChartSnapshot(chart_id=chart_id, chart_dir=d, raw_scans=tuple(sorted(scans)),
                            roles=dict(roles or {}), rejected={k: tuple(v) for k, v in (rejected or {}).items()})


def tree_digest(root):
    root = Path(root)
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def data_rows(path):
    _, fmt, rows = parse_ti3(Path(path).read_text(encoding="ascii", errors="replace"))   # concat headers carry a UTF-8 dash
    return fmt, rows


# ── plan_inputs ─────────────────────────────────────────────────────────────
def test_last_uses_the_last_included_scan_and_legacy_ignores_its_rejections(tmp_path):
    snap = make_chart(tmp_path, "CHT-A", {"20260101_100000.ti3": ti3_text(grid_rows()),
                                         "20260101_110000.ti3": ti3_text(grid_rows()),
                                         "20260101_120000.ti3": ti3_text(grid_rows())},
                      roles={"20260101_120000.ti3": "excluded"},
                      rejected={"20260101_110000.ti3": ["5"], "20260101_100000.ti3": ["7"],
                                "20260101_120000.ti3": ["2"], "gone.ti3": ["1"]})
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(base=None))
    assert plan.base_effective == "last" and [s.scan for s in plan.current] == ["20260101_110000.ti3"]
    assert plan.current[0].rejections_applied == ()                           # legacy: ignored…
    reasons = {(n.scan, n.reason, n.sids) for n in plan.rejections_not_applied}
    assert reasons == {("20260101_110000.ti3", bi.REASON_LEGACY_LAST, ("5",)),   # …but TRACED
                       ("20260101_100000.ti3", bi.REASON_NOT_USED, ("7",)),
                       ("20260101_120000.ti3", bi.REASON_EXCLUDED, ("2",)),
                       ("gone.ti3", bi.REASON_NOT_FOUND, ("1",))}


def test_apply_all_policy_is_defined_and_applies_in_last(tmp_path):
    snap = make_chart(tmp_path, "CHT-A", {"s1.ti3": ti3_text(grid_rows())}, rejected={"s1.ti3": ["5"]})
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(policy="apply_all"))
    assert plan.current[0].rejections_applied == ("5",) and not plan.rejections_not_applied
    with pytest.raises(bi.BuildInputsError):
        bi.plan_inputs(snap, [], bi.BuildRequest(policy="auto_exclude"))


def test_average_applies_rejections_and_falls_back_to_last_below_two_scans(tmp_path):
    two = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())},
                     rejected={"a.ti3": ["3", "1"]})
    p = bi.plan_inputs(two, [], bi.BuildRequest(base="average"))
    assert p.base_requested == "average" and p.base_effective == "average"
    assert [(s.scan, s.rejections_applied) for s in p.current] == [("a.ti3", ("1", "3")), ("b.ti3", ())]
    one = make_chart(tmp_path, "CHT-B", {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())},
                     roles={"b.ti3": "excluded"}, rejected={"a.ti3": ["3"]})
    p1 = bi.plan_inputs(one, [], bi.BuildRequest(base="average"))
    assert p1.base_requested == "average" and p1.base_effective == "last"     # traced fallback
    assert p1.current[0].rejections_applied == ()
    assert [n.reason for n in p1.rejections_not_applied] == [bi.REASON_LEGACY_LAST]


def test_phantom_and_non_numeric_sample_ids(tmp_path):
    rows = grid_rows(2, sid=lambda i: f"R{i // 4}C{i % 4}")                  # R0C0 … R1C3
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(rows), "b.ti3": ti3_text(rows)},
                      rejected={"a.ti3": ["R0C2", "R9C9", "C0"]})
    plan = bi.plan_inputs(snap, [], bi.BuildRequest(base="average"))
    assert plan.current[0].rejections_applied == ("R0C2",)
    assert [(n.scan, n.sids, n.reason) for n in plan.rejections_unmatched] == [("a.ti3", ("C0", "R9C9"), "unmatched")]
    eff = bi.materialize(plan, tmp_path / "w")
    fmt, out = data_rows(tmp_path / "w" / "charts" / "CHT-A" / "a_qcfilt.ti3")
    assert [r[0] for r in out] == [r[0] for r in rows if r[0] != "R0C2"]
    assert eff.n_rows == 2 * len(rows) - 1


def test_plan_is_deterministic_and_never_writes(tmp_path):
    snap = make_chart(tmp_path / "store", "CHT-A", {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())},
                      rejected={"a.ti3": ["4"]})
    before = tree_digest(tmp_path / "store")
    p1 = bi.plan_inputs(snap, [], bi.BuildRequest(base="average", colprof_flags=("-v", "-qh")))
    p2 = bi.plan_inputs(snap, [], bi.BuildRequest(base="average", colprof_flags=("-v", "-qh")))
    assert tree_digest(tmp_path / "store") == before
    assert p1.to_dict() == p2.to_dict() and p1.fingerprint == p2.fingerprint


def test_fingerprint_follows_the_inputs_only(tmp_path):
    files = {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())}
    base = bi.plan_inputs(make_chart(tmp_path, "CHT-A", files), [], bi.BuildRequest(base="last"))
    # a rejection on a scan NOT used by `last` does not change the inputs
    same = bi.plan_inputs(make_chart(tmp_path, "CHT-A", files, rejected={"a.ti3": ["1"]}), [], bi.BuildRequest())
    assert same.fingerprint == base.fingerprint
    # used data changed → different
    changed = dict(files, **{"b.ti3": ti3_text(grid_rows()).replace("5.00000", "5.10000", 1)})
    assert bi.plan_inputs(make_chart(tmp_path / "other", "CHT-A", changed), [], bi.BuildRequest()).fingerprint \
        != base.fingerprint
    # flags and the files they name are part of it
    icc = tmp_path / "src.icc"
    icc.write_bytes(b"one")
    f1 = bi.plan_inputs(make_chart(tmp_path, "CHT-A", files), [], bi.BuildRequest(colprof_flags=("-S", str(icc))))
    icc.write_bytes(b"two")
    f2 = bi.plan_inputs(make_chart(tmp_path, "CHT-A", files), [], bi.BuildRequest(colprof_flags=("-S", str(icc))))
    assert f1.fingerprint != f2.fingerprint and f1.flag_files[0][0] == str(icc)


def test_extra_chart_without_included_scan_is_refused(tmp_path):
    cur = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows())})
    x = make_chart(tmp_path, "CHT-X", {"x.ti3": ti3_text(grid_rows())}, roles={"x.ti3": "excluded"})
    with pytest.raises(bi.BuildInputsError, match="CHT-X: no included scan"):
        bi.plan_inputs(cur, [x], bi.BuildRequest())


# ── materialize ─────────────────────────────────────────────────────────────
def test_last_is_a_byte_copy_and_nothing_is_written_outside_the_build_dir(tmp_path):
    snap = make_chart(tmp_path / "store", "CHT-A", {"a.ti3": ti3_text(grid_rows())}, rejected={"a.ti3": ["2"]})
    before = tree_digest(tmp_path / "store")
    plan = bi.plan_inputs(snap, [], bi.BuildRequest())
    eff = bi.materialize(plan, tmp_path / "w")
    assert eff.bytes_passthrough and not eff.renumbered
    assert eff.profile_ti3.read_bytes() == (snap.chart_dir / "measurements" / "a.ti3").read_bytes()
    assert eff.profile_ti3.parent.parent.parent == tmp_path / "w"
    assert tree_digest(tmp_path / "store") == before                      # raw data untouched, nothing added


def test_average_is_the_historical_concat(tmp_path):
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())},
                      rejected={"b.ti3": ["9"]})
    eff = bi.materialize(bi.plan_inputs(snap, [], bi.BuildRequest(base="average")), tmp_path / "w")
    assert eff.profile_ti3.name == "CHT-A_avg.ti3" and eff.renumbered
    # same call as the historical pipeline, on the same inputs
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "a.ti3").write_bytes((snap.chart_dir / "measurements" / "a.ti3").read_bytes())
    from lib.z9_client.multipass import filter_ti3_text
    (ref / "b_qcfilt.ti3").write_text(filter_ti3_text((snap.chart_dir / "measurements" / "b.ti3").read_text(), {"9"}))
    concat_ti3([ref / "a.ti3", ref / "b_qcfilt.ti3"], ref / "avg.ti3", labels=["scan1", "scan2"],
               dedup_strategy="keep", descriptor="freeglaz average CHT-A")
    assert data_rows(eff.profile_ti3) == data_rows(ref / "avg.ti3")
    assert [e["out_sid_range"] for e in eff.layout] == [[1, 27], [28, 53]]


def test_multisource_provenance_with_identical_sample_ids(tmp_path):
    """Two charts with the SAME SAMPLE_IDs and RGB, independent rejections: every effective
    row maps back to the right (chart, scan, SAMPLE_ID), from the manifest alone."""
    rows = grid_rows()
    cur = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(rows)}, rejected={"a.ti3": ["1"]})   # ignored (last)
    xb = make_chart(tmp_path, "CHT-B", {"b1.ti3": ti3_text(rows).replace(" 5.00000", " 6.00000"),
                                        "b2.ti3": ti3_text(rows)}, rejected={"b1.ti3": ["2", "27"]})
    xc = make_chart(tmp_path, "CHT-C", {"c.ti3": ti3_text(rows)}, rejected={"c.ti3": ["14"]})
    plan = bi.plan_inputs(cur, [xb, xc], bi.BuildRequest())
    eff = bi.materialize(plan, tmp_path / "w")
    manifest = json.loads(json.dumps(bi.build_manifest(plan, eff, build_id="x")))   # JSON round trip
    prov = bi.reconstruct_provenance(manifest, lambda c, s: tmp_path / c / "measurements" / s)
    fmt, out = data_rows(eff.profile_ti3)
    assert [r[0] for r in out] == [p["out_sid"] for p in prov] == [str(i) for i in range(1, len(out) + 1)]
    assert len(out) == 27 + 25 + 27 + 26
    raw = {(c, s): {r[0]: r for r in data_rows(tmp_path / c / "measurements" / s)[1]}
           for c, s in [("CHT-A", "a.ti3"), ("CHT-B", "b1.ti3"), ("CHT-B", "b2.ti3"), ("CHT-C", "c.ti3")]}
    for row, p in zip(out, prov):
        assert row[1:] == raw[(p["chart_id"], p["scan"])][p["sample_id"]][1:]      # same reading, byte for byte
    excluded = {(p["chart_id"], p["sample_id"]) for p in prov if p["scan"] in ("b1.ti3", "c.ti3")}
    assert ("CHT-B", "2") not in excluded and ("CHT-C", "14") not in excluded
    assert ("CHT-A", "1") in {(p["chart_id"], p["sample_id"]) for p in prov}       # legacy last: kept


def test_source_profile_extraction_is_confined_and_traced(tmp_path):
    cur = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows())})
    icc = tmp_path / "repo" / "p.icc"
    icc.parent.mkdir()
    icc.write_bytes(b"ICC")
    seen = {}

    def fake_extract(icc_path, out, descriptor):
        seen.update(icc=icc_path, out=out, descriptor=descriptor)
        out.write_text(ti3_text(grid_rows(2)))

    plan = bi.plan_inputs(cur, [], bi.BuildRequest(source_profiles=(str(icc),)))
    eff = bi.materialize(plan, tmp_path / "w", extract_profile=fake_extract)
    assert seen["icc"].parent.parent.parent == tmp_path / "w" and seen["icc"].name == "p.icc"   # a frozen copy
    assert seen["descriptor"] == "freeglaz source p.icc" and seen["out"].name == "src_profile_0.ti3"
    assert eff.profile_ti3.name == "CHT-A_multisource.ti3"
    assert [(e["kind"], e["out_sid_range"]) for e in eff.layout] == [("scan", [1, 27]), ("profile", [28, 35])]
    assert sorted(p.name for p in icc.parent.iterdir()) == ["p.icc"]               # nothing next to the ICC

    def broken(icc_path, out, descriptor):
        raise ValueError("no CIED tag")
    with pytest.raises(bi.BuildInputsError, match="source profile p.icc: extraction failed"):
        bi.materialize(plan, tmp_path / "w2", extract_profile=broken)


def test_a_source_changed_after_planning_aborts(tmp_path):
    snap = make_chart(tmp_path, "CHT-A", {"a.ti3": ti3_text(grid_rows())})
    plan = bi.plan_inputs(snap, [], bi.BuildRequest())
    (snap.chart_dir / "measurements" / "a.ti3").write_text(ti3_text(grid_rows(2)))
    with pytest.raises(bi.SourceChangedError):
        bi.materialize(plan, tmp_path / "w")


# ── build directories ───────────────────────────────────────────────────────
def test_build_dirs_are_unique_and_only_build_dirs_are_ever_removed(tmp_path):
    a, da = bi.create_build_dir(tmp_path)
    b, db = bi.create_build_dir(tmp_path)
    assert a != b and da.parent == db.parent == tmp_path / ".build"
    with pytest.raises(bi.BuildInputsError):
        bi.remove_build_dir(tmp_path / "measurements")
    keep_file = tmp_path / ".build" / "notes.txt"
    keep_file.write_text("user file")
    other = tmp_path / ".build" / "not-a-build"
    other.mkdir()
    old = time.time() - 10 * 3600
    for p in (da, keep_file, other):
        os.utime(p, (old, old))
    removed = bi.sweep_stale_build_dirs(tmp_path)
    assert removed == [a] and not da.exists() and db.exists()                 # young build dir kept
    assert keep_file.exists() and other.exists()                              # never touched


# ── the route, colprof faked ────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


def _chart(client, scans, state=None):
    cid = _create_a3(client)
    d = cache.charts_dir(SERIAL) / cid
    for name, text in scans.items():
        (d / "measurements").mkdir(parents=True, exist_ok=True)
        (d / "measurements" / name).write_text(text, encoding="ascii")
    if state is not None:
        (d / "scan_state.json").write_text(json.dumps(state))
    return cid, d


def _fake_colprof(monkeypatch, on_build=None, fail=False):
    fed = {}

    def _fb(self, ti3_base, *, descriptor, output_icc_path, colprof_flags):
        ti3 = Path(str(ti3_base) + ".ti3")
        fed.update(ti3_bytes=ti3.read_bytes(), ti3_path=ti3, out=Path(output_icc_path))
        if on_build:
            on_build()
        if fail:
            raise RuntimeError("colprof: Error - simulated")
        Path(output_icc_path).write_bytes(b"NEW-ICC" + hashlib.sha256(fed["ti3_bytes"]).digest())
        return SimpleNamespace(output_icc_path=str(output_icc_path), output_icc_size_bytes=39)

    monkeypatch.setattr("lib.z9_client.profiling.ProfilingOps.build_profile", _fb)
    return fed


def _leftovers(d):
    meas = sorted(p.name for p in (d / "measurements").iterdir())
    builds = sorted(p.name for p in (d / ".build").iterdir()) if (d / ".build").exists() else []
    return meas, builds


def test_route_last_feeds_the_raw_bytes_and_traces_ignored_rejections(client, monkeypatch):
    raw = ti3_text(grid_rows())
    cid, d = _chart(client, {"20260101_100000.ti3": raw}, {"rejected_readings": {"20260101_100000.ti3": ["5"]}})
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 200, r.text
    assert fed["ti3_bytes"] == raw.encode()                              # legacy: rejection NOT applied
    assert ".build" in fed["ti3_path"].parts and fed["out"].parent == fed["ti3_path"].parent.parent.parent
    assert _leftovers(d) == (["20260101_100000.ti3"], [])                # no .icc next to the raw scan
    m = json.loads((d / "profile_manifest.json").read_text())
    assert m["plan"]["rejections_not_applied"] == [{"chart_id": cid, "scan": "20260101_100000.ti3",
                                                    "sids": ["5"], "reason": bi.REASON_LEGACY_LAST}]
    assert m["materialized"]["effective_ti3"]["bytes_passthrough"] is True
    assert m["result"]["icc_sha256"] == hashlib.sha256((d / f"{cid}.icc").read_bytes()).hexdigest()
    st = json.loads((d / "scan_state.json").read_text())
    assert st["profile_built_from"] == ["20260101_100000.ti3"] and st["profile_built_rejections"] == {}


def test_route_colprof_failure_leaves_everything_intact(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())},
                    {"rejected_readings": {"a.ti3": ["3"]}})
    (d / f"{cid}.icc").write_bytes(b"PREVIOUS PROFILE")
    before = tree_digest(d)
    _fake_colprof(monkeypatch, fail=True)
    r = client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"})
    assert r.status_code == 422 and "colprof failed" in r.text
    assert tree_digest(d) == before                                      # ICC, scans, scan_state: same bytes
    assert _leftovers(d)[1] == []


def test_route_publishes_atomically_after_colprof(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    (d / f"{cid}.icc").write_bytes(b"PREVIOUS PROFILE")
    during = {}
    _fake_colprof(monkeypatch, on_build=lambda: during.update(icc=(d / f"{cid}.icc").read_bytes()))
    replaced = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (replaced.append((Path(a), Path(b))), real_replace(a, b))[1])
    assert client.post(f"/api/charts/{cid}/profile", json={}).status_code == 200
    assert during["icc"] == b"PREVIOUS PROFILE"                          # untouched while colprof runs
    assert (d / f"{cid}.icc").read_bytes().startswith(b"NEW-ICC")
    icc_moves = [(a, b) for a, b in replaced if b == d / f"{cid}.icc"]
    assert len(icc_moves) == 1 and icc_moves[0][0].parent == d          # one os.replace from a temp file
    assert icc_moves[0][0].name.startswith(f".{cid}.icc.")
    bid = json.loads((d / "profile_manifest.json").read_text())["pointer"]["build_id"]
    assert (d / "builds" / bid / "profile.icc").read_bytes() == (d / f"{cid}.icc").read_bytes()


def test_route_snapshot_is_what_gets_traced(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows()), "b.ti3": ti3_text(grid_rows())},
                    {"rejected_readings": {"a.ti3": ["3"]}})

    def user_edits_during_build():                                       # the user acts while colprof runs
        st = json.loads((d / "scan_state.json").read_text())
        st["roles"] = {"b.ti3": "excluded"}
        st["rejected_readings"] = {"a.ti3": ["3", "4"]}
        (d / "scan_state.json").write_text(json.dumps(st))

    fed = _fake_colprof(monkeypatch, on_build=user_edits_during_build)
    assert client.post(f"/api/charts/{cid}/profile", json={"profile_base": "average"}).status_code == 200
    st = json.loads((d / "scan_state.json").read_text())
    assert st["profile_built_from"] == ["a.ti3", "b.ti3"]                # what was USED, not re-read
    assert st["profile_built_rejections"] == {"a.ti3": ["3"]}
    assert st["roles"] == {"b.ti3": "excluded"} and st["rejected_readings"] == {"a.ti3": ["3", "4"]}  # edits kept
    m = json.loads((d / "profile_manifest.json").read_text())
    assert [s["rejections_applied"] for s in m["plan"]["current_scans"]] == [["3"], []]
    _, _, rows_fed = parse_ti3(fed["ti3_bytes"].decode("utf-8", "replace"))
    assert len(rows_fed) == 26 + 27                                      # a minus SID 3, b complete


def test_route_multisource_extra_rejections_stay_in_the_build_dir(client, monkeypatch):
    rows = grid_rows()
    cid, d = _chart(client, {"a.ti3": ti3_text(rows)})
    xid, xd = _chart(client, {"x.ti3": ti3_text(rows)}, {"rejected_readings": {"x.ti3": ["2", "11"]}})
    x_before = tree_digest(xd)
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={"extra_chart_ids": [xid]})
    assert r.status_code == 200, r.text
    _, fmt, out = parse_ti3(fed["ti3_bytes"].decode("utf-8", "replace"))
    assert len(out) == 27 + 25 and fed["ti3_path"].name == f"{cid}_multisource.ti3"
    assert tree_digest(xd) == x_before                                   # no _qcfilt in the other chart
    m = json.loads((d / "profile_manifest.json").read_text())
    assert [(e["chart_id"], e["out_sid_range"]) for e in m["materialized"]["layout"]] == [(cid, [1, 27]), (xid, [28, 52])]
    prov = bi.reconstruct_provenance(m, lambda c, s: cache.locate_chart_dir(c) / "measurements" / s)
    assert {(p["chart_id"], p["sample_id"]) for p in prov if p["chart_id"] == xid}.isdisjoint({(xid, "2"), (xid, "11")})


def test_route_source_modified_during_build_is_not_published(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    (d / f"{cid}.icc").write_bytes(b"PREVIOUS PROFILE")
    real = bi.materialize

    def tampering(plan, workdir, **kw):
        (d / "measurements" / "a.ti3").write_text(ti3_text(grid_rows(2)))   # external change
        return real(plan, workdir, **kw)

    monkeypatch.setattr(bi, "materialize", tampering)
    _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 422 and "changed since the build started" in r.text
    assert (d / f"{cid}.icc").read_bytes() == b"PREVIOUS PROFILE" and _leftovers(d)[1] == []


def test_route_truncated_scan_is_refused_clearly_before_colprof(client, monkeypatch):
    text = ti3_text(grid_rows())
    cid, d = _chart(client, {"a.ti3": text[: len(text) // 2]})          # truncated: no END_DATA
    (d / f"{cid}.icc").write_bytes(b"PREVIOUS PROFILE")
    before = tree_digest(d)
    fed = _fake_colprof(monkeypatch)
    r = client.post(f"/api/charts/{cid}/profile", json={})
    assert r.status_code == 422 and f"unreadable ti3 {cid}/a.ti3" in r.text
    assert fed == {} and tree_digest(d) == before and _leftovers(d)[1] == []


def test_route_sweeps_an_interrupted_build_and_nothing_else(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    stale = d / ".build" / "20260101T000000Z-abc123"                     # left by a killed process
    stale.mkdir(parents=True)
    (stale / "a.icc").write_bytes(b"")
    user = d / ".build" / "keep-me"
    user.mkdir()
    old = time.time() - 10 * 3600
    os.utime(stale, (old, old))
    _fake_colprof(monkeypatch)
    assert client.post(f"/api/charts/{cid}/profile", json={}).status_code == 200
    assert not stale.exists() and user.exists()
