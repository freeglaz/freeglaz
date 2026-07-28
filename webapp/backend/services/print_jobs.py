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

"""In-memory storage of print jobs + asyncio pub/sub for SSE.

Not thread-safe: intended for a single event loop (FastAPI / uvicorn
standard). No persistence: a restart empties all in-memory jobs
(acceptable Phase 1, local single-user usage).
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from webapp.backend.models import (
    JobEvent, JobStage, PrintJobState, PrintParams,
)

logger = logging.getLogger(__name__)

# Sentinel pushed onto the subscriber queues to signal "end of stream"
_END_OF_STREAM = object()
_TERMINAL_STAGES = (JobStage.DONE, JobStage.ERROR, JobStage.CANCELLED)


@dataclass
class _JobSlot:
    state: PrintJobState
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    task: Optional[asyncio.Task] = None


class JobStore:
    """Storage + asyncio pub/sub. One instance per app."""

    def __init__(self) -> None:
        self._slots: dict[str, _JobSlot] = {}

    def create(self, job_id: str, file_id: str, params: PrintParams) -> PrintJobState:
        state = PrintJobState(
            job_id=job_id, file_id=file_id, params=params,
            status=JobStage.PREPARING, progress=0,
            elapsed_seconds=0.0,
            started_at=datetime.now(timezone.utc),
            events=[],
        )
        self._slots[job_id] = _JobSlot(state=state)
        return state

    def get(self, job_id: str) -> Optional[PrintJobState]:
        slot = self._slots.get(job_id)
        return slot.state if slot else None

    def attach_task(self, job_id: str, task: asyncio.Task) -> None:
        slot = self._slots.get(job_id)
        if slot:
            slot.task = task

    def publish(self, job_id: str, event: JobEvent) -> None:
        """Append to the history + put_nowait to all subscribers."""
        slot = self._slots.get(job_id)
        if slot is None:
            return
        state = slot.state
        state.events.append(event)
        state.status = event.stage
        state.progress = event.progress
        state.elapsed_seconds = (
            datetime.now(timezone.utc) - state.started_at
        ).total_seconds()
        if event.stage in _TERMINAL_STAGES:
            state.finished_at = datetime.now(timezone.utc)
        for q in list(slot.subscribers):
            try:
                q.put_nowait(event)
                if event.stage in _TERMINAL_STAGES:
                    q.put_nowait(_END_OF_STREAM)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue pleine sur job %s — event dropped", job_id)

    def set_error(self, job_id: str, code: str, message: str) -> None:
        slot = self._slots.get(job_id)
        if slot:
            slot.state.error_code = code
            slot.state.error_message = message

    async def stream_events(self, job_id: str) -> AsyncIterator[JobEvent]:
        """Stream for SSE: history (snapshot) then live, ends on terminal stage.

        If the job is already finished at subscription time, we yield the history
        and stop immediately.
        """
        slot = self._slots.get(job_id)
        if slot is None:
            return

        # ─────────────────────────────────────────────────────────────
        # asyncio atomicity: the ``list(events)`` snapshot + adding the
        # subscriber are part of the same micro-step without ``await``.
        # In single-threaded asyncio (standard FastAPI/uvicorn case),
        # no other coroutine can interleave, so no race
        # is possible: no lock needed. DO NOT add one
        # "just to be safe": it would degrade without any use.
        # ─────────────────────────────────────────────────────────────
        history = list(slot.state.events)
        terminal = slot.state.status in _TERMINAL_STAGES
        q: asyncio.Queue = asyncio.Queue()
        if not terminal:
            slot.subscribers.append(q)

        try:
            for evt in history:
                yield evt
            if terminal:
                return
            while True:
                item = await q.get()
                if item is _END_OF_STREAM:
                    return
                yield item
        finally:
            if q in slot.subscribers:
                slot.subscribers.remove(q)

    async def cancel_all(self) -> None:
        """Lifespan shutdown: cancel all tasks still in progress."""
        tasks = [s.task for s in self._slots.values()
                 if s.task and not s.task.done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_job(self, job_id: str) -> bool:
        """Request cancellation of an active job. Returns True if the worker
        task existed and was not yet finished — the ``CancelledError``
        will be raised on its next ``await``, which will publish a
        ``CANCELLED`` ``JobEvent`` via the ``_runner`` handler on the route side.

        Returns False if the job is unknown or already terminal (the route
        then decides what to return to the client).
        """
        slot = self._slots.get(job_id)
        if slot is None or slot.task is None or slot.task.done():
            return False
        slot.task.cancel()
        return True
