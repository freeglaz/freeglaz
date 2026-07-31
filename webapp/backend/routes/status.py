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

"""Endpoint /api/status — machine + paper + inks + alerts."""
import asyncio
import logging
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.exceptions import Z9ConnectionError

from webapp.backend.models import (
    Alert, ArgyllStatus, Ink, LoadedPaper, Status, Z9Activity)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["status"])


INK_LABELS_FR = {
    "magenta": "Magenta",
    "yellow": "Yellow",
    "cyan": "Cyan",
    "chromatic-red": "Chromatic red",
    "chromatic-blue": "Chromatic blue",
    "chromatic-green": "Chromatic green",
    "photo-black": "Photo black",
    "matte-black": "Matte black",
    "gray": "Gray",
    "post-treatment": "Gloss enhancer",
}

ACTIVITY_TEXT = {
    "NoActivity": "Idle",
    "Processing": "Printing",
    "PreparingToPrint": "Preparing to print",
    "Drying": "Drying",
    "TerminatingPrint": "Finishing print",
    "CancellingPrint": "Cancelling",
    "Scanning": "Scanning",
    "ReplacingConsumables": "Replacing consumable",
    "DeviceMaintenance": "Maintenance",
    "DeviceOptimizing": "Optimizing",
    "CheckingPrinter": "Checking",
    "Upgrade": "Firmware update",
    "InstallingAccessory": "Installing accessory",
}


def get_z9(request: Request) -> Optional[Z9Client]:
    """Get the Z9Client shared by the lifespan (or None if not configured)."""
    return getattr(request.app.state, "z9", None)


def _unreachable_status(message: str, *, configured: bool = True,
                        argyll: Optional[ArgyllStatus] = None) -> Status:
    return Status(
        model="?",
        serial="?",
        firmware="?",
        ready=False,
        state_text="Z9 unreachable",
        z9_configured=configured,
        loaded_paper=None,
        inks=[],
        alerts=[Alert(severity="error", code="Z9_UNREACHABLE", message=message)],
        argyll=argyll,
    )


def _app_argyll(request: Request) -> Optional[ArgyllStatus]:
    """Argyll availability resolved once at startup (lifespan). None before ready."""
    return getattr(request.app.state, "argyll", None)


def _short_id(paper_id: str) -> Optional[int]:
    return int(paper_id) if paper_id.isdigit() else None


def _ink_state(state: Optional[str]) -> Literal["ok", "warning", "error"]:
    if state == "Error":
        return "error"
    if state == "Warning":
        return "warning"
    return "ok"


def _build_inks(levels: dict, warnings: list[dict]) -> list[Ink]:
    state_by_color = {w["color"]: w["state"] for w in warnings}
    return [
        Ink(
            color=color,
            label=INK_LABELS_FR.get(color, color),
            level_pct=max(0, min(100, round(pct))),
            state=_ink_state(state_by_color.get(color)),
        )
        for color, pct in levels.items()
    ]


def _bool_default(value: Optional[bool], default: bool = True) -> bool:
    return default if value is None else bool(value)


def build_loaded_paper(
    z9: Z9Client, dashboard: dict, caps_cache: dict[str, dict]
) -> Optional[LoadedPaper]:
    loaded_id = dashboard.get("loaded_paper_id")
    if not loaded_id:
        return None

    paper = z9.paper.get(loaded_id) or {}
    media_source = dashboard.get("loaded_paper_source") or "?"
    width_mm = dashboard.get("loaded_paper_width_mm")
    length_mm = dashboard.get("loaded_paper_length_mm")
    is_roll = media_source.upper() == "ROLL"

    # capabilities() is expensive (SOAP getMediumList) — we cache per paper_id
    caps = caps_cache.get(loaded_id)
    if caps is None:
        try:
            caps = z9.paper.capabilities(loaded_id) or {}
        except Z9Error as e:
            logger.info("capabilities(%s) failed: %s", loaded_id, e)
            caps = {}
        caps_cache[loaded_id] = caps

    return LoadedPaper(
        id=loaded_id,
        short_id=_short_id(loaded_id),
        name=dashboard.get("loaded_paper_name") or paper.get("name") or "?",
        category=paper.get("category_id") or "?",
        is_factory=bool(paper.get("is_factory")),
        media_source=media_source,
        sheet_width_mm=None if is_roll else width_mm,
        sheet_height_mm=None if is_roll else length_mm,
        roll_width_mm=width_mm if is_roll else None,
        # Default-False for GE: unknown capability ⇒ not offered (aligned with
        # the pipeline guard). max_detail/profiling keep the permissive default.
        gloss_enhancer_supported=_bool_default(
            caps.get("supports_gloss_enhancer"), default=False),
        max_detail_supported=_bool_default(caps.get("supports_max_detail")),
        profilable=_bool_default(caps.get("supports_profiling")),
    )


def _state_text(global_status: str, activity_name: str, loaded: Optional[LoadedPaper]) -> str:
    if activity_name and activity_name != "NoActivity":
        return ACTIVITY_TEXT.get(activity_name, activity_name)
    if global_status == "Ready":
        return "Ready — No paper loaded" if loaded is None else "Ready"
    if global_status == "WithAlerts":
        return "Active alerts"
    return f"State: {global_status}"


def _build_alerts(inks: list[Ink], loaded: Optional[LoadedPaper]) -> list[Alert]:
    alerts: list[Alert] = []
    if loaded is None:
        alerts.append(Alert(severity="info", code="NO_PAPER", message="No paper detected"))
    for ink in inks:
        if ink.state == "ok":
            continue
        sev = "error" if ink.state == "error" else "warning"
        alerts.append(Alert(
            severity=sev,
            code=f"INK_{ink.color.upper()}",
            message=f"{ink.label} : {ink.state}",
        ))
    return alerts


def _dashboard_to_status(
    dashboard: dict,
    z9: Optional[Z9Client],
    caps_cache: dict,
    activity_name: Optional[str] = None,
    activity_progress_pct: Optional[float] = None,
    argyll: Optional[ArgyllStatus] = None,
    demo: bool = False,
) -> Status:
    """Map a raw dashboard (= what device.status() returns) to our
    Pydantic Status. Factored to serve both GET /api/status and the SSE
    route /api/status/events.

    The physical activity (ActivitiesOverview / MostRelevantActivity)
    is not in ``device.status()``; the callers pass it explicitly:
    - GET /api/status: from ``device.device_status()`` (1 extra GET)
    - SSE /api/status/events: from the subscriber snapshot which polls it
      explicitly (cf. ``_poll_activity_if_due``).

    If ``activity_name`` is None (subscriber not yet primed), we build a
    ``Z9Activity(NoActivity, "Idle")`` rather than leaving the field at
    None — simpler frontend semantics.
    """
    inks = _build_inks(dashboard.get("ink_levels", {}), dashboard.get("ink_warnings", []))
    loaded_paper = build_loaded_paper(z9, dashboard, caps_cache) if z9 is not None else None
    global_status = dashboard.get("global_status", "Unknown")
    activity = activity_name or "NoActivity"
    state_text = _state_text(global_status, activity, loaded_paper)
    alerts = _build_alerts(inks, loaded_paper)
    ready = global_status == "Ready" and activity == "NoActivity"
    ident = dashboard.get("identification", {})
    return Status(
        model=ident.get("ModelName", "?"),
        serial=ident.get("SerialNumber", "?"),
        firmware=ident.get("FwReleaseName", "?"),
        ready=ready,
        state_text=state_text,
        loaded_paper=loaded_paper,
        inks=inks,
        alerts=alerts,
        z9_activity=Z9Activity(
            name=activity,
            progress_pct=activity_progress_pct,
        ),
        argyll=argyll,
        demo=demo,
    )


@router.get("/status", response_model=Status)
def get_status(request: Request, z9: Optional[Z9Client] = Depends(get_z9)) -> Status:
    """Machine + paper + inks + alerts state. Never raises 500."""
    argyll = _app_argyll(request)
    if z9 is None:
        return _unreachable_status(
            "No printer configured", configured=False, argyll=argyll)

    try:
        dashboard = z9.device.status()
        device = z9.device.device_status()
    except (Z9ConnectionError, Z9Error) as e:
        logger.warning("Z9 unreachable: %s", e)
        return _unreachable_status(f"Z9 unreachable ({e})", argyll=argyll)

    # Reuses the same extraction helper as the subscriber — guarantees
    # that GET and SSE produce a strictly identical Z9Activity.
    from webapp.backend.services.z9_status_subscriber import _extract_activity
    activity = _extract_activity(device)
    return _dashboard_to_status(
        dashboard, z9, request.app.state.capabilities_cache,
        activity_name=activity["activity_name"],
        activity_progress_pct=activity["activity_progress_pct"],
        argyll=argyll,
        demo=getattr(request.app.state, "demo", False),
    )


# ═════════════════════════════════════════════════════════════════════════
# SSE — /api/status/events
#
# Pushes the changes detected by Z9StatusPollSubscriber.
# Format event :
#   event: status_full   — full snapshot on connect (and after Z9 reconnect)
#   event: status_diff   — push on each change detected on the Z9 side
#   event: z9_state      — state-machine transitions (error, etc.)
#   event: ping          — keepalive every 15 s (anti proxy timeout)
# ═════════════════════════════════════════════════════════════════════════


def _get_status_subscriber(request: Request):
    """Get the Z9StatusPollSubscriber singleton from the lifespan."""
    return getattr(request.app.state, "z9_status_subscriber", None)


@router.get("/status/events")
async def stream_status_events(request: Request) -> EventSourceResponse:
    """SSE stream of the live Z9 state. See module docstring for the format."""
    sub = _get_status_subscriber(request)
    if sub is None:
        # "No printer configured" (z9=None) = a LEGITIMATE state, not an error.
        # We do NOT 503 (a 503 permanently closed the EventSource → the frontend
        # never saw z9_configured:false → onboarding never triggered). We emit
        # a VALID SSE stream: a "not configured" status_full (same snapshot as
        # GET /api/status) + keepalive, so the frontend triggers onboarding.
        # (Later reconfiguration: goes through the usual frontend refresh/reload —
        # this static stream just carries the "nothing configured" state.)
        snapshot = _unreachable_status("No printer configured", configured=False,
                                       argyll=_app_argyll(request))

        async def _gen_unconfigured():
            yield {"event": "status_full", "data": snapshot.model_dump_json()}
            while True:
                await asyncio.sleep(15.0)
                yield {"event": "ping", "data": ""}

        return EventSourceResponse(_gen_unconfigured())

    z9 = getattr(request.app.state, "z9", None)
    caps_cache = request.app.state.capabilities_cache
    argyll = _app_argyll(request)
    # Constant for the life of this stream: enabling/leaving demo swaps the
    # client + restarts the subscribers, which ends this stream (client reconnects).
    demo = getattr(request.app.state, "demo", False)

    # Per-client queue → a slow client does not block the subscriber nor
    # the other clients. maxsize=64 large to absorb a burst.
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)

    def on_change(event_type: str, data: dict) -> None:
        """Callback invoked by the subscriber from the FastAPI event loop.
        We enqueue, the generator consumes at its own pace."""
        try:
            queue.put_nowait((event_type, data))
        except asyncio.QueueFull:
            logger.warning("SSE queue full for /api/status/events (slow client?) — drop %s", event_type)

    sub.subscribe(on_change)  # immediate push of status_full with the current snapshot

    async def _gen():
        loop = asyncio.get_running_loop()
        try:
            while True:
                try:
                    # Keepalive: if nothing for 15 s, push a ping to
                    # prevent a proxy from cutting the connection.
                    event_type, data = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
                    continue

                if event_type in ("status_full", "status_diff"):
                    # Map raw dashboard → Pydantic Status → dict for JSON.
                    # The subscriber snapshot includes activity_name and
                    # activity_progress_pct which we extract
                    # here to pass to the mapper.
                    #
                    # _dashboard_to_status → build_loaded_paper → paper.get →
                    # rest GET /Paper/List = a NON-cached Z9 network call. On this
                    # SSE stream (event loop), on EACH event, it would stall the
                    # loop (all clients + async routes). We OFFLOAD it to
                    # thread (run_in_executor pattern of the calibration/profile SSE).
                    # Offload, not cache: the loaded paper can change → no
                    # stale (CLC cache lesson), and build_loaded_paper stays
                    # shared/synchronous for def handlers (threadpool).
                    status = await loop.run_in_executor(
                        None,
                        lambda d=data: _dashboard_to_status(
                            d, z9, caps_cache,
                            activity_name=d.get("activity_name"),
                            activity_progress_pct=d.get("activity_progress_pct"),
                            argyll=argyll,
                            demo=demo,
                        ),
                    )
                    yield {"event": event_type, "data": status.model_dump_json()}
                elif event_type == "z9_state":
                    yield {"event": "z9_state", "data": _json_dumps(data)}
                else:
                    # Unknown type — relayed as-is for future evolution
                    yield {"event": event_type, "data": _json_dumps(data)}
        finally:
            sub.unsubscribe(on_change)

    return EventSourceResponse(_gen())


def _json_dumps(data) -> str:
    """Minimal JSON-encode for non-Pydantic payloads (z9_state, etc.)."""
    import json
    return json.dumps(data, ensure_ascii=False, default=str)
