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

"""Print workers.

- ``run_mock_job`` (5a): simulates a complete job by publishing spaced
  events, without touching the Z9. Used in pytest and in demos via
  ``FREEGLAZ_MOCK_PRINT=1``.
- ``run_real_job`` (5b): calls ``z9.print.send`` (lib PrintOps) via
  ``asyncio.to_thread``, maps its ``on_progress`` callback onto the
  webapp ``sink``. The two functions are interchangeable on the route
  side.

Thread-safety
─────────────
``PrintOps.send`` is synchronous. Launched via ``to_thread``, its
``on_progress`` callback is invoked from the worker thread. The
``asyncio.Queue`` of the SSE subscribers are NOT thread-safe → we
dispatch each event to the event loop via
``loop.call_soon_threadsafe(sink, event)``. The ``sink`` (ultimately
``JobStore.publish``) only touches the queues from the event loop,
returning to the single-thread guarantee.

Timing override for the tests
─────────────────────────────
The route ``POST /api/print`` reads ``request.app.state.mock_timing``
(None by default). In production: default timing (~5 s total). In the
pytest tests: we assign ``app.state.mock_timing = MockTiming(0.01, ...)``
before ``TestClient(app)`` so that the SSE tests stay sub-second.
"""
import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.exceptions import Z9PrintError
from lib.z9_client.printing import PrintJob as LibPrintJob
from lib.z9_client.printing import fetch_resident_icc

from webapp.backend.models import JobEvent, JobStage, PrintParams

logger = logging.getLogger(__name__)

# Signature of the event publication callback.
EventSink = Callable[[JobEvent], None]


@dataclass
class MockTiming:
    """Delays between stages, in seconds. Default values = production."""
    preparing_s: float = 0.2
    preflight_s: float = 0.5
    rendering_s: float = 1.5
    sending_half_s: float = 1.5
    sending_quarter_s: float = 1.0


def _evt(stage: JobStage, progress: int, message: str,
         data: Optional[dict] = None) -> JobEvent:
    return JobEvent(
        timestamp=datetime.now(timezone.utc),
        stage=stage, progress=progress, message=message, data=data,
    )


async def run_mock_job(
    file_id: str,
    params: PrintParams,
    sink: EventSink,
    *,
    timing: Optional[MockTiming] = None,
    fail_at: Optional[JobStage] = None,
    fail_code: str = "MOCK-FAILURE",
    fail_message: str = "Error simulated by the mock worker",
) -> None:
    """Simulate a complete print job by publishing events on ``sink``.

    If ``fail_at`` is provided, publishes an ERROR event instead of
    continuing after reaching that stage (used in pytest to test the
    error path — not exposed via HTTP).
    """
    t = timing or MockTiming()
    stages = [
        (JobStage.PREPARING, 5,  "Preparing the job",       t.preparing_s),
        (JobStage.PREFLIGHT, 15, "Validating the file",     t.preflight_s),
        (JobStage.RENDERING, 35, "Generating the PRN",      t.rendering_s),
        (JobStage.SENDING,   50, "Sending to the Z9...",    t.sending_half_s),
        (JobStage.SENDING,   75, "Sending to the Z9... 50 %", t.sending_quarter_s),
        (JobStage.SENDING,   95, "Sending to the Z9... 90 %", 0.2),
    ]
    for stage, progress, message, delay in stages:
        sink(_evt(stage, progress, message))
        await asyncio.sleep(delay)
        if fail_at == stage:
            sink(_evt(JobStage.ERROR, progress, fail_message,
                      data={"code": fail_code}))
            return
    sink(_evt(JobStage.DONE, 100, "Print sent"))


# ═════════════════════════════════════════════════════════════════════════
# run_real_job — wiring onto PrintOps.send
# ═════════════════════════════════════════════════════════════════════════

# Mapping of PrintOps stage callback → (webapp JobStage, progress %, message).
_LIB_STAGE_MAP: dict[str, tuple[JobStage, int, str]] = {
    "validate":  (JobStage.PREPARING, 5,   "Validating parameters"),
    "pdf":       (JobStage.RENDERING, 20,  "Generating the PDF/X-4"),
    "preflight": (JobStage.PREFLIGHT, 35,  "Checking PDF/X-4"),
    "prn":       (JobStage.RENDERING, 60,  "Generating the PRN"),
    "send":      (JobStage.SENDING,   85,  "Sending to the Z9"),
    "done":      (JobStage.DONE,      100, "Print sent"),
}


def _serialize_progress_data(data: dict[str, Any]) -> dict[str, Any]:
    """Serialize for JSON: ``Path`` → ``str``, omit the rich objects.

    The lib callback sometimes passes non-encodable objects (PrintResult
    for ``done``, Path for the intermediate paths). We filter them to
    propagate only JSON scalars to the frontend.
    """
    out: dict[str, Any] = {}
    for k, v in (data or {}).items():
        if isinstance(v, Path):
            out[k] = str(v)
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        # PrintResult, etc.: deliberately omitted
    return out


def _build_lib_job(
    source: Path,
    params: PrintParams,
    loaded,      # LoadedPaper — typing avoided so as not to create an import cycle
    geometry,    # GeometryResult
    original_filename: Optional[str] = None,
) -> LibPrintJob:
    """Convert (webapp PrintParams + geometry) → lib ``LibPrintJob``.

    - ``offset_x/y_mm``: we pass the **absolute** values (``geometry.image_x_mm``),
      not the user deltas. The delta→absolute conversion was done by
      ``print_geometry.compute_geometry``.
    - ``media_source`` API "MANUAL_FEED" → lib "MANUALFEED" (drop underscore).
    - ``gloss_enhancer`` webapp → ``gloss`` lib (rename only).
    - ``original_filename``: file name uploaded as-is by the user, read
      from ``file_storage.get_original_name(file_id)``. Propagated to
      ``LibPrintJob.source_filename`` so that the PJL JOB NAME shows
      "that name (1 page) - freeglaz" and not "source.tif (1 page) - freeglaz"
      in the Z9 queue (B17 follow-up P1).
    """
    is_tiff = source.suffix.lower() in (".tif", ".tiff")
    lib_media_source = (
        "MANUALFEED" if geometry.media_source == "MANUAL_FEED" else geometry.media_source
    )
    return LibPrintJob(
        tiff_path=source if is_tiff else None,
        pdf_path=None if is_tiff else source,
        paper_id=loaded.id,
        paper_name=loaded.name,
        media_source=lib_media_source,
        sheet_w_mm=geometry.sheet_width_mm,
        sheet_h_mm=geometry.sheet_height_mm,
        image_w_mm=geometry.image_width_mm,
        image_h_mm=geometry.image_height_mm,
        offset_x_mm=geometry.image_x_mm,
        offset_y_mm=geometry.image_y_mm,
        gloss=params.gloss_enhancer,
        quality=params.quality,
        rendermode=params.rendermode,
        max_detail=params.max_detail,
        drytime=params.drytime,
        cutter="ON",
        copies=params.copies,
        # Orientation: the geometry (image_w/h_mm, sheet_h) already
        # reflects the transposed dims; the lib rotates the pixel buffer
        # accordingly.
        orientation=params.orientation,
        username=os.getenv("USER") or "freeglaz-webapp",
        source_filename=original_filename,
    )


async def run_real_job(
    file_id: str,
    params: PrintParams,
    sink: EventSink,
    *,
    z9: Z9Client,
    capabilities_cache: dict,
) -> None:
    """Real print pipeline via ``PrintOps.send``.

    All Z9 exceptions (``Z9PrintError`` and family) are caught and turned
    into a ``JobStage.ERROR`` event rather than propagated: we never crash
    the server, we let the UI show the error via SSE.
    """
    # Lazy import to avoid a services↔routes cycle at initial load.
    from webapp.backend.routes.status import build_loaded_paper
    from webapp.backend.services import file_storage, print_geometry
    from webapp.backend.services.file_inspector import to_file_info

    # Initial event: the UI shows something as soon as the 202
    sink(_evt(JobStage.PREPARING, 2, "Initializing"))

    try:
        # ─── Setup (event loop, fast sync) ──────────────────────────
        source = file_storage.get_source(file_id)
        if source is None:
            raise Z9PrintError("Source file not found on the server")
        # Original uploaded name for the PJL JOB NAME. Fallback
        # silently to source.name (= "source.tif") if the metadata
        # was not persisted — should be rare in practice but does not
        # break the pipeline.
        original_name = file_storage.get_original_name(file_id) or source.name

        try:
            dashboard = z9.device.status()
        except Z9Error as e:
            raise Z9PrintError(f"Z9 unreachable: {e}") from e

        loaded = build_loaded_paper(z9, dashboard, capabilities_cache)
        if loaded is None:
            raise Z9PrintError("No paper loaded in the Z9")

        file_info = to_file_info(file_id, source.name, source)
        if file_info.blocking_issues:
            raise Z9PrintError("; ".join(file_info.blocking_issues))

        # Dims transposed upstream if orientation is 90/270 (compute_geometry
        # stays agnostic; the lib rotates the pixels via np.rot90).
        eff_w_mm, eff_h_mm = print_geometry.oriented_dims(
            params.orientation, file_info.width_mm, file_info.height_mm,
        )
        geometry = print_geometry.compute_geometry(
            loaded, params, eff_w_mm, eff_h_mm,
        )
        g_blocking, _ = print_geometry.detect_geometry_issues(
            geometry, geometry.media_source,
        )
        if g_blocking:
            raise Z9PrintError("; ".join(g_blocking))

        job = _build_lib_job(source, params, loaded, geometry, original_filename=original_name)

        # ─── Embed the live RESIDENT profile, read FRESH at the print go ──
        # freeglaz never converts values: it embeds the resident of the loaded
        # (paper, GE) so source == what the firmware decodes with → the user's
        # already-converted RGB reach the inks untouched. Read FRESH here (not
        # from the preview cache) so the embedded profile == the resident loaded
        # AT THIS INSTANT (L == F). No silent fallback to the file's ICC — a
        # failure blocks loudly (surfaces as an ERROR event via Z9PrintError).
        job.icc_override = fetch_resident_icc(
            z9, loaded.id, params.gloss_enhancer, params.rendermode,
        )

        # ─── PrintOps.send in a thread, callback dispatched to the loop ──
        loop = asyncio.get_running_loop()
        # Capture the PRN path at the "send" stage (the PRN was just written
        # by build_prn right before this stage). Used post-submit to
        # extract the JobAcct5 and generate the freeglaz thumbnail.
        captured_prn_path: dict[str, Optional[Path]] = {"path": None}

        def on_progress(stage: str, data: dict) -> None:
            if stage == "send" and "prn" in data:
                captured_prn_path["path"] = Path(data["prn"])
            mapping = _LIB_STAGE_MAP.get(stage)
            if not mapping:
                logger.debug("Unknown PrintOps stage, ignored: %s", stage)
                return
            target_stage, progress, message = mapping
            payload = _serialize_progress_data(data)
            evt = _evt(target_stage, progress, message, data=payload or None)
            # Thread-safe dispatch to the event loop (sink touches the asyncio queues)
            loop.call_soon_threadsafe(sink, evt)

        # Snapshot of the firmware_uuid BEFORE submit for the helper
        # ``detect_new_firmware_uuid`` post-submit (cf. job_lifecycle).
        from webapp.backend.services import job_lifecycle
        before_uuids = await asyncio.to_thread(
            job_lifecycle.snapshot_current_uuids, z9,
        )

        await asyncio.to_thread(
            z9.print.send, job, on_progress=on_progress,
        )

        # ─── Post-submit: thumb + mapping (best-effort, non-blocking) ──
        # If any step fails (PRN not found, polling timeout, etc.),
        # we log but do not crash the job — the user already received
        # the DONE via on_progress, the print goes to the Z9 no matter
        # what.
        prn = captured_prn_path["path"]
        if prn is not None:
            try:
                # we pass the real geometry (before the placement hack)
                # + the GE flag to generate a page-realistic composite rather
                # than a simple thumb of the source image. The dimensions
                # come from ``compute_geometry`` computed above, the GE flag
                # comes from the UI params (``'image'`` = enabled on the
                # image zone, other values = disabled on the freeglaz side).
                composite_kwargs = {
                    "sheet_w_mm":   geometry.sheet_width_mm,
                    "sheet_h_mm":   geometry.sheet_height_mm,
                    "image_w_mm":   geometry.image_width_mm,
                    "image_h_mm":   geometry.image_height_mm,
                    "image_x_mm":   geometry.image_x_mm,
                    "image_y_mm":   geometry.image_y_mm,
                    "media_source": geometry.media_source,
                    "gloss_enhancer": params.gloss_enhancer == "image",
                }
                await job_lifecycle.finalize_submitted_job_async(
                    z9, source, prn, before_uuids,
                    composite_kwargs=composite_kwargs,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Post-submit thumb+mapping failed — job already sent OK"
                )

    except Z9PrintError as e:
        sink(_evt(JobStage.ERROR, 0, str(e),
                  data={"code": type(e).__name__}))
    except Z9Error as e:
        sink(_evt(JobStage.ERROR, 0, str(e),
                  data={"code": type(e).__name__}))
    except Exception as e:  # noqa: BLE001 — we turn EVERYTHING into an ERROR event
        logger.exception("run_real_job : erreur inattendue")
        sink(_evt(JobStage.ERROR, 0, str(e),
                  data={"code": "INTERNAL"}))
