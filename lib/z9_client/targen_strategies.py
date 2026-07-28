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

"""targen — live help, argv construction, USER presets (free chart).

Aligned with the colprof pattern (``strategies.py`` + ``refine.colprof_help``) but for
``targen`` (Argyll). V1: **no built-in presets** (FREE flag line, like the
colprof advanced side); extensible USER presets in
``~/Documents/freeglaz/targen_strategies.toml`` (flags + description).

`-d2` (Print RGB) is FIXED: the only mode applicable to freeglaz (Z9 contone RGBX). It
stays VISIBLE in the line shown to the user; ``build_targen_argv`` forces it
no matter what. Never a shell: execution via argv (list).
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import tomllib
except ModuleNotFoundError:                      # py<3.11
    import tomli as tomllib                       # type: ignore
try:
    import tomli_w
except ModuleNotFoundError:                      # pragma: no cover
    tomli_w = None

PRINT_RGB_FLAG = "-d2"      # Print RGB — only mode applicable to freeglaz (fixed, visible)


def targen_help(targen_path: str = "targen") -> str:
    """Real help output of the installed targen version (exec, never shell).
    targen with no argument prints the usage (on stderr) and exits with a non-zero code.

    :raises FileNotFoundError: if targen is absent.
    :raises RuntimeError: if no output / timeout.
    """
    from .argyll import resolve_argyll_binary
    _targen = resolve_argyll_binary(targen_path)   # raises ArgyllNotFound (actionable message)
    try:
        proc = subprocess.run([_targen], capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise RuntimeError("targen timed out.")
    out = ((proc.stderr or "") + (proc.stdout or "")).strip()
    if not out:
        raise RuntimeError("targen returned no output.")
    return out


def extract_f_count(flags_line: str | None) -> int | None:
    """Count ``-f`` (full spread) in a targen line (``-f 200`` or ``-f200``).
    None if absent/unreadable. Used for the -f <= max(format) guard UPSTREAM."""
    toks = shlex.split(flags_line or "")
    for i, t in enumerate(toks):
        if t == "-f" and i + 1 < len(toks):
            try:
                return int(toks[i + 1])
            except ValueError:
                return None
        if t.startswith("-f") and len(t) > 2:
            try:
                return int(t[2:])
            except ValueError:
                return None
    return None


def build_targen_argv(flags_line: str | None, out_basename, *,
                      precond_icc=None, targen_path: str = "targen") -> list:
    """targen argv from a FREE line. FORCES -d2 (Print RGB). Injects ``-c
    precond_icc`` if provided. The count (-f) is left as-is (validated upstream).
    Never a shell — returns a list of arguments."""
    toks = shlex.split(flags_line or "")
    if toks and Path(toks[0]).name == "targen":          # strip a leading 'targen'
        toks = toks[1:]
    # Remove any -dN selection (combined '-d2' or separate '-d 2') then force -d2.
    cleaned, skip = [], False
    for t in toks:
        if skip:
            skip = False
            continue
        if t == "-d":
            skip = True
            continue
        if t.startswith("-d") and len(t) > 2 and t[2:].isdigit():
            continue
        cleaned.append(t)
    argv = [targen_path, PRINT_RGB_FLAG] + cleaned
    if precond_icc:
        argv += ["-c", str(precond_icc)]
    argv.append(str(out_basename))
    return argv


def run_targen(flags_line: str | None, out_basename, *,
               precond_icc=None, targen_path: str = "targen", timeout: float = 120.0):
    """Run targen (argv, never shell) -> writes ``<out_basename>.ti1``.

    Returns ``(ti1_path, run_info)`` where ``run_info`` = ``{argv, stdout, returncode}``
    — the REAL executed command (with the forced ``-d2`` and any ``-c`` preconditioning,
    in the exact order) + targen's stdout on success. Persisted by the caller into
    chart.json so the flags actually honored are auditable a posteriori (#23) — no more
    "silent window" where only the REQUESTED line was recorded.

    :raises FileNotFoundError: targen absent. :raises RuntimeError: failure/no .ti1.
    """
    from .argyll import resolve_argyll_binary
    _targen = resolve_argyll_binary(targen_path)   # absolute path (raises ArgyllNotFound if absent)
    argv = build_targen_argv(flags_line, out_basename, precond_icc=precond_icc,
                             targen_path=_targen)
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    ti1 = Path(f"{out_basename}.ti1")
    if not ti1.exists():
        raise RuntimeError(
            f"targen failed (exit {proc.returncode}) : "
            f"{((proc.stderr or proc.stdout) or '')[:300]}"
        )
    run_info = {
        "argv": argv,
        "stdout": (proc.stdout or "").strip(),
        "returncode": proc.returncode,
    }
    logger.info("targen executed: %s", " ".join(argv))
    return ti1, run_info


# ─── USER presets (flags + description), user-extensible ──────────────
def _user_presets_path() -> Path:
    # Derived from the store root (cache.root_dir) — a single source of truth for the path (honors
    # the FREEGLAZ_STORE_ROOT / config override; ready for the centralized rename step A).
    from . import cache
    return cache.root_dir() / "targen_strategies.toml"


def list_targen_presets(path=None) -> list[dict]:
    p = Path(path) if path else _user_presets_path()
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError):
        return []
    return [{"key": k, "flags": v.get("flags", ""),
             "description": v.get("description", "")}
            for k, v in (data.get("preset") or {}).items()]


def save_targen_preset(key: str, flags: str, description: str = "", path=None) -> None:
    if tomli_w is None:                                  # pragma: no cover
        raise RuntimeError("tomli_w required to save a targen preset.")
    p = Path(path) if path else _user_presets_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if p.exists():
        with open(p, "rb") as f:
            data = tomllib.load(f)
    data.setdefault("preset", {})[key] = {"flags": flags, "description": description}
    with open(p, "wb") as f:
        tomli_w.dump(data, f)
