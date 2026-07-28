"""Tests of the CRUD HTTP routes of the "personal Z9" space (repo/z9/).

Covers:
- GET    /api/profiles/z9            (tree grouped by paper)
- POST   /api/profiles/z9            (stores a profile, anti-collision, chosen name)
- DELETE /api/profiles/z9            (deletion)
- POST   /api/profiles/z9/rename     (label rename, filename unchanged)
- POST   /api/profiles/z9/tags       (purpose tags, strict ASCII)
- GET    /api/profiles/z9/export     (direct download of the .icc)

POST /z9 is the generic disk storage (layers 1/2/3 in the sidecar),
shared: called directly by the Measurements build (charts.py) and exposed here over
HTTP. (Refine-with-Argyll was removed; these routes survive it as is.)

Isolation: FREEGLAZ_STORE_ROOT pointed to tmp_path by fixture.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path))
    from webapp.backend.routes.profiles import router
    a = FastAPI()
    a.include_router(router)
    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def _minimal_icc(extra: bytes = b"\x00" * 64) -> bytes:
    head = bytearray(128)
    head[36:40] = b"acsp"
    return bytes(head) + extra


# ─── CRUD /z9 ─────────────────────────────────────────────────────────


def test_z9_empty_skeleton(client, tmp_path):
    r = client.get("/api/profiles/z9")
    assert r.status_code == 200
    data = r.json()
    assert data["serials"] == []
    assert data["repo_z9_dir"].endswith("repo/z9")


def test_z9_range_then_list_grouped(client, tmp_path):
    icc = tmp_path / "variant.icc"
    icc.write_bytes(_minimal_icc())
    body = {
        "source_path": str(icc),
        "serial": "CNXXXXXXXX",
        "media_id": "157F2E9355D517302ABB75C16029A140",
        "paper_name": "Canson Photolustre RC 2021",
        "label": "Photo standard qm-r1.0",
        "gloss_slot": "OFF",
        "method": "argyll", "method_flags": "-v -qm -r1.0",
        "n_patches": 928, "source_profile": "base.icc",
        "purpose_tags": ["portrait"], "notes": "essai 1",
    }
    r = client.post("/api/profiles/z9", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    assert out["filename"] == "HPZ9_photo-standard-qm-r1-0_GE-OFF.icc"   # printer + GE after paper
    assert out["meta"]["paper_name"] == "Canson Photolustre RC 2021"
    assert out["meta"]["purpose_tags"] == ["portrait"]

    r2 = client.get("/api/profiles/z9")
    serials = r2.json()["serials"]
    assert len(serials) == 1
    s = serials[0]
    assert s["serial"] == "CNXXXXXXXX"
    assert s["n_papers"] == 1
    paper = s["papers"][0]
    assert paper["paper_name"] == "Canson Photolustre RC 2021"
    assert len(paper["profiles"]) == 1
    assert paper["profiles"][0]["label"] == "Photo standard qm-r1.0"


def test_z9_range_collision_409_without_intention(client, tmp_path):
    # OS paradigm: without on_conflict, a collision (even on AUTO LABEL) → 409 + suggestion
    # (no more silent -N). "Keep both" (keep_both) restores the explicit -N suffix.
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    common = {
        "source_path": str(icc), "serial": "CNXXXXXXXX",
        "media_id": "AABBCCDDEEFF00112233445566778899",
        "paper_name": "P", "label": "Photo standard", "gloss_slot": "OFF",
    }
    r1 = client.post("/api/profiles/z9", json=common)
    assert r1.status_code == 200
    assert r1.json()["filename"] == "HPZ9_photo-standard_GE-OFF.icc"
    # 2nd without intention → 409 + suggestion -2
    r2 = client.post("/api/profiles/z9", json=common)
    assert r2.status_code == 409, r2.text
    det = r2.json()["detail"]
    assert det["name"] == "HPZ9_photo-standard_GE-OFF"
    assert det["suggestion"] == "HPZ9_photo-standard_GE-OFF-2"
    # keep_both → explicit -2 suffix
    r3 = client.post("/api/profiles/z9", json={**common, "on_conflict": "keep_both"})
    assert r3.status_code == 200
    assert r3.json()["filename"] == "HPZ9_photo-standard_GE-OFF-2.icc"


def test_z9_range_replace_overwrites_same_file(client, tmp_path):
    # "Replace": writes the SAME name (no -2), overwrites the repo/z9 copy.
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    common = {
        "source_path": str(icc), "serial": "CNXXXXXXXX",
        "media_id": "AABBCCDDEEFF00112233445566778899",
        "paper_name": "P", "label": "Photo standard", "gloss_slot": "OFF",
    }
    r1 = client.post("/api/profiles/z9", json=common)
    r2 = client.post("/api/profiles/z9", json={**common, "on_conflict": "replace"})
    assert r1.status_code == 200 and r2.status_code == 200, r2.text
    assert r1.json()["filename"] == r2.json()["filename"] == "HPZ9_photo-standard_GE-OFF.icc"


# ─── /z9: CHOSEN name (path A policy, same as the Measurements build) ──


def test_z9_range_custom_name_used_as_exact_filename(client, tmp_path):
    # Chosen name → filename = slugify(name) EXACT (not the HPZ9_..._GE format), label = name.
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "CNXXXXXXXX",
        "media_id": "AABBCCDDEEFF00112233445566778899",
        "paper_name": "P", "label": "qm-r1.0_2026-06-19", "gloss_slot": "OFF",
        "name": "My Portrait Profile"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["filename"] == "my-portrait-profile.icc"     # exact name, not HPZ9_..._GE
    assert out["meta"]["label"] == "My Portrait Profile"


def test_z9_range_custom_name_collision_refused_409(client, tmp_path):
    # Collision on a CHOSEN name → 409 + suggestion -N (never a silent -N).
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    common = {
        "source_path": str(icc), "serial": "CNXXXXXXXX",
        "media_id": "AABBCCDDEEFF00112233445566778899",
        "paper_name": "P", "label": "x", "gloss_slot": "OFF", "name": "Mon Profil"}
    r1 = client.post("/api/profiles/z9", json=common)
    r2 = client.post("/api/profiles/z9", json=common)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 409, r2.text
    det = r2.json()["detail"]
    assert det["name"] == "mon-profil"
    assert det["suggestion"] == "mon-profil-2"


def test_z9_range_custom_name_rejects_non_ascii(client, tmp_path):
    # Free input → strict ASCII (provenance rule): accent refused (400).
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S", "media_id": "M",
        "paper_name": "P", "label": "x", "name": "Profil accentué"})
    assert r.status_code == 400, r.text


def test_z9_range_custom_name_too_long_422(client, tmp_path):
    # Length bounded to 63 (ICC V2 desc) — Pydantic max_length → 422.
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S", "media_id": "M",
        "paper_name": "P", "label": "x", "name": "x" * 64})
    assert r.status_code == 422, r.text


def test_z9_range_no_name_keeps_auto_label(client, tmp_path):
    # Non-regression: without `name`, auto label unchanged (HPZ9_..._GE format, silent -N suffix).
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "CNXXXXXXXX",
        "media_id": "AABBCCDDEEFF00112233445566778899",
        "paper_name": "P", "label": "Photo standard", "gloss_slot": "OFF"})
    assert r.status_code == 200, r.text
    assert r.json()["filename"] == "HPZ9_photo-standard_GE-OFF.icc"


def test_z9_range_rejects_non_icc(client, tmp_path):
    bad = tmp_path / "bad.icc"
    bad.write_bytes(b"\x00" * 500)
    r = client.post("/api/profiles/z9", json={
        "source_path": str(bad), "serial": "S", "media_id": "M",
        "paper_name": "P", "label": "x"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "icc_invalid_signature"


def test_z9_range_404_missing_source(client):
    r = client.post("/api/profiles/z9", json={
        "source_path": "/tmp/nope-freeglaz.icc", "serial": "S",
        "media_id": "M", "paper_name": "P", "label": "x"})
    assert r.status_code == 404


def test_z9_delete(client, tmp_path):
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S1", "media_id": "M1",
        "paper_name": "P", "label": "x", "gloss_slot": "OFF"})
    fname = r.json()["filename"]
    rd = client.delete("/api/profiles/z9",
                       params={"serial": "S1", "media_id": "M1", "filename": fname})
    assert rd.status_code == 200
    assert client.get("/api/profiles/z9").json()["serials"] == []


def test_z9_delete_404_on_missing(client):
    r = client.delete("/api/profiles/z9",
                      params={"serial": "S", "media_id": "M", "filename": "nope.icc"})
    assert r.status_code == 404


def test_z9_rename_changes_label_keeps_filename(client, tmp_path):
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S1", "media_id": "M1",
        "paper_name": "P", "label": "qm-r1.0_20260601", "gloss_slot": "OFF"})
    fname = r.json()["filename"]
    rr = client.post("/api/profiles/z9/rename", json={
        "serial": "S1", "media_id": "M1", "filename": fname,
        "new_label": "Photo douce"})
    assert rr.status_code == 200, rr.text
    assert rr.json()["label"] == "Photo douce"
    # the list reflects the new label, the filename is unchanged
    paper = client.get("/api/profiles/z9").json()["serials"][0]["papers"][0]
    prof = paper["profiles"][0]
    assert prof["label"] == "Photo douce"
    assert prof["filename"] == fname


def test_z9_rename_404_on_missing(client):
    r = client.post("/api/profiles/z9/rename", json={
        "serial": "S", "media_id": "M", "filename": "nope.icc",
        "new_label": "x"})
    assert r.status_code == 404


def test_z9_rename_422_on_empty_label(client, tmp_path):
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S1", "media_id": "M1",
        "paper_name": "P", "label": "x", "gloss_slot": "OFF"})
    fname = r.json()["filename"]
    rr = client.post("/api/profiles/z9/rename", json={
        "serial": "S1", "media_id": "M1", "filename": fname, "new_label": "   "})
    assert rr.status_code == 422


def test_z9_rename_422_on_non_ascii_label(client, tmp_path):
    # Free input (case 1): an accented label is REFUSED (422), no strip.
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S1", "media_id": "M1",
        "paper_name": "P", "label": "x", "gloss_slot": "OFF"})
    fname = r.json()["filename"]
    rr = client.post("/api/profiles/z9/rename", json={
        "serial": "S1", "media_id": "M1", "filename": fname, "new_label": "Hahnemühle"})
    assert rr.status_code == 422


def test_z9_set_tags_normalizes_and_refuses_non_ascii(client, tmp_path):
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S1", "media_id": "M1",
        "paper_name": "P", "label": "x", "gloss_slot": "OFF"})
    fname = r.json()["filename"]
    # trim + dedup + removal of empties
    rr = client.post("/api/profiles/z9/tags", json={
        "serial": "S1", "media_id": "M1", "filename": fname,
        "tags": [" portrait ", "portrait", "", "mat"]})
    assert rr.status_code == 200
    assert rr.json()["purpose_tags"] == ["portrait", "mat"]
    # strict ASCII: an accented tag is refused (422)
    bad = client.post("/api/profiles/z9/tags", json={
        "serial": "S1", "media_id": "M1", "filename": fname, "tags": ["café"]})
    assert bad.status_code == 422


# ─── Export of a repo/z9 profile (direct download) ──────


def test_z9_export_returns_icc_attachment(client, tmp_path):
    icc = tmp_path / "v.icc"
    icc.write_bytes(_minimal_icc(b"EXPORTME"))
    r = client.post("/api/profiles/z9", json={
        "source_path": str(icc), "serial": "S1", "media_id": "M1",
        "paper_name": "Baryta", "label": "Photo douce qm-r1.0", "gloss_slot": "OFF"})
    fname = r.json()["filename"]
    rx = client.get("/api/profiles/z9/export",
                    params={"serial": "S1", "media_id": "M1", "filename": fname})
    assert rx.status_code == 200, rx.text
    assert rx.headers["content-type"] == "application/vnd.iccprofile"
    cd = rx.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "Photo douce qm-r1.0.icc" in cd
    assert rx.content == _minimal_icc(b"EXPORTME")


def test_z9_export_404_on_missing(client):
    r = client.get("/api/profiles/z9/export",
                   params={"serial": "S", "media_id": "M", "filename": "nope.icc"})
    assert r.status_code == 404


def test_z9_export_rejects_traversal_filename(client):
    r = client.get("/api/profiles/z9/export",
                   params={"serial": "S1", "media_id": "M1",
                           "filename": "../../escape.icc"})
    assert r.status_code in (403, 404, 422)
