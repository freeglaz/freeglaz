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

"""Management of temporary storage for webapp uploads.

Files are stored in ``<ROOT>/<uuid4>/source.<ext>``.
Default ROOT = /tmp/freeglaz-webapp. The tests monkeypatch it.
"""
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path("/tmp/freeglaz-webapp")

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def is_valid_file_id(file_id: str) -> bool:
    """A real UUID4 — prevents any path traversal via ``..`` or others."""
    return bool(_UUID4_RE.match(file_id))


def _ensure_root() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)


def new_storage() -> Tuple[str, Path]:
    """Create a fresh folder and return (file_id, dir_path)."""
    _ensure_root()
    file_id = str(uuid.uuid4())
    dir_path = ROOT / file_id
    dir_path.mkdir(parents=True, exist_ok=False)
    return file_id, dir_path


def get_storage(file_id: str) -> Optional[Path]:
    if not is_valid_file_id(file_id):
        return None
    dir_path = ROOT / file_id
    return dir_path if dir_path.is_dir() else None


def get_source(file_id: str) -> Optional[Path]:
    """Return the stored source.<ext> path, or None."""
    dir_path = get_storage(file_id)
    if dir_path is None:
        return None
    for p in dir_path.iterdir():
        if p.name.startswith("source."):
            return p
    return None


# The source file is stored under the standardized name ``source.<ext>``
# to simplify resolution, but we keep the original name on the
# metadata file side to be able to re-propagate it to the PJL JOB NAME
# (B17 follow-up: without it, all jobs are called "source.tif"
# in the Z9 queue and the user cannot distinguish anything).
_ORIGINAL_NAME_FILE = ".original_name"


def save_original_name(dir_path: Path, original_name: str) -> None:
    """Persist the original filename as uploaded by the client."""
    try:
        (dir_path / _ORIGINAL_NAME_FILE).write_text(original_name, encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to save original_name for %s: %s", dir_path, e)


def get_original_name(file_id: str) -> Optional[str]:
    """Return the original uploaded name, or None if it was not kept
    (legacy case: storage created before this metadata was introduced)."""
    dir_path = get_storage(file_id)
    if dir_path is None:
        return None
    name_file = dir_path / _ORIGINAL_NAME_FILE
    if not name_file.exists():
        return None
    try:
        return name_file.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def cleanup_all() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT, ignore_errors=True)
        logger.info("Cleanup uploads: %s removed", ROOT)


def cleanup_old(max_age_hours: float = 24.0) -> int:
    """Remove subfolders older than ``max_age_hours``."""
    if not ROOT.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for child in ROOT.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
    if removed:
        logger.info("Cleanup old uploads: %d removed", removed)
    return removed
