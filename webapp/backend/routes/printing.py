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

"""Endpoints /api/print: geometric preview, job start, state + SSE."""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sse_starlette.sse import EventSourceResponse

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.exceptions import Z9ConnectionError

from webapp.backend.models import (
    GeometryResult, JobEvent, JobStage, PreviewRequest, PrintJobState,
    PrintPreview, StartPrintRequest, StartPrintResponse,
)
from webapp.backend.routes.status import build_loaded_paper, get_z9
from webapp.backend.services import file_storage, print_geometry
from webapp.backend.services.file_inspector import to_file_info
from webapp.backend.services.print_jobs import JobStore
from webapp.backend.services.print_worker import run_mock_job, run_real_job

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/print", tags=["print"])

# Strict UUID4 regex on ``{job_id}`` — prevents any collision with ``/preview``.
_UUID4_REGEX = (
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

# JobStage → SSE event-name mapping (intermediate → "stage").
_EVENT_NAME_BY_STAGE = {
    JobStage.DONE:      "done",
    JobStage.ERROR:     "error",
    JobStage.CANCELLED: "cancelled",
}


def get_job_store(request: Request) -> JobStore:
    return request.app.state.job_store


def _empty_geometry(file_w_mm: float, file_h_mm: float) -> GeometryResult:
    """Inert geometry returned when nothing can be computed (no paper, Z9 down)."""
    return GeometryResult(
        sheet_width_mm=0.0, sheet_height_mm=0.0,
        image_width_mm=file_w_mm, image_height_mm=file_h_mm,
        image_x_mm=0.0, image_y_mm=0.0,
        auto_x_mm=0.0, auto_y_mm=0.0,
        margin_left_mm=0.0, margin_top_mm=0.0,
        margin_right_mm=0.0, margin_bottom_mm=0.0,
        media_source="MANUAL_FEED",
        centered_x=False, centered_y=False,
    )


@router.post("/preview", response_model=PrintPreview)
def print_preview(
    body: PreviewRequest,
    request: Request,
    z9: Optional[Z9Client] = Depends(get_z9),
) -> PrintPreview:
    """Compute the print geometry. Never contacts the printer to print."""
    params = body.params
    src = file_storage.get_source(body.file_id)
    if src is None:
        raise HTTPException(404, detail="unknown file_id")
    file_info = to_file_info(body.file_id, src.name, src)

    blocking: list[str] = []
    warnings: list[str] = list(file_info.warnings)

    # Z9 + loaded paper
    loaded = None
    if z9 is None:
        blocking.append("Z9 not configured")
    else:
        dashboard = None
        try:
            dashboard = z9.device.status()
        except (Z9ConnectionError, Z9Error) as e:
            logger.warning("Z9 unreachable during preview: %s", e)
            blocking.append(f"Z9 unreachable ({e})")
        if dashboard is not None:
            loaded = build_loaded_paper(
                z9, dashboard, request.app.state.capabilities_cache,
            )
            if loaded is None:
                blocking.append("No paper loaded in the Z9")

    # Geometry — dims transposed upstream if the image is oriented 90/270
    # (compute_geometry stays orientation-agnostic).
    eff_w_mm, eff_h_mm = print_geometry.oriented_dims(
        params.orientation, file_info.width_mm, file_info.height_mm,
    )
    if loaded is not None:
        geometry = print_geometry.compute_geometry(
            loaded, params, eff_w_mm, eff_h_mm,
        )
        g_blocking, g_warnings = print_geometry.detect_geometry_issues(
            geometry, geometry.media_source,
        )
        blocking.extend(g_blocking)
        warnings.extend(g_warnings)
    else:
        geometry = _empty_geometry(eff_w_mm, eff_h_mm)

    # ICC actif (firmware-authoritative via SOAP get_profile, cached)
    paper_icc = print_geometry.PaperIccInfo()
    if loaded is not None and z9 is not None:
        paper_icc = print_geometry.resolve_active_paper_icc_info(
            z9,
            paper_id=loaded.id,
            gloss_enhancer=params.gloss_enhancer,
            rendermode=params.rendermode,
            cache=request.app.state.paper_icc_cache,
        )

    icc_match, icc_reason = print_geometry.icc_match_status(
        file_icc_name=file_info.icc_name,
        file_icc_color_hash=file_info.icc_color_hash,
        paper_icc_name=paper_icc.name,
        paper_icc_color_hash=paper_icc.color_hash,
    )
    if icc_match == "mismatch":
        # Defensive display: a None name becomes "—" for the textual warning,
        # but the user also sees the exact names in the sidebar.
        warnings.append(
            f"File ICC profile ({file_info.icc_name or '—'}) differs from the "
            f"paper ({paper_icc.name or '—'}). The rendering may differ. For a "
            "faithful result, re-export the file with the paper profile beforehand."
        )

    # Inherit file blockers last (they can be independent
    # of the paper — e.g. missing DPI — and stay true even if the Z9 is down)
    blocking.extend(file_info.blocking_issues)

    return PrintPreview(
        geometry=geometry,
        can_print=(not blocking),
        blocking_issues=blocking,
        warnings=warnings,
        file_icc_name=file_info.icc_name,
        file_icc_md5=file_info.icc_md5,
        paper_icc_name=paper_icc.name,
        paper_icc_md5=paper_icc.md5,
        icc_match=icc_match,
        icc_match_reason=icc_reason,
    )


# ═════════════════════════════════════════════════════════════════════════
# POST /api/print          — starts a job, returns a job_id
# GET  /api/print/{job_id} — full state (for polling as an SSE alternative)
# GET  /api/print/{job_id}/events — SSE stream of events
# ═════════════════════════════════════════════════════════════════════════


@router.post("", response_model=StartPrintResponse, status_code=202)
async def start_print(
    body: StartPrintRequest,
    request: Request,
    store: JobStore = Depends(get_job_store),
    z9: Optional[Z9Client] = Depends(get_z9),
) -> StartPrintResponse:
    """Start a print job. Returns immediately (worker in background)."""
    if file_storage.get_source(body.file_id) is None:
        raise HTTPException(404, detail="unknown file_id")

    job_id = str(uuid.uuid4())
    state = store.create(job_id, body.file_id, body.params)
    timing = getattr(request.app.state, "mock_timing", None)
    use_mock = getattr(request.app.state, "use_mock_print", False) or z9 is None

    async def _runner() -> None:
        try:
            if use_mock:
                await run_mock_job(
                    body.file_id, body.params,
                    sink=lambda e: store.publish(job_id, e),
                    timing=timing,
                )
            else:
                await run_real_job(
                    body.file_id, body.params,
                    sink=lambda e: store.publish(job_id, e),
                    z9=z9,
                    capabilities_cache=request.app.state.capabilities_cache,
                )
        except asyncio.CancelledError:
            current = store.get(job_id)
            store.publish(job_id, JobEvent(
                timestamp=datetime.now(timezone.utc),
                stage=JobStage.CANCELLED,
                progress=current.progress if current else 0,
                message="Job cancelled",
            ))
            raise
        except Exception as e:  # noqa: BLE001 — safety net, run_real_job already catches
            logger.exception("Job %s failed", job_id)
            store.set_error(job_id, "INTERNAL", str(e))
            current = store.get(job_id)
            store.publish(job_id, JobEvent(
                timestamp=datetime.now(timezone.utc),
                stage=JobStage.ERROR,
                progress=current.progress if current else 0,
                message=str(e),
                data={"code": "INTERNAL"},
            ))

    store.attach_task(job_id, asyncio.create_task(_runner()))
    return StartPrintResponse(job_id=job_id, status=state.status)


@router.get("/{job_id}", response_model=PrintJobState)
def get_print_job(
    job_id: str = Path(..., pattern=_UUID4_REGEX),
    store: JobStore = Depends(get_job_store),
) -> PrintJobState:
    state = store.get(job_id)
    if state is None:
        raise HTTPException(404, detail="unknown job_id")
    return state


@router.post("/{job_id}/cancel", status_code=202)
def cancel_print_job(
    request: Request,
    job_id: str = Path(..., pattern=_UUID4_REGEX),
    store: JobStore = Depends(get_job_store),
) -> dict:
    """Cancel a running job on the webapp worker side.

    Three branches depending on the worker state and the Z9 activity:

    1. **Non-terminal worker** (PREPARING / PREFLIGHT / RENDERING / SENDING):
       ``Task.cancel()`` — the worker receives ``CancelledError`` and publishes
       a CANCELLED ``JobEvent`` via its ``_runner`` handler. On the Z9 side:
       if we are before the PRN is sent, nothing has left the machine. If we
       are during the send, the ``nc 9100`` is interrupted and the Z9 may
       show a partial job (rare case — the send takes a few seconds).

    2. **Worker finished DONE** (PRN sent) **but Z9 active** (Processing
       / Drying / etc.): no way to cancel remotely without investigating a
       LEDM endpoint (cf. bug B6 in the roadmap). We respond 409 with a
       clear UX message inviting the user to use the Z9 front panel.

    3. **Everything terminal and Z9 idle** (already cancelled / failed /
       done idle): silent 200, the UI can clean up its jobId.
    """
    state = store.get(job_id)
    if state is None:
        raise HTTPException(404, detail="unknown job_id")

    z9_sub = getattr(request.app.state, "z9_status_subscriber", None)
    activity = "NoActivity"
    if z9_sub is not None:
        try:
            activity = z9_sub.current_snapshot().get("activity_name", "NoActivity")
        except Exception:  # noqa: BLE001 — defensive, we fall back to assumed Idle
            activity = "NoActivity"
    z9_busy = activity != "NoActivity"

    terminal = state.status in (JobStage.DONE, JobStage.ERROR, JobStage.CANCELLED)
    if terminal:
        if state.status == JobStage.DONE and z9_busy:
            # Case 2 — worker finished, Z9 still printing. No LEDM cancel available.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "post_send_no_remote_cancel",
                    "message": (
                        "The print is in progress on the Z9. "
                        "Cancel it from the printer front panel. "
                        "(Remote cancel via LEDM is not yet "
                        "implemented in freeglaz — cf. bug B6.)"
                    ),
                    "z9_activity": activity,
                },
            )
        # Case 3 — everything is already quiet, silent OK
        return {"ok": True, "already_terminal": True, "status": state.status.value}

    # Case 1 — non-terminal worker, we cancel the task. The CANCELLED event
    # will be published asynchronously by the _runner handler.
    cancelled = store.cancel_job(job_id)
    return {"ok": cancelled, "status": "cancelling"}


@router.get("/{job_id}/events")
async def stream_print_events(
    job_id: str = Path(..., pattern=_UUID4_REGEX),
    store: JobStore = Depends(get_job_store),
) -> EventSourceResponse:
    """SSE stream of a job's events. Replays the history then switches to live."""
    if store.get(job_id) is None:
        raise HTTPException(404, detail="unknown job_id")

    async def _gen():
        async for evt in store.stream_events(job_id):
            yield {
                "event": _EVENT_NAME_BY_STAGE.get(evt.stage, "stage"),
                "data": evt.model_dump_json(),
            }

    return EventSourceResponse(_gen())
