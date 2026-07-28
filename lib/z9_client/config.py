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

"""
freeglaz CLI configuration — reading the user TOML file + defaults.

Lets the user customize default behaviors without having to pass dozens
of flags on every command. Particularly useful for colprof parameters
(quality, source gamut, manufacturer, copyright) which are stable from
one project to the next.

Configuration hierarchy:
    1. Hardcoded defaults (the `_DEFAULTS` dict)
    2. User file ~/.freeglazrc.toml (merged if present)
    3. Explicit CLI flags (always override)

File format (TOML):

    [colprof]
    quality = "h"
    source_gamut = ""          # empty = no -S; otherwise alias ("Rec2020") or .icc path
    manufacturer = "HP"
    model = "DesignJet Z9"
    copyright = "No copyright, use freely"
    extra_flags = "-cmt -dpp"

    [multipass]
    dedup = "keep"
    keep_intermediates = false

    [profile]
    check_lab_white_a_max = 4.0
    check_lab_white_b_max = 5.0
    check_lab_white_b_min = -8.0

    [paths]
    sessions_root = "~/Documents/freeglaz/sessions"

Aliases for common source profiles (automatic resolution):
    "AdobeRGB1998" → /System/Library/ColorSync/Profiles/AdobeRGB1998.icc
    "sRGB"         → /System/Library/ColorSync/Profiles/sRGB Profile.icc
    "Rec2020"      → looked up in ColorSync, else error
    "ProPhotoRGB"  → same

Public API:
    get_config() -> dict[str, dict]
    get_config_path() -> Path
    resolve_source_gamut(name_or_path: str) -> str
    write_default_config(path: Path | None = None) -> Path
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None  # Degraded mode: no TOML support

try:
    import tomli_w  # for write_default_config and config set
except ImportError:
    tomli_w = None  # Degraded mode: fall back to manual formatting


# ─── Hardcoded defaults ────────────────────────────────────────────────


_DEFAULTS: dict[str, dict[str, Any]] = {
    "colprof": {
        # colprof quality: "l" (low), "m" (medium), "h" (high), "u" (ultra)
        "quality": "h",
        # Source profile for perceptual gamut (-S) — USER choice, default EMPTY
        # (no -S imposed: portable, no macOS ColorSync path). The user sets an
        # alias ("Rec2020", "sRGB"…) or an absolute path if they want a mapping.
        "source_gamut": "",
        # -A manufacturer
        "manufacturer": "HP",
        # -M model
        "model": "DesignJet Z9",
        # -C copyright. "No copyright, use freely" (Argyll phrasing): the profile
        # COMPUTATION comes from Argyll colprof (which claims nothing and attributes
        # the profile to the author of the measurements) → we do not appropriate the
        # output. Free profiles, consistent with the open-source spirit of freeglaz.
        # Customizable via user config.
        "copyright": "No copyright, use freely",
        # Extra free-form flags (shell-split). "-cmt -dpp" = tone curves and
        # extended precision, recommended for photo.
        "extra_flags": "-cmt -dpp",
    },
    "multipass": {
        # Dedup strategy for RGB patches duplicated across passes.
        # "keep" recommended: Argyll averages implicitly.
        "dedup": "keep",
        # Keep the intermediate ti3 files (pass1.ti3, pass2.ti3...) after
        # build-multipass-profile. False by default (auto cleanup).
        "keep_intermediates": False,
    },
    "profile": {
        # Detection thresholds for the XYZ bug via profile check.
        # A normal paper white has a*≈0±2, b*≈+1±3. Beyond that, suspect.
        "check_lab_white_a_max": 4.0,
        "check_lab_white_b_max": 5.0,
        "check_lab_white_b_min": -8.0,
    },
    "paths": {
        # Root of the local freeglaz store. Tilde and environment variables
        # are expanded. All areas derive from this root:
        # mirror/<serial>/, repo/{printers,displays,workingspaces}/,
        # backups/, sessions/. Overridable via FREEGLAZ_STORE_ROOT (env).
        "store_root": "~/Documents/freeglaz",
        # Root of the profiling sessions (legacy — now derived from store_root;
        # value kept for read backward-compat).
        "sessions_root": "~/Documents/freeglaz/sessions",
    },
}


# BUNDLED reference ICC profiles (Elle Stone, CC-BY-SA, portable — cf.
# lib/z9_client/assets/REFERENCE_PROFILES_LICENSE.md).
_BUNDLE_ASSETS = Path(__file__).parent / "assets"

# Source gamut aliases (-S) → BUNDLED file. Resolved in the bundle first
# (portable), macOS ColorSync as legacy fallback. DCI-P3 removed (unused).
_SOURCE_GAMUT_ALIASES = {
    "sRGB":          "sRGB-elle-V2-srgbtrc.icc",
    "AdobeRGB":      "ClayRGB-elle-V2-g22.icc",   # Elle Stone ClayRGB = AdobeRGB-compatible
    "AdobeRGB1998":  "ClayRGB-elle-V2-g22.icc",
    "ProPhoto":      "LargeRGB-elle-V2-g18.icc",  # Elle Stone LargeRGB = ProPhoto
    "ProPhotoRGB":   "LargeRGB-elle-V2-g18.icc",
    "Rec2020":       "Rec2020-elle-V2-rec709.icc",
    "Rec.2020":      "Rec2020-elle-V2-rec709.icc",
    "Rec709":        "Rec709-elle-V2-rec709.icc",
    "Rec.709":       "Rec709-elle-V2-rec709.icc",
}

_COLORSYNC_SEARCH_DIRS = [
    Path("/System/Library/ColorSync/Profiles"),
    Path("/Library/ColorSync/Profiles"),
    Path.home() / "Library" / "ColorSync" / "Profiles",
]


# ─── Reading the config file ───────────────────────────────────────────


def get_config_path() -> Path:
    """Standard path of the user config file."""
    return Path.home() / ".freeglazrc.toml"


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursive merge of 2 dicts (override takes precedence over base)."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def get_config(config_path: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """Return the effective config (defaults + user file).

    :param config_path: path of the TOML file. Default: ~/.freeglazrc.toml.
                        If the file does not exist, the defaults are returned.
    :return: dict with sections [colprof, multipass, profile, paths]
    """
    if config_path is None:
        config_path = get_config_path()

    if not config_path.exists():
        return dict(_DEFAULTS)  # copy

    if tomllib is None:
        # Python < 3.11 without tomli: silent warning, the file is ignored
        # (defaults only). To be documented in the CLI.
        return dict(_DEFAULTS)

    try:
        with open(config_path, "rb") as f:
            user_config = tomllib.load(f)
    except (OSError, ValueError) as e:
        # If the file is corrupted or invalid, fall back on defaults
        # (the error will be visible via `config show` if the user looks)
        return dict(_DEFAULTS)

    return _deep_merge(_DEFAULTS, user_config)


# ─── Alias resolution ──────────────────────────────────────────────────


def resolve_source_gamut(name_or_path: str) -> str:
    """Resolve an alias name (e.g. 'Rec2020') or a path to an absolute path.

    A path (containing '/' or '\\') is returned as-is if it exists. A known
    alias is resolved to the BUNDLED file (portable, assets/). As a last resort,
    a raw filename is looked up in ColorSync (macOS, legacy).

    :raises FileNotFoundError: if the alias is unknown or the file is absent
    """
    # Case 1: existing absolute or relative path (user choice) → as-is
    if '/' in name_or_path or '\\' in name_or_path:
        p = Path(name_or_path).expanduser().resolve()
        if not p.exists():
            raise FileNotFoundError(
                f"Source gamut profile not found: {p}"
            )
        return str(p)

    # Case 2: known alias → BUNDLED file (Elle Stone, portable) in priority
    filename = _SOURCE_GAMUT_ALIASES.get(name_or_path)
    if filename is not None:
        bundled = _BUNDLE_ASSETS / filename
        if bundled.exists():
            return str(bundled)

    # Case 3: legacy macOS fallback — raw filename looked up in ColorSync
    raw = filename or (name_or_path if name_or_path.lower().endswith(('.icc', '.icm'))
                       else name_or_path + '.icc')
    for d in _COLORSYNC_SEARCH_DIRS:
        candidate = d / raw
        if candidate.exists():
            return str(candidate)

    raise FileNotFoundError(
        f"Source gamut profile not found: '{name_or_path}'.\n"
        f"Searched in the bundle ({_BUNDLE_ASSETS}) then ColorSync "
        f"{[str(d) for d in _COLORSYNC_SEARCH_DIRS]}\n"
        f"Known aliases: {list(_SOURCE_GAMUT_ALIASES.keys())}"
    )


# colprof -s/-S both take "src.icc | percentage" (Argyll): a bare number is a
# compression/expansion percentage, NOT a source profile → must be left as-is.
_GAMUT_PERCENT_RE = re.compile(r"^\d+(?:\.\d+)?$")


def resolve_gamut_aliases_in_flags(flags: list[str]) -> list[str]:
    """Resolve source-gamut ALIASES/paths inside a free-form colprof flag list.

    For every ``-s`` / ``-S`` gamut-mapping flag whose value is a known alias
    ('AdobeRGB', 'Rec2020'…) or a path, replace it with the absolute BUNDLED
    ICC path (assets/, via resolve_source_gamut) — exactly like the CLI does
    for config[colprof][source_gamut]. This makes ``-S AdobeRGB`` typed in the
    webapp "colprof options" field point at assets/ClayRGB-elle-V2-g22.icc
    instead of being passed literally.

    The PERCENTAGE form (``-s 90``, ``-S 2.0``) is left untouched — colprof
    accepts both a source profile and an expansion/compression percentage on
    these flags, so a bare number must not be treated as an alias.

    :raises FileNotFoundError: if a non-numeric ``-s``/``-S`` value is neither a
                               known alias nor an existing path (surfaced to the
                               caller as an actionable error, like the CLI).
    """
    out = list(flags)
    for i, tok in enumerate(out):
        if tok in ("-s", "-S") and i + 1 < len(out):
            val = out[i + 1]
            if _GAMUT_PERCENT_RE.match(val):
                continue                       # percentage form → leave as-is
            out[i + 1] = resolve_source_gamut(val)  # alias/path → absolute assets path
    return out


# ─── Key.by.path access (for `config get/set`) ──────────────────────────


def get_config_value(config: dict, dotted_key: str) -> Any:
    """Get a value via a dotted key (e.g. 'colprof.quality').

    :raises KeyError: if the key does not exist
    """
    parts = dotted_key.split('.')
    cur = config
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            raise KeyError(dotted_key)
        cur = cur[p]
    return cur


def set_config_value(config: dict, dotted_key: str, value: Any) -> None:
    """Set a value via a dotted key (recursive creation).

    Modifies the dict in place. Coerces simple types from string:
    "true"/"false" → bool, "123"/"1.5" → int/float, otherwise string.
    """
    parts = dotted_key.split('.')
    cur = config
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur[p], dict):
            cur[p] = {}
        cur = cur[p]

    # Type coercion from string
    if isinstance(value, str):
        if value.lower() in ('true', 'yes', 'on'):
            value = True
        elif value.lower() in ('false', 'no', 'off'):
            value = False
        else:
            try:
                value = int(value)
            except ValueError:
                try:
                    value = float(value)
                except ValueError:
                    pass  # stays a string

    cur[parts[-1]] = value


# ─── Building the colprof command from the config ──────────────────────


def build_colprof_command(
    ti3_basename: str | Path,
    descriptor: str,
    config: dict,
    *,
    colprof_path: str = "colprof",
    extra_flags_override: Optional[list[str]] = None,
) -> list[str]:
    """Build the colprof argument list from the config.

    Resolves aliases (source_gamut → absolute path), assembles all the
    flags (-q, -S, -A, -M, -C, -D, extra), then the final ti3_basename.

    :param ti3_basename: basename of the ti3 (without extension)
    :param descriptor: -D of the ICC profile
    :param config: config dict (cf. get_config())
    :param colprof_path: name of the colprof binary
    :param extra_flags_override: if provided, replaces config[colprof][extra_flags]
                                  (used by the CLI flag --colprof-extra)
    :return: shell-ready argument list
    :raises FileNotFoundError: if source_gamut is not resolvable
    """
    import shlex

    cp = config.get("colprof", _DEFAULTS["colprof"])

    quality = cp.get("quality", "h")
    quality_flag = f"-q{quality}"

    # Source gamut (-S, perceptual mapping) = USER choice, not an imposed default.
    # Empty → no -S (colorimetric profile without gamut mapping). Also avoids the
    # non-portable macOS ColorSync path (Adobe's AdobeRGB1998 is neither free nor
    # cross-OS).
    source_gamut = cp.get("source_gamut", "")

    manufacturer = cp.get("manufacturer", "HP")
    model = cp.get("model", "DesignJet Z9")
    copyright_str = cp.get("copyright", "No copyright, use freely")

    if extra_flags_override is not None:
        extra_flags_list = extra_flags_override
    else:
        extra_flags_str = cp.get("extra_flags", "")
        extra_flags_list = shlex.split(extra_flags_str) if extra_flags_str else []

    ti3_str = str(ti3_basename)
    if ti3_str.endswith('.ti3'):
        ti3_str = ti3_str[:-4]

    from .argyll import resolve_argyll_binary
    cmd = [resolve_argyll_binary(colprof_path), "-v", quality_flag]
    if source_gamut:
        cmd += ["-S", resolve_source_gamut(source_gamut)]
    cmd.extend(extra_flags_list)
    cmd.extend([
        "-A", manufacturer,
        "-M", model,
        "-C", copyright_str,
        "-D", descriptor,
        ti3_str,
    ])
    return cmd


# ─── Writing the config file ───────────────────────────────────────────


def write_default_config(path: Optional[Path] = None) -> Path:
    """Write a config file with the commented defaults.

    :param path: destination path. Default: ~/.freeglazrc.toml
    :return: path of the written file
    :raises FileExistsError: if the file already exists
    """
    if path is None:
        path = get_config_path()

    if path.exists():
        raise FileExistsError(
            f"Config file already present: {path}\n"
            f"Delete it manually or edit it directly with your editor."
        )

    # Manual format (no mandatory tomli_w dependency) so as to keep
    # the useful comments in the file
    template = '''# freeglaz CLI configuration
# Edit this file to customize the default behaviors.
# Explicit CLI flags always take precedence over these defaults.

[colprof]
# ICC profile quality: "l" (low), "m" (medium), "h" (high), "u" (ultra)
quality = "h"

# Source profile for perceptual gamut (-S) — user's CHOICE, empty by default
# (no gamut mapping imposed). Aliases: "sRGB", "Rec2020", "AdobeRGB", "ProPhoto", "Rec709"
# (searched in ColorSync on macOS) or an absolute path to a .icc.
source_gamut = ""

# ICC profile metadata (-A, -M, -C)
manufacturer = "HP"
model = "DesignJet Z9"
copyright = "No copyright, use freely"

# Additional colprof flags (shell-split).
# "-cmt -dpp" recommended for photo profiles (tone curves + extended precision).
extra_flags = "-cmt -dpp"

[multipass]
# Dedup strategy for RGB patches duplicated between passes.
# "keep" = keep all (Argyll averages implicitly, recommended)
# "first" = keep the 1st occurrence
# "last" = override by later sources
# "mean" = explicit average
dedup = "keep"

# Keep the intermediate ti3 files (pass1.ti3, pass2.ti3...) after build.
keep_intermediates = false

[profile]
# Thresholds for automatic detection of the XYZ bug via `profile check`.
# A normal paper white is typically at a*≈0±2, b*≈+1±3.
# Beyond that: suspect (likely CIE table bug).
check_lab_white_a_max = 4.0
check_lab_white_b_max = 5.0
check_lab_white_b_min = -8.0

[paths]
# Root of the local freeglaz store (Inc 17.1).
# All zones derive from it: mirror/<serial>/, repo/{printers,displays,
# workingspaces}/, backups/, sessions/. Tilde and $VAR expanded.
store_root = "~/Documents/freeglaz"

# Root of the profiling sessions (legacy).
sessions_root = "~/Documents/freeglaz/sessions"
'''

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(template)

    return path


def save_config(config: dict, path: Optional[Path] = None) -> Path:
    """Write the config dict to the TOML file.

    Used by `config set` which modifies a single value. Preserves the
    structure but loses the comments (limitation of tomli_w / no
    comment-tolerant parser in the stdlib).

    :raises ImportError: if tomli_w is not installed
    """
    if path is None:
        path = get_config_path()

    if tomli_w is None:
        # Fallback: minimal manual writing (no support for all TOML
        # structures, but fine for our simple case)
        return _save_config_manual(config, path)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(config, f)
    return path


def _save_config_manual(config: dict, path: Path) -> Path:
    """Minimalist TOML save without an external dependency.

    Handles only the types: bool, int, float, str, list of these types.
    No support for deeply nested structures.
    """
    def fmt_value(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            # Basic escaping
            escaped = v.replace('\\', '\\\\').replace('"', '\\"')
            return f'"{escaped}"'
        if isinstance(v, list):
            return "[" + ", ".join(fmt_value(x) for x in v) + "]"
        raise TypeError(f"Unsupported type for manual TOML: {type(v)}")

    lines = ["# freeglaz CLI configuration (saved automatically)\n"]
    for section, values in config.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in values.items():
            lines.append(f"{k} = {fmt_value(v)}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ─── Config display (for `config show`) ─────────────────────────────────


def format_config_for_display(config: dict, indent: int = 2) -> str:
    """Format the config for human display (TOML-like, plus colors)."""
    lines = []
    pad = " " * indent
    for section, values in config.items():
        if not isinstance(values, dict):
            continue
        lines.append(f"[{section}]")
        for k, v in values.items():
            if isinstance(v, bool):
                rv = "true" if v else "false"
            elif isinstance(v, str):
                rv = f'"{v}"'
            else:
                rv = str(v)
            lines.append(f"{pad}{k} = {rv}")
        lines.append("")
    return "\n".join(lines)
