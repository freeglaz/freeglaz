"""Tests for /api/profiles/store routes + repo CRUD + mirror copy.

Covers:
- GET /api/profiles/store (pure filesystem read)
- POST /api/profiles/repo (upload ICC into repo)
- DELETE /api/profiles/repo (removal)
- PATCH /api/profiles/repo (rename/move)
- POST /api/profiles/mirror/copy-to-repo
- Anti path-traversal (../)
- Reinjection refusal (conflict 409)

Isolation: FREEGLAZ_STORE_ROOT pointed at tmp_path by fixture.
"""
from __future__ import annotations

import io
import pytest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_minimal_icc(extra: bytes = b"\x00" * 64) -> bytes:
    """Forge a minimal ICC file: 128 bytes of header + `acsp` signature."""
    # Header: 128 bytes. 'acsp' signature at offset 36.
    head = bytearray(128)
    head[36:40] = b"acsp"
    return bytes(head) + extra


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    # Import after monkeypatch so root_dir() points at tmp_path
    from webapp.backend.routes.profiles import router
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


# ─── GET /api/profiles/store ──────────────────────────────────────────


def test_get_store_returns_skeleton_when_empty(client, tmp_path):
    r = client.get("/api/profiles/store")
    assert r.status_code == 200
    data = r.json()
    assert data["store_root"] == str(tmp_path)
    assert data["mirrors"] == []
    assert data["repo"] == {"printers": [], "displays": [], "workingspaces": []}


def test_get_store_lists_mirror_after_save(client, tmp_path):
    from lib.z9_client import cache
    serial = "CN1234X"
    paper = {"id": "X", "name": "Test Paper", "donor_id": "2090",
             "is_factory": False, "is_user_custom": True,
             "category_id": "CUSTOM", "category_name": "Custom"}
    cache.save_mirror_profile(
        icc_bytes=_make_minimal_icc(),
        serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    r = client.get("/api/profiles/store")
    data = r.json()
    assert len(data["mirrors"]) == 1
    m = data["mirrors"][0]
    assert m["serial"] == serial
    assert m["n_papers"] == 1
    paper_entry = m["papers"][0]
    assert paper_entry["donor_paper_id"] == "2090"
    assert len(paper_entry["profiles"]) == 1
    prof = paper_entry["profiles"][0]
    assert prof["custom"] is False
    assert prof["gloss_enhancer"] == "OFF"
    assert prof["filename"].endswith(".freeglaz-factory.icc")


# ─── POST /api/profiles/repo ──────────────────────────────────────────


def test_upload_workingspace_writes_file_and_meta(client, tmp_path):
    icc = _make_minimal_icc()
    r = client.post("/api/profiles/repo",
                    files={"file": ("Rec2020.icc", icc, "application/octet-stream")},
                    data={"category": "workingspaces", "display_name": "Rec.2020"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    p = Path(data["absolute_path"])
    assert p.exists()
    assert p.read_bytes() == icc
    meta = p.parent / f".{p.name}.meta.json"
    assert meta.exists()


def test_upload_printers_requires_device(client):
    r = client.post("/api/profiles/repo",
                    files={"file": ("x.icc", _make_minimal_icc(), "application/octet-stream")},
                    data={"category": "printers"})
    assert r.status_code == 422


def test_upload_rejects_non_icc_signature(client):
    r = client.post("/api/profiles/repo",
                    files={"file": ("fake.icc", b"\x00" * 500, "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "icc_invalid_signature"


def test_upload_accepts_spaces_and_accents_in_filename(client):
    """Filename with spaces/accents is accepted."""
    icc = _make_minimal_icc()
    r = client.post("/api/profiles/repo",
                    files={"file": ("Mon Écran Eizo (2026).icc", icc,
                                    "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 200, r.text
    path = Path(r.json()["absolute_path"])
    assert path.exists()
    # The name is preserved as-is (not slugified)
    assert path.name == "Mon Écran Eizo (2026).icc"


def test_safe_filename_helper_rejects_separator_directly():
    """The _safe_filename helper rejects forbidden characters.

    The multipart upload strips NULL/separators at the HTTP level before
    reaching the code. The guarded paths are DELETE/PATCH which receive
    the raw filename in query/body and pass through _safe_filename — we
    cover the invariant directly on the helper."""
    from webapp.backend.routes.profiles import _safe_filename
    from fastapi import HTTPException
    # Accepts usual characters
    assert _safe_filename("Mon Écran Eizo (2026).icc", "x") \
        == "Mon Écran Eizo (2026).icc"
    # Rejects separators / dangerous characters
    for bad in ("a/b.icc", "a\\b.icc", "a\x00b.icc", "a*b.icc",
                "a\nb.icc", "..", ".", ".hidden.icc"):
        with pytest.raises(HTTPException) as exc:
            _safe_filename(bad, "x")
        assert exc.value.status_code == 422


def test_delete_rejects_filename_with_separator(client):
    """DELETE rejects a filename containing '/' (anti-traversal)."""
    r = client.delete("/api/profiles/repo",
                      params={"category": "workingspaces",
                              "filename": "../escape.icc"})
    assert r.status_code in (422, 404)


def test_patch_rejects_new_filename_with_separator(client):
    """PATCH rejects new_filename with a separator."""
    icc = _make_minimal_icc()
    r = client.post("/api/profiles/repo",
                    files={"file": ("a.icc", icc, "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 200
    r2 = client.patch("/api/profiles/repo",
                      json={"category": "workingspaces", "filename": "a.icc",
                            "new_filename": "../escape.icc"})
    assert r2.status_code == 422


def test_upload_strips_path_components_safely(client, tmp_path):
    """Path traversal mitigated by Path(filename).name (basename only)
    before _safe_segment validation. The file is written at the safe path,
    not outside the repo root."""
    r = client.post("/api/profiles/repo",
                    files={"file": ("../../escape.icc", _make_minimal_icc(),
                                    "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 200, r.text
    dest = Path(r.json()["absolute_path"])
    # The file must be under repo/workingspaces/, not escaped upward.
    assert "repo/workingspaces" in str(dest)
    assert dest.name == "escape.icc"
    # Anti-traversal effective: the file is indeed within tmp_path.
    assert str(tmp_path) in str(dest)


def test_upload_conflict_on_existing_file(client):
    icc = _make_minimal_icc()
    r1 = client.post("/api/profiles/repo",
                     files={"file": ("dup.icc", icc, "application/octet-stream")},
                     data={"category": "workingspaces"})
    assert r1.status_code == 200
    r2 = client.post("/api/profiles/repo",
                     files={"file": ("dup.icc", icc, "application/octet-stream")},
                     data={"category": "workingspaces"})
    assert r2.status_code == 409


# ─── DELETE /api/profiles/repo ────────────────────────────────────────


def test_delete_removes_file_and_meta(client):
    icc = _make_minimal_icc()
    r = client.post("/api/profiles/repo",
                    files={"file": ("del.icc", icc, "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 200
    p = Path(r.json()["absolute_path"])
    meta = p.parent / f".{p.name}.meta.json"
    assert p.exists() and meta.exists()

    r2 = client.delete("/api/profiles/repo",
                       params={"category": "workingspaces", "filename": "del.icc"})
    assert r2.status_code == 200
    assert not p.exists()
    assert not meta.exists()


def test_delete_404_on_missing(client):
    r = client.delete("/api/profiles/repo",
                      params={"category": "workingspaces", "filename": "nope.icc"})
    assert r.status_code == 404


# ─── PATCH /api/profiles/repo ─────────────────────────────────────────


def test_patch_renames_within_category(client):
    icc = _make_minimal_icc()
    r = client.post("/api/profiles/repo",
                    files={"file": ("a.icc", icc, "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 200
    r2 = client.patch("/api/profiles/repo",
                      json={"category": "workingspaces", "filename": "a.icc",
                            "new_filename": "b.icc"})
    assert r2.status_code == 200, r2.text
    new_path = Path(r2.json()["absolute_path"])
    assert new_path.exists()
    assert new_path.name == "b.icc"


def test_patch_moves_to_another_category(client):
    icc = _make_minimal_icc()
    r = client.post("/api/profiles/repo",
                    files={"file": ("m.icc", icc, "application/octet-stream")},
                    data={"category": "workingspaces"})
    assert r.status_code == 200
    r2 = client.patch("/api/profiles/repo",
                      json={"category": "workingspaces", "filename": "m.icc",
                            "new_category": "printers", "new_device": "Epson"})
    assert r2.status_code == 200, r2.text
    new_path = Path(r2.json()["absolute_path"])
    assert "printers/Epson" in str(new_path)


# ─── POST /api/profiles/mirror/copy-to-repo ───────────────────────────


def test_copy_mirror_to_repo_preserves_bytes(client, tmp_path):
    from lib.z9_client import cache
    serial = "CNXXXXXXXX"
    paper = {"id": "X", "name": "Photolustre", "donor_id": "2090",
             "is_factory": False, "is_user_custom": True}
    icc = _make_minimal_icc(b"X" * 256)
    path, _entry = cache.save_mirror_profile(
        icc_bytes=icc, serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    assert path.exists()

    r = client.post("/api/profiles/mirror/copy-to-repo",
                    json={"source_path": str(path),
                          "target_category": "printers",
                          "target_device": "Z9",
                          "display_name": "Photolustre factory OFF"})
    assert r.status_code == 200, r.text
    dest = Path(r.json()["absolute_path"])
    assert dest.exists()
    assert dest.read_bytes() == icc  # bit-identical
    assert "repo/printers/Z9" in str(dest)


def test_copy_refuses_source_outside_mirror(client, tmp_path):
    # Create a fake ICC file outside the mirror
    fake = tmp_path / "outside.icc"
    fake.write_bytes(_make_minimal_icc())
    r = client.post("/api/profiles/mirror/copy-to-repo",
                    json={"source_path": str(fake),
                          "target_category": "workingspaces"})
    assert r.status_code == 403


def test_copy_refuses_when_destination_exists(client):
    from lib.z9_client import cache
    serial = "CNXXXXXXXX"
    paper = {"id": "X", "name": "Paper2",
             "is_factory": False, "is_user_custom": True}
    path, _ = cache.save_mirror_profile(
        icc_bytes=_make_minimal_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    r1 = client.post("/api/profiles/mirror/copy-to-repo",
                     json={"source_path": str(path),
                           "target_category": "workingspaces",
                           "target_filename": "copy.icc"})
    assert r1.status_code == 200
    r2 = client.post("/api/profiles/mirror/copy-to-repo",
                     json={"source_path": str(path),
                           "target_category": "workingspaces",
                           "target_filename": "copy.icc"})
    assert r2.status_code == 409


def test_copy_writes_meta_with_mirror_origin(client):
    from lib.z9_client import cache
    serial = "CNXXXXXXXX"
    paper = {"id": "X", "name": "Paper3",
             "is_factory": False, "is_user_custom": True}
    path, _ = cache.save_mirror_profile(
        icc_bytes=_make_minimal_icc(), serial=serial, paper=paper,
        profile={"gloss_enhancer": "OFF", "color_space": "PRINTER_RGB",
                 "custom": False, "uuid": "u", "icc_name": "n", "date": "d"},
    )
    r = client.post("/api/profiles/mirror/copy-to-repo",
                    json={"source_path": str(path),
                          "target_category": "displays",
                          "target_filename": "from-mirror.icc"})
    assert r.status_code == 200
    # Verify via reading /store that the origin is recorded
    r2 = client.get("/api/profiles/store")
    items = r2.json()["repo"]["displays"]
    assert len(items) == 1
    assert items[0]["origin"] == "mirror_copy"
    assert items[0]["origin_detail"] == str(path)
