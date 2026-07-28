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

"""Z9JobsSubscriber: periodic polling of the Z9 queue.

Inc 14 Phase 2. Maintains an atomic snapshot of the queue (queue
status + job list) up to date in the backend process, fed by a call to
``client.jobs.get_jobs_snapshot()`` every 3 s.

The snapshot is exposed via the route ``GET /api/jobs`` and stays
available even when the Z9 is momentarily unreachable (we keep the last
valid snapshot until the next success).

Error strategy:

- ``Z9Error`` at initial discovery: we continue with an empty snapshot
  and will retry on the next tick.
- ``Z9Error`` at polling: we keep the last known snapshot and increment
  a consecutive-failures counter (exposed for debug).
- ``Z9RESTError 404 "Unkownw QueueID"``: handled in the lib
  ``JobQueueOps.get_jobs_snapshot`` which re-discovers automatically.

No event bus / pub-sub (cf. ``Z9StatusPollSubscriber``) — the queue
changes less frequently and 3 s polling on the frontend side or a
future SSE side is sufficient.
"""
import asyncio
import logging
from typing import Any, Optional

from lib.z9_client import Z9Client, Z9Error

logger = logging.getLogger(__name__)


class Z9JobsSubscriber:
    """Periodic polling of ``Z9Client.jobs.get_jobs_snapshot()``.

    Atomic snapshot kept in memory, accessible via
    ``current_snapshot()``. Started at the lifespan of the FastAPI app,
    stopped cleanly at shutdown.
    """

    POLL_INTERVAL_DEFAULT = 3.0  # s

    # Exponential backoff on consecutive failure. Avoids log spam and network
    # hammer when the Z9 is down for a long time. Indexed by ``consecutive_failures - 1``
    # (clamped to len-1). Reset to 0 on first success. CEILING 120 s (anti-hammer:
    # do not poke a down/errored Z9 every 60 s indefinitely — let it
    # breathe; auto recovery within <=2 min when it comes back).
    BACKOFF_STEPS_SECONDS = (5.0, 10.0, 30.0, 60.0, 120.0)

    # "Empty" snapshot returned until a poll has succeeded.
    # Format identical to ``JobQueueOps.get_jobs_snapshot`` so that the
    # consumers (route GET /api/jobs, frontend) have a stable schema.
    EMPTY_SNAPSHOT = {
        "queue_status": "Unknown",
        "number_of_jobs": 0,
        "modification_number": 0,
        "timestamp": "",
        "jobs": [],
    }

    def __init__(self, z9: Z9Client, poll_interval: float = POLL_INTERVAL_DEFAULT):
        self._z9 = z9
        self._poll_interval = poll_interval
        self._snapshot: dict[str, Any] = dict(self.EMPTY_SNAPSHOT)
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        # Diagnostic: consecutive-errors counter, exposed for debug.
        # Reset on each success.
        self._consecutive_failures = 0
        # ISO timestamp of the last successful fetch (useful for the UI: if
        # the frontend sees `last_success_at` aging, it can show a
        # "data possibly stale" warning).
        self._last_success_at: Optional[str] = None

    # ─── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start polling in the background. Non-blocking.

        The first tick happens after ``poll_interval`` (not immediately)
        to avoid blocking the app lifespan. If the UI requests
        ``current_snapshot()`` before this tick, it receives ``EMPTY_SNAPSHOT``
        (queue_status='Unknown', jobs=[]) — expected behavior for an
        initial "Loading..." display on the frontend side.
        """
        if self._task is not None:
            logger.warning("Z9JobsSubscriber already started — start() ignored")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("Z9JobsSubscriber started (interval=%.1fs)", self._poll_interval)

    async def stop(self) -> None:
        """Stop polling cleanly. Idempotent."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Z9JobsSubscriber stopped")

    # ─── snapshot accessor ──────────────────────────────────────────────

    def current_snapshot(self) -> dict:
        """Return the last known snapshot (defensive copy).

        The format is guaranteed stable (same keys as JobQueueOps), even
        if no poll has succeeded yet (EMPTY_SNAPSHOT by default).
        Includes freshness metadata for the UI.
        """
        return {
            **self._snapshot,
            "_meta": {
                "consecutive_failures": self._consecutive_failures,
                "last_success_at": self._last_success_at,
                "poll_interval_seconds": self._poll_interval,
            },
        }

    # ─── internal loop ─────────────────────────────────────────────────

    def _next_sleep_seconds(self) -> float:
        """Return the wait delay before the next tick.

        - 0 consecutive failure → ``poll_interval`` (3 s by default).
        - 1+ failure → step from ``BACKOFF_STEPS_SECONDS`` (5/10/30/60 s,
          clamped to the last). Avoids network hammer and log spam when
          the Z9 is down.
        """
        if self._consecutive_failures == 0:
            return self._poll_interval
        idx = min(self._consecutive_failures - 1, len(self.BACKOFF_STEPS_SECONDS) - 1)
        return self.BACKOFF_STEPS_SECONDS[idx]

    async def _poll_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                # ``client.jobs.get_jobs_snapshot()`` is synchronous (REST) —
                # we run it in a thread so as not to block the event loop.
                snapshot = await asyncio.to_thread(self._z9.jobs.get_jobs_snapshot)
                self._snapshot = snapshot
                if self._consecutive_failures > 0:
                    logger.info(
                        "Z9JobsSubscriber polling recovered after %d consecutive failures",
                        self._consecutive_failures,
                    )
                self._consecutive_failures = 0
                self._last_success_at = snapshot.get("timestamp") or ""
            except Z9Error as e:
                self._consecutive_failures += 1
                # The exponential backoff naturally limits the frequency
                # of logs: from the 4th failure on we wait 60 s between
                # ticks, so a log on each tick = 1 log/60 s, in line with
                # the user spec "log only once every 60 s".
                logger.warning(
                    "Z9JobsSubscriber polling failed (#%d, next retry in %.0fs): %s",
                    self._consecutive_failures, self._next_sleep_seconds(), e,
                )
            except Exception:  # noqa: BLE001 — defensive, do not crash the loop
                self._consecutive_failures += 1
                logger.exception("Unexpected error in Z9 jobs polling loop")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._next_sleep_seconds(),
                )
            except asyncio.TimeoutError:
                pass  # normal tick
