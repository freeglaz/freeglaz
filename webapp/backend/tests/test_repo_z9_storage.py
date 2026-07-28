"""Part A of profile refinement: per-paper "custom Z9" space.

Covers the storage foundation in ``repo/z9/<serial>/papers/<media_id>/``:
paths, 3-layer sidecar, save / read / list / delete, name-collision
handling, and NON-REGRESSION of the existing ``repo/printers/`` space
(which must never be touched by the new space)."""

import os

import pytest

from lib.z9_client import cache


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    cache.ensure_store()
    return tmp_path / "store"


SERIAL = "CNXXXXXXXX"
MEDIA = "157F2E9355D517302ABB75C16029A140"
ICC = b"\x00" * 36 + b"acsp" + b"\x00" * 100


def test_z9_dirs_are_under_repo_z9_not_printers():
    z9 = cache.repo_z9_dir()
    assert z9.name == "z9"
    assert z9.parent == cache.repo_dir()
    assert cache.repo_z9_paper_dir(SERIAL, MEDIA) == z9 / SERIAL / "papers" / MEDIA


def test_save_writes_icc_and_sidecar_with_three_layers():
    path, meta = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="Canson Photolustre RC 2021", label="Photo standard qm-r1.0",
        gloss_slot="OFF", method="argyll", method_flags="-qm -r1.0",
        n_patches=464, source_profile="base.icc",
        purpose_tags=["portrait"], notes="essai 1",
    )
    assert path.exists()
    assert path.name == "HPZ9_photo-standard-qm-r1-0_GE-OFF.icc"   # printer + GE after paper
    sidecar = path.parent / f".{path.name}.meta.json"
    assert sidecar.exists()
    assert meta["paper_name"] == "Canson Photolustre RC 2021"   # layer 1
    assert meta["media_id"] == MEDIA
    assert meta["gloss_slot"] == "OFF"
    assert meta["label"] == "Photo standard qm-r1.0"
    assert meta["method"] == "argyll"                            # layer 2
    assert meta["method_flags"] == "-qm -r1.0"
    assert meta["n_patches"] == 464
    assert meta["source_profile"] == "base.icc"
    assert meta["purpose_tags"] == ["portrait"]                  # layer 3
    assert meta["notes"] == "essai 1"
    assert meta["md5"] and meta["size_bytes"] == len(ICC)


def test_set_tags_writes_meta_icc_untouched():
    # Classification tags (purpose_tags) → sidecar .meta ONLY; the .icc stays
    # byte-for-byte identical (colorimetric fidelity).
    path, _ = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="x", gloss_slot="OFF")
    before = path.read_bytes()
    meta = cache.set_repo_z9_profile_tags(SERIAL, MEDIA, path.name, ["portrait", "mat"])
    assert meta["purpose_tags"] == ["portrait", "mat"]
    assert path.read_bytes() == before                                   # .icc UNCHANGED
    assert cache.read_repo_z9_profile_meta(path)["purpose_tags"] == ["portrait", "mat"]


def test_basename_truncated_to_63_keeps_printer_ge_date():
    # Auto name capped at 63 (desc ICC V2): SMART truncation — trims the paper slug,
    # keeps HPZ9_ + GE-<slot> + date.
    base = cache.repo_z9_profile_basename("OFF", "Canson " + "X" * 90, date_str="2026-06-19")
    assert len(base) <= 63
    assert base.startswith("HPZ9_")
    assert base.endswith("_GE-OFF_2026-06-19")          # GE + date preserved


def test_basename_is_ascii_and_equals_filename_stem():
    # The ICC desc at build time = repo_z9_profile_basename → must be ASCII (accents slugified)
    # and == filename without `.icc` (single source of the naming convention).
    base = cache.repo_z9_profile_basename("FULLPAGE", "Hahnemühle Baryta", date_str="2026-06-18")
    assert base == "HPZ9_hahnemuhle-baryta_GE-ON_2026-06-18"
    assert base.isascii()
    path = cache.repo_z9_profile_path(SERIAL, MEDIA, "FULLPAGE", "Hahnemühle Baryta", date_str="2026-06-18")
    assert path.stem == base       # desc = filename without extension


def test_fullpage_slot_presented_as_ge_on_in_name():
    # PRESENTATION: internal slot 'FULLPAGE' → 'GE-ON' in the NAME; the meta gloss_slot
    # stays 'FULLPAGE' (firmware value untouched).
    path, meta = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="Photolustre", label="Photolustre", gloss_slot="FULLPAGE",
        date_str="2026-06-17")
    assert path.name == "HPZ9_photolustre_GE-ON_2026-06-17.icc"   # FULLPAGE → GE-ON (name)
    assert meta["gloss_slot"] == "FULLPAGE"                        # internal UNCHANGED


def test_date_str_suffix_after_ge():
    # Build naming: <paper>_GE-<slot>_<date> (GE after the paper identity, date as suffix).
    path, _ = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="Canson Photolustre RC", label="Canson Photolustre RC",
        gloss_slot="OFF", date_str="2026-06-11")
    assert path.name == "HPZ9_canson-photolustre-rc_GE-OFF_2026-06-11.icc"


def test_paper_name_survives_in_sidecar_for_autonomy():
    path, _ = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="Un Papier Disparu", label="x", gloss_slot="ON",
    )
    assert cache.read_repo_z9_profile_meta(path)["paper_name"] == "Un Papier Disparu"


def test_filename_collision_keep_both_gets_numeric_suffix():
    # "Keep both" (on_conflict=keep_both) = -N uniqueness behavior (historical).
    a, _ = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="Photo standard", gloss_slot="OFF", on_conflict="keep_both")
    b, _ = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="Photo standard", gloss_slot="OFF", on_conflict="keep_both")
    assert a.name == "HPZ9_photo-standard_GE-OFF.icc"
    assert b.name == "HPZ9_photo-standard_GE-OFF-2.icc"      # uniqueness: suffix -2 UNCHANGED
    assert a != b


def test_save_default_raises_on_collision():
    # SAFE default (on_conflict None = cancel): collision → ProfileNameConflictError, NO
    # write (the caller/route returns 409 + suggestion). First write OK (no collision).
    cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="Photo standard", gloss_slot="OFF")
    with pytest.raises(cache.ProfileNameConflictError) as exc:
        cache.save_repo_z9_profile(
            icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
            paper_name="P", label="Photo standard", gloss_slot="OFF")
    assert exc.value.name == "HPZ9_photo-standard_GE-OFF"
    assert exc.value.suggestion == "HPZ9_photo-standard_GE-OFF-2"
    # only one .icc written (the 2nd raised before writing)
    paper_dir = cache.repo_z9_paper_dir(SERIAL, MEDIA)
    assert len(list(paper_dir.glob("*.icc"))) == 1


def test_save_replace_overwrites_entirely_no_preservation():
    # "Replace" (option A): overwrites ENTIRELY (fresh icc + meta). NO preservation —
    # same name ≠ same profile → grafting the old curation would be a lie. To keep
    # tags/notes, the user chooses "Keep both" (tested elsewhere).
    a, m0 = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="Photo standard", gloss_slot="OFF",
        n_patches=464, purpose_tags=["boom", "youpi"], notes="ma note")
    bigger = ICC + b"\x00" * 50                                   # different content → md5 changes
    b, m1 = cache.save_repo_z9_profile(
        icc_bytes=bigger, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="Photo standard", gloss_slot="OFF",
        n_patches=928, on_conflict="replace")                    # new build WITHOUT tags/notes
    assert b == a                                                # SAME file (overwritten, not -2)
    assert b.read_bytes() == bigger and m1["md5"] == cache.md5_bytes(bigger)
    assert m1["n_patches"] == 928                                # metadata of the NEW profile
    assert m1["purpose_tags"] == []                              # old tags NOT carried over (fresh)
    assert m1["notes"] == ""                                     # old notes NOT carried over
    assert m1["created_at"]                                      # fresh created_at (of the new one)
    assert "updated_at" not in m1                                # FRESH meta, not a merge (proof)
    assert m0["purpose_tags"] == ["boom", "youpi"]               # (the old meta read before overwrite)
    assert len(list(cache.repo_z9_paper_dir(SERIAL, MEDIA).glob("*.icc"))) == 1


def test_name_conflict_helper_detects_without_writing():
    assert cache.repo_z9_name_conflict(SERIAL, MEDIA, "OFF", "Photo standard") is None
    cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="Photo standard", gloss_slot="OFF")
    coll = cache.repo_z9_name_conflict(SERIAL, MEDIA, "OFF", "Photo standard")
    assert coll == {"name": "HPZ9_photo-standard_GE-OFF",
                    "suggestion": "HPZ9_photo-standard_GE-OFF-2"}


def test_list_returns_saved_profiles_with_path_context():
    cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="qm-r1.0", gloss_slot="OFF", method="argyll")
    cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="qh-r0.5", gloss_slot="OFF", method="argyll")
    listed = cache.list_repo_z9_profiles(SERIAL)
    assert len(listed) == 2
    labels = {e["label"] for e in listed}
    assert labels == {"qm-r1.0", "qh-r0.5"}
    for e in listed:
        assert e["serial"] == SERIAL
        assert e["media_id"] == MEDIA
        assert e["filename"].endswith(".icc")
        assert os.path.exists(e["path"])


def test_delete_removes_icc_and_sidecar():
    path, _ = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="x", gloss_slot="OFF")
    sidecar = path.parent / f".{path.name}.meta.json"
    assert path.exists() and sidecar.exists()
    assert cache.delete_repo_z9_profile(SERIAL, MEDIA, path.name) is True
    assert not path.exists() and not sidecar.exists()
    assert cache.delete_repo_z9_profile(SERIAL, MEDIA, path.name) is False


def test_rename_changes_label_only_file_unchanged():
    path, meta0 = cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="qm-r1.0_20260601", gloss_slot="OFF")
    assert path.name == "HPZ9_qm-r1-0_20260601_GE-OFF.icc"
    md5_before = meta0["md5"]
    updated = cache.rename_repo_z9_profile(
        SERIAL, MEDIA, path.name, "Mon beau profil")
    assert updated is not None
    assert updated["label"] == "Mon beau profil"
    assert updated["md5"] == md5_before
    assert "renamed_at" in updated
    # the .icc file keeps its name and content
    assert path.exists()
    assert path.read_bytes() == ICC
    # the sidecar reflects the new label
    assert cache.read_repo_z9_profile_meta(path)["label"] == "Mon beau profil"


def test_rename_missing_returns_none():
    assert cache.rename_repo_z9_profile(SERIAL, MEDIA, "nope.icc", "x") is None


def test_repo_printers_untouched_by_z9_space():
    printers = cache.repo_printers_dir()
    dev = printers / "HP_Designjet_Z9"
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "existing.icc").write_bytes(ICC)
    cache.save_repo_z9_profile(
        icc_bytes=ICC, serial=SERIAL, media_id=MEDIA,
        paper_name="P", label="x", gloss_slot="OFF")
    assert (dev / "existing.icc").exists()
    assert list(printers.glob("z9")) == []
    assert cache.repo_z9_dir().exists()
