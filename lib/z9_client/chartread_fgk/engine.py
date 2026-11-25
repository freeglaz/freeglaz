# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Find and check the ``chartread-fgK`` engine; build its command line.

**Which binary.** Only the freeglaz engine, never the user's ``chartread``: the official
one has no ``-K`` (readings live in memory until the end of the session) and may contain
serial-port code (``-c 1`` could then open a Bluetooth port and hang). The places looked
at, in order, are all explicit:

1. ``FREEGLAZ_CHARTREAD_FGK`` (environment): path of the binary;
2. ``[argyll] chartread_fgk`` in ``~/.freeglazrc.toml``: path of the binary;
3. the copy shipped with freeglaz: the macOS app bundle (``<bundle>/chartread-fgK/``) or
   the Flatpak (``/app/chartread-fgK/``).

A pinned path (1 or 2) is used as is, even broken: no fallback to another binary. Never
``PATH``, never the Argyll bin directory, never :func:`argyll.find_argyll_binary`.

**Checked without running it.** Running ``chartread`` without a valid chart (``-?``, no
argument, a bad option) prints the usage, which enumerates the instrument ports, i.e.
probes USB and serial devices. So the check only READS files: the manifest written next to
the binary when it was built (``chartread-fgK.manifest.json``), the sha256 of the binary
against it, the patch it declares against the patches freeglaz knows, the platform, and
text the patch puts in the binary (its ``-K`` prompts and checkpoint messages). The last
proof comes at run time, later: the ``-K`` line of the first strip prompt.

The result (:class:`EngineStatus`) is computed here, by the backend, each time. It is a
fact about a local file, not provenance: a measurement record never carries it as proof.

**Trust.** The manifest proves the binary is the one it describes (integrity), not where it
comes from: whoever can write the binary can write its manifest, and a shipped location is
only a folder. So an engine is a TRUSTED BUILD only when the sha256 of its bytes is listed in
:data:`TRUSTED_BUILDS`, which is part of freeglaz's own code (replacing it means replacing
freeglaz). The origin derived from it (``approved`` only for a trusted build found where
freeglaz ships it) is decided in :class:`~.session.EngineSpec`. The list holds only the
reproducible builds that were approved, one per platform (macOS on Apple Silicon so far):
any other engine is a development one and nothing measured with it is published. Tests
inject entries by monkeypatching the list, in their own process only; no environment
variable, configuration key or file can add one.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .. import measurement_identity as mi

ENGINE_NAME = "chartread-fgK"
MANIFEST_NAME = "chartread-fgK.manifest.json"
MANIFEST_SCHEMA = "freeglaz.chartread_fgk_manifest"
MANIFEST_SCHEMA_VERSION = 1
ENV_PATH = "FREEGLAZ_CHARTREAD_FGK"
CONFIG_KEY = "chartread_fgk"                  # [argyll] chartread_fgk = "/path/chartread-fgK"
BUNDLE_DIR_NAME = "chartread-fgK"             # <app bundle resources>/chartread-fgK/
FLATPAK_INFO = Path("/.flatpak-info")         # present inside every Flatpak sandbox
FLATPAK_DIR = Path("/app/chartread-fgK")

# Checkpoint patches freeglaz knows (sha256 of the .patch file → version). v1 and v2 are
# NOT accepted: v1 hides a failed directory flush, neither knows EOPNOTSUPP (notes 138-139).
# v3.1 is v3 plus the dated notice of modification (AGPL v3 §5a) in a comment: same C
# tokens, same binary with the same toolchain (note 143).
ACCEPTED_PATCHES = {
    "f38d53cadb0b9bb49d5ae0a2d82e0f852f2033eb6cdca81dabf13246a2b68cd8": "3",
    "7c3dcdc6676f5ad1c7613cf1be9bb929845eaba39ff153f628aca748dba6b5c4": "3.1",
}
SUPPORTED_ARGYLL = ("3.5.0",)
SUPPORTED_PLATFORMS = ("linux", "darwin")

# Text the v3 patch puts in the binary on every platform (format strings of chartread.c,
# kept by the compiler).
MARKERS = (
    b"Save the .ti3 after each strip pass",   # -K usage line
    b"passes read so far are saved",          # -K strip prompt
    b"Checkpoint: %d patches saved to",       # checkpoint confirmation
    b"Checkpoint not saved",                  # refused checkpoint
    b"Checkpoint not durable",                # v2+: directory flush failed after rename
)
# Text present only where F_FULLFSYNC exists (macOS). On Linux the reduced-durability branch
# can never run, and the compiler drops it with its message (-O2 build of the official
# 3.5.0 source + v3, note 142): requiring it there would refuse every genuine Linux build.
PLATFORM_MARKERS = {
    "darwin": (b"Checkpoint durability reduced",),   # v2+: F_FULLFSYNC not supported, fsync only
}

# The builds freeglaz trusts as its own, by (platform, arch) → sha256 of the binary. Part of
# freeglaz's code: the trust anchor, independent of any manifest (see the module doc). One
# value per platform, the engine shipped with this version of freeglaz; changing it is a
# reviewed change of this list and of the test that names it.
#
# darwin/arm64: chartread-fgK 3.5.0-fg3.1 (official ArgyllCMS 3.5.0 source zip f8576ce5…,
# checkpoint patch v3.1 7c3dcdc6…, USB instruments only), built by build-chartread-fgK.sh with
# the Command Line Tools 27.2 (Apple clang 21.0.0 clang-2100.4.1.1, ld-27106.1),
# MACOSX_DEPLOYMENT_TARGET 14.0, then signed ad hoc ALONE (`codesign -s - --force`, no -i)
# before bundling. Two builds in separate folders gave these bytes, unchanged by PyInstaller
# and by the bundle's `codesign --deep` (2026-11-23). Corresponding Source:
# chartread-fgK-3.5.0-fg3.1-src.tar.gz, sha256 f066305c…, shipped next to the engine. The
# sha256 also depends on the signing sequence (padding left by the linker signature): the
# release build re-measures it inside the signed app and is refused if it differs.
# No Linux build is listed yet: the Flatpak does not ship the engine.
TRUSTED_BUILDS: dict[tuple[str, str], frozenset[str]] = {
    ("darwin", "arm64"): frozenset({
        "6406192585406eaebab05e1d30072bf9849b6c620e1e9fa735d68ea846b424cf",
    }),
}

# chartread port: the engine is built for USB instruments only, so port 1 is the first USB
# instrument and never a serial port. Which instrument is plugged in is checked elsewhere,
# passively, before any run.
PORT = "1"
_BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EngineStatus:
    """What the backend established about the engine, now. ``ok`` only when every check
    passed; otherwise ``code`` says the first one that failed."""
    ok: bool
    code: Optional[str]                  # None | engine_* (stable)
    message: str
    source: Optional[str] = None         # env | config | bundle
    path: Optional[str] = None
    binary_sha256: Optional[str] = None
    argyll_version: Optional[str] = None
    patch_version: Optional[str] = None
    patch_sha256: Optional[str] = None
    usb_only: Optional[bool] = None
    trusted: bool = False                # binary_sha256 is a trusted build for this platform

    def to_dict(self) -> dict:
        return {"name": ENGINE_NAME, **asdict(self)}


class EngineError(RuntimeError):
    def __init__(self, status: EngineStatus):
        super().__init__(f"{status.code}: {status.message}")
        self.status = status
        self.code = status.code


class ArgvError(ValueError):
    """The chartread command line asked for is not allowed. ``code`` is stable:
    argv_engine_path | argv_instrument_unsupported | argv_filter_required |
    argv_filter_not_applicable | argv_filter_unsupported | argv_base_invalid."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ─── platform ───────────────────────────────────────────────────────────────
def _platform() -> str:
    p = sys.platform
    return "linux" if p.startswith("linux") else p


def _arch() -> str:
    m = platform.machine().lower()
    return {"amd64": "x86_64", "aarch64": "arm64"}.get(m, m)


# ─── where ──────────────────────────────────────────────────────────────────
def _config_path() -> Optional[str]:
    from ..argyll import _argyll_config
    v = _argyll_config().get(CONFIG_KEY)
    return str(v) if v else None


def _bundle_candidates() -> list[Path]:
    out = []
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:           # PyInstaller app (macOS .app)
        out.append(Path(meipass) / BUNDLE_DIR_NAME / ENGINE_NAME)
    if FLATPAK_INFO.is_file():                              # inside the Flatpak sandbox
        out.append(FLATPAK_DIR / ENGINE_NAME)
    return out


def locate_engine() -> tuple[Optional[Path], Optional[str]]:
    """(path, source) of the engine to check, or (None, None). A pinned path (environment,
    then config) is returned even when it does not exist: no fallback."""
    if (p := os.environ.get(ENV_PATH)):
        return Path(p), "env"
    if (p := _config_path()):
        return Path(p), "config"
    for cand in _bundle_candidates():
        if cand.exists():
            return cand, "bundle"
    return None, None


# ─── check ──────────────────────────────────────────────────────────────────
def required_markers(platform: Optional[str] = None) -> tuple[bytes, ...]:
    return MARKERS + PLATFORM_MARKERS.get(platform or _platform(), ())


def trusted_build(sha256: Optional[str], platform: Optional[str] = None,
                  arch: Optional[str] = None) -> bool:
    """Is ``sha256`` a build freeglaz trusts on this platform? Read from the code only."""
    return bool(sha256) and sha256 in TRUSTED_BUILDS.get((platform or _platform(), arch or _arch()),
                                                         frozenset())


def _fail(code: str, message: str, **kw) -> EngineStatus:
    return EngineStatus(False, code, message, **kw)


def _read_manifest(path: Path) -> dict:
    """The manifest next to ``path``, validated. Raises ValueError with the reason."""
    data = json.loads((path.parent / MANIFEST_NAME).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"schema must be {MANIFEST_SCHEMA!r}")
    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {MANIFEST_SCHEMA_VERSION}")
    patch, build = data.get("patch"), data.get("build")
    if not isinstance(patch, dict) or not isinstance(build, dict):
        raise ValueError("patch and build must be objects")
    for where, obj, key, typ in (("", data, "binary", str), ("", data, "sha256", str),
                                 ("", data, "argyll_version", str), ("patch.", patch, "version", str),
                                 ("patch.", patch, "sha256", str), ("build.", build, "platform", str),
                                 ("build.", build, "arch", str), ("build.", build, "usb_only", bool)):
        if not isinstance(obj.get(key), typ):
            raise ValueError(f"{where}{key} must be a {typ.__name__}")
    for where, v in (("sha256", data["sha256"]), ("patch.sha256", patch["sha256"])):
        if not _SHA256.match(v):
            raise ValueError(f"{where} must be a lowercase hex sha256")
    return data


def check_engine(path: Path, source: Optional[str] = None) -> EngineStatus:
    """Every check on ``path`` and its manifest, reading files only. Never raises."""
    where = {"source": source, "path": str(path)}
    if not path.is_file():
        return _fail("engine_missing", f"{ENGINE_NAME} not found at {path}", **where)
    if not os.access(path, os.X_OK):
        return _fail("engine_not_executable", f"{path} is not executable", **where)
    if not (path.parent / MANIFEST_NAME).is_file():
        return _fail("engine_manifest_missing", f"no {MANIFEST_NAME} next to {path}", **where)
    try:
        man = _read_manifest(path)
    except (OSError, UnicodeDecodeError, ValueError) as e:
        return _fail("engine_manifest_invalid", f"{MANIFEST_NAME}: {e}", **where)
    patch, build = man["patch"], man["build"]
    declared = {**where, "argyll_version": man["argyll_version"], "patch_version": patch["version"],
                "patch_sha256": patch["sha256"], "usb_only": build["usb_only"]}
    if man["binary"] != path.name:
        return _fail("engine_manifest_invalid",
                     f"the manifest describes {man['binary']!r}, not {path.name!r}", **declared)
    if (build["platform"], build["arch"]) != (_platform(), _arch()):
        return _fail("engine_platform_mismatch",
                     f"built for {build['platform']}/{build['arch']}, this is {_platform()}/{_arch()}",
                     **declared)
    if ACCEPTED_PATCHES.get(patch["sha256"]) != patch["version"]:
        return _fail("engine_patch_unknown",
                     f"checkpoint patch {patch['version']} ({patch['sha256'][:12]}…) is not one "
                     "freeglaz accepts", **declared)
    if man["argyll_version"] not in SUPPORTED_ARGYLL:
        return _fail("engine_argyll_unsupported",
                     f"ArgyllCMS {man['argyll_version']} is not supported ({', '.join(SUPPORTED_ARGYLL)})",
                     **declared)
    if not build["usb_only"]:
        return _fail("engine_build_unsupported",
                     "the engine must be built for USB instruments only (no serial port code)",
                     **declared)
    try:
        data = path.read_bytes()            # hashed and searched from the same bytes
    except OSError as e:
        return _fail("engine_unreadable", f"{path}: {e}", **declared)
    sha = hashlib.sha256(data).hexdigest()
    if sha != man["sha256"]:
        return _fail("engine_sha256_mismatch",
                     f"{path.name} does not match its manifest (sha256 {sha[:12]}…)",
                     binary_sha256=sha, **declared)
    missing = [m.decode() for m in required_markers() if m not in data]
    if missing:
        return _fail("engine_markers_missing",
                     f"{path.name} lacks the checkpoint patch text ({missing[0]!r})",
                     binary_sha256=sha, **declared)
    return EngineStatus(True, None, f"{ENGINE_NAME} ArgyllCMS {man['argyll_version']} + checkpoint "
                        f"patch v{patch['version']}", binary_sha256=sha, trusted=trusted_build(sha),
                        **declared)


def engine_status() -> EngineStatus:
    """Locate and check the engine (never raises, never runs anything)."""
    if _platform() not in SUPPORTED_PLATFORMS:
        return _fail("engine_unsupported_platform",
                     f"external measurement is not supported on {_platform()} (Linux and macOS only)")
    path, source = locate_engine()
    if path is None:
        return _fail("engine_missing", f"{ENGINE_NAME} is not installed (neither {ENV_PATH}, "
                     f"nor [argyll] {CONFIG_KEY} in ~/.freeglazrc.toml, nor a copy shipped with freeglaz)")
    return check_engine(path, source)


def require_engine() -> EngineStatus:
    """:func:`engine_status`, raising EngineError unless it is ok."""
    st = engine_status()
    if not st.ok:
        raise EngineError(st)
    return st


# ─── command line ───────────────────────────────────────────────────────────
def build_argv(engine_path: str, *, instrument: str, chart_base: str,
               filter_option: Optional[str] = None, resume: bool = False,
               no_initial_calibration: bool = False) -> list[str]:
    """The ONLY command line freeglaz runs:
    ``<engine> -v -c 1 -K [-F f] [-r] [-N] <chart_base>`` (run in the session folder that
    holds ``<chart_base>.ti2``).

    - ``-K`` always; never ``-S`` (Argyll's wrong-strip and unexpected-value checks stay on);
      never ``-n``, ``-x``, ``-t``, ``-e``, ``-d``, ``-p``; no long option.
    - ``-F``: required for a model with a selectable filter, from the conditions that model
      offers (i1Pro 3 Plus: n M0, 5 M1, u M2 — always explicit, never guessed); refused
      for a model without one (ColorMunki).
    - ``-r`` (resume a partly read chart) and ``-N`` (reuse the saved calibration) are the
      caller's decision.
    - ``chart_base`` is a plain file name: no path, no extension, cannot start with ``-``."""
    if not os.path.isabs(engine_path):
        raise ArgvError("argv_engine_path", "the engine path must be absolute")
    model = mi.INSTRUMENT_MODELS.get(instrument)
    if model is None or model.method != mi.METHOD_EXTERNAL:
        raise ArgvError("argv_instrument_unsupported", f"{instrument!r} is not an external instrument")
    if model.filter_selectable:
        if filter_option is None:
            raise ArgvError("argv_filter_required",
                            f"{model.key} needs an explicit measurement condition (-F)")
        if mi.CHARTREAD_FILTERS.get(filter_option) not in model.conditions:
            raise ArgvError("argv_filter_unsupported", f"-F {filter_option!r} is not offered by {model.key}")
    elif filter_option is not None:
        raise ArgvError("argv_filter_not_applicable", f"{model.key} has no selectable filter")
    if not _BASE.match(chart_base or "") or chart_base.lower().endswith((".ti1", ".ti2", ".ti3")):
        raise ArgvError("argv_base_invalid", f"{chart_base!r} is not a plain chart base name")
    argv = [engine_path, "-v", "-c", PORT, "-K"]
    if filter_option is not None:
        argv += ["-F", filter_option]
    if resume:
        argv.append("-r")
    if no_initial_calibration:
        argv.append("-N")
    argv.append(chart_base)
    return argv
