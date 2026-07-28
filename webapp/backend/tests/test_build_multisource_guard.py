"""BLOCK 2 — consistency guard for multi-source build (paper + GE).

Tests the primitives `_chart_paper_ge` (structured extraction from chart.json)
and `_assert_source_compatible` (rejects different paper/GE). Physical safety:
NEVER concatenate measurements from different papers/GE.
"""
import json
from pathlib import Path

import pytest

from lib.z9_client import cache
from webapp.backend.routes.charts import (
    _assert_source_compatible, _chart_paper_ge, _profile_identity)

SERIAL = "TESTSERIAL"   # per-serial : charts/<serial>/<chart_id>/


def _write_profile(parent: Path, filename: str, paper_id, ge) -> Path:
    """Write a dummy .icc + its adjacent _paper.json (authoritative manifest)."""
    parent.mkdir(parents=True, exist_ok=True)
    (parent / filename).write_bytes(b"FAKEICC")
    manifest = {"paper_name": "p", "profiles": [{"filename": filename, "gloss_enhancer": ge}]}
    if paper_id is not None:
        manifest["paper_id"] = paper_id
    (parent / "_paper.json").write_text(json.dumps(manifest), encoding="utf-8")
    return parent / filename


def _write_chart(root: Path, chart_id: str, media_id, ge) -> None:
    d = root / "charts" / SERIAL / chart_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "chart.json").write_text(json.dumps(
        {"paper": {"media_id": media_id, "gloss_enhancer": ge}}), encoding="utf-8")


def test_chart_paper_ge_reads_structured_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    _write_chart(tmp_path, "CHT-20260616-1200-AA", "MID_1", "FULLPAGE")
    assert _chart_paper_ge("CHT-20260616-1200-AA") == ("MID_1", "FULLPAGE")


def test_chart_paper_ge_defaults_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    _write_chart(tmp_path, "CHT-20260616-1200-BB", "MID_1", None)   # GE absent -> OFF
    assert _chart_paper_ge("CHT-20260616-1200-BB") == ("MID_1", "OFF")


def test_assert_compatible_passes_same_paper_ge():
    # same paper + same GE -> does not raise
    _assert_source_compatible("MID_1", "OFF", "MID_1", "OFF", "src")
    # GE normalization tolerance ('GE-OFF' / case == 'OFF')
    _assert_source_compatible("MID_1", "OFF", "MID_1", "GE-off", "src")


def test_assert_rejects_different_paper():
    with pytest.raises(ValueError, match="incompatible"):
        _assert_source_compatible("MID_1", "OFF", "MID_2", "OFF", "autre charte")


def test_assert_rejects_different_ge():
    with pytest.raises(ValueError, match="incompatible"):
        _assert_source_compatible("MID_1", "OFF", "MID_1", "FULLPAGE", "profil")


# ─── Source resolver (BLOCK 3) — guard active in _build_profile_now ─────
def _setup_chart_with_ti3(root, chart_id, media_id, ge):
    _write_chart(root, chart_id, media_id, ge)
    meas = root / "charts" / SERIAL / chart_id / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    (meas / "20260616_120000.ti3").write_text("FAKE", encoding="ascii")


def test_build_rejects_extra_chart_different_paper(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes.charts import ProfileChartBody, _build_profile_now
    _setup_chart_with_ti3(tmp_path, "CHT-20260616-1200-AA", "MID_1", "OFF")
    _setup_chart_with_ti3(tmp_path, "CHT-20260616-1200-BB", "MID_2", "OFF")  # different paper
    with pytest.raises(ValueError, match="incompatible"):
        _build_profile_now("CHT-20260616-1200-AA",
                           ProfileChartBody(extra_chart_ids=["CHT-20260616-1200-BB"]),
                           lambda p: None)


def test_build_passes_guard_same_paper_then_reaches_concat(tmp_path, monkeypatch):
    # Same paper+GE -> guard PASSES -> source is aggregated -> we reach the
    # multi-source concat (which fails on FAKE ti3, but NOT on the guard ->
    # proof the source was indeed added to source_paths).
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes.charts import ProfileChartBody, _build_profile_now
    _setup_chart_with_ti3(tmp_path, "CHT-20260616-1200-AA", "MID_1", "OFF")
    _setup_chart_with_ti3(tmp_path, "CHT-20260616-1200-CC", "MID_1", "OFF")  # same paper+GE
    with pytest.raises(ValueError) as exc:
        _build_profile_now("CHT-20260616-1200-AA",
                           ProfileChartBody(extra_chart_ids=["CHT-20260616-1200-CC"]),
                           lambda p: None)
    assert "incompatible" not in str(exc.value)          # guard PASSED
    assert "multi-source" in str(exc.value) or "inconsistent" in str(exc.value)


# ─── Authoritative server-side profile identity (hardening of guard c) ────────
def test_profile_identity_authoritative(tmp_path):
    path = _write_profile(tmp_path / "mir" / "papers" / "pap", "prof.icc", "MID_1", "OFF")
    assert _profile_identity(str(path)) == ("MID_1", "OFF")


def test_profile_identity_refuses_orphan(tmp_path):
    # .icc WITHOUT adjacent _paper.json -> clean rejection
    (tmp_path / "orphan.icc").write_bytes(b"FAKEICC")
    with pytest.raises(ValueError, match="unauthenticatable"):
        _profile_identity(str(tmp_path / "orphan.icc"))


def test_profile_identity_refuses_legacy_no_paper_id(tmp_path):
    path = _write_profile(tmp_path / "pap", "prof.icc", None, "OFF")  # no paper_id
    with pytest.raises(ValueError, match="paper_id"):
        _profile_identity(str(path))


def test_profile_identity_refuses_filename_absent(tmp_path):
    # manifest present but no entry for the right filename
    parent = tmp_path / "pap"
    _write_profile(parent, "other.icc", "MID_1", "OFF")
    (parent / "prof.icc").write_bytes(b"FAKEICC")            # not in profiles[]
    with pytest.raises(ValueError, match="absent from the manifest"):
        _profile_identity(str(parent / "prof.icc"))


def test_build_rejects_source_profile_different_paper(tmp_path, monkeypatch):
    # Identity derived SERVER-SIDE from the manifest (MID_2) != current chart (MID_1)
    # -> guard rejects, NO trust in any client-supplied field.
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes.charts import ProfileChartBody, SourceProfileRef, _build_profile_now
    _setup_chart_with_ti3(tmp_path, "CHT-20260616-1200-AA", "MID_1", "OFF")
    prof = _write_profile(tmp_path / "mir" / "MID_2dir", "p.icc", "MID_2", "OFF")
    with pytest.raises(ValueError, match="incompatible"):
        _build_profile_now("CHT-20260616-1200-AA",
                           ProfileChartBody(source_profiles=[SourceProfileRef(path=str(prof))]),
                           lambda p: None)


# ─── BLOCK 4 — source enumeration (same paper+GE, authoritative filtering) ───
def test_list_build_sources_same_paper_ge_only(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes import charts as cm
    cur = "CHT-20260616-1200-AA"
    match = "CHT-20260616-1200-CC"
    other_paper = "CHT-20260616-1200-DD"
    other_ge = "CHT-20260616-1200-EE"
    no_scan = "CHT-20260616-1200-FF"
    _setup_chart_with_ti3(tmp_path, cur, "MID_1", "OFF")
    _setup_chart_with_ti3(tmp_path, match, "MID_1", "OFF")          # ✅ (b)
    _setup_chart_with_ti3(tmp_path, other_paper, "MID_2", "OFF")    # different paper
    _setup_chart_with_ti3(tmp_path, other_ge, "MID_1", "FULLPAGE")  # different GE
    _write_chart(tmp_path, no_scan, "MID_1", "OFF")                 # same paper/GE but 0 scans
    # _sc.list_charts stubbed (isolate from store parsing)
    monkeypatch.setattr(cm._sc, "list_charts", lambda: [
        {"chart_id": c, "paper_media_id": mid, "paper": "P"}
        for c, mid in [(match, "MID_1"), (other_paper, "MID_2"),
                       (other_ge, "MID_1"), (no_scan, "MID_1")]])
    # on-disk profiles (repo_z9) : one match, one other paper
    _write_profile(cache.repo_z9_dir() / "d1", "good.icc", "MID_1", "OFF")   # ✅ (c)
    _write_profile(cache.repo_z9_dir() / "d2", "bad.icc", "MID_2", "OFF")    # different paper

    res = cm.list_build_sources(cur)
    assert res["paper_media_id"] == "MID_1" and res["gloss_enhancer"] == "OFF"
    assert [x["chart_id"] for x in res["extra_charts"]] == [match]    # CC only
    assert [Path(p["path"]).name for p in res["profiles"]] == ["good.icc"]


# ─── P1 — patch rejections from SOURCE charts (included scans × kept patches) ──
_TI3 = ("CTI3\nNUMBER_OF_SETS 3\n"
        "BEGIN_DATA_FORMAT\nSAMPLE_ID RGB_R RGB_G RGB_B XYZ_X XYZ_Y XYZ_Z\nEND_DATA_FORMAT\n"
        "BEGIN_DATA\n"
        "1 0 0 0 1.1 1.2 1.3\n"
        "2 50 50 50 30 31 32\n"
        "3 100 100 100 90 91 92\n"
        "END_DATA\n")


def _chart_with_scan(root: Path, chart_id: str, ti3_name: str, *, rejected=None):
    """Chart + 1 realistic ti3 scan ; rejected = discarded SAMPLE_IDs (scan_state)."""
    import json as _j
    _write_chart(root, chart_id, "MID_1", "OFF")
    meas = root / "charts" / SERIAL / chart_id / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    (meas / ti3_name).write_text(_TI3, encoding="ascii")
    if rejected:
        (root / "charts" / SERIAL / chart_id / "scan_state.json").write_text(
            _j.dumps({"rejected_readings": {ti3_name: list(rejected)}}), encoding="utf-8")


def test_apply_patch_rejections_drops_rejected_sid(tmp_path, monkeypatch):
    # RE-PROVES : the rejected reading (SID 2) of the SOURCE does NOT enter the build.
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes.charts import _apply_patch_rejections, _chart_dir, _included_ti3_paths
    _chart_with_scan(tmp_path, "CHT-20260617-1200-AA", "20260617_120000.ti3", rejected=["2"])
    cd = _chart_dir("CHT-20260617-1200-AA")
    used, tmp = _apply_patch_rejections(cd, _included_ti3_paths(cd))
    assert len(used) == 1 and len(tmp) == 1                  # 1 temporary filtered copy
    body = used[0].read_text(encoding="ascii")
    assert "\n2 50 50 50" not in body                        # rejected patch ABSENT
    assert "\n1 0 0 0" in body and "\n3 100 100 100" in body  # kept
    assert "NUMBER_OF_SETS 2" in body                        # count re-adjusted


def test_apply_patch_rejections_passthrough_when_none(tmp_path, monkeypatch):
    # No rejection : reuse the ORIGINAL ti3 (zero copy, zero side effect).
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes.charts import _apply_patch_rejections, _chart_dir, _included_ti3_paths
    _chart_with_scan(tmp_path, "CHT-20260617-1200-BB", "20260617_120000.ti3")
    cd = _chart_dir("CHT-20260617-1200-BB")
    incl = _included_ti3_paths(cd)
    used, tmp = _apply_patch_rejections(cd, incl)
    assert used == incl and tmp == []                        # strict passthrough


def test_list_build_sources_reports_included_and_rejected(tmp_path, monkeypatch):
    # PART B : n_scans = INCLUDED scans ; n_rejected = discarded readings (transparency).
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes import charts as cm
    cur, src = "CHT-20260617-1200-CC", "CHT-20260617-1200-DD"
    _chart_with_scan(tmp_path, cur, "20260617_120000.ti3")
    # source : 2 included scans, one of which has a rejected reading
    _write_chart(tmp_path, src, "MID_1", "OFF")
    meas = tmp_path / "charts" / SERIAL / src / "measurements"
    meas.mkdir(parents=True, exist_ok=True)
    (meas / "20260617_120000.ti3").write_text(_TI3, encoding="ascii")
    (meas / "20260617_130000.ti3").write_text(_TI3, encoding="ascii")
    import json as _j
    (tmp_path / "charts" / SERIAL / src / "scan_state.json").write_text(
        _j.dumps({"rejected_readings": {"20260617_130000.ti3": ["2"]}}), encoding="utf-8")
    monkeypatch.setattr(cm._sc, "list_charts", lambda: [
        {"chart_id": src, "paper_media_id": "MID_1", "paper": "P"}])

    res = cm.list_build_sources(cur)
    [x] = res["extra_charts"]
    assert x["chart_id"] == src
    assert x["n_scans"] == 2          # 2 included scans
    assert x["n_rejected"] == 1       # 1 discarded reading
