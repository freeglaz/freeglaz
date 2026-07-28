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

"""Persistent mapping ``firmware_uuid → jobacct5_uuid``.

Phase 3 — Part 2 (thumbnails). Why this mapping:

The Z9 firmware does **not** re-expose the ``JobAcctN`` attributes of the
PJL header in the ``/jobs/all`` XML (cf. quirk #7 documented in
``Docs/HP_DesignJet_Z9_API_Documentation.md``). We therefore cannot
ask the Z9 snapshot "give me the ``JobAcct5`` of this job to
find the freeglaz thumbnail". We maintain the
correspondence ourselves on the backend, persisted to disk:

  { "<firmware_job_uuid>": "<jobacct5_uuid>", ... }

Populated after each successful submit (the worker polls ``/jobs/all`` to
detect the new firmware job and associate it with the ``JobAcct5``
we generated in the PJL). Read by the endpoint
``GET /api/jobs/{uuid}/preview`` to find the local thumb file
``webapp/data/job_previews/<jobacct5>.jpg``.

Persistence: simple JSON, atomic write (tmp + os.replace) on
each update, survives backend reboots. Module-level lock to
avoid races between worker and preview endpoint.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default path, monkeypatchable in tests to avoid polluting
# the prod file during pytest tests.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MAPPING_FILE = DATA_DIR / "job_mapping.json"

# Module-level lock — all writes go through it to
# avoid a worker/CLI race corrupting the JSON.
_lock = threading.RLock()


def _ensure_dir() -> None:
    """Create ``DATA_DIR`` if absent. Idempotent."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load() -> dict[str, str]:
    """Load the mapping from disk, or return ``{}`` if absent / corrupt.

    A corrupt mapping (invalid JSON, wrong type) is logged as a
    warning and the service returns ``{}`` so as not to block the
    backend. The corrupt file is left on disk for
    manual investigation — we do not silently overwrite.
    """
    with _lock:
        if not MAPPING_FILE.exists():
            return {}
        try:
            data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Mapping JSON illisible (%s) — fallback dict vide", e)
            return {}
        if not isinstance(data, dict):
            logger.warning("Mapping JSON pas un dict (%r) — fallback vide", type(data))
            return {}
        # Defensive filtering: only str → str pairs. Ignore the rest.
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def _save_atomic(data: dict[str, str]) -> None:
    """Atomic write: we write ``mapping.json.tmp`` then ``os.replace``.

    Avoids a crash during writing leaving a truncated file.
    """
    _ensure_dir()
    tmp = MAPPING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, MAPPING_FILE)


def register(firmware_uuid: str, jobacct5_uuid: str) -> None:
    """Register / update the correspondence for a firmware UUID.

    If ``firmware_uuid`` already existed with another value, we
    overwrite silently (hardlink reprint case: we want the
    new firmware_uuid to point to the same jobacct5 as the original).
    """
    with _lock:
        data = load()
        data[firmware_uuid] = jobacct5_uuid
        _save_atomic(data)
        logger.info(
            "job_mapping: registered %s → %s (total=%d)",
            firmware_uuid, jobacct5_uuid, len(data),
        )


def lookup(firmware_uuid: str) -> Optional[str]:
    """Return the associated ``jobacct5_uuid``, or ``None`` if unknown."""
    return load().get(firmware_uuid)


def all_entries() -> dict[str, str]:
    """Full snapshot of the mapping (copy)."""
    return load()


def remove_jobacct5(jobacct5_uuid: str) -> int:
    """Purge all entries that point to this ``jobacct5_uuid``.

    Used by the cleanup-previews CLI command when a thumb file is
    removed: we also remove all mapping entries that
    pointed to it (several firmware_uuid can point to the same
    thumb in case of hardlinked reprint).

    :return: number of entries removed.
    """
    with _lock:
        data = load()
        to_remove = [fw for fw, ja5 in data.items() if ja5 == jobacct5_uuid]
        if not to_remove:
            return 0
        for fw in to_remove:
            del data[fw]
        _save_atomic(data)
        logger.info(
            "job_mapping: purged %d entries pointing to %s",
            len(to_remove), jobacct5_uuid,
        )
        return len(to_remove)


def remove_orphans(known_firmware_uuids: set[str]) -> int:
    """Purge entries whose ``firmware_uuid`` no longer appears in the queue.

    Used by the cleanup CLI with ``--older-than`` when we want to
    clean up the mapping too. The caller passes the set of
    ``firmware_uuid`` currently alive in all queues — the
    others are considered purged.

    :return: number of entries removed.
    """
    with _lock:
        data = load()
        to_remove = [fw for fw in data if fw not in known_firmware_uuids]
        if not to_remove:
            return 0
        for fw in to_remove:
            del data[fw]
        _save_atomic(data)
        logger.info("job_mapping: purged %d orphan entries", len(to_remove))
        return len(to_remove)
