# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Argyll settings service + endpoint — focus on the empty-root RESET path.

An empty root is a valid intent ("go back to auto-detection"), NOT an error: it
clears the GUI-managed [argyll].root from the TOML while preserving every other
section, and the cascade falls back to the system/Homebrew Argyll.
"""
import tomllib

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.services import argyll_settings


@pytest.fixture
def toml_path(tmp_path, monkeypatch):
    """Redirect the config store to a throwaway file — never touch the real
    ~/.freeglazrc.toml. Patched at the service's import site."""
    p = tmp_path / ".freeglazrc.toml"
    monkeypatch.setattr(argyll_settings, "get_config_path", lambda: p)
    return p


# ── service: clear_root ────────────────────────────────────────────────

def test_clear_root_drops_argyll_section_when_only_root(toml_path):
    toml_path.write_text('[argyll]\nroot = "/opt/argyll"\n')
    argyll_settings.clear_root()
    data = tomllib.loads(toml_path.read_text())
    assert "argyll" not in data            # section removed entirely
    assert argyll_settings.read_root() is None


def test_clear_root_preserves_other_sections(toml_path):
    toml_path.write_text(
        '[argyll]\nroot = "/opt/argyll"\nbin_dir = "/opt/argyll/bin"\n'
        '[colprof]\nquality = "high"\n')
    argyll_settings.clear_root()
    data = tomllib.loads(toml_path.read_text())
    assert "argyll" not in data
    assert data["colprof"] == {"quality": "high"}   # untouched


def test_clear_root_keeps_argyll_with_foreign_keys(toml_path):
    # If [argyll] carries a key we don't manage, drop only root/bin_dir/ref_dir.
    toml_path.write_text('[argyll]\nroot = "/opt/argyll"\nfuture = "keep-me"\n')
    argyll_settings.clear_root()
    data = tomllib.loads(toml_path.read_text())
    assert data["argyll"] == {"future": "keep-me"}


def test_clear_root_noop_when_no_file(toml_path):
    assert not toml_path.exists()
    argyll_settings.clear_root()             # must not raise / must not create
    assert not toml_path.exists()


def test_write_then_clear_round_trip(toml_path):
    argyll_settings.write_root("/opt/argyll")
    assert argyll_settings.read_root() == "/opt/argyll"
    argyll_settings.clear_root()
    assert argyll_settings.read_root() is None


# ── endpoint: PUT with empty root = reset, not 422 ─────────────────────

@pytest.fixture
def client():
    return TestClient(app)


def test_put_empty_root_resets_to_autodetection(client, toml_path, monkeypatch):
    # Env must not shadow the reset for the assertion to be meaningful.
    for k in ("FREEGLAZ_ARGYLL_ROOT", "FREEGLAZ_ARGYLL_BIN",
              "FREEGLAZ_ARGYLL_REF", "ARGYLL_BIN"):
        monkeypatch.delenv(k, raising=False)
    toml_path.write_text('[argyll]\nroot = "/opt/argyll"\n')

    r = client.put("/api/settings/argyll", json={"root": ""})
    assert r.status_code == 200                    # NOT 422
    assert r.json()["root"] is None                # custom root cleared
    assert "argyll" not in tomllib.loads(toml_path.read_text())


def test_put_absent_root_also_resets(client, toml_path, monkeypatch):
    for k in ("FREEGLAZ_ARGYLL_ROOT", "FREEGLAZ_ARGYLL_BIN",
              "FREEGLAZ_ARGYLL_REF", "ARGYLL_BIN"):
        monkeypatch.delenv(k, raising=False)
    toml_path.write_text('[argyll]\nroot = "/opt/argyll"\n')

    r = client.put("/api/settings/argyll", json={})
    assert r.status_code == 200
    assert r.json()["root"] is None


def test_put_invalid_nonempty_root_still_422(client, toml_path):
    r = client.put("/api/settings/argyll", json={"root": str(toml_path.parent / "nope")})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "argyll_root_invalid"
