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

"""DURABLE and CROSS-PLATFORM resolution of Argyll CMS binaries AND data.

SINGLE POINT used by ALL Argyll access in the project — both the executables
(colprof, targen, spec2cie, profcheck, iccdump…) and the reference data
directory ``ref/`` (chart definitions ``.cht``, ``.ti2``, working-space
profiles like ``Rec2020.icm`` used with colprof ``-S``). Argyll is NOT
bundled: it is a SYSTEM dependency (no duplication, no license concern).

Why a resolver and not just the plain PATH: a GUI app (pywebview / .app on
macOS, Linux desktop launcher) does NOT load the shell profile (.zshrc/.bashrc)
-> its PATH lacks /opt/homebrew/bin etc. -> "colprof not found" even though it
is available from the CLI. And the ``ref/`` directory is not on any PATH and
sits at a platform-specific location (Homebrew ships it under
``/opt/homebrew/opt/argyll-cms/ref``, Debian/Fedora under
``/usr/share/color/argyll/ref``). So we resolve both to ABSOLUTE paths via a
robust cascade.

The Argyll tools are STANDALONE C executables (they do not invoke each other)
-> resolving the called binary to an absolute path is ENOUGH; no need to mutate
os.environ['PATH'] (global side effect avoided, stays testable).

Configuration cascade (decreasing priority), for BOTH bin and ref:
  1. explicit override:
       - binary: absolute path passed by the caller, then env
         ``FREEGLAZ_ARGYLL_<NAME>`` (a specific binary);
       - directory: env ``FREEGLAZ_ARGYLL_BIN`` / ``FREEGLAZ_ARGYLL_REF``,
         then ``FREEGLAZ_ARGYLL_ROOT`` (root of a self-contained Argyll install
         — the official argyllcms.com tarball layout is ``<root>/bin`` +
         ``<root>/ref``);
  2. config file ``~/.freeglazrc.toml`` ``[argyll]``:
       ``bin_dir`` / ``ref_dir``, then ``root`` (``<root>/bin`` + ``<root>/ref``);
  3. per-platform auto-detection (macOS Homebrew / Intel, Linux system paths);
  4. binaries only: legacy ``ARGYLL_BIN`` env (backward compat), then PATH
     (``shutil.which``) preserving the historical CLI behavior.
Otherwise -> ``ArgyllNotFound`` (actionable message).
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

__all__ = [
    "ArgyllNotFound",
    "REQUIRED_BINARIES",
    "OPTIONAL_BINARIES",
    "find_argyll_binary",
    "resolve_argyll_binary",
    "find_argyll_ref_dir",
    "resolve_argyll_ref_dir",
    "check_argyll",
]

# The Argyll executables the profiling pipeline actually needs. targen (chart
# generation) + colprof (profile build) are the core; spec2cie (spectral→XYZ,
# official CIE table) + profcheck (profile validation) are used by build/verify.
REQUIRED_BINARIES = ("targen", "colprof", "spec2cie", "profcheck")

# Binaries used by OPTIONAL tooling: the Convert module (collink = build a
# DeviceLink, cctiff = apply it, tiffgamut = image-aware gamut extraction) and
# the diagnostic gamut bench (xicclu = look colours up through a profile/link).
# Resolved ON DEMAND via find_argyll_binary and NEVER gate check_argyll's ``ok``
# — a freeglaz install without them prints and profiles perfectly.
OPTIONAL_BINARIES = ("collink", "cctiff", "tiffgamut", "xicclu")


class ArgyllNotFound(RuntimeError):
    """Argyll component not found (neither configured, nor system, nor PATH)."""


# ─── Config sources (env + ~/.freeglazrc.toml [argyll]) ──────────────────────


def _argyll_config() -> dict:
    """Read the ``[argyll]`` section of ``~/.freeglazrc.toml``.

    Returns ``{}`` on any problem (missing file, no section, parse error) so
    resolution degrades gracefully to auto-detection. Kept tiny and decoupled
    from ``config.get_config`` (which merges a schema of unrelated sections);
    only the path convention is reused. Monkeypatchable in tests."""
    try:
        import tomllib
        from .config import get_config_path
        p = get_config_path()
        if not p.is_file():
            return {}
        with open(p, "rb") as f:
            return tomllib.load(f).get("argyll", {}) or {}
    except Exception:  # noqa: BLE001 — config must never break resolution
        return {}


def _is_exe(p: Path) -> bool:
    return p.is_file() and os.access(p, os.X_OK)


# ─── Binary resolution ───────────────────────────────────────────────────────


def _system_dirs() -> list[str]:
    """Standard Argyll bin locations, per platform (never a hardcoded macOS
    path on the Linux side)."""
    if sys.platform == "darwin":
        return ["/opt/homebrew/bin", "/usr/local/bin"]
    return ["/usr/bin", "/usr/local/bin"]      # linux + other unix


def _bin_dir_candidates() -> list[str]:
    """Configured bin directories, in priority order: env BIN > env ROOT/bin >
    toml bin_dir > toml root/bin. (env beats toml.)"""
    out: list[str] = []
    if (b := os.environ.get("FREEGLAZ_ARGYLL_BIN")):
        out.append(b)
    if (r := os.environ.get("FREEGLAZ_ARGYLL_ROOT")):
        out.append(str(Path(r) / "bin"))
    cfg = _argyll_config()
    if (b := cfg.get("bin_dir")):
        out.append(str(b))
    if (r := cfg.get("root")):
        out.append(str(Path(r) / "bin"))
    return out


def find_argyll_binary(name: str) -> str | None:
    """Resolve an Argyll binary to an ABSOLUTE path, or None if not found.
    Non-raising variant for TOLERANT calls (optional spec2cie,
    profcheck, iccdump: absence is handled gracefully by the caller)."""
    # 0. explicit path passed by the caller (separator or absolute) -> honored as
    #    is; NO silent fallback if it is pinned but absent (the caller wanted THIS
    #    exact binary — we do not substitute another one).
    cand = Path(name)
    if cand.is_absolute() or cand.parent != Path("."):
        return str(cand) if _is_exe(cand) else None

    # 1. explicit single-binary override via env
    spec = os.environ.get(f"FREEGLAZ_ARGYLL_{name.upper()}")
    if spec and _is_exe(Path(spec)):
        return spec

    # 2. configured bin directories (env BIN/ROOT, then toml bin_dir/root)
    for bindir in _bin_dir_candidates():
        if _is_exe(Path(bindir) / name):
            return str(Path(bindir) / name)

    # 3. per-platform auto-detection
    for d in _system_dirs():
        if _is_exe(Path(d) / name):
            return str(Path(d) / name)

    # 4. legacy bare ARGYLL_BIN env (backward compat)
    legacy = os.environ.get("ARGYLL_BIN")
    if legacy and _is_exe(Path(legacy) / name):
        return str(Path(legacy) / name)

    # 5. PATH (historical CLI behavior preserved)
    return shutil.which(name)


def resolve_argyll_binary(name: str) -> str:
    """Like find_argyll_binary but RAISES ArgyllNotFound (actionable message)
    instead of returning None. For calls where Argyll is REQUIRED (colprof, targen)."""
    p = find_argyll_binary(name)
    if p:
        return p
    raise ArgyllNotFound(
        f"Argyll CMS binary not found: '{name}' is neither configured "
        f"(FREEGLAZ_ARGYLL_ROOT / FREEGLAZ_ARGYLL_BIN / FREEGLAZ_ARGYLL_{name.upper()} / "
        f"[argyll] in ~/.freeglazrc.toml), nor in the system paths, nor in the PATH. "
        f"{_install_hint()}"
    )


# ─── Reference-data (ref/) resolution ────────────────────────────────────────


def _system_ref_dirs() -> list[str]:
    """Standard Argyll ``ref/`` locations, per platform. macOS Homebrew ships it
    under the (version-independent) opt prefix ``<prefix>/ref`` — NOT under
    ``share/color/argyll`` (which is the Debian/Fedora layout)."""
    if sys.platform == "darwin":
        return [
            "/opt/homebrew/opt/argyll-cms/ref",   # Homebrew Apple Silicon (real layout)
            "/usr/local/opt/argyll-cms/ref",      # Homebrew Intel
            "/opt/homebrew/share/color/argyll/ref",
            "/usr/local/share/color/argyll/ref",
        ]
    return ["/usr/share/color/argyll/ref", "/usr/local/share/color/argyll/ref"]


def _ref_dir_candidates() -> list[str]:
    """Configured ref directories, in priority order: env REF > env ROOT/ref >
    toml ref_dir > toml root/ref."""
    out: list[str] = []
    if (r := os.environ.get("FREEGLAZ_ARGYLL_REF")):
        out.append(r)
    if (root := os.environ.get("FREEGLAZ_ARGYLL_ROOT")):
        out.append(str(Path(root) / "ref"))
    cfg = _argyll_config()
    if (rd := cfg.get("ref_dir")):
        out.append(str(rd))
    if (troot := cfg.get("root")):
        out.append(str(Path(troot) / "ref"))
    return out


def find_argyll_ref_dir() -> str | None:
    """Resolve the Argyll ``ref/`` data directory to an ABSOLUTE path, or None.
    Cascade: env REF > env ROOT/ref > toml ref_dir/root > per-platform paths."""
    for d in _ref_dir_candidates():
        if Path(d).is_dir():
            return d
    for d in _system_ref_dirs():
        if Path(d).is_dir():
            return d
    return None


def resolve_argyll_ref_dir() -> str:
    """Like find_argyll_ref_dir but RAISES ArgyllNotFound (actionable message)."""
    d = find_argyll_ref_dir()
    if d:
        return d
    raise ArgyllNotFound(
        "Argyll CMS reference data (ref/) not found: neither configured "
        "(FREEGLAZ_ARGYLL_ROOT / FREEGLAZ_ARGYLL_REF / [argyll] in ~/.freeglazrc.toml) "
        f"nor at the standard system locations ({', '.join(_system_ref_dirs())}). "
        f"{_install_hint()}"
    )


# ─── Combined availability check (bin + ref) ─────────────────────────────────


def _ref_has_witness(ref_dir: str) -> bool:
    """A resolved ref dir is USABLE only if it actually holds Argyll reference
    data. We witness on ``*.cht`` chart definitions (present in every Argyll
    install) — guards against an override pointing at an existing-but-wrong dir."""
    try:
        return any(Path(ref_dir).glob("*.cht"))
    except OSError:
        return False


def check_argyll() -> dict:
    """Structured availability check for the whole Argyll dependency (bin + ref).

    Non-raising: returns a report the CLI (`freeglaz check`) and the webapp
    (startup warning + /api/status) can render without try/except. Shape::

        {
          "ok": bool,                       # bin_ok and ref_ok
          "bin_ok": bool,                   # all REQUIRED_BINARIES resolved
          "bin_dir": str | None,            # dir of the resolved binaries (hint)
          "binaries": {name: path|None},    # per-binary resolution
          "ref_ok": bool,                   # ref dir resolved AND holds *.cht
          "ref_dir": str | None,
          "missing": [str, ...],            # human tokens: missing bin names + "ref"
          "install_hint": str,              # actionable, platform-aware
        }
    """
    binaries = {name: find_argyll_binary(name) for name in REQUIRED_BINARIES}
    missing_bins = [n for n, p in binaries.items() if not p]
    bin_ok = not missing_bins
    resolved = [p for p in binaries.values() if p]
    bin_dir = str(Path(resolved[0]).parent) if resolved else None

    ref_dir = find_argyll_ref_dir()
    ref_ok = bool(ref_dir) and _ref_has_witness(ref_dir)

    missing = list(missing_bins)
    if not ref_ok:
        missing.append("ref")

    # Optional binaries (DeviceLink conversion): reported but NON-FATAL — they
    # never enter `missing` nor affect `ok`. Absence only disables Convert.
    optional = {name: find_argyll_binary(name) for name in OPTIONAL_BINARIES}

    return {
        "ok": bin_ok and ref_ok,
        "bin_ok": bin_ok,
        "bin_dir": bin_dir,
        "binaries": binaries,
        "optional": optional,
        "ref_ok": ref_ok,
        "ref_dir": ref_dir,
        "missing": missing,
        "install_hint": _install_hint(),
    }


def _install_hint() -> str:
    if sys.platform == "darwin":
        pkg = "'brew install argyll-cms' (or the official binaries from argyllcms.com)"
    else:
        pkg = ("'apt install argyll' (Debian/Ubuntu/Mint), 'dnf install argyllcms' "
               "(Fedora), or the official binaries from argyllcms.com")
    return (f"Install Argyll CMS via {pkg}, or point freeglaz at your install via "
            "FREEGLAZ_ARGYLL_ROOT (a directory with bin/ and ref/) or the [argyll] "
            "section of ~/.freeglazrc.toml.")
