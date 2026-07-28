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

"""Unified ICC backup service, per-serial (user store).

B-backups — merges the 2 former mechanisms (CLI
``cache.save_backup`` flat + webapp ``services/icc_backups.py`` under
``webapp/data/``) into ONE single service, in the user store:

    ``backups/<serial>/<mediaid>/<ge_state>/<YYYY-MM-DDTHH-MM-SSZ>.icc``

Principles:
- **Location**: user store (`cache.root_dir()/backups/…`), NEVER
  ``webapp/data/``, nor mirror/firmware/repo (golden rule).
- **Key**: ``mediaid/ge_state`` (stable), rotation **max 5 per slot**
  ``(serial, mediaid, ge_state)`` — logic unchanged, we just add
  ``<serial>`` at the head.
- **mkdir-on-demand** in the directory accessor (``slot_dir``) -> the
  binary is never written into a non-existent directory (fixes the
  ``write_bytes``-before-``mkdir`` of the old ``cache.save_backup``).
- **No sidecar**: the profile's original name lives in the ``desc`` tag
  of the ``.icc`` itself (read at rollback via Pillow). The timestamp lives in
  the filename. No side metadata to persist.

The serial comes from the ``store.get_serial(client)`` bridge on the write/rollback side;
the listing (route without client) enumerates the serials on the FS (``serial=None``).
"""
from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import cache

logger = logging.getLogger(__name__)

MAX_KEEP_DEFAULT = 5

# Strict segments (anti path-traversal).
_GE_STATE_REGEX = re.compile(r"^(off|on|single)$")
_MEDIAID_REGEX = re.compile(r"^[0-9A-Fa-f]{2,32}$")
_BACKUP_NAME_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.icc$")

# The CLI speaks SOAP gloss_enhancer (FULLPAGE/OFF); the path uses the
# ge_state token (off/on/single). The CLI does not know "single" (webapp distinction).
_GLOSS_TO_GE_STATE = {"FULLPAGE": "on", "OFF": "off"}

# Concurrent rotation/consumption lock — single-user, one backend.
_lock = threading.RLock()


def ge_state_from_gloss(gloss_enhancer: str) -> str:
    """SOAP gloss_enhancer (FULLPAGE/OFF) -> path ge_state token (on/off)."""
    val = _GLOSS_TO_GE_STATE.get(gloss_enhancer)
    if val is None:
        raise ValueError(f"invalid gloss_enhancer for backup: {gloss_enhancer!r}")
    return val


def backups_root() -> Path:
    """``backups/`` directory (parent — multi-serial enumeration). Contains
    only ``<serial>/`` subdirectories."""
    return cache.root_dir() / "backups"


def _validate_segments(serial: str, mediaid: str, ge_state: str) -> None:
    if not serial:
        raise ValueError("serial required (e.g. 'CNXXXXXXXX')")
    if not _MEDIAID_REGEX.match(mediaid or ""):
        raise ValueError(f"invalid mediaid: {mediaid!r}")
    if not _GE_STATE_REGEX.match(ge_state or ""):
        raise ValueError(f"invalid ge_state: {ge_state!r}")


def slot_dir(serial: str, mediaid: str, ge_state: str) -> Path:
    """``backups/<serial>/<mediaid>/<ge_state>/`` — created on demand
    (mkdir-on-demand: no write into a non-existent directory)."""
    _validate_segments(serial, mediaid, ge_state)
    d = backups_root() / serial / mediaid / ge_state
    d.mkdir(parents=True, exist_ok=True)
    return d


def _iso_compact_now() -> str:
    """``2026-06-24T07-30-15Z`` — ISO 8601 UTC without ``:`` (FS-safe)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def new_backup_path(serial: str, mediaid: str, ge_state: str) -> Path:
    """Path of a new backup (the directory exists after this call; the caller
    writes the ICC binary there, then calls ``rotate``)."""
    return slot_dir(serial, mediaid, ge_state) / f"{_iso_compact_now()}.icc"


def _list_in(d: Path) -> list[Path]:
    if not d.exists():
        return []
    files = [p for p in d.iterdir()
             if p.is_file() and _BACKUP_NAME_REGEX.match(p.name)]
    files.sort(key=lambda p: p.name, reverse=True)   # lexical = reverse chronological
    return files


def list_backups(serial: str, mediaid: str, ge_state: str) -> list[Path]:
    """Backups of a slot, from newest to oldest."""
    _validate_segments(serial, mediaid, ge_state)
    return _list_in(backups_root() / serial / mediaid / ge_state)


def list_backups_any(mediaid: str, ge_state: str) -> list[Path]:
    """Backups of a slot for ALL known Z9s (listing without client):
    FS enumeration ``backups/<serial>/<mediaid>/<ge_state>/`` — V1 mono = 1
    serial. Sorted newest to oldest (by ISO filename)."""
    if not _MEDIAID_REGEX.match(mediaid or "") or not _GE_STATE_REGEX.match(ge_state or ""):
        raise ValueError(f"invalid segment: {mediaid!r}/{ge_state!r}")
    root = backups_root()
    if not root.is_dir():
        return []
    out: list[Path] = []
    for sdir in root.iterdir():
        if sdir.is_dir():
            out.extend(_list_in(sdir / mediaid / ge_state))
    out.sort(key=lambda p: p.name, reverse=True)
    return out


def latest_backup(serial: str, mediaid: str, ge_state: str) -> Optional[Path]:
    """The most recent backup of the slot, or None."""
    backups = list_backups(serial, mediaid, ge_state)
    return backups[0] if backups else None


def rotate(serial: str, mediaid: str, ge_state: str,
           max_keep: int = MAX_KEEP_DEFAULT) -> int:
    """Delete backups beyond ``max_keep`` (the oldest ones). To
    be called AFTER writing a new backup. Returns the number deleted."""
    with _lock:
        to_delete = list_backups(serial, mediaid, ge_state)[max_keep:]
        deleted = 0
        for p in to_delete:
            try:
                p.unlink()
                deleted += 1
                logger.info("icc_backups: rotated %s/%s/%s/%s (kept %d)",
                            serial, mediaid, ge_state, p.name, max_keep)
            except OSError as e:
                logger.warning("icc_backups: rotate failed on %s: %s", p, e)
        return deleted


def consume(backup_path: Path) -> bool:
    """Delete a backup (rollback after application). Best-effort."""
    with _lock:
        try:
            backup_path.unlink()
            logger.info("icc_backups: consumed %s", backup_path.name)
            return True
        except OSError as e:
            logger.warning("icc_backups: consume failed %s: %s", backup_path, e)
            return False


def purge_media(serial: str, mediaid: str) -> bool:
    """Delete ALL backups of a paper (all ge_state) for a serial —
    called when the paper is deleted (orphan backups). Best-effort."""
    if not serial or not _MEDIAID_REGEX.match(mediaid or ""):
        raise ValueError(f"invalid serial/mediaid: {serial!r}/{mediaid!r}")
    d = backups_root() / serial / mediaid
    if not d.exists():
        return False
    import shutil
    try:
        shutil.rmtree(d)
        logger.info("icc_backups: purged orphan %s/%s", serial, mediaid)
        return True
    except OSError as e:
        logger.warning("icc_backups: purge failed on %s: %s", d, e)
        return False


def backup_metadata(path: Path) -> dict:
    """``{name, timestamp, size_bytes}`` — human ISO timestamp reconstructed
    from the filename (the PROFILE name itself lives in the desc tag of the
    .icc, not here)."""
    stem = path.stem  # without ``.icc``
    iso_human = (stem[:13] + ":" + stem[14:16] + ":" + stem[17:19] + "Z"
                 if len(stem) == 20 else stem)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {"name": path.name, "timestamp": iso_human, "size_bytes": size}


def backups_summary(mediaid: str, ge_state: str,
                    serial: Optional[str] = None) -> dict:
    """Summary for ``GET …/backups``. ``serial=None`` (route without client) ->
    multi-serial enumeration; serial given -> that precise slot.

    ``{count, latest, items: [{name, timestamp, size_bytes}, …]}``
    """
    backups = (list_backups(serial, mediaid, ge_state) if serial is not None
               else list_backups_any(mediaid, ge_state))
    items = [backup_metadata(p) for p in backups]
    return {
        "count": len(items),
        "latest": items[0]["timestamp"] if items else None,
        "items": items,
    }
