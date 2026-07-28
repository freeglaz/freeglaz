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

"""Event loop watchdog — forensic dump if the asyncio loop stalls.

Motivation (webapp freeze of 10/06): the backend froze on a gray screen without
leaving ANY usable trace in `freeglaz.log` (INFO logging does not
capture a hang). This watchdog fills the gap: an asyncio *heartbeat*
updates a timestamp; an independent daemon thread checks that it
progresses. If the event loop is blocked (sync call in the loop, deadlock,
Z9 connection contention, etc.) the heartbeat freezes → the thread dumps the
stacktraces of ALL threads into ``freeglaz_stall.log`` + a CRITICAL in the
main log. We will finally know WHERE it blocks.

The thread runs outside the event loop: it keeps observing even when the
loop is frozen (that is the whole point).
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LoopWatchdog:
    """Detect an event loop stall and dump the stacks of all threads.

    :param stall_log: file where to dump the stacktraces (append).
    :param beat_interval: period of the asyncio heartbeat (s).
    :param check_interval: check period of the daemon thread (s).
    :param stall_threshold: heartbeat lag beyond which we consider
        the event loop blocked and we dump (s). Chosen >> the normal
        slow Z9 ops (10-30 s timeouts) so as not to cry wolf.
    """

    def __init__(
        self,
        stall_log: Path,
        beat_interval: float = 2.0,
        check_interval: float = 5.0,
        stall_threshold: float = 45.0,
    ) -> None:
        self._stall_log = Path(stall_log)
        self._beat_interval = beat_interval
        self._check_interval = check_interval
        self._stall_threshold = stall_threshold
        self._last_beat = time.monotonic()
        self._beat_task: Optional[asyncio.Task] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._dumped_for_stall = False  # only one dump per stall episode

    async def start(self) -> None:
        self._last_beat = time.monotonic()
        self._stop.clear()
        self._beat_task = asyncio.create_task(self._beat(), name="loop-watchdog-beat")
        self._thread = threading.Thread(
            target=self._watch, name="loop-watchdog", daemon=True
        )
        self._thread.start()
        logger.info(
            "LoopWatchdog started (stall threshold=%.0fs, dump → %s)",
            self._stall_threshold,
            self._stall_log,
        )

    async def stop(self) -> None:
        self._stop.set()
        if self._beat_task is not None:
            self._beat_task.cancel()
            try:
                await self._beat_task
            except asyncio.CancelledError:
                pass
            self._beat_task = None

    async def _beat(self) -> None:
        """Update the timestamp as long as the event loop runs."""
        while True:
            self._last_beat = time.monotonic()
            await asyncio.sleep(self._beat_interval)

    def _watch(self) -> None:
        """Daemon thread: observe the heartbeat outside the event loop."""
        while not self._stop.wait(self._check_interval):
            lag = time.monotonic() - self._last_beat
            if lag > self._stall_threshold:
                if not self._dumped_for_stall:
                    self._dump(lag)
                    self._dumped_for_stall = True
            else:
                self._dumped_for_stall = False  # reset on recovery

    def _dump(self, lag: float) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self._stall_log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._stall_log, "a", encoding="utf-8") as f:
                f.write(
                    f"\n===== EVENT LOOP STALL — lag={lag:.1f}s @ {ts} "
                    f"(threshold {self._stall_threshold:.0f}s) =====\n"
                )
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.flush()
        except OSError:
            logger.exception("LoopWatchdog: failed to write the stall dump")
        # CRITICAL in the main log (pointer to the detailed dump).
        logger.critical(
            "EVENT LOOP STALL detected (lag=%.1fs) — stacks of all threads "
            "dumped in %s",
            lag,
            self._stall_log,
        )
