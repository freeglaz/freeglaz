"""Immutable profile versions and the publication protocol (lib/z9_client/profile_versions.py),
and their use by the build route: no profile is ever lost, an interrupted publication is
detected and repaired, the replaced repo/z9 copy is kept."""
import hashlib
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import cache
from lib.z9_client import profile_versions as pv
from webapp.backend.main import app
from webapp.backend.tests.test_build_inputs import _chart, _fake_colprof, grid_rows, ti3_text, tree_digest

CID = "CHT-20260101-1200-AA"
_REAL_WRITE_JSON = pv._write_json_atomic


def sha(b):
    return hashlib.sha256(b).hexdigest()


def manifest_for(build_id, icc_bytes, **extra):
    return {"manifest_version": 1, "kind": "build", "build_id": build_id,
            "result": {"icc_sha256": sha(icc_bytes), "icc_size_bytes": len(icc_bytes)}, **extra}


def publish(d, build_id, icc_bytes):
    tmp = d / f"{build_id}.icc.src"
    tmp.write_bytes(icc_bytes)
    try:
        return pv.publish(d, CID, build_id, tmp, manifest_for(build_id, icc_bytes))
    finally:
        tmp.unlink(missing_ok=True)


# ── protocol ────────────────────────────────────────────────────────────────
def test_publish_creates_an_immutable_version_the_current_profile_and_the_pointer(tmp_path):
    out = publish(tmp_path, "20260101T120000Z-aaaaaa", b"ICC-1")
    assert out["pointer_written"] and not out["warnings"]
    v = tmp_path / "builds" / "20260101T120000Z-aaaaaa"
    assert (v / "profile.icc").read_bytes() == b"ICC-1" == (tmp_path / f"{CID}.icc").read_bytes()
    ptr = pv.read_pointer(tmp_path)["pointer"]
    assert ptr["build_id"] == "20260101T120000Z-aaaaaa" and ptr["icc_sha256"] == sha(b"ICC-1")
    assert ptr["manifest_sha256"] == sha((v / "manifest.json").read_bytes())
    assert pv.inspect(tmp_path, CID)["state"] == "ok"
    with pytest.raises(pv.VersionError, match="immutable"):              # a version is never rewritten
        publish(tmp_path, "20260101T120000Z-aaaaaa", b"ICC-1")
    with pytest.raises(pv.VersionError, match="does not match"):
        pv.publish(tmp_path, CID, "20260101T130000Z-bbbbbb", v / "profile.icc",
                   manifest_for("20260101T130000Z-bbbbbb", b"OTHER"))


def test_a_pre_versions_profile_is_preserved_with_unknown_provenance(tmp_path):
    (tmp_path / f"{CID}.icc").write_bytes(b"LEGACY ICC")
    trace = {"profile_built_from": ["a.ti3"], "profile_built_rejections": {}}
    assert pv.inspect(tmp_path, CID)["problems"] == ["current_not_in_history"]
    bid = pv.preserve_current(tmp_path, CID, trace)
    assert bid and bid.endswith("-preserved-" + sha(b"LEGACY ICC")[:8])
    m = pv.read_version_manifest(tmp_path, bid)
    assert m["kind"] == "preserved" and m["provenance"] == "unknown" and m["plan"] is None   # nothing invented
    assert m["legacy_trace"]["profile_built_from"] == ["a.ti3"] and "not verified" in m["legacy_trace"]["note"]
    assert pv.preserve_current(tmp_path, CID, trace) is None             # idempotent
    publish(tmp_path, "20260102T000000Z-cccccc", b"NEW ICC")
    assert (tmp_path / "builds" / bid / "profile.icc").read_bytes() == b"LEGACY ICC"   # still there


def test_a_4b_manifest_matching_the_current_profile_is_kept_as_its_provenance(tmp_path):
    (tmp_path / f"{CID}.icc").write_bytes(b"4B ICC")
    (tmp_path / "profile_manifest.json").write_text(json.dumps(
        {"plan": {"policy": "legacy"}, "materialized": {"layout": []}, "result": {"icc_sha256": sha(b"4B ICC")}}))
    m = pv.read_version_manifest(tmp_path, pv.preserve_current(tmp_path, CID))
    assert m["provenance"] == "from_previous_manifest" and m["plan"] == {"policy": "legacy"}


def test_interruption_before_the_version_is_complete(tmp_path, monkeypatch):
    publish(tmp_path, "20260101T120000Z-aaaaaa", b"ICC-1")
    before = tree_digest(tmp_path)
    monkeypatch.setattr(os, "rename", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        publish(tmp_path, "20260101T130000Z-bbbbbb", b"ICC-2")
    monkeypatch.undo()
    rep = pv.inspect(tmp_path, CID)
    assert rep["state"] == "ok" and rep["staging_leftovers"] == [".staging-20260101T130000Z-bbbbbb"]
    assert (tmp_path / f"{CID}.icc").read_bytes() == b"ICC-1"            # previous profile current
    assert pv.repair(tmp_path, CID) == []                                 # young leftover: kept
    old = time.time() - 7200
    os.utime(tmp_path / "builds" / ".staging-20260101T130000Z-bbbbbb", (old, old))
    assert pv.repair(tmp_path, CID) == ["removed staging leftover .staging-20260101T130000Z-bbbbbb"]
    assert tree_digest(tmp_path) == before


def test_interruption_between_version_and_current_profile(tmp_path, monkeypatch):
    publish(tmp_path, "20260101T120000Z-aaaaaa", b"ICC-1")
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("crash"))
                        if Path(b).name == f"{CID}.icc" else real(a, b))
    with pytest.raises(OSError):
        publish(tmp_path, "20260101T130000Z-bbbbbb", b"ICC-2")
    monkeypatch.undo()
    rep = pv.inspect(tmp_path, CID)
    assert (tmp_path / f"{CID}.icc").read_bytes() == b"ICC-1" and rep["current_build_id"] == "20260101T120000Z-aaaaaa"
    assert rep["orphan_versions"] == ["20260101T130000Z-bbbbbb"] and rep["state"] == "ok"
    assert (tmp_path / "builds" / "20260101T130000Z-bbbbbb" / "profile.icc").read_bytes() == b"ICC-2"   # kept


def test_interruption_between_current_profile_and_pointer_is_repaired(tmp_path, monkeypatch):
    publish(tmp_path, "20260101T120000Z-aaaaaa", b"ICC-1")
    monkeypatch.setattr(pv, "_write_json_atomic", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    out = publish(tmp_path, "20260101T130000Z-bbbbbb", b"ICC-2")
    monkeypatch.undo()
    assert not out["pointer_written"] and "repaired at the next build" in out["warnings"][0]
    rep = pv.inspect(tmp_path, CID)
    assert rep["problems"] == ["pointer_stale"] and rep["state"] == "repairable"
    assert pv.repair(tmp_path, CID) == ["pointer rewritten to 20260101T130000Z-bbbbbb"]
    ptr = pv.read_pointer(tmp_path)["pointer"]
    assert ptr["build_id"] == "20260101T130000Z-bbbbbb" and ptr["repaired"] is True
    assert pv.inspect(tmp_path, CID)["state"] == "ok"


def test_a_corrupt_version_is_reported_and_never_touched(tmp_path):
    publish(tmp_path, "20260101T120000Z-aaaaaa", b"ICC-1")
    publish(tmp_path, "20260101T130000Z-bbbbbb", b"ICC-2")
    p = tmp_path / "builds" / "20260101T120000Z-aaaaaa" / "profile.icc"
    p.write_bytes(b"BIT ROT")
    rep = pv.inspect(tmp_path, CID)
    assert rep["corrupt_versions"] == ["20260101T120000Z-aaaaaa"] and rep["state"] == "degraded"
    pv.repair(tmp_path, CID, preserve=True)
    assert p.read_bytes() == b"BIT ROT"


# ── the build route ─────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_successive_builds_keep_every_version(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    (d / f"{cid}.icc").write_bytes(b"PROFILE BUILT BEFORE VERSIONS")
    old = time.time() - 30 * 86400                                     # built weeks ago
    os.utime(d / f"{cid}.icc", (old, old))
    _fake_colprof(monkeypatch)
    r1 = client.post(f"/api/charts/{cid}/profile", json={"name": "v-one"})
    assert r1.status_code == 200, r1.text
    (d / "measurements" / "b.ti3").write_text(ti3_text(grid_rows()).replace(" 5.00000", " 5.50000"))
    r2 = client.post(f"/api/charts/{cid}/profile", json={"name": "v-two"})
    assert r2.status_code == 200, r2.text
    versions = pv.list_versions(d)
    kinds = [v["kind"] for v in versions]
    assert kinds == ["preserved", "build", "build"] and all(v["intact"] for v in versions)
    assert [v["build_id"] for v in versions][1:] == [r1.json()["build_id"], r2.json()["build_id"]]
    assert (d / "builds" / versions[0]["build_id"] / "profile.icc").read_bytes() == b"PROFILE BUILT BEFORE VERSIONS"
    assert (d / f"{cid}.icc").read_bytes() == (d / "builds" / r2.json()["build_id"] / "profile.icc").read_bytes()
    assert json.loads((d / "scan_state.json").read_text())["last_build_id"] == r2.json()["build_id"]
    m1 = pv.read_version_manifest(d, r1.json()["build_id"])
    assert m1["request"]["name"] == "v-one" and m1["snapshot"]["included"] == ["a.ti3"]
    assert m1["software"]["build_inputs"] == 1
    refs = json.loads((d / "builds" / r1.json()["build_id"] / "references.json").read_text())["references"]
    meta = json.loads(Path(refs[0]["path"]).with_name(f".{Path(refs[0]['path']).name}.meta.json").read_text())
    assert meta["build_id"] == r1.json()["build_id"] and meta["build"]["build_id"] == r1.json()["build_id"]
    assert meta["manifest_sha256"] == sha((d / "builds" / r1.json()["build_id"] / "manifest.json").read_bytes())


def test_failed_build_creates_no_version_and_changes_nothing(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    (d / f"{cid}.icc").write_bytes(b"PREVIOUS")
    before = tree_digest(d)
    _fake_colprof(monkeypatch, fail=True)
    assert client.post(f"/api/charts/{cid}/profile", json={}).status_code == 422
    assert tree_digest(d) == before and not (d / "builds").exists()


def test_build_after_an_interrupted_publication_repairs_then_publishes(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    _fake_colprof(monkeypatch)
    monkeypatch.setattr(pv, "_write_json_atomic", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    r1 = client.post(f"/api/charts/{cid}/profile", json={"name": "p1"})
    assert r1.status_code == 200 and r1.json()["publication_warnings"]                 # pointer stale
    monkeypatch.setattr(pv, "_write_json_atomic", _REAL_WRITE_JSON)
    r2 = client.post(f"/api/charts/{cid}/profile", json={"name": "p2"})
    assert r2.status_code == 200 and not r2.json()["publication_warnings"]
    assert pv.read_pointer(d)["pointer"]["build_id"] == r2.json()["build_id"]
    assert [v["build_id"] for v in pv.list_versions(d)] == [r1.json()["build_id"], r2.json()["build_id"]]



def test_repo_replace_keeps_the_replaced_profile(client, monkeypatch):
    cid, d = _chart(client, {"a.ti3": ti3_text(grid_rows())})
    _fake_colprof(monkeypatch)
    r1 = client.post(f"/api/charts/{cid}/profile", json={"name": "same-name"})
    first = Path(r1.json()["ranged_icc_path"])
    first_bytes, first_meta = first.read_bytes(), first.with_name(f".{first.name}.meta.json").read_text()
    (d / "measurements" / "b.ti3").write_text(ti3_text(grid_rows()).replace(" 5.00000", " 5.50000"))
    assert client.post(f"/api/charts/{cid}/profile", json={"name": "same-name"}).status_code == 409   # safe default
    r2 = client.post(f"/api/charts/{cid}/profile", json={"name": "same-name", "on_conflict": "replace"})
    assert r2.status_code == 200 and Path(r2.json()["ranged_icc_path"]) == first
    kept = sorted((first.parent / cache.REPLACED_DIR_NAME).iterdir())
    icc_kept = [p for p in kept if p.name.endswith("-same-name.icc")]
    assert len(icc_kept) == 1 and icc_kept[0].read_bytes() == first_bytes
    assert (first.parent / cache.REPLACED_DIR_NAME / f".{icc_kept[0].name}.meta.json").read_text() == first_meta
    new_meta = json.loads(first.with_name(f".{first.name}.meta.json").read_text())
    assert new_meta["replaces"]["path"] == str(icc_kept[0]) and new_meta["build_id"] == r2.json()["build_id"]
    listed = [e["filename"] for e in cache.list_repo_z9_profiles()]
    assert listed.count("same-name.icc") == 1 and not any("-same-name.icc" in f for f in listed)
