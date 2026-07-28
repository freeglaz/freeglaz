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

"""ICC profiling job (Print & Scan) in a background thread.

P5. The Z9 can only do one profiling at a time (locks
heads + paper + scanner), so we maintain a module-level
**singleton** for the active job.

Architecture identical to ``calibration_jobs`` (P4):
- ``client.paper.profile(ref, ..., on_progress=...)`` is synchronous
  blocking (~7-10 min depending on workflow). We run it in a
  daemon ``threading.Thread``.
- The ``on_progress`` callback is called from this thread at each
  firmware SOAP getStatus poll (every 10s by default).
- Events are pushed into a thread-safe ``queue.Queue``. The
  SSE subscribers read via ``asyncio.run_in_executor``.
- Only one active job at a time (events are consumed). The global status
  bar polls ``current()`` for its "Profiling in progress" badge.

No cancel in V1 — the profile operation is non-interruptible on the
firmware side (cf brief P5 + P4 CLC consistency). The "Cancel" button
of step 3 of the wizard is ``disabled`` with an explicit tooltip.

State mapping:
- ``"starting"`` : thread started, first firmware poll not received
- ``"running"``  : at least one on_progress received, in progress
- ``"done"``     : workflow finished successfully (profile_uuid retrieved)
- ``"error"``    : lib exception (Z9Error, timeout, etc.)
"""
import logging
import queue
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Sentinel to signal the end of the events stream (consumed on the SSE side)
_END = object()

# Mapping webapp bool → SOAP/lib string for gloss_enhancer (cf
# papers.py ICC route, on FULLPAGE value).
_GE_TO_LIB = {True: "FULLPAGE", False: "OFF"}

# Mapping webapp bool → lib string for max_detail.
_MAX_DETAIL_TO_LIB = {True: "ON", False: "OFF"}

# Estimated durations per workflow (seconds). Used for
# ``ProfileResponse.estimated_duration_s`` and for the frontend ETA
# before the first firmware poll.
ESTIMATED_DURATION_S = {
    "PRINT_AND_SCAN": 600,  # ~10 min
    "PRINT_ONLY":     120,  # ~2 min (print chart only)
    "SCAN_ONLY":      420,  # ~7 min
}


class ProfileJob:
    """State of a profiling in progress or finished.

    Attributes:
    - ``id`` : stable UUID4 for this job
    - ``mediaid`` : target paper
    - ``workflow`` : ``"PRINT_AND_SCAN"`` | ``"SCAN_ONLY"``
    - ``profile_name`` : name of the requested profile
    - ``gloss_enhancer`` : bool (FULLPAGE if True, OFF otherwise)
    - ``state`` : ``"starting"`` | ``"running"`` | ``"done"`` | ``"error"``
    - ``progress`` : int 0-100 or -1 if N/A
    - ``process`` : current firmware phase (PRINTING, DRYING, …)
    - ``started_at`` : Unix timestamp
    - ``elapsed`` : seconds since the start (updated at each poll)
    - ``result`` : return dict of ``paper.profile()`` after success, else None
    - ``error`` : error message if state=="error", else None
    - ``events_queue`` : ``queue.Queue`` of events for SSE
    """

    def __init__(
        self,
        mediaid: str,
        workflow: str,
        profile_name: str,
        gloss_enhancer: bool,
        quality: str,
        max_detail: bool,
        color_space: str,
    ):
        self.id: str = str(uuid.uuid4())
        self.mediaid: str = mediaid
        self.workflow: str = workflow
        self.profile_name: str = profile_name
        self.gloss_enhancer: bool = gloss_enhancer
        self.quality: str = quality
        self.max_detail: bool = max_detail
        self.color_space: str = color_space
        self.state: str = "starting"
        self.progress: int = -1
        self.process: str = ""
        self.started_at: float = time.time()
        self.elapsed: float = 0.0
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.events_queue: queue.Queue = queue.Queue(maxsize=256)

    def snapshot(self) -> dict:
        """JSON-friendly representation for REST endpoints + initial SSE snapshot."""
        return {
            "id": self.id,
            "mediaid": self.mediaid,
            "workflow": self.workflow,
            "profile_name": self.profile_name,
            "gloss_enhancer": self.gloss_enhancer,
            "state": self.state,
            "progress": self.progress,
            "process": self.process,
            "started_at": self.started_at,
            "elapsed": self.elapsed,
            "result": self.result,
            "error": self.error,
        }

    def _emit(self, event_type: str, data: dict) -> None:
        """Push an event into the queue. Lossy if queue full."""
        try:
            self.events_queue.put_nowait({"type": event_type, "data": data})
        except queue.Full:
            logger.warning(
                "profile_jobs: events queue full for job %s, event dropped",
                self.id,
            )

    def finish_stream(self) -> None:
        """Signal the end of the SSE stream (the subscriber will exit cleanly)."""
        try:
            self.events_queue.put_nowait(_END)
        except queue.Full:
            pass


# ─── Module-level singleton ───────────────────────────────────────────

_active: Optional[ProfileJob] = None
_lock = threading.RLock()


def current() -> Optional[ProfileJob]:
    """Active job or None. Includes finished jobs as long as no new one
    has been started (allows retrieving the result after SSE
    reconnection)."""
    with _lock:
        return _active


def is_busy() -> bool:
    """True if a profiling is still in progress (state starting/running)."""
    with _lock:
        return _active is not None and _active.state in ("starting", "running")


def start(
    mediaid: str,
    workflow: str,
    profile_name: str,
    gloss_enhancer: bool,
    quality: str,
    max_detail: bool,
    color_space: str,
    z9_client,
) -> ProfileJob:
    """Start a profiling in a daemon thread. Raises if already in progress.

    :raises RuntimeError: if a profiling is already in progress
    """
    global _active
    with _lock:
        if is_busy():
            raise RuntimeError(
                f"A profiling is already in progress on {_active.mediaid}",
            )
        job = ProfileJob(
            mediaid=mediaid,
            workflow=workflow,
            profile_name=profile_name,
            gloss_enhancer=gloss_enhancer,
            quality=quality,
            max_detail=max_detail,
            color_space=color_space,
        )
        _active = job

    job._emit("profile_started", {
        "mediaid": mediaid,
        "id": job.id,
        "workflow": workflow,
        "estimated_duration_s": ESTIMATED_DURATION_S.get(workflow, 600),
    })

    t = threading.Thread(
        target=_run_job, args=(job, z9_client), daemon=True,
        name=f"profile-{job.id[:8]}",
    )
    t.start()
    logger.info(
        "profile_jobs: started job %s on %s (workflow=%s, ge=%s)",
        job.id, mediaid, workflow, gloss_enhancer,
    )
    return job


def _run_job(job: ProfileJob, z9_client) -> None:
    """Function run in the daemon thread. Calls paper.profile
    and emits events as progress advances."""

    def on_progress(d: dict) -> None:
        # Callback from the paper.profile thread. Updates the job + emits
        # an SSE progress event.
        job.state = "running"
        job.progress = d.get("percent", -1)
        job.process = d.get("process") or ""
        job.elapsed = d.get("elapsed", 0.0)
        job._emit("progress", {
            "percent": job.progress,
            "process": job.process,
            "elapsed": job.elapsed,
            "operation_id": d.get("operation_id"),
        })

    try:
        result = z9_client.paper.profile(
            ref=job.mediaid,
            workflow_kind=job.workflow,
            profile_name=job.profile_name,
            gloss_enhancer=_GE_TO_LIB[job.gloss_enhancer],
            quality=job.quality,
            max_detail=_MAX_DETAIL_TO_LIB[job.max_detail],
            color_space=job.color_space,
            on_progress=on_progress,
            poll_interval=10,
            timeout=1500,
        )
        job.state = "done"
        job.result = result
        job.elapsed = result.get("elapsed", job.elapsed)
        # Sync-on-event (#1): the profiling created/changed a slot profile
        # → refresh the mirror cache of THIS paper (forced: the
        # getMediumListVersion gate is blind to this change). Best-effort:
        # never fails the already-succeeded job.
        try:
            from lib.z9_client import store as _store
            _store.refetch_paper(z9_client, job.mediaid)
        except Exception:  # noqa: BLE001
            logger.warning("refetch_paper after profile failed (mediaid=%s)",
                           job.mediaid, exc_info=True)
        job._emit("profile_finished", {
            "outcome": "success",
            "profile_uuid": result.get("profile_uuid"),
            "profile_icc_name": result.get("profile_icc_name"),
            "profile_name": result.get("profile_name"),
            "profile_date": result.get("profile_date"),
            "gloss_enhancer": result.get("gloss_enhancer"),
            "elapsed": job.elapsed,
        })
        logger.info(
            "profile_jobs: job %s SUCCESS in %.1fs (icc=%s)",
            job.id, job.elapsed, result.get("profile_icc_name"),
        )
    except Exception as e:  # noqa: BLE001 — catch everything so the thread does not crash
        job.state = "error"
        job.error = str(e)
        job._emit("profile_finished", {
            "outcome": "error",
            "message": str(e),
            "process": job.process,
            "elapsed": time.time() - job.started_at,
        })
        logger.warning(
            "profile_jobs: job %s ERROR in phase %s: %s",
            job.id, job.process or "?", e,
        )
    finally:
        # Always close the stream to release the SSE generator
        job.finish_stream()


def reset_for_tests() -> None:
    """Reset the singleton state — for pytest tests only."""
    global _active
    with _lock:
        _active = None
