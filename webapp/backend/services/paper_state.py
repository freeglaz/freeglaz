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

"""Local paper state: favorites + user notes.

P1.A. Pattern identical to ``job_mapping``:
- 2 simple JSON files in ``webapp/data/``
- Module-level lock (RLock)
- Atomic write (tmp + os.replace)
- Tolerance for corrupt JSON / wrong types → fallback to empty dict

Files:
- ``webapp/data/paper_favorites.json`` : ``{mediaid: True, ...}``
  Only ``True`` entries are stored (a toggle off removes the
  key). No index for the favorites order in V1 — the UI displays
  in alphabetical order of the name.
- ``webapp/data/paper_notes.json`` : ``{mediaid: "markdown string", ...}``
  Extensible format: the value may later become a dict
  ``{notes: "...", oba: {...}}`` (cf. brief P1 — not implemented
  in V1, just string).
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
FAVORITES_FILE = DATA_DIR / "paper_favorites.json"
NOTES_FILE = DATA_DIR / "paper_notes.json"

# Single lock for both files — very low concurrency (single
# user, one backend), a single lock is enough and simplifies the reasoning.
_lock = threading.RLock()


def _ensure_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_json_safe(path: Path) -> dict:
    """Load a JSON, return ``{}`` on absence / corruption / wrong
    type. No write — pure read."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("paper_state: %s illisible (%s) — fallback vide", path.name, e)
        return {}
    if not isinstance(data, dict):
        logger.warning("paper_state: %s pas un dict — fallback vide", path.name)
        return {}
    return data


def _save_json_atomic(path: Path, data: dict) -> None:
    """Atomic write tmp + os.replace. Avoids corruption in case
    of a crash mid-write."""
    _ensure_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


# ─── Favorites ────────────────────────────────────────────────────────


def load_favorites() -> dict[str, bool]:
    """Snapshot of the favorites dict. Filters out non-bool values for
    robustness (case where a future corrupt file would have strings)."""
    with _lock:
        raw = _load_json_safe(FAVORITES_FILE)
        return {k: bool(v) for k, v in raw.items() if isinstance(k, str) and v}


def is_favorite(mediaid: str) -> bool:
    return bool(load_favorites().get(mediaid))


def toggle_favorite(mediaid: str) -> bool:
    """Toggle the favorite state of a paper. Returns the new state.

    If the paper was in the favorites → we remove it (key deleted).
    Otherwise → we add it with value True.
    """
    with _lock:
        data = _load_json_safe(FAVORITES_FILE)
        if data.get(mediaid):
            del data[mediaid]
            new_state = False
        else:
            data[mediaid] = True
            new_state = True
        _save_json_atomic(FAVORITES_FILE, data)
        logger.info(
            "paper_state: favorite %s %s (total favorites=%d)",
            mediaid, "ADDED" if new_state else "REMOVED", len(data),
        )
        return new_state


# ─── Notes ────────────────────────────────────────────────────────────


def get_notes(mediaid: str) -> str:
    """Return the Markdown notes for a paper, or an empty string."""
    with _lock:
        raw = _load_json_safe(NOTES_FILE)
        v = raw.get(mediaid)
        if isinstance(v, str):
            return v
        return ""


def set_notes(mediaid: str, notes: str) -> None:
    """Store the notes for a paper.

    If ``notes`` is empty or whitespace-only, we remove the key to
    keep the file clean (and so that ``has_notes`` returns False).
    """
    with _lock:
        data = _load_json_safe(NOTES_FILE)
        if notes and notes.strip():
            data[mediaid] = notes
            action = "SAVED"
        else:
            data.pop(mediaid, None)
            action = "CLEARED"
        _save_json_atomic(NOTES_FILE, data)
        logger.info(
            "paper_state: notes %s for %s (length=%d, total=%d)",
            action, mediaid, len(notes or ""), len(data),
        )


def notes_keys() -> set[str]:
    """Set of mediaids that have non-empty notes. Used by the
    transformer to compute ``has_notes`` at the global snapshot."""
    with _lock:
        raw = _load_json_safe(NOTES_FILE)
        return {
            k for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str) and v.strip()
        }
