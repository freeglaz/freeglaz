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

"""Endpoints /api/jobs — Z9 job queue (Phase 2).

Exposes the snapshot maintained by ``Z9JobsSubscriber`` and the control
operations (pause/resume/cancel/remove/preview). Write operations
are proxied to the ``Z9Client.jobs`` lib without any intermediate
business logic — the lib handles the firmware quirks (404 re-discovery, 502
silent on Deleted jobs, etc.) and we surface the appropriate HTTP
statuses.

Auth: the operations are accessible without auth (the Z9 itself does not
require auth for queue commands). The ``preview`` propagates
a 401 if the Z9 requires admin auth (cf. ``Z9_ADMIN_PWD``).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.exceptions import Z9AuthError, Z9RESTError
from webapp.backend.routes.status import get_z9  # reuses the same Depends

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# Strict UUID4 — prevents injection of arbitrary path into the Z9 URL.
_UUID_REGEX = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _require_z9(z9: Optional[Z9Client] = Depends(get_z9)) -> Z9Client:
    """Wrapper that raises 503 if the Z9 is not configured. Allows
    chaining with ``app.dependency_overrides[get_z9]`` to inject a
    mock in tests."""
    if z9 is None:
        raise HTTPException(503, detail="Z9 not configured (Z9_HOST missing)")
    return z9


def _get_subscriber(request: Request):
    """Get the Z9JobsSubscriber singleton (None if not started)."""
    return getattr(request.app.state, "z9_jobs_subscriber", None)


# ─── Snapshot ─────────────────────────────────────────────────────────


def _enrich_jobs(jobs: list[dict]) -> list[dict]:
    """Add ``preview_source`` to each job of the snapshot.

    Possible values:
    - ``"local"``  : known mapping + local thumb present on disk
    - ``"firmware"`` : no local thumb, but the firmware exposes
      one via the ``preview_uri`` field (PIWS).
    - ``None`` : no preview available (unknown mapping and no
      firmware preview_uri).

    We enrich each dict in place (but we return a new list
    to avoid mutating the subscriber's snapshot, which could be read
    concurrently by other consumers).
    """
    from webapp.backend.services import job_mapping, job_preview

    mapping = job_mapping.load()
    out: list[dict] = []
    for j in jobs:
        job = dict(j)  # defensive copy
        firmware_uuid = job.get("uuid", "")
        jobacct5 = mapping.get(firmware_uuid)
        if jobacct5 and job_preview.thumbnail_path(jobacct5).exists():
            job["preview_source"] = "local"
        elif job.get("preview_uri"):
            job["preview_source"] = "firmware"
        else:
            job["preview_source"] = None
        # can_reprint flag (the firmware only keeps the
        # resource for Completed / Cancelled).
        job["can_reprint"] = _can_reprint(job)
        out.append(job)
    return out


@router.get("")
def get_jobs_snapshot(request: Request) -> dict:
    """Return the latest snapshot of the Z9 job queue.

    Purely in-memory read (fed by ``Z9JobsSubscriber``).
    Includes ``_meta`` with ``consecutive_failures`` and ``last_success_at``
    so the UI can detect staleness.

    Each job is enriched with a ``preview_source`` field
    (``"local"`` / ``"firmware"`` / ``None``) so the UI knows where
    to get the thumbnail (cf. "Source freeglaz" vs "Source
    firmware Z9" badge in the tooltip).
    """
    sub = _get_subscriber(request)
    if sub is None:
        # Z9 not configured, or subscriber not started (fail at start).
        # Return an empty snapshot to avoid breaking the frontend.
        return {
            "queue_status": "Unknown",
            "number_of_jobs": 0,
            "modification_number": 0,
            "timestamp": "",
            "jobs": [],
            "_meta": {
                "consecutive_failures": 0,
                "last_success_at": None,
                "poll_interval_seconds": 0.0,
                "subscriber_started": False,
            },
        }
    snap = sub.current_snapshot()
    snap["jobs"] = _enrich_jobs(snap.get("jobs", []))
    snap["_meta"]["subscriber_started"] = True
    return snap


# ─── Queue control ───────────────────────────────────────────────────


@router.post("/queue/pause", status_code=202)
def pause_queue(z9: Z9Client = Depends(_require_z9)) -> dict:
    """Pause the Z9 job queue. Idempotent."""
    try:
        z9.jobs.pause_queue()
    except Z9Error as e:
        logger.warning("pause_queue failed: %s", e)
        raise HTTPException(502, detail=f"Z9 pause failed: {e}")
    return {"ok": True, "action": "pause"}


@router.post("/queue/resume", status_code=202)
def resume_queue(z9: Z9Client = Depends(_require_z9)) -> dict:
    """Resume the Z9 job queue. Idempotent."""
    try:
        z9.jobs.resume_queue()
    except Z9Error as e:
        logger.warning("resume_queue failed: %s", e)
        raise HTTPException(502, detail=f"Z9 resume failed: {e}")
    return {"ok": True, "action": "resume"}


@router.delete("/queue", status_code=200)
def clear_queue(z9: Z9Client = Depends(_require_z9)) -> dict:
    """Clear the Z9 job queue — pivot 25/05/2026: individual DELETE
    loop instead of the HP batch ``/control/removeJobs`` (which
    returned a persistent 405, cf. ``JobQueueOps.clear_all``).

    Best-effort on jobs one by one, with 1.5 s throttling between
    operations (firmware respect). Individual failures are logged
    but do not interrupt the loop.

    Responses (always HTTP 200, except 503 if Z9 missing):
    - ``{ok: true, removed_count: N, failed_count: M, queue_uuid: "..."}``
      — N successes, M failures. The frontend derives its toast from these
      counters (total / partial / failed success).
    """
    try:
        removed, failed = z9.jobs.clear_all()
    except Z9Error as e:
        # Error OUTSIDE the loop (e.g. snapshot fail). Individual
        # failures in the loop are already captured in ``failed``.
        logger.warning("clear_queue: pre-loop error: %s", e)
        raise HTTPException(502, detail=f"Z9 clear queue failed: {e}")
    queue_uuid = z9.jobs.queue_uuid
    logger.info(
        "clear_queue: %d removed, %d failed (queue=%s)",
        removed, failed, queue_uuid,
    )
    return {
        "ok": True,
        "removed_count": removed,
        "failed_count": failed,
        "queue_uuid": queue_uuid,
    }


# ─── Job control ─────────────────────────────────────────────────────


@router.post("/{job_uuid}/cancel", status_code=202)
def cancel_job(
    job_uuid: str = Path(..., pattern=_UUID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Cancel a job. The job stays in the queue with status Cancelled.

    Returns ``{ok: true, no_op: false}`` if the firmware accepted the
    command, ``{ok: true, no_op: true}`` if the firmware responded 502
    (job already Deleted — cf. quirks in API doc). Raises
    on other error.
    """
    try:
        accepted = z9.jobs.cancel_job(job_uuid)
    except Z9Error as e:
        logger.warning("cancel_job(%s) failed: %s", job_uuid, e)
        raise HTTPException(502, detail=f"Z9 cancel failed: {e}")
    return {"ok": True, "no_op": not accepted, "action": "cancel"}


@router.post("/{job_uuid}/remove", status_code=202)
def remove_job(
    job_uuid: str = Path(..., pattern=_UUID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Remove a job from the queue (disappears).

    See ``cancel_job`` for the ``no_op`` semantics. Also invalidates
    the firmware preview cache — the job can no longer be
    fetched on the firmware side once removed, and keeping a stale
    cache entry has no value.
    """
    from webapp.backend.services import preview_cache
    try:
        accepted = z9.jobs.remove_job(job_uuid)
    except Z9Error as e:
        logger.warning("remove_job(%s) failed: %s", job_uuid, e)
        raise HTTPException(502, detail=f"Z9 remove failed: {e}")
    preview_cache.invalidate(job_uuid)
    return {"ok": True, "no_op": not accepted, "action": "remove"}


@router.post("/{job_uuid}/reprint", status_code=200)
def reprint_job_endpoint(
    request: Request,
    job_uuid: str = Path(..., pattern=_UUID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Reprint a job.

    The firmware creates a new job in the queue with a different
    UUID (and ``Priority=3``). We identify it via polling
    ``/jobs/all`` post-reprint then we hardlink the freeglaz thumbnail
    of the original job to the new one (mapping ``new_firmware_uuid →
    jobacct5_original``) so the preview stays accessible.

    Success response:
      - 200 ``{original_uuid, new_uuid}`` if all OK
      - 200 ``{original_uuid, new_uuid: null, warning}`` if reprint
        submitted but the new job is not yet visible in the
        queue (the frontend can refresh in 1-2 s)

    Error responses (structured JSON, never "Internal Server Error"):
      - 422 ``{error, stage, detail, ...}`` if not reprint-able
      - 502 ``{error, stage, detail, ...}`` if Z9 error
      - 500 ``{error, stage: <step>, detail, firmware_succeeded, ...}``
        with a JSON body usable on the UI side on unexpected exception
    """
    # All steps of the flow are wrapped in a global try/except
    # that turns ANY non-HTTPException into a structured JSONResponse 500.
    # Indispensable because a silent crash is masked
    # by the default FastAPI handler (text/plain "Internal Server
    # Error") and the macOS .app has no terminal to see the
    # traceback.
    from webapp.backend.services import job_mapping

    state = {
        "stage": "init",
        "firmware_succeeded": False,
        "new_uuid": None,
        "job_uuid": job_uuid,
    }
    logger.info("reprint: start job_uuid=%s", job_uuid)

    try:
        # ─── 1. Check that the job is reprint-able ──────────────
        state["stage"] = "can_reprint_check"
        sub = _get_subscriber(request)
        if sub is not None:
            snap = sub.current_snapshot()
            job = next(
                (j for j in snap.get("jobs", []) if j.get("uuid") == job_uuid),
                None,
            )
            if job is not None and not _can_reprint(job):
                logger.info(
                    "reprint: refused 422 — job status=%r not reprint-able",
                    job.get("status"),
                )
                raise HTTPException(
                    422,
                    detail=(
                        f"Job status={job.get('status')!r} not reprint-able. "
                        "The firmware only keeps the resource for "
                        "Completed / Cancelled jobs."
                    ),
                )

        # ─── 2. Snapshot BEFORE reprint ────────────────────────────
        state["stage"] = "snapshot_before"
        try:
            snap_before = z9.jobs.get_jobs_snapshot()
        except Z9Error as e:
            logger.warning("reprint: snapshot_before failed: %s", e)
            raise HTTPException(
                502, detail=f"Z9 snapshot before reprint failed: {e}",
            )
        before_uuids = {
            j["uuid"] for j in snap_before.get("jobs", []) if j.get("uuid")
        }
        before_mod = snap_before.get("modification_number", 0)
        logger.info(
            "reprint: snapshot_before mod=%d before_uuids_count=%d",
            before_mod, len(before_uuids),
        )

        # ─── 3. Trigger reprint on firmware ──────────────────────
        state["stage"] = "firmware_call"
        try:
            firmware_response = z9.jobs.reprint_job(job_uuid, copies=1)
        except Z9Error as e:
            logger.warning("reprint: firmware_call failed: %s", e)
            raise HTTPException(502, detail=f"Z9 reprint failed: {e}")
        state["firmware_succeeded"] = True
        logger.info(
            "reprint: firmware OK — status=%r original_uuid=%s",
            firmware_response.get("status"), firmware_response.get("original_uuid"),
        )

        # ─── 4. Identify the new firmware_uuid ───────────────
        state["stage"] = "find_new_uuid"
        new_uuid = z9.jobs.find_new_reprint_job(before_uuids, before_mod)
        state["new_uuid"] = new_uuid
        logger.info("reprint: find_new_uuid result=%r", new_uuid)

        # ─── 5. Inherit the thumb via mapping ──────────────────────
        if new_uuid is not None:
            state["stage"] = "thumb_inheritance"
            original_jobacct5 = job_mapping.lookup(job_uuid)
            logger.info(
                "reprint: mapping lookup original_uuid=%s → jobacct5=%r",
                job_uuid, original_jobacct5,
            )
            if original_jobacct5 is not None:
                job_mapping.register(new_uuid, original_jobacct5)
                logger.info(
                    "reprint: mapping registered %s → %s "
                    "(inherited from original %s)",
                    new_uuid, original_jobacct5, job_uuid,
                )

        # ─── 6. Response ───────────────────────────────────────────
        state["stage"] = "respond"
        if new_uuid is None:
            logger.warning(
                "reprint: new job not yet visible — UI will refresh "
                "manually (firmware accepted, the job will appear)"
            )
            return {
                "original_uuid": job_uuid,
                "new_uuid": None,
                "warning": "Reprint submitted, new job not yet visible",
            }
        logger.info(
            "reprint: success original=%s new=%s", job_uuid, new_uuid,
        )
        return {"original_uuid": job_uuid, "new_uuid": new_uuid}

    except HTTPException:
        # Business errors already structured (422, 502) — we let them
        # pass, FastAPI turns them into JSON.
        raise
    except Exception as exc:  # noqa: BLE001
        # CATCH-ALL: an unexpected crash (KeyError, AttributeError,
        # IOError, XML parsing, etc.) must NEVER end up as
        # text/plain "Internal Server Error" — the frontend must receive
        # usable JSON, and the log file must contain the full
        # traceback for debug.
        logger.exception(
            "reprint: unexpected CRASH stage=%r — traceback above",
            state.get("stage"),
        )
        return _reprint_error_response(state, exc)


def _reprint_error_response(state: dict, exc: Exception):
    """Build a structured JSONResponse 500 for reprint crashes.

    The frontend can show a contextual message ("Reprint submitted
    on the firmware side but the thumb mapping could not be created")
    rather than a generic message.
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={
            "error": "reprint_failed",
            "stage": state.get("stage"),
            "detail": f"{type(exc).__name__}: {exc}",
            "firmware_succeeded": state.get("firmware_succeeded", False),
            "new_uuid": state.get("new_uuid"),
            "original_uuid": state.get("job_uuid"),
        },
    )


_REPRINTABLE_STATUSES = frozenset({"Completed", "Cancelled"})

# Known firmware statuses that we do NOT whitelist (jobs still pending
# or active) — used for defensive logging to detect new unexpected
# statuses that would appear in prod.
_KNOWN_NON_REPRINTABLE_STATUSES = frozenset({
    "Pending", "Paused", "WaitingToPrint", "Active", "Processing",
    "Held", "Deleted", "Unknown", "",
})


def _can_reprint(job: dict) -> bool:
    """Decide whether a job can be reprinted. **Strict whitelist**.

    Post-validation patch 25/05/2026 (observed bug: reprint
    accepted on a Pending job while the Z9 queue is paused, with a side
    effect on the firmware side on the source job's status).

    Policy:
    - True ONLY if ``status`` ∈ {Completed, Cancelled} — that is,
      the jobs where the firmware still keeps the resource AND where the
      operation makes sense for the user (reprint something that has
      already been printed).
    - False for EVERYTHING else: Deleted (firmware-purged), Pending /
      Paused / WaitingToPrint / Active / Processing / Held / Unknown /
      empty status / future unknown status.

    We log a warning if an unknown status appears — allows tracing
    in prod if HP adds a new PIWS JobStatus (already observed: the
    firmware quirks are not fixed).
    """
    status = job.get("status", "")
    if status and status not in _KNOWN_NON_REPRINTABLE_STATUSES and status not in _REPRINTABLE_STATUSES:
        logger.warning(
            "_can_reprint: unexpected firmware status %r — defaults to non "
            "reprintable. If it is a legitimate status, add it to the whitelist.",
            status,
        )
    return status in ("Completed", "Cancelled")


# ─── Preview ──────────────────────────────────────────────────────────


@router.get("/{job_uuid}/preview")
def get_job_preview(
    job_uuid: str = Path(..., pattern=_UUID_REGEX),
    page: int = Query(1, ge=1, le=100),
    refresh: int = Query(0, ge=0, le=1),
    z9: Z9Client = Depends(_require_z9),
) -> Response:
    """Get a job's preview thumbnail — local-first, firmware fallback.

    Resolution strategy:

    1. **Local**: if we have a mapping entry for this ``job_uuid`` (the
       job was submitted via freeglaz), we serve directly the local
       JPEG thumb ``webapp/data/job_previews/<jobacct5>.jpg``.
       Advantage: survives firmware purges (``Deleted`` jobs), source
       faithful to the uploaded file (not the post-hack PDF).

    2. **Firmware** (cached): otherwise (jobs submitted by another application), we
       first look in the in-memory cache (TTL 10 min, max 200
       entries — cf. ``preview_cache``). On miss, proxy to
       ``z9.jobs.get_job_preview`` (admin auth via ``Z9_ADMIN_PWD``),
       cache storage, and response to the client. Returns 401 if no
       auth, 410 if Deleted on the firmware side.

    :param refresh: if 1, invalidate the firmware cache for this job before
        the lookup. Useful for live debug or to force a re-fetch
        after suspected staleness.
    """
    # ─── 1. Local lookup ──────────────────────────────────────────
    from webapp.backend.services import job_mapping, job_preview, preview_cache

    jobacct5 = job_mapping.lookup(job_uuid)
    if jobacct5 is not None:
        local_path = job_preview.thumbnail_path(jobacct5)
        if local_path.exists():
            try:
                content = local_path.read_bytes()
                return Response(content=content, media_type="image/jpeg")
            except OSError:
                logger.exception("Local thumb read %s failed — firmware fallback", local_path)
                # Fall through to the firmware fallback below.

    # ─── 2. Firmware fallback with TTL cache ─────────────────────
    # The fetcher is called under the cache lock ONLY on a miss
    # — protects the Z9 against hammering (project memory: Z9 can
    # crash under excess requests, reboot ~10 min).
    def _fetch_firmware() -> tuple[bytes, str]:
        data = z9.jobs.get_job_preview(job_uuid, page=page)
        # Guess the Content-Type from magic bytes.
        if data[:4] == b"\x89PNG":
            return data, "image/png"
        if data[:2] == b"\xff\xd8":
            return data, "image/jpeg"
        return data, "application/octet-stream"

    try:
        data, content_type = preview_cache.fetch_or_cache(
            job_uuid, _fetch_firmware, force_refresh=bool(refresh),
        )
    except Z9AuthError as e:
        raise HTTPException(
            401,
            detail=(
                f"Z9 requires admin auth for the preview: {e}. "
                "Configure Z9_ADMIN_PWD on the backend."
            ),
        )
    except Z9RESTError as e:
        if e.status_code == 500:
            # Job likely Deleted on the firmware side → 410 Gone.
            # NB: we NEVER cache errors (cf. preview_cache —
            # the fetcher raises, the exception propagates without touching the cache).
            raise HTTPException(410, detail=f"Job preview unavailable (Deleted?): {e}")
        raise HTTPException(502, detail=f"Z9 preview failed: {e}")
    except Z9Error as e:
        raise HTTPException(502, detail=f"Z9 preview failed: {e}")

    return Response(content=data, media_type=content_type)
