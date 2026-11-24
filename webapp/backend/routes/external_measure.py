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

"""External measurement API (chartread-fgK sessions), local only: no printer is involved.

    GET  /api/charts/{id}/external-measure            snapshot (polling fallback)
    GET  /api/charts/{id}/external-measure/events     SSE: snapshot at start and on each change
    POST /api/charts/{id}/external-measure            create the session, or return the open one
    POST /api/charts/{id}/external-measure/start      start / resume the engine (-r -N -K)
    POST /api/charts/{id}/external-measure/command    calibrate, retry, give_up, accept, prev,
                                                      next, next_unread, abort_calibration, pause
    POST /api/charts/{id}/external-measure/correct    withdraw a saved strip pass (no run)
    POST /api/charts/{id}/external-measure/finalize   publish the complete measurement
    POST /api/charts/{id}/external-measure/abandon    close the session without publishing
    GET  /api/external-measure/current                which chart has a live run, if any

Every POST except creation carries ``expect_state`` and ``seq`` from the snapshot it was
decided on: a command on an older state gets 409 ``stale_command`` and does nothing. The
client never names an executable, an argument or an engine origin: the backend resolves
and checks chartread-fgK itself.
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from webapp.backend.services import external_measure as em

router = APIRouter(tags=["external-measure"])
KEEPALIVE_S = 15.0


class CreateBody(BaseModel):
    filter_option: Optional[str] = None          # i1Pro 3 Plus: n (M0), 5 (M1), u (M2)


class StateBody(BaseModel):
    expect_state: Optional[str] = None
    seq: Optional[int] = None


class CommandBody(StateBody):
    command: str


class CorrectBody(StateBody):
    pass_label: str


def _call(fn, *args):
    try:
        return fn(*args)
    except em.ExternalMeasureError as e:
        raise HTTPException(e.status, detail=e.detail()) from None


@router.get("/api/external-measure/current")
def current() -> dict:
    return {"chart_id": em.get_service().active_chart()}


@router.get("/api/charts/{chart_id}/external-measure")
def get_snapshot(chart_id: str) -> dict:
    return _call(em.get_service().snapshot, chart_id)


@router.post("/api/charts/{chart_id}/external-measure")
def create(chart_id: str, body: CreateBody) -> dict:
    return _call(em.get_service().create, chart_id, body.filter_option)


@router.post("/api/charts/{chart_id}/external-measure/start")
def start(chart_id: str, body: StateBody) -> dict:
    return _call(em.get_service().start, chart_id, body.expect_state, body.seq)


@router.post("/api/charts/{chart_id}/external-measure/command")
def command(chart_id: str, body: CommandBody) -> dict:
    return _call(em.get_service().command, chart_id, body.command, body.expect_state, body.seq)


@router.post("/api/charts/{chart_id}/external-measure/correct")
def correct(chart_id: str, body: CorrectBody) -> dict:
    return _call(em.get_service().correct, chart_id, body.pass_label, body.expect_state, body.seq)


@router.post("/api/charts/{chart_id}/external-measure/finalize")
def finalize(chart_id: str, body: StateBody) -> dict:
    return _call(em.get_service().finalize, chart_id, body.expect_state, body.seq)


@router.post("/api/charts/{chart_id}/external-measure/abandon")
def abandon(chart_id: str, body: StateBody) -> dict:
    return _call(em.get_service().abandon, chart_id, body.expect_state, body.seq)


@router.get("/api/charts/{chart_id}/external-measure/events")
async def events(chart_id: str, request: Request, since: Optional[int] = None,
                 last_event_id: Optional[str] = Header(default=None)):
    """A snapshot at once (always, so a reconnection starts from the present state), then
    one per change of this chart; ``id`` is the seq, so EventSource resumes with
    Last-Event-ID. A slow reader skips to the latest snapshot: nothing queues up.

    Snapshots are built on the worker threads, as for the other routes, never on the event
    loop, and without waiting for a USB listing (the last verdict is sent; the wizard's own
    reads wait for a fresh one)."""
    svc = em.get_service()
    first = await run_in_threadpool(_call, svc.snapshot, chart_id, 0.0)
    try:
        resume = int(last_event_id) if last_event_id else since
    except ValueError:
        resume = since
    loop = asyncio.get_running_loop()

    async def gen():
        snap, sent = first, None
        yield {"event": "snapshot", "id": str(snap["seq"]), "data": json.dumps({**snap, "resumed_from": resume})}
        sent = snap["seq"]
        while True:
            if await request.is_disconnected():
                break
            rev = await loop.run_in_executor(None, svc.broadcast.wait, chart_id, sent, KEEPALIVE_S)
            if rev <= sent:
                yield {"event": "ping", "data": ""}
                continue
            try:
                snap = await run_in_threadpool(svc.snapshot, chart_id, 0.0)
            except em.ExternalMeasureError:
                break                                    # the chart was deleted
            yield {"event": "snapshot", "id": str(snap["seq"]), "data": json.dumps(snap)}
            sent = max(rev, snap["seq"])
    return EventSourceResponse(gen())
