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

"""Minimal JSON store for freeglaz app-level settings.

Persists a settings dict in ``webapp/data/settings.json``. Schema
validated on the write side (unknown keys accepted with a warning, unknown
values on closed enums rejected).

Only a single key is exposed: ``inspection.gamut_reference``,
preparation for the 3D Gamut view.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from webapp.backend.paths import data_dir

logger = logging.getLogger(__name__)


_DATA_DIR = data_dir()
_SETTINGS_FILE = _DATA_DIR / "settings.json"


# Schema: {dotted key: (default, validator)}. Validator returns True
# if the value is admissible. Unlisted keys are ignored on write.
_SCHEMA = {
    # Default reference for the 3D Gamut view.
    "inspection.gamut_reference": (
        "sRGB",
        lambda v: v in {"sRGB", "AdobeRGB", "ProPhoto",
                         "Rec.2020", "Rec.709", "none"},   # DCI-P3 removed (out of use)
    ),
    # CLUT sub-sampling resolution for 3D scatter in
    # LUT popovers (advanced). String for i18n consistency of the options.
    "gamut.lut_scatter_resolution": (
        "9",
        lambda v: v in {"9", "17"},
    ),
    # gamut.boundary_method removed: single method
    # device_surface_grid. Legacy keys present in settings.json
    # are silently cleaned by _migrate_legacy_settings.
}


def _migrate_legacy_settings(data: dict) -> bool:
    """Cleanup of legacy gamut keys.

    Removes ``gamut.extraction_method`` and
    ``gamut.boundary_method`` if present.
    Returns True if a migration was applied. Idempotent.
    """
    gamut = data.get("gamut")
    if not isinstance(gamut, dict):
        return False
    changed = False
    if gamut.pop("extraction_method", None) is not None:
        changed = True
    if gamut.pop("boundary_method", None) is not None:
        changed = True
    return changed


_lock = threading.Lock()


def _defaults() -> dict:
    out: dict = {}
    for dotted, (default, _v) in _SCHEMA.items():
        cur = out
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = default
    return out


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Merge overlay into base (in-place on a copy). overlay wins."""
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
    return base


def _read_raw() -> dict:
    if not _SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("settings.json unreadable (%s) — logical reset", e)
        return {}
    # Silent migration: cleanup of legacy gamut keys
    if _migrate_legacy_settings(data):
        try:
            _write_raw(data)
            logger.info("settings: legacy gamut keys cleanup applied")
        except OSError as e:
            logger.warning("settings migration not persisted (%s)", e)
    return data


def _write_raw(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _SETTINGS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(_SETTINGS_FILE)


def _get_dotted(d: dict, dotted: str) -> Any:
    cur: Any = d
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _set_dotted(d: dict, dotted: str, value: Any) -> None:
    cur = d
    parts = dotted.split(".")
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def get_all() -> dict:
    """Return the full dict (defaults + user overrides)."""
    with _lock:
        merged = _defaults()
        overlay = _read_raw()
        return _deep_merge(merged, overlay)


def get(dotted: str) -> Any:
    """Read a dotted key (e.g. 'inspection.gamut_reference')."""
    return _get_dotted(get_all(), dotted)


def update(patch: dict) -> dict:
    """Apply a patch dict (nested form, not dotted). Validates
    against _SCHEMA for known keys. Persists atomically.

    Returns the effective state after application.

    Raises:
        ValueError if a value of a known key is not admissible.
    """
    with _lock:
        current = _read_raw()
        merged = _deep_merge(_defaults(), current)
        _deep_merge(merged, patch)

        # Validation of known keys
        for dotted, (_default, validator) in _SCHEMA.items():
            v = _get_dotted(merged, dotted)
            if v is not None and not validator(v):
                raise ValueError(
                    f"Valeur invalide pour '{dotted}' : {v!r}"
                )

        # Persist only non-default values to stay compact
        to_persist: dict = {}
        defaults = _defaults()
        for dotted, _ in _SCHEMA.items():
            current_v = _get_dotted(merged, dotted)
            default_v = _get_dotted(defaults, dotted)
            if current_v != default_v:
                _set_dotted(to_persist, dotted, current_v)
        _write_raw(to_persist)
        return merged
