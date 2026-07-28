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

"""Post-submit orchestration: JobAcct5 extraction + firmware
UUID detection + register mapping.

Phase 3 — Part 2 (thumbnails). When a job is submitted
via the webapp worker:

1. The PRN is written (by ``PrintOps.build_prn``) with a ``JobAcct5``
   UUID4 generated on the lib side (cf. ``lib/z9_client/printing.py``). We extract
   this UUID by re-reading the ASCII PJL header of the PRN.
2. ``PrintOps.send`` sends the PRN to the Z9 (port 9100) — the firmware
   assigns a new internal ``firmware_uuid`` to the job in the queue.
3. We poll ``/jobs/all`` until the new ``firmware_uuid`` appears
   (max 10 s). We identify it as the job that did not exist before
   submit.
4. We register ``{firmware_uuid → jobacct5}`` in ``job_mapping``,
   and trigger generation of the freeglaz thumbnail.

All of this is best-effort: a failure at any step does not prevent
printing from continuing; the thumb will just be unavailable on the
``/api/jobs/{uuid}/preview`` side and the UI will fall back to firmware
preview or 404.
"""
import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Optional

from lib.z9_client import Z9Client, Z9Error

from webapp.backend.services import job_mapping, job_preview

logger = logging.getLogger(__name__)

# Regex capturing the JobAcct5 in the PJL header — format observed
# empirically:  @PJL SET JOBATTR = "JobAcct5=UUID-IN-UPPERCASE"
_JOBACCT5_RE = re.compile(
    rb'JobAcct5\s*=\s*([A-F0-9-]{36})',
    re.IGNORECASE,
)

# How many bytes to read at the head of the PRN to look for the JobAcct5
# (the PJL header is typically a few KB).
_PJL_HEADER_READ_BYTES = 8192

# Polling /jobs/all after submit to detect the new firmware_uuid.
_POLL_INTERVAL_S = 1.0
_POLL_TIMEOUT_S = 10.0


def extract_jobacct5_from_prn(prn_path: Path) -> Optional[str]:
    """Read the ASCII PJL header at the head of the PRN and extract ``JobAcct5``.

    The PRN is a binary file with an ASCII PJL header at the start
    (a few KB) before the binary PostScript/PDF payload. We read
    just the first bytes to match the regex — no need to
    load the ~50 MB of a photo PRN.

    :return: uppercase UUID with hyphens (e.g. ``"A1B2C3D4-..."``), or
             ``None`` if the PRN does not exist / the header lacks the
             JobAcct5 tag.
    """
    if not prn_path.exists():
        logger.warning("extract_jobacct5: PRN introuvable %s", prn_path)
        return None
    try:
        with open(prn_path, "rb") as f:
            header = f.read(_PJL_HEADER_READ_BYTES)
    except OSError:
        logger.exception("extract_jobacct5: PRN read failed %s", prn_path)
        return None
    m = _JOBACCT5_RE.search(header)
    if not m:
        logger.warning(
            "extract_jobacct5: pas de JobAcct5 dans header PRN %s",
            prn_path,
        )
        return None
    return m.group(1).decode("ascii").upper()


def detect_new_firmware_uuid(
    z9: Z9Client,
    before_uuids: set[str],
    *,
    poll_interval_s: float = _POLL_INTERVAL_S,
    timeout_s: float = _POLL_TIMEOUT_S,
) -> Optional[str]:
    """Poll ``/jobs/all`` until a new ``firmware_uuid`` appears.

    Strategy: the caller snapshots ``before_uuids`` (all UUIDs
    present before submit), we poll after submit, we identify the first
    newly arrived UUID. If ``timeout_s`` is exceeded without a diff, we return
    ``None``.

    :param z9: Z9 client (with ``z9.jobs.get_jobs_snapshot()`` configured)
    :param before_uuids: set of ``firmware_uuid`` known BEFORE the submit
    :param poll_interval_s: delay between 2 polls (default 1 s)
    :param timeout_s: global timeout (default 10 s)
    :return: ``firmware_uuid`` of the new job, or ``None`` on timeout
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            snap = z9.jobs.get_jobs_snapshot()
        except Z9Error as e:
            logger.warning("detect_new_firmware_uuid: Z9Error %s", e)
            time.sleep(poll_interval_s)
            continue
        current_uuids = {j["uuid"] for j in snap.get("jobs", []) if j.get("uuid")}
        new = current_uuids - before_uuids
        if new:
            # If several appear at the same time (rare), we take one
            # arbitrarily — the caller should avoid this case by serializing
            # submits on the UI side. Logs a warning for visibility.
            if len(new) > 1:
                logger.warning(
                    "detect_new_firmware_uuid: %d nouveaux UUID en // %r",
                    len(new), new,
                )
            return next(iter(new))
        time.sleep(poll_interval_s)
    logger.warning(
        "detect_new_firmware_uuid: timeout %.0fs sans nouveau job",
        timeout_s,
    )
    return None


def snapshot_current_uuids(z9: Z9Client) -> set[str]:
    """Helper: return the ``firmware_uuid`` currently in the queue.

    Used as ``before_uuids`` before submit. Returns ``set()`` on
    Z9 error (the caller will just act as if there was nothing before
    → first new UUID = the right one).
    """
    try:
        snap = z9.jobs.get_jobs_snapshot()
    except Z9Error as e:
        logger.warning("snapshot_current_uuids: Z9Error %s — set vide", e)
        return set()
    return {j["uuid"] for j in snap.get("jobs", []) if j.get("uuid")}


def finalize_submitted_job(
    z9: Z9Client,
    source_path: Path,
    prn_path: Path,
    before_uuids: set[str],
    *,
    composite_kwargs: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Full post-submit pipeline: thumb + mapping. Best-effort.

    Called by the worker just after ``PrintOps.send`` has
    returned OK. Synchronous (the caller runs it in ``to_thread``).

    Steps:
      1. Extract JobAcct5 from the PRN (PJL header regex)
      2. Detect the new firmware_uuid (poll /jobs/all up to 10s)
      3. Register mapping {firmware_uuid → jobacct5}
      4. Generate the JPEG thumbnail

    :param composite_kwargs: if provided (dict), we generate a **composite
        page render** (P3.G) via ``job_preview.render_page_composite`` instead
        of a resize thumb of the source image. The caller must
        pass the geometric metadata (sheet/image dims, position,
        media_source, gloss_enhancer). If None, fall back on the simple
        thumb (case where geometry is not available, e.g. legacy code
        or unit tests).

    :return: ``(jobacct5, firmware_uuid)`` — one or both may
             be ``None`` on failure.
    """
    jobacct5 = extract_jobacct5_from_prn(prn_path)
    if jobacct5 is None:
        logger.warning("finalize_submitted_job: pas de JobAcct5 → skip thumb+mapping")
        return None, None

    # Generation of the local freeglaz preview.
    # If we have the full geometry (standard webapp worker case post-P3.G),
    # we generate a page-realistic composite. Otherwise, fall back to simple thumb.
    if composite_kwargs:
        job_preview.render_page_composite(source_path, jobacct5, **composite_kwargs)
    else:
        job_preview.generate_thumbnail(source_path, jobacct5)

    firmware_uuid = detect_new_firmware_uuid(z9, before_uuids)
    if firmware_uuid is None:
        logger.warning(
            "finalize_submitted_job: new firmware_uuid not detected "
            "in the queue — mapping not created (thumb exists but will be "
            "inaccessible from /api/jobs/.../preview)"
        )
        return jobacct5, None

    job_mapping.register(firmware_uuid, jobacct5)
    return jobacct5, firmware_uuid


async def finalize_submitted_job_async(
    z9: Z9Client,
    source_path: Path,
    prn_path: Path,
    before_uuids: set[str],
    *,
    composite_kwargs: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Async variant for calling from an asyncio worker (run_real_job).

    Delegates the sync work to ``asyncio.to_thread`` so as not to block
    the event loop during the 1 Hz / 10 s polling.
    """
    return await asyncio.to_thread(
        finalize_submitted_job, z9, source_path, prn_path, before_uuids,
        composite_kwargs=composite_kwargs,
    )
