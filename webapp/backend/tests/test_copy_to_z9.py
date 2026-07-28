"""The "copy" button places the profile in the dedicated Z9 repo (per
paper), NOT in repo/printers/Z9, and WITHOUT a double .icc extension.

POST /api/profiles/mirror/copy-to-z9 derives serial/media_id/GE/paper_name from
the mirror path + _paper.json, then writes into repo/z9/<serial>/papers/<media_id>/
via save_repo_z9_profile (name rebuilt cleanly).
"""
from __future__ import annotations

import json
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


def _minimal_icc() -> bytes:
    head = bytearray(128)
    head[36:40] = b"acsp"
    return bytes(head) + b"\x00" * 64


SERIAL = "CNXXXXXXXX"
SLUG = "hahnemuhle-fine-art-baryta-satin"
MEDIA_ID = "355783D031D38CD4F1D8B8426FE3C51A"
# The mirror file ALREADY carries .icc (the double-extension trap).
ICC_NAME = "freeglaz roundtrip test 2026-05-13.icc"


def _seed_mirror(tmp_path: Path) -> Path:
    """Create a minimal mirror paper (1 custom FULLPAGE profile) and return the
    absolute path of the ICC."""
    paper_dir = tmp_path / "mirror" / SERIAL / "papers" / SLUG
    paper_dir.mkdir(parents=True, exist_ok=True)
    icc = paper_dir / ICC_NAME
    icc.write_bytes(_minimal_icc())
    (paper_dir / "_paper.json").write_text(json.dumps({
        "paper_id": MEDIA_ID,
        "paper_name": "Hahnemühle Fine Art Baryta Satin",
        "profiles": [{
            "filename": ICC_NAME,
            "gloss_enhancer": "FULLPAGE",
            "color_space": "PRINTER_RGB",
            "custom": True,
        }],
    }), encoding="utf-8")
    return icc


def test_copy_to_z9_lands_in_dedicated_repo_single_ext(client, tmp_path):
    icc = _seed_mirror(tmp_path)
    r = client.post("/api/profiles/mirror/copy-to-z9",
                    json={"source_path": str(icc), "label": "Baryta Satin · GE FULLPAGE (custom)"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    dest = Path(out["absolute_path"])

    # 1. Destination = repo/z9/<serial>/papers/<media_id>/ (NOT repo/printers/Z9)
    assert dest.parts[-4:-1] == (SERIAL, "papers", MEDIA_ID), dest
    assert "repo/z9/" in dest.as_posix()
    assert "printers/Z9" not in dest.as_posix()
    assert not (tmp_path / "repo" / "printers" / "Z9").exists()

    # 2. SINGLE .icc extension (not .icc.icc)
    assert dest.name.endswith(".icc")
    assert not dest.name.endswith(".icc.icc")
    assert dest.exists()

    # 3. Metadata derived correctly + origin mirror_copy
    assert out["serial"] == SERIAL
    assert out["media_id"] == MEDIA_ID
    assert out["meta"]["gloss_slot"] in ("FULLPAGE", "ON")  # _norm_ge
    assert out["meta"]["origin"] == "mirror_copy"

    # 4. Visible in the Z9 tree grouped by paper
    tree = client.get("/api/profiles/z9").json()
    assert any(s["serial"] == SERIAL for s in tree["serials"])


def test_copy_to_z9_rejects_non_mirror_source(client, tmp_path):
    outside = tmp_path / "elsewhere.icc"
    outside.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/mirror/copy-to-z9",
                    json={"source_path": str(outside)})
    assert r.status_code == 403


def test_copy_to_z9_404_on_missing_manifest(client, tmp_path):
    # ICC under mirror but without _paper.json
    paper_dir = tmp_path / "mirror" / SERIAL / "papers" / "orphan"
    paper_dir.mkdir(parents=True, exist_ok=True)
    icc = paper_dir / "x.icc"
    icc.write_bytes(_minimal_icc())
    r = client.post("/api/profiles/mirror/copy-to-z9", json={"source_path": str(icc)})
    assert r.status_code == 404
