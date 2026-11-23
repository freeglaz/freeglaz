"""The chartread-fgK engine (lib.z9_client.chartread_fgk.engine): where it is looked for,
how it is checked WITHOUT running it, and the only command line freeglaz builds.

Offline, no instrument: the "binaries" are small shell scripts carrying the patch's text;
if anything ever executed one, it would leave a ``.ran`` file next to it."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import argyll
from lib.z9_client import measurement_identity as mi
from lib.z9_client.chartread_fgk import engine as E
from webapp.backend.main import app
from webapp.backend.routes import charts as charts_routes
from webapp.backend.tests.test_measurement_identity import colormunki_ti3, i1pro3_ti3, ident, record

V3 = "f38d53cadb0b9bb49d5ae0a2d82e0f852f2033eb6cdca81dabf13246a2b68cd8"
V2 = "9229df03207dc46a0cc4fb65974e58865882c5bf6f341282d3ae81a80b17e2d6"
V1 = "21c8f45feda3ddea9a4963495f6fdbfa6a1b130c9fc694f0e78105413bd74510"


def _never(*a, **kw):
    raise AssertionError("the engine must never be run by these checks")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No engine from the developer's environment or ~/.freeglazrc.toml, no Flatpak, and
    no process may be started."""
    monkeypatch.delenv(E.ENV_PATH, raising=False)
    monkeypatch.setattr(argyll, "_argyll_config", lambda: {})
    monkeypatch.setattr(E, "FLATPAK_INFO", tmp_path / "no-flatpak-info")
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, _never)
    for name in ("execv", "execve", "execvp", "execvpe", "posix_spawn", "posix_spawnp", "system"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _never)
    yield
    assert not list(tmp_path.rglob("*.ran")), "an engine was executed"


def make_engine(d, *, markers=None, name=E.ENGINE_NAME, mode=0o755, manifest=True,
                patch=None, build=None, **top):
    """A fake engine file + its manifest. The body is a shell script that would leave a
    ``.ran`` file if run, followed by the marker text."""
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    markers = E.required_markers() if markers is None else markers
    body = b'#!/bin/sh\ntouch "$0.ran"\nexit 0\n# ' + b"\n# ".join(markers) + b"\n"
    p.write_bytes(body)
    p.chmod(mode)
    if manifest:
        man = {"schema": E.MANIFEST_SCHEMA, "schema_version": 1, "binary": name,
               "sha256": hashlib.sha256(body).hexdigest(), "argyll_version": "3.5.0",
               "patch": {"name": "fg-chartread-checkpoint", "version": "3", "sha256": V3, **(patch or {})},
               "build": {"platform": E._platform(), "arch": E._arch(), "usb_only": True, **(build or {})},
               **top}
        (d / E.MANIFEST_NAME).write_text(json.dumps(man))
    return p


# ─── where it is looked for ─────────────────────────────────────────────────
def test_verified_engine_from_the_environment(monkeypatch, tmp_path):
    p = make_engine(tmp_path / "eng")
    monkeypatch.setenv(E.ENV_PATH, str(p))
    st = E.engine_status()
    assert st.ok and st.code is None and st.source == "env" and st.path == str(p)
    assert st.binary_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()
    assert (st.argyll_version, st.patch_version, st.patch_sha256, st.usb_only) == ("3.5.0", "3", V3, True)
    assert E.require_engine() == st
    d = st.to_dict()
    assert d["name"] == "chartread-fgK" and d["ok"] is True


def test_engine_from_the_config_file(monkeypatch, tmp_path):
    p = make_engine(tmp_path / "eng")
    monkeypatch.setattr(argyll, "_argyll_config", lambda: {"chartread_fgk": str(p)})
    st = E.engine_status()
    assert st.ok and st.source == "config"


def test_a_pinned_path_is_never_replaced_by_another_engine(monkeypatch, tmp_path):
    good = make_engine(tmp_path / "good")
    meipass = tmp_path / "meipass"
    make_engine(meipass / E.BUNDLE_DIR_NAME)                       # a valid shipped copy too
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setenv(E.ENV_PATH, str(tmp_path / "missing" / E.ENGINE_NAME))
    monkeypatch.setattr(argyll, "_argyll_config", lambda: {"chartread_fgk": str(good)})
    st = E.engine_status()
    assert (st.ok, st.code, st.source) == (False, "engine_missing", "env")
    monkeypatch.delenv(E.ENV_PATH)
    monkeypatch.setattr(argyll, "_argyll_config",
                        lambda: {"chartread_fgk": str(tmp_path / "gone" / E.ENGINE_NAME)})
    st = E.engine_status()
    assert (st.ok, st.code, st.source) == (False, "engine_missing", "config")


def test_path_and_argyll_directories_are_never_searched(monkeypatch, tmp_path):
    """A perfectly valid chartread-fgK on PATH or in the Argyll bin directory, and an official
    chartread next to it: neither is taken."""
    on_path = tmp_path / "bin"
    make_engine(on_path)
    make_engine(on_path, name="chartread", markers=())
    argyll_bin = tmp_path / "argyll" / "bin"
    make_engine(argyll_bin)
    monkeypatch.setenv("PATH", f"{on_path}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FREEGLAZ_ARGYLL_BIN", str(argyll_bin))
    monkeypatch.setenv("FREEGLAZ_ARGYLL_ROOT", str(tmp_path / "argyll"))
    st = E.engine_status()
    assert (st.ok, st.code, st.path, st.source) == (False, "engine_missing", None, None)


def test_copy_shipped_in_the_macos_app(monkeypatch, tmp_path):
    meipass = tmp_path / "meipass"
    p = make_engine(meipass / E.BUNDLE_DIR_NAME)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    st = E.engine_status()
    assert st.ok and st.source == "bundle" and st.path == str(p)


def test_copy_shipped_in_the_flatpak_only_inside_the_sandbox(monkeypatch, tmp_path):
    p = make_engine(tmp_path / "app" / "chartread-fgK")
    monkeypatch.setattr(E, "FLATPAK_DIR", p.parent)
    assert E.engine_status().code == "engine_missing"            # not in a Flatpak sandbox
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\n")
    monkeypatch.setattr(E, "FLATPAK_INFO", info)
    st = E.engine_status()
    assert st.ok and st.source == "bundle"


def test_unsupported_platform(monkeypatch):
    monkeypatch.setattr(E, "_platform", lambda: "win32")
    st = E.engine_status()
    assert (st.ok, st.code) == (False, "engine_unsupported_platform")
    with pytest.raises(E.EngineError) as e:
        E.require_engine()
    assert e.value.code == "engine_unsupported_platform"


# ─── how it is checked ──────────────────────────────────────────────────────
def _status(monkeypatch, p):
    monkeypatch.setenv(E.ENV_PATH, str(p))
    return E.engine_status()


@pytest.mark.parametrize("kw,code", [
    ({"mode": 0o644}, "engine_not_executable"),
    ({"manifest": False}, "engine_manifest_missing"),
    ({"schema": "other"}, "engine_manifest_invalid"),
    ({"schema_version": 2}, "engine_manifest_invalid"),
    ({"sha256": "ABC"}, "engine_manifest_invalid"),
    ({"binary": "chartread"}, "engine_manifest_invalid"),            # describes another file
    ({"build": {"usb_only": "yes"}}, "engine_manifest_invalid"),
    ({"build": {"arch": "riscv64"}}, "engine_platform_mismatch"),
    ({"build": {"platform": "darwin" if sys.platform.startswith("linux") else "linux"}},
     "engine_platform_mismatch"),
    ({"patch": {"sha256": V1, "version": "1"}}, "engine_patch_unknown"),
    ({"patch": {"sha256": V2, "version": "2"}}, "engine_patch_unknown"),
    ({"patch": {"version": "2"}}, "engine_patch_unknown"),           # v3 hash, wrong version
    ({"argyll_version": "3.4.1"}, "engine_argyll_unsupported"),
    ({"build": {"usb_only": False}}, "engine_build_unsupported"),     # serial code inside
    ({"markers": E.MARKERS[:4]}, "engine_markers_missing"),           # a v1 build labelled v3
    ({"markers": ()}, "engine_markers_missing"),                      # the official chartread
])
def test_every_check_refuses_with_a_stable_code(monkeypatch, tmp_path, kw, code):
    st = _status(monkeypatch, make_engine(tmp_path / "eng", **kw))
    assert (st.ok, st.code) == (False, code), st.message


def test_unreadable_manifest(monkeypatch, tmp_path):
    p = make_engine(tmp_path / "eng")
    (p.parent / E.MANIFEST_NAME).write_text("{not json")
    assert _status(monkeypatch, p).code == "engine_manifest_invalid"


@pytest.mark.parametrize("plat,arch", [("linux", "x86_64"), ("darwin", "arm64")])
def test_reduced_durability_text_is_required_only_where_it_can_exist(monkeypatch, tmp_path, plat, arch):
    """The v3 patch prints "Checkpoint durability reduced" only after F_FULLFSYNC refused:
    macOS. A genuine Linux build (official 3.5.0 + v3, gcc -O2) does not contain the text at
    all (note 142): it must be accepted on Linux, and still required on macOS."""
    monkeypatch.setattr(E, "_platform", lambda: plat)
    monkeypatch.setattr(E, "_arch", lambda: arch)
    linux_build = make_engine(tmp_path / "eng", markers=E.MARKERS)
    st = _status(monkeypatch, linux_build)
    if plat == "linux":
        assert st.ok, st.message
    else:
        assert (st.ok, st.code) == (False, "engine_markers_missing")
        assert _status(monkeypatch, make_engine(tmp_path / "mac", markers=E.required_markers())).ok


def test_a_valid_manifest_is_not_trust(monkeypatch, tmp_path):
    """Integrity is not authenticity: a binary and its manifest written together pass every
    file check, in the shipped place too, and are still not a trusted build. Only the list in
    freeglaz's code makes one; no environment variable or configuration reaches it."""
    p = make_engine(tmp_path / "app" / "chartread-fgK")
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\n")
    monkeypatch.setattr(E, "FLATPAK_INFO", info)
    monkeypatch.setattr(E, "FLATPAK_DIR", p.parent)
    st = E.engine_status()
    assert (st.ok, st.source, st.trusted) == (True, "bundle", False)
    for var in ("FREEGLAZ_TRUSTED_ENGINE", "FREEGLAZ_CHARTREAD_FGK_SHA256", "FREEGLAZ_DEV"):
        monkeypatch.setenv(var, st.binary_sha256)
    monkeypatch.setattr(argyll, "_argyll_config", lambda: {"chartread_fgk_trusted": st.binary_sha256})
    assert E.engine_status().trusted is False
    assert E.trusted_build(st.binary_sha256) is False


def test_no_build_is_trusted_before_one_is_approved():
    """Guard: nothing is approved until a reproducible build is (distribution, D1). Adding an
    entry is a reviewed change of this list, with the build's provenance."""
    assert E.TRUSTED_BUILDS == {}


def test_binary_changed_after_its_manifest(monkeypatch, tmp_path):
    p = make_engine(tmp_path / "eng")
    p.write_bytes(p.read_bytes() + b"\n")
    st = _status(monkeypatch, p)
    assert st.code == "engine_sha256_mismatch" and st.binary_sha256 != V3


# ─── command line ───────────────────────────────────────────────────────────
ENG = "/app/chartread-fgK/chartread-fgK"


def test_colormunki_command_line():
    assert E.build_argv(ENG, instrument="colormunki", chart_base="CHT-20261124-1015-AB") == \
        [ENG, "-v", "-c", "1", "-K", "CHT-20261124-1015-AB"]
    assert E.build_argv(ENG, instrument="colormunki", chart_base="c", resume=True,
                        no_initial_calibration=True) == [ENG, "-v", "-c", "1", "-K", "-r", "-N", "c"]


@pytest.mark.parametrize("f", ["n", "5", "u"])
def test_i1pro3plus_condition_is_always_explicit(f):
    assert E.build_argv(ENG, instrument="i1pro3plus", chart_base="c", filter_option=f) == \
        [ENG, "-v", "-c", "1", "-K", "-F", f, "c"]


@pytest.mark.parametrize("kw,code", [
    ({"engine_path": "chartread-fgK"}, "argv_engine_path"),
    ({"instrument": "spyder"}, "argv_instrument_unsupported"),
    ({"instrument": "hp_z9_embedded"}, "argv_instrument_unsupported"),
    ({"instrument": "i1pro3plus"}, "argv_filter_required"),
    ({"instrument": "i1pro3plus", "filter_option": "6"}, "argv_filter_unsupported"),   # D65
    ({"instrument": "i1pro3plus", "filter_option": "p"}, "argv_filter_unsupported"),   # M3
    ({"instrument": "i1pro3plus", "filter_option": "z"}, "argv_filter_unsupported"),
    ({"filter_option": "n"}, "argv_filter_not_applicable"),                            # ColorMunki
    ({"chart_base": "-r"}, "argv_base_invalid"),
    ({"chart_base": "--replay"}, "argv_base_invalid"),
    ({"chart_base": "a/b"}, "argv_base_invalid"),
    ({"chart_base": "../c"}, "argv_base_invalid"),
    ({"chart_base": ""}, "argv_base_invalid"),
    ({"chart_base": "with space"}, "argv_base_invalid"),
    ({"chart_base": "c.ti2"}, "argv_base_invalid"),
    ({"chart_base": "x" * 129}, "argv_base_invalid"),
])
def test_refused_command_lines(kw, code):
    args = {"engine_path": ENG, "instrument": "colormunki", "chart_base": "c", **kw}
    with pytest.raises(E.ArgvError) as e:
        E.build_argv(args.pop("engine_path"), **args)
    assert e.value.code == code


def test_built_command_lines_pass_the_provenance_contract():
    """What freeglaz runs is exactly what a record may keep: accepted, with the condition
    read back from -F."""
    cm = E.build_argv(ENG, instrument="colormunki", chart_base="c", resume=True, no_initial_calibration=True)
    assert ident(colormunki_ti3(), record("colormunki", cm)).condition == "fixed"
    for f, flt, cond in (("n", None, "M0"), ("5", None, "M1"), ("u", "UVCUT", "M2")):
        argv = E.build_argv(ENG, instrument="i1pro3plus", chart_base="c", filter_option=f)
        assert ident(i1pro3_ti3(instrument_filter=flt), record("i1pro3plus", argv)).condition == cond
    for argv in (cm,):
        assert not {"-S", "-n", "-x", "-t", "-e", "-d", "-p"} & set(argv)
        assert not any(a.startswith("--") for a in argv)
        mi.check_external_argv(argv)


# ─── /printtarg-options tells the engine from the official chartread ───────
@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    app.dependency_overrides[charts_routes.get_chart_z9] = _never
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_options_report_the_official_chartread_and_the_engine_apart(client, monkeypatch, tmp_path):
    monkeypatch.setattr(argyll, "find_argyll_binary", lambda name: f"/usr/bin/{name}")
    o = client.get("/api/charts/printtarg-options").json()
    assert o["binaries"]["chartread"] == {"available": True, "used_for_measurement": False}
    assert (o["measurement_engine"]["ok"], o["measurement_engine"]["code"]) == (False, "engine_missing")
    p = make_engine(tmp_path / "eng")
    monkeypatch.setenv(E.ENV_PATH, str(p))
    eng = client.get("/api/charts/printtarg-options").json()["measurement_engine"]
    assert eng["ok"] is True and eng["patch_version"] == "3" and eng["source"] == "env"
