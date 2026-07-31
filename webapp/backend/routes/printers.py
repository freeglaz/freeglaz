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

"""Known printers routes (IP configuration, connection test).

POST /api/printers/test {ip} — tests an ARBITRARY IP (not the configured
client, so NO Z9 auth): validates "this is a DesignJet" + retrieves serial/model via
``printer_probe.test_connection`` (lib, throwaway). Persists nothing.

(List management — view/add/remove/edit/active — will come with the UI
of brief 3, via the cache.add_printer/... helpers already delivered in brief 1.)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from lib.z9_client import cache, printer_probe

router = APIRouter(prefix="/api/printers", tags=["printers"])


class TestPrinterBody(BaseModel):
    ip: str = Field(..., min_length=1, description="IP or hostname to test")


class AddPrinterBody(BaseModel):
    ip: str = Field(..., min_length=1)
    serial: str = Field(..., min_length=1)
    name: str = ""
    model_support: Optional[str] = None   # "validated" | "untested" (from the test)
    admin_pwd: Optional[str] = None       # Z9 admin password (protected endpoints / job queue)


class UpdatePrinterBody(BaseModel):
    ip: Optional[str] = None
    name: Optional[str] = None
    # admin_pwd: None = leave unchanged, "" = clear, other = set (cf. cache.update_printer)
    admin_pwd: Optional[str] = None


def _redact(entry: Optional[dict]) -> Optional[dict]:
    """Strip the cleartext admin password from an outgoing store entry, exposing
    only whether one is set (``has_admin_pwd``). The secret is stored on disk
    (accepted, same posture as .env) but must NOT be broadcast to the browser on
    every poll/list."""
    if entry is None:
        return None
    out = {k: v for k, v in entry.items() if k != "admin_pwd"}
    out["has_admin_pwd"] = bool(entry.get("admin_pwd"))
    return out


@router.post("/test")
def test_printer(body: TestPrinterBody) -> dict:
    """Test a submitted IP (sync → FastAPI threadpool, does not block the event loop).

    Returns: ``{status: ok|unreachable|not_a_designjet, …}`` — always 200, the
    status carries the verdict (the UI decides the display / the untested warning)."""
    return printer_probe.test_connection(body.ip)


# ─── Registry CRUD (no Z9 auth: config, not a machine operation) ──────


@router.get("")
def list_printers() -> dict:
    """List of known printers + the active one (for the Printers modal).
    Admin passwords are redacted → only ``has_admin_pwd`` is exposed."""
    return {"printers": [_redact(p) for p in cache.read_printers()],
            "active": _redact(cache.active_printer())}


@router.post("", status_code=201)
def create_printer(body: AddPrinterBody) -> dict:
    """Register a printer (upsert by serial). The first one becomes active
    automatically (onboarding). An optional admin password is stored (job queue)."""
    first = not cache.read_printers()
    entry = cache.add_printer(ip=body.ip, serial=body.serial, name=body.name,
                              active=first, model_support=body.model_support,
                              admin_pwd=body.admin_pwd)
    return _redact(entry)


@router.put("/{serial}")
def edit_printer(serial: str, body: UpdatePrinterBody) -> dict:
    """Edit the IP/name/admin password of a printer (not the active one → /activate)."""
    entry = cache.update_printer(serial, ip=body.ip, name=body.name,
                                 admin_pwd=body.admin_pwd)
    if entry is None:
        raise HTTPException(404, detail=f"unknown printer: {serial}")
    return _redact(entry)


@router.post("/{serial}/activate")
def activate_printer(serial: str) -> dict:
    """Mark the printer as active (deactivates the others). Change takes effect at
    the next startup (no hot re-pointing)."""
    if not cache.set_active_printer(serial):
        raise HTTPException(404, detail=f"unknown printer: {serial}")
    return {"active": _redact(cache.active_printer())}


@router.delete("/{serial}")
def delete_printer(serial: str) -> dict:
    """Remove a printer from the registry."""
    if not cache.remove_printer(serial):
        raise HTTPException(404, detail=f"unknown printer: {serial}")
    return {"removed": serial}


# ─── Offline demo mode (mock Z9) ──────────────────────────────────────────
# Two POST endpoints (not a DELETE — it would collide with DELETE /{serial}).
async def _swap_z9(app, new_client, *, demo: bool, mock_print: bool) -> None:
    """Hot-swap app.state.z9 + restart the subscribers against it. The app has
    'no hot re-pointing' for real printers (a printer change needs a restart);
    demo mode is the one controlled runtime swap."""
    from webapp.backend.main import start_z9_subscribers, stop_z9_subscribers
    await stop_z9_subscribers(app)
    old = getattr(app.state, "z9", None)
    if old is not None:
        try:
            old.close()
        except Exception:  # noqa: BLE001 — best-effort
            pass
    app.state.z9 = new_client
    app.state.demo = demo
    app.state.use_mock_print = mock_print
    app.state.capabilities_cache = {}
    app.state.paper_icc_cache = {}
    await start_z9_subscribers(app)


@router.post("/demo")
async def enable_demo(request: Request) -> dict:
    """Enable offline DEMO mode: a MockZ9Client (curated canned data) replaces the
    Z9 client so the Print screen works without a printer. Printing is simulated
    (mock worker, nothing sent). Idempotent."""
    import logging
    from webapp.backend.services.mock_z9 import MockZ9Client
    await _swap_z9(request.app, MockZ9Client(), demo=True, mock_print=True)
    logging.getLogger(__name__).info("Demo mode enabled (mock Z9)")
    return {"demo": True}


@router.post("/demo/exit")
async def disable_demo(request: Request) -> dict:
    """Leave demo mode: rebuild the real Z9 client from config (or None)."""
    import logging
    import os
    from lib.z9_client import Z9Client, Z9Error
    try:
        client = Z9Client.from_env()
    except Z9Error:
        client = None
    mock_print = os.getenv("FREEGLAZ_MOCK_PRINT") == "1"
    await _swap_z9(request.app, client, demo=False, mock_print=mock_print)
    logging.getLogger(__name__).info("Demo mode disabled")
    return {"demo": False}
