"""Tests for the local freeglaz store.

Covers:
- root resolver (env override, default)
- zone helpers (mirror, repo, backups, sessions)
- ensure_store / ensure_mirror idempotent
- save_donor / load_donor / donor_exists / donor_needs_refresh
- slugify (Unicode characters, separators, empty)
- store_status (without client = pure local read)
- silent migration of legacy donors/ (presence ignored)
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point FREEGLAZ_STORE_ROOT at tmp_path to isolate from the real store."""
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    yield tmp_path


# ─── root_dir and resolvers ───────────────────────────────────────────


def test_root_dir_env_override(isolated_store):
    from lib.z9_client import cache
    assert cache.root_dir() == isolated_store


def test_root_dir_default_when_no_env(monkeypatch):
    monkeypatch.delenv("FREEGLAZ_STORE_ROOT", raising=False)
    from lib.z9_client import cache
    # Default uses Path.home() / Documents / freeglaz. No assertion on
    # the content (machine-dependent), just that we land on freeglaz.
    assert cache.root_dir().name == "freeglaz"


def test_zone_dirs_derive_from_root(isolated_store):
    from lib.z9_client import cache
    assert cache.mirror_dir() == isolated_store / "mirror"
    assert cache.repo_dir() == isolated_store / "repo"
    assert cache.repo_printers_dir() == isolated_store / "repo" / "printers"
    assert cache.repo_displays_dir() == isolated_store / "repo" / "displays"
    assert cache.repo_workingspaces_dir() == isolated_store / "repo" / "workingspaces"
    assert cache.backups_dir() == isolated_store / "backups"
    assert cache.sessions_dir() == isolated_store / "sessions"


def test_mirror_serial_dir_requires_serial(isolated_store):
    from lib.z9_client import cache
    with pytest.raises(ValueError):
        cache.mirror_serial_dir("")


def test_mirror_paper_dir_uses_slug(isolated_store):
    from lib.z9_client import cache
    p = cache.mirror_paper_dir("CN1234X", "Canson Baryta Photographique")
    assert p == isolated_store / "mirror" / "CN1234X" / "papers" / "canson-baryta-photographique"


# ─── ensure_store / ensure_mirror ─────────────────────────────────────


def test_ensure_store_idempotent(isolated_store):
    from lib.z9_client import cache
    cache.ensure_store()
    # Global skeleton created (charts/, sessions/ AND backups/ are now on-demand)
    for d in (cache.mirror_dir(), cache.repo_dir(), cache.repo_printers_dir()):
        assert d.exists() and d.is_dir()
    # sessions/ and backups/ are NO LONGER created by ensure_store (on-demand)
    assert not cache.sessions_dir().exists()
    assert not cache.backups_dir().exists()
    # store.json created with store_version=1
    manifest = isolated_store / "store.json"
    assert manifest.exists()
    # Idempotent: a 2nd call breaks nothing and does not modify the manifest
    mtime1 = manifest.stat().st_mtime
    cache.ensure_store()
    assert manifest.stat().st_mtime == mtime1


def test_ensure_mirror_creates_serial_dir(isolated_store):
    from lib.z9_client import cache
    sdir = cache.ensure_mirror("CNXXXXXXXX")
    assert sdir.exists()
    assert (sdir / "_mirror.json").exists()
    assert (sdir / "papers").exists()


def test_ensure_store_ignores_legacy_donors_dir(isolated_store):
    """If an old donors/ directory lingers, ensure_store does not crash."""
    from lib.z9_client import cache
    legacy = isolated_store / "donors"
    legacy.mkdir()
    (legacy / "leftover.icc").write_bytes(b"legacy")
    cache.ensure_store()  # must not raise
    # The legacy dir is left untouched
    assert (legacy / "leftover.icc").exists()


# ─── slugify ──────────────────────────────────────────────────────────


def test_slugify_basic():
    from lib.z9_client import cache
    assert cache.slugify("Canson Baryta Photographique") == "canson-baryta-photographique"


def test_slugify_unicode_accents():
    from lib.z9_client import cache
    assert cache.slugify("Hahnemühle Fine Art Baryta") == "hahnemuhle-fine-art-baryta"


def test_slugify_special_chars():
    from lib.z9_client import cache
    assert cache.slugify("HP Photo Gloss / Semi-Gloss") == "hp-photo-gloss-semi-gloss"


def test_slugify_empty_returns_unnamed():
    from lib.z9_client import cache
    assert cache.slugify("") == "unnamed"
    assert cache.slugify("   /// ") == "unnamed"


# ─── Donor: save / load / refresh ─────────────────────────────────────


def _fake_icc() -> bytes:
    """Dummy ICC payload (reasonable size, stable content)."""
    return b"freeglaz-test-icc-payload" * 64


def test_save_donor_writes_to_mirror(isolated_store):
    from lib.z9_client import cache
    serial = "CNXXXXXXXX"
    icc = _fake_icc()
    path, entry = cache.save_donor(
        icc_bytes=icc,
        paper_id="X1", paper_name="Canson Baryta", serial=serial,
        donor_id="2090", donor_name="HP Photo Gloss",
        gloss_enhancer_extracted_from="FULLPAGE",
        color_space="PRINTER_RGB",
        z9_uuid="uuid-1", z9_icc_name="HP Z9, Canson",
        z9_date="2026-05-29",
        extracted_from_host="192.168.1.50",
    )
    # The donor is in mirror/<serial>/papers/<slug>/
    assert path.parent == cache.mirror_paper_dir(serial, "Canson Baryta")
    # freeglaz-factory marker in the name
    assert cache.FACTORY_MARKER in path.name
    assert cache.is_freeglaz_factory_filename(path.name)
    # Paper manifest created
    manifest = path.parent / "_paper.json"
    assert manifest.exists()


def test_donor_exists_and_load(isolated_store):
    from lib.z9_client import cache
    serial = "CN1234X"
    icc = _fake_icc()
    cache.save_donor(
        icc_bytes=icc, paper_id="X", paper_name="Test Paper", serial=serial,
        donor_id=None, donor_name=None,
        gloss_enhancer_extracted_from="OFF", color_space="PRINTER_RGB",
        z9_uuid="u", z9_icc_name="n", z9_date="d",
        extracted_from_host="host",
    )
    assert cache.donor_exists("Test Paper", serial)
    assert not cache.donor_exists("Test Paper", "OTHER_SERIAL")

    path, data, meta = cache.load_donor("Test Paper", serial, verify_md5=True)
    assert data == icc
    assert meta["z9_uuid"] == "u"


def test_load_donor_detects_corruption(isolated_store):
    from lib.z9_client import cache
    serial = "CN1234X"
    icc = _fake_icc()
    path, _ = cache.save_donor(
        icc_bytes=icc, paper_id="X", paper_name="Tampered", serial=serial,
        donor_id=None, donor_name=None,
        gloss_enhancer_extracted_from="OFF", color_space="PRINTER_RGB",
        z9_uuid="u", z9_icc_name="n", z9_date="d",
        extracted_from_host="host",
    )
    # Corrupt the file
    path.write_bytes(b"tampered content")
    with pytest.raises(ValueError, match="integrity"):
        cache.load_donor("Tampered", serial, verify_md5=True)


def test_donor_needs_refresh_logic(isolated_store):
    from lib.z9_client import cache
    serial = "CN1234X"
    # Absent: needs refresh
    assert cache.donor_needs_refresh("Nope", serial, "uuid-1") is True

    cache.save_donor(
        icc_bytes=_fake_icc(), paper_id="X", paper_name="P", serial=serial,
        donor_id=None, donor_name=None,
        gloss_enhancer_extracted_from="OFF", color_space="PRINTER_RGB",
        z9_uuid="uuid-1", z9_icc_name="n", z9_date="d",
        extracted_from_host="host",
    )
    # Same UUID: no refresh
    assert cache.donor_needs_refresh("P", serial, "uuid-1") is False
    # Different UUID: refresh
    assert cache.donor_needs_refresh("P", serial, "uuid-2") is True
    # Without current_uuid: do not invalidate
    assert cache.donor_needs_refresh("P", serial, None) is False


def test_is_freeglaz_factory_filename_marker():
    from lib.z9_client import cache
    assert cache.is_freeglaz_factory_filename(
        "canson-baryta.freeglaz-factory.icc"
    )
    assert cache.is_freeglaz_factory_filename(
        "/abs/path/canson-baryta.freeglaz-factory.icc"
    )
    assert not cache.is_freeglaz_factory_filename("custom-user-profile.icc")


# ─── store_status (without client) ────────────────────────────────────


def test_store_status_empty_returns_minimal(isolated_store):
    from lib.z9_client import store
    s = store.store_status(client=None)
    assert s["store_root"] == str(isolated_store)
    assert s["store_version"] == 1
    assert s["mirrors"] == []
    assert s["repo"] == {"printers": 0, "displays": 0, "workingspaces": 0}


def test_store_status_after_save_donor(isolated_store):
    from lib.z9_client import cache, store
    serial = "CNXXXXXXXX"
    cache.save_donor(
        icc_bytes=_fake_icc(),
        paper_id="X", paper_name="Canson Baryta", serial=serial,
        donor_id=None, donor_name=None,
        gloss_enhancer_extracted_from="OFF", color_space="PRINTER_RGB",
        z9_uuid="u", z9_icc_name="n", z9_date="d",
        extracted_from_host="host",
    )
    s = store.store_status(client=None)
    assert len(s["mirrors"]) == 1
    m = s["mirrors"][0]
    assert m["serial"] == serial
    assert m["n_papers"] == 1
    # counters per type
    assert m["n_profiles_factory"] == 1
    assert m["n_profiles_custom"] == 0
    assert m["n_profiles_total"] == 1
    # No client -> no remote_version
    assert "remote_version" not in m


# ─── Backups (legacy schema preserved) ────────────────────────────────


# ─── Pure mirror (all slots, factory AND custom) ──────────────────────


def test_save_mirror_profile_writes_factory_and_custom(isolated_store):
    """A custom-only paper with only custom profiles must be mirrorable
    (no exclusion when factory is absent)."""
    from lib.z9_client import cache
    serial = "CN1234X"
    paper = {
        "id": "ABCD1234567890ABCDEF1234567890AB",
        "name": "Custom Only Paper",
        "donor_id": "2090",
        "is_factory": False,
        "is_user_custom": True,
        "category_id": "100",
        "category_name": "Custom Paper",
    }
    # 2 custom profiles, NO factory slot
    for ge in ("OFF", "FULLPAGE"):
        cache.save_mirror_profile(
            icc_bytes=_fake_icc(),
            serial=serial,
            paper=paper,
            profile={
                "gloss_enhancer": ge, "color_space": "PRINTER_RGB",
                "custom": True,
                "uuid": f"u-{ge}", "icc_name": f"name-{ge}", "date": "2026-05-30",
            },
        )
    manifest = cache.read_paper_manifest(serial, "Custom Only Paper")
    assert len(manifest["profiles"]) == 2
    assert all(p["custom"] for p in manifest["profiles"])
    assert manifest["paper_id"] == paper["id"]
    assert manifest["donor_paper_id"] == "2090"
    assert manifest["is_user_custom"] is True
    # No `.freeglaz-factory.` file for this paper
    paper_dir = cache.mirror_paper_dir(serial, "Custom Only Paper")
    factory_files = [f for f in paper_dir.iterdir()
                     if cache.is_freeglaz_factory_filename(f.name)]
    assert factory_files == []


def test_find_factory_profile_returns_none_when_custom_only(isolated_store):
    from lib.z9_client import cache
    serial = "CN1234X"
    paper = {"id": "X", "name": "P", "donor_id": None,
             "is_factory": False, "is_user_custom": True}
    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": True, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    assert cache.find_factory_profile(serial, "P") is None
    # And load_donor must raise cleanly
    with pytest.raises(FileNotFoundError, match="No factory profile"):
        cache.load_donor("P", serial)


def test_find_factory_profile_prefers_ge_off(isolated_store):
    """The canonical donor is on GE=OFF, but accepts FULLPAGE as fallback."""
    from lib.z9_client import cache
    serial = "CN1234X"
    paper = {"id": "X", "name": "P", "donor_id": None,
             "is_factory": True, "is_user_custom": False}
    # Only FULLPAGE factory
    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "FULLPAGE", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u-fp", "icc_name": "n", "date": "d"},
    )
    f = cache.find_factory_profile(serial, "P")
    assert f is not None
    assert f["gloss_enhancer"] == "FULLPAGE"

    # Add OFF: must pick OFF as priority
    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u-off", "icc_name": "n", "date": "d"},
    )
    f = cache.find_factory_profile(serial, "P")
    assert f["gloss_enhancer"] == "OFF"


def test_save_mirror_profile_replaces_same_slot(isolated_store):
    """Re-saving the same slot (same GE+CS) overwrites the previous entry."""
    from lib.z9_client import cache
    serial = "CN1234X"
    paper = {"id": "X", "name": "P", "is_factory": False, "is_user_custom": True}
    for uuid in ("u1", "u2", "u3"):
        cache.save_mirror_profile(
            icc_bytes=_fake_icc(), serial=serial, paper=paper,
            profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                     "custom": True, "uuid": uuid, "icc_name": "n", "date": "d"},
        )
    manifest = cache.read_paper_manifest(serial, "P")
    # Only 1 slot, so only 1 entry — the last one
    assert len(manifest["profiles"]) == 1
    assert manifest["profiles"][0]["z9_uuid"] == "u3"


# ─── Clean purge + schema version + legacy donor cleanup ──────────────


def test_purge_mirror_paper_removes_dir(isolated_store):
    from lib.z9_client import cache
    serial = "CN1234X"
    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial,
        paper={"id": "X", "name": "P", "is_factory": False, "is_user_custom": True},
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": True, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    paper_dir = cache.mirror_paper_dir(serial, "P")
    assert paper_dir.exists()
    n = cache.purge_mirror_paper(serial, "P")
    assert n is True or n == 1 or n is True  # True (boolean) expected
    assert not paper_dir.exists()
    # Idempotent: 2nd call does not raise
    assert cache.purge_mirror_paper(serial, "P") is False


def test_purge_mirror_serial_papers_removes_all_dirs(isolated_store):
    from lib.z9_client import cache
    serial = "CN1234X"
    for name in ("A", "B", "C"):
        cache.save_mirror_profile(
            icc_bytes=_fake_icc(), serial=serial,
            paper={"id": name, "name": name,
                   "is_factory": False, "is_user_custom": True},
            profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                     "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
        )
    papers_dir = cache.mirror_papers_dir(serial)
    assert sum(1 for d in papers_dir.iterdir() if d.is_dir()) == 3
    n = cache.purge_mirror_serial_papers(serial)
    assert n == 3
    # papers_dir must still exist (emptied, not deleted)
    assert papers_dir.exists()
    assert sum(1 for d in papers_dir.iterdir() if d.is_dir()) == 0


def test_update_paper_manifest_drops_legacy_donor_block(isolated_store):
    """If a _paper.json contains a legacy `donor` block, the next
    profile write purges it silently."""
    from lib.z9_client import cache
    import json
    serial = "CN1234X"
    paper_name = "Legacy Paper"
    # Force a manifest with a legacy donor block
    cache.ensure_mirror(serial)
    paper_dir = cache.mirror_paper_dir(serial, paper_name)
    paper_dir.mkdir(parents=True, exist_ok=True)
    legacy = {
        "paper_id": "X",
        "paper_name": paper_name,
        "donor": {
            "extracted_at": "2026-05-29T01:02:03+02:00",
            "note": "Résidu format 17.1 — à purger",
            "md5": "deadbeef",
            "z9_uuid": "u",
        },
        "profiles": [],
    }
    mp = paper_dir / "_paper.json"
    mp.write_text(json.dumps(legacy), encoding="utf-8")

    # Write a real entry (simulate an update via sync) — the donor block
    # must disappear.
    cache.update_paper_manifest_for_profile(
        serial=serial,
        paper={"id": "X", "name": paper_name, "donor_id": "2090",
               "is_factory": False, "is_user_custom": True},
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
        filename="x.freeglaz-factory.icc",
        md5="md5val",
        size_bytes=1000,
    )
    rewritten = json.loads(mp.read_text(encoding="utf-8"))
    assert "donor" not in rewritten      # legacy block purged
    assert rewritten["donor_paper_id"] == "2090"   # firmware ref kept
    assert len(rewritten["profiles"]) == 1


def test_save_mirror_profile_also_drops_legacy_donor(isolated_store):
    """Same silent purge via save_mirror_profile (R&D donor-export path)."""
    from lib.z9_client import cache
    import json
    serial = "CN1234X"
    paper_name = "Legacy Paper 2"
    cache.ensure_mirror(serial)
    paper_dir = cache.mirror_paper_dir(serial, paper_name)
    paper_dir.mkdir(parents=True, exist_ok=True)
    legacy = {"paper_id": "Y", "paper_name": paper_name,
              "donor": {"extracted_at": "old", "note": "legacy"},
              "profiles": []}
    mp = paper_dir / "_paper.json"
    mp.write_text(json.dumps(legacy), encoding="utf-8")

    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial,
        paper={"id": "Y", "name": paper_name, "donor_id": "2090",
               "is_factory": False, "is_user_custom": True},
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    rewritten = json.loads(mp.read_text(encoding="utf-8"))
    assert "donor" not in rewritten
    assert rewritten["donor_paper_id"] == "2090"


def test_sync_schema_version_constant_is_at_least_3():
    """Safeguard: SYNC_SCHEMA_VERSION exposed in store.py and >= 3
    (v0/v1, then v2, then v3 — faithful z9_icc_name naming)."""
    from lib.z9_client import store
    assert hasattr(store, "SYNC_SCHEMA_VERSION")
    assert store.SYNC_SCHEMA_VERSION >= 3


# ─── safe_fs_name + mirror_profile_path(icc_name=...) ─────────────────


def test_safe_fs_name_preserves_spaces_and_accents():
    from lib.z9_client import cache
    assert cache.safe_fs_name("HPDesignjetZ9 Hahnemühle Fine Art Baryta Satin GE OFF") \
        == "HPDesignjetZ9 Hahnemühle Fine Art Baryta Satin GE OFF"
    # Spaces, accents, parentheses, commas preserved
    assert cache.safe_fs_name("Mon Écran Eizo (2026), v2") \
        == "Mon Écran Eizo (2026), v2"


def test_safe_fs_name_strips_dangerous_chars():
    from lib.z9_client import cache
    assert "/" not in cache.safe_fs_name("Foo/Bar")
    assert "\\" not in cache.safe_fs_name("Foo\\Bar")
    assert "\x00" not in cache.safe_fs_name("With\x00NULL")
    # The result stays non-empty
    assert cache.safe_fs_name("/// ").endswith("_") is False  # collapse
    assert cache.safe_fs_name("///") == "unnamed"


def test_safe_fs_name_strips_leading_dot():
    from lib.z9_client import cache
    assert not cache.safe_fs_name(".hidden").startswith(".")


def test_mirror_profile_path_uses_icc_name_when_provided(isolated_store):
    from lib.z9_client import cache
    p = cache.mirror_profile_path(
        "CN1234", "Hahnemühle", "OFF", "PRINTER_RGB",
        custom=False, icc_name="HPDesignjetZ9 Hahnemühle GE OFF",
    )
    assert "HPDesignjetZ9 Hahnemühle GE OFF" in p.name
    assert p.name.endswith(".freeglaz-factory.icc")


def test_find_resident_profile_returns_exact_slot_and_path(isolated_store):
    """Resident-tag switch — find_resident_profile returns the EXACT
    slot (factory OR custom), distinguishes by GE and color_space."""
    from lib.z9_client import cache
    serial = "CN9999X"
    paper = {"id": "Y", "name": "Test",
             "is_factory": False, "is_user_custom": True}
    # OFF factory + FULLPAGE custom (Photolustre case observed live)
    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u-off",
                 "icc_name": "Test GE OFF", "date": "d"},
    )
    cache.save_mirror_profile(
        icc_bytes=_fake_icc() + b"x", serial=serial, paper=paper,
        profile={"gloss_enhancer": "FULLPAGE", "color_space": "PRINTER_RGB",
                 "custom": True, "uuid": "u-fp",
                 "icc_name": "Test GE ON custom", "date": "d"},
    )
    found_off = cache.find_resident_profile(serial, "Test", "OFF", "PRINTER_RGB")
    assert found_off is not None
    path_off, meta_off = found_off
    assert path_off.exists()
    assert meta_off["custom"] is False
    assert meta_off["z9_uuid"] == "u-off"

    found_fp = cache.find_resident_profile(serial, "Test", "FULLPAGE", "PRINTER_RGB")
    assert found_fp is not None
    path_fp, meta_fp = found_fp
    assert path_fp != path_off
    assert meta_fp["custom"] is True   # resident-tag on a custom slot = OK
    assert meta_fp["z9_uuid"] == "u-fp"


def test_find_resident_profile_returns_none_when_slot_absent(isolated_store):
    from lib.z9_client import cache
    serial = "CN9999X"
    paper = {"id": "Y", "name": "Test",
             "is_factory": False, "is_user_custom": True}
    cache.save_mirror_profile(
        icc_bytes=_fake_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u",
                 "icc_name": "n", "date": "d"},
    )
    # FULLPAGE slot absent -> None
    assert cache.find_resident_profile(
        serial, "Test", "FULLPAGE", "PRINTER_RGB",
    ) is None
    # Different ColorSpace -> None too
    assert cache.find_resident_profile(
        serial, "Test", "OFF", "PRINTER_GRAYSCALE",
    ) is None


def test_find_resident_profile_returns_none_when_paper_absent(isolated_store):
    from lib.z9_client import cache
    assert cache.find_resident_profile(
        "CN9999X", "Inexistant", "OFF", "PRINTER_RGB",
    ) is None


def test_mirror_profile_path_falls_back_to_slot_when_no_icc_name(isolated_store):
    from lib.z9_client import cache
    p = cache.mirror_profile_path(
        "CN1234", "Some Paper", "OFF", "PRINTER_RGB", custom=False,
    )
    assert "GE-OFF" in p.name
    assert "PRINTER_RGB" in p.name


# (test removed) cache.save_backup / cache.list_backups no longer exist:
# ICC backup unified in lib/z9_client/icc_backups.py (cf. test_icc_backups_lib.py).
