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

"""Color linear calibration (CLC) job in a background thread.

P4. The Z9 can only do one calibration at a time (the
calibration locks the head + paper), so we maintain a
module-level **singleton** for the active job.

Architecture:
- ``client.paper.calibrate(ref, on_progress, ...)`` is synchronous blocking
  (~5-10 min). We run it in a daemon ``threading.Thread``.
- The ``on_progress`` callback is called from this thread at each firmware
  poll (every 10s by default).
- Progress events are pushed into a thread-safe ``queue.Queue``
  per job. The SSE subscribers read via
  ``asyncio.run_in_executor`` (non-blocking on the event loop side).
- Only one active subscriber at a time (events are consumed). The
  global status bar polls ``current()`` for its badge.

No cancel in V1 — the calibrate operation is non-interruptible on the
firmware side (cf brief P4).
"""
import logging
import queue
import threading
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Sentinel to signal the end of the events stream (consumed on the SSE side)
_END = object()


class CalibrationJob:
    """State of a calibration in progress or finished.

    Attributes:
    - ``id`` : stable UUID4 for this job
    - ``mediaid`` : target paper
    - ``state`` : ``"starting"`` | ``"running"`` | ``"done"`` | ``"error"``
    - ``progress`` : int 0-100 or -1 if N/A
    - ``process`` : current firmware phase ("PRINTING", "DRYING", …)
    - ``started_at`` : Unix timestamp
    - ``elapsed`` : seconds since the start (updated at each poll)
    - ``result`` : ``paper.calibrate()`` dict after success, else None
    - ``error`` : error message if state=="error", else None
    - ``events_queue`` : ``queue.Queue`` of events to broadcast to SSE
    """

    def __init__(self, mediaid: str):
        self.id: str = str(uuid.uuid4())
        self.mediaid: str = mediaid
        self.state: str = "starting"
        self.progress: int = -1
        self.process: str = ""
        self.started_at: float = time.time()
        self.elapsed: float = 0.0
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.events_queue: queue.Queue = queue.Queue(maxsize=256)
        self._subscriber_attached: bool = False

    def snapshot(self) -> dict:
        """JSON-friendly representation for the REST endpoints."""
        return {
            "id": self.id,
            "mediaid": self.mediaid,
            "state": self.state,
            "progress": self.progress,
            "process": self.process,
            "started_at": self.started_at,
            "elapsed": self.elapsed,
            "result": self.result,
            "error": self.error,
        }

    def _emit(self, event_type: str, data: dict) -> None:
        """Push an event into the queue. Lossy if queue full (>256
        events buffered), harmless for a 10 min calibration."""
        try:
            self.events_queue.put_nowait({"type": event_type, "data": data})
        except queue.Full:
            logger.warning(
                "calibration_jobs: events queue full for job %s, event dropped",
                self.id,
            )

    def finish_stream(self) -> None:
        """Signal the end of the SSE stream (the subscriber will exit cleanly)."""
        try:
            self.events_queue.put_nowait(_END)
        except queue.Full:
            pass


# ─── Module-level singleton ───────────────────────────────────────────

_active: Optional[CalibrationJob] = None
_lock = threading.RLock()


def current() -> Optional[CalibrationJob]:
    """Active job or None. Includes finished jobs as long as no new one
    has been started (useful so that a client joining just after the
    end sees the result)."""
    with _lock:
        return _active


def is_busy() -> bool:
    """True if a calibration is still in progress."""
    with _lock:
        return _active is not None and _active.state in ("starting", "running")


def start(mediaid: str, z9_client) -> CalibrationJob:
    """Start a calibration in a daemon thread. Raises if already in progress.

    :raises RuntimeError: if a calibration is already in progress
    """
    global _active
    with _lock:
        if is_busy():
            raise RuntimeError(
                f"A calibration is already in progress on {_active.mediaid}",
            )
        job = CalibrationJob(mediaid)
        _active = job

    job._emit("calibration_started", {"mediaid": mediaid, "id": job.id})

    t = threading.Thread(
        target=_run_job, args=(job, z9_client), daemon=True,
        name=f"calibration-{job.id[:8]}",
    )
    t.start()
    logger.info(
        "calibration_jobs: started job %s on %s", job.id, mediaid,
    )
    return job


def _run_job(job: CalibrationJob, z9_client) -> None:
    """Function run in the daemon thread. Calls paper.calibrate
    and emits events as progress advances."""

    def on_progress(d: dict) -> None:
        # Callback from the paper.calibrate thread. Updates the job + emits.
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
        result = z9_client.paper.calibrate(
            ref=job.mediaid,
            on_progress=on_progress,
            poll_interval=10,
            timeout=1200,
        )
        job.state = "done"
        job.result = result
        job.elapsed = result.get("elapsed", job.elapsed)
        # P3.A2: invalidate the LEDM cache so that the next
        # GET /api/papers sees the fresh status (valid instead of
        # running / pending) without waiting for the end of the 30s TTL.
        try:
            from webapp.backend.services import ledm_calibration_cache
            ledm_calibration_cache.invalidate()
        except Exception:
            pass
        # Sync-on-event (#1): the CLC changed the paper state →
        # refresh the mirror cache of THIS paper (forced). Best-effort.
        try:
            from lib.z9_client import store as _store
            _store.refetch_paper(z9_client, job.mediaid)
        except Exception:  # noqa: BLE001
            logger.warning("refetch_paper after calibrate failed (mediaid=%s)",
                           job.mediaid, exc_info=True)
        job._emit("calibration_finished", {
            "outcome": "success",
            "clc_date": result.get("calibration_date"),
            "calibration_valid": result.get("calibration_valid"),
            "elapsed": job.elapsed,
        })
        logger.info(
            "calibration_jobs: job %s SUCCESS in %.1fs",
            job.id, job.elapsed,
        )
    except Exception as e:  # noqa: BLE001 — catch everything so the thread does not crash
        job.state = "error"
        job.error = str(e)
        job._emit("calibration_finished", {
            "outcome": "error",
            "message": str(e),
            "elapsed": time.time() - job.started_at,
        })
        logger.warning(
            "calibration_jobs: job %s ERROR: %s", job.id, e,
        )
    finally:
        # Always close the stream to release the SSE generator
        job.finish_stream()


def reset_for_tests() -> None:
    """Reset the singleton state — for pytest tests only."""
    global _active
    with _lock:
        _active = None
