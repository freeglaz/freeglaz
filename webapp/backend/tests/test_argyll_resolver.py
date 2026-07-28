"""PERSISTENT resolver for the Argyll binaries AND ref/ data (lib/z9_client/argyll).

Covers the cascade (explicit → env ROOT/BIN/REF → toml [argyll] → system paths →
PATH) for both binaries and the reference-data directory, and above all the case
of the P2 bug: resolution from a MINIMAL PATH (env of a GUI app without the
shell's PATH) still finds the binary via the system paths / the env override.
"""
from pathlib import Path

import pytest

from lib.z9_client.argyll import (
    ArgyllNotFound, REQUIRED_BINARIES, find_argyll_binary, resolve_argyll_binary,
    find_argyll_ref_dir, resolve_argyll_ref_dir, check_argyll)


@pytest.fixture(autouse=True)
def _clean_argyll_env(monkeypatch):
    """Isolate every test from the ambient env and the real ~/.freeglazrc.toml so
    the cascade is exercised deterministically (no leakage from the dev machine)."""
    for var in ("FREEGLAZ_ARGYLL_ROOT", "FREEGLAZ_ARGYLL_BIN", "FREEGLAZ_ARGYLL_REF",
                "FREEGLAZ_ARGYLL_COLPROF", "ARGYLL_BIN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("lib.z9_client.argyll._argyll_config", lambda: {})


def _fake_exe(d: Path, name: str) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text("#!/bin/sh\nexit 0\n")
    p.chmod(0o755)
    return p


# ─── binary resolution ───────────────────────────────────────────────────────


def test_minimal_path_resolves_via_system_dir(tmp_path, monkeypatch):
    # Simulate the env of a GUI app: EMPTY PATH (no /opt/homebrew/bin). The binary
    # is in a "system path" → resolved despite the minimal PATH (core of the P2 bug).
    sysbin = tmp_path / "sysbin"
    _fake_exe(sysbin, "colprof")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [str(sysbin)])
    assert resolve_argyll_binary("colprof") == str(sysbin / "colprof")


def test_explicit_env_bin_dir_wins(tmp_path, monkeypatch):
    envbin = tmp_path / "envbin"
    _fake_exe(envbin, "targen")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(envbin))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("targen") == str(envbin / "targen")


def test_env_root_derives_bin(tmp_path, monkeypatch):
    # FREEGLAZ_ARGYLL_ROOT points at a self-contained install → bin under <root>/bin.
    root = tmp_path / "argyll"
    _fake_exe(root / "bin", "targen")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_ROOT", str(root))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("targen") == str(root / "bin" / "targen")


def test_toml_bin_dir_used(tmp_path, monkeypatch):
    # [argyll] bin_dir in ~/.freeglazrc.toml (lower priority than env, higher than auto).
    tomlbin = tmp_path / "tomlbin"
    _fake_exe(tomlbin, "colprof")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("lib.z9_client.argyll._argyll_config",
                        lambda: {"bin_dir": str(tomlbin)})
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("colprof") == str(tomlbin / "colprof")


def test_env_bin_beats_toml(tmp_path, monkeypatch):
    envbin = tmp_path / "envbin"
    _fake_exe(envbin, "colprof")
    tomlbin = tmp_path / "tomlbin"
    _fake_exe(tomlbin, "colprof")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(envbin))
    monkeypatch.setattr("lib.z9_client.argyll._argyll_config",
                        lambda: {"bin_dir": str(tomlbin)})
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("colprof") == str(envbin / "colprof")


def test_legacy_argyll_bin_env(tmp_path, monkeypatch):
    # Backward compat: bare ARGYLL_BIN still resolves (lower priority than the auto paths).
    legacy = tmp_path / "legacy"
    _fake_exe(legacy, "colprof")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ARGYLL_BIN", str(legacy))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("colprof") == str(legacy / "colprof")


def test_per_binary_env_override_wins(tmp_path, monkeypatch):
    custom = _fake_exe(tmp_path / "custom", "colprof")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_COLPROF", str(custom))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: ["/opt/homebrew/bin"])
    assert find_argyll_binary("colprof") == str(custom)


def test_path_fallback(tmp_path, monkeypatch):
    pathbin = tmp_path / "pathbin"
    _fake_exe(pathbin, "spec2cie")
    monkeypatch.setenv("PATH", str(pathbin))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("spec2cie") == str(pathbin / "spec2cie")


def test_explicit_path_arg_honored(tmp_path, monkeypatch):
    # An explicit path passed by the caller (e.g. absolute colprof_path) is honored as
    # is; if it is pinned but absent → None (no silent substitution).
    exe = _fake_exe(tmp_path / "b", "colprof")
    assert find_argyll_binary(str(exe)) == str(exe)
    assert find_argyll_binary(str(tmp_path / "b" / "absent")) is None


def test_not_found_raises_actionable(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    with pytest.raises(ArgyllNotFound) as exc:
        resolve_argyll_binary("colprof")
    assert "Argyll CMS binary not found" in str(exc.value)
    assert "FREEGLAZ_ARGYLL_ROOT" in str(exc.value)        # actionable message
    assert "FREEGLAZ_ARGYLL_BIN" in str(exc.value)


def test_find_returns_none_when_absent(monkeypatch):
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    assert find_argyll_binary("colprof") is None


# ─── ref/ data resolution ────────────────────────────────────────────────────


def test_env_ref_dir_wins(tmp_path, monkeypatch):
    ref = tmp_path / "ref"
    ref.mkdir()
    monkeypatch.setenv("FREEGLAZ_ARGYLL_REF", str(ref))
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    assert find_argyll_ref_dir() == str(ref)


def test_env_root_derives_ref(tmp_path, monkeypatch):
    root = tmp_path / "argyll"
    (root / "ref").mkdir(parents=True)
    monkeypatch.setenv("FREEGLAZ_ARGYLL_ROOT", str(root))
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    assert find_argyll_ref_dir() == str(root / "ref")


def test_toml_ref_dir_used(tmp_path, monkeypatch):
    ref = tmp_path / "tomlref"
    ref.mkdir()
    monkeypatch.setattr("lib.z9_client.argyll._argyll_config",
                        lambda: {"ref_dir": str(ref)})
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    assert find_argyll_ref_dir() == str(ref)


def test_ref_system_dir_autodetect(tmp_path, monkeypatch):
    ref = tmp_path / "sysref"
    ref.mkdir()
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [str(ref)])
    assert find_argyll_ref_dir() == str(ref)


def test_ref_absent_returns_none(monkeypatch):
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    assert find_argyll_ref_dir() is None


def test_ref_not_found_raises_actionable(monkeypatch):
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    with pytest.raises(ArgyllNotFound) as exc:
        resolve_argyll_ref_dir()
    assert "reference data" in str(exc.value)
    assert "FREEGLAZ_ARGYLL_REF" in str(exc.value)


# ─── combined availability check ─────────────────────────────────────────────


def _full_argyll(tmp_path):
    """Build a self-contained fake Argyll (all required bins + a ref/ with a
    witness .cht) and return (bin_dir, ref_dir)."""
    bindir = tmp_path / "bin"
    for name in REQUIRED_BINARIES:
        _fake_exe(bindir, name)
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "ColorChecker.cht").write_text("CHT\n")
    return bindir, ref


def test_check_all_present(tmp_path, monkeypatch):
    bindir, ref = _full_argyll(tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(bindir))
    monkeypatch.setenv("FREEGLAZ_ARGYLL_REF", str(ref))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    rep = check_argyll()
    assert rep["ok"] and rep["bin_ok"] and rep["ref_ok"]
    assert rep["missing"] == []
    assert rep["bin_dir"] == str(bindir)
    assert rep["ref_dir"] == str(ref)


def test_check_missing_ref(tmp_path, monkeypatch):
    bindir, _ = _full_argyll(tmp_path)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(bindir))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    rep = check_argyll()
    assert rep["bin_ok"] and not rep["ref_ok"] and not rep["ok"]
    assert rep["missing"] == ["ref"]


def test_check_ref_dir_without_witness_is_not_ok(tmp_path, monkeypatch):
    # A ref dir that exists but holds no chart definition (*.cht) is NOT usable.
    bindir, _ = _full_argyll(tmp_path)
    empty_ref = tmp_path / "empty_ref"
    empty_ref.mkdir()
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(bindir))
    monkeypatch.setenv("FREEGLAZ_ARGYLL_REF", str(empty_ref))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    rep = check_argyll()
    assert not rep["ref_ok"] and "ref" in rep["missing"]


def test_check_missing_binaries_listed(tmp_path, monkeypatch):
    # Only targen present → the other required binaries are reported missing by name.
    bindir = tmp_path / "partialbin"
    _fake_exe(bindir, "targen")
    ref = tmp_path / "ref"
    ref.mkdir()
    (ref / "ColorChecker.cht").write_text("CHT\n")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(bindir))
    monkeypatch.setenv("FREEGLAZ_ARGYLL_REF", str(ref))
    monkeypatch.setattr("lib.z9_client.argyll._system_dirs", lambda: [])
    monkeypatch.setattr("lib.z9_client.argyll._system_ref_dirs", lambda: [])
    rep = check_argyll()
    assert not rep["bin_ok"]
    assert "colprof" in rep["missing"] and "targen" not in rep["missing"]
