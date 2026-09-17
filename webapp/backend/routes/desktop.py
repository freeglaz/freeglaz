"""Desktop-only bridge: hand a file to the RUNNING window (single instance).

freeglaz handles ONE image at a time by construction, so a second launch must
not open a second window. Instead the second process uploads the file to the
running instance through the ordinary ``POST /api/files``, then calls
``POST /api/desktop/open`` here, which navigates the live window onto it — the
very URL the open-on-launch path already builds (``/?file_id=…&name=…``).

The handler is registered by ``webapp/desktop.py`` once the window exists. In
browser (web server) mode nothing registers it, so every call answers 503 and
this route exposes no behaviour at all.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desktop", tags=["desktop"])

# Set by webapp/desktop.py; stays None in browser mode (see module docstring).
_open_handler: Optional[Callable[[str, str], None]] = None


def set_open_handler(handler: Optional[Callable[[str, str], None]]) -> None:
    """Register (or clear, with ``None``) the native window's open callback."""
    global _open_handler
    _open_handler = handler


class OpenInWindow(BaseModel):
    """A file already uploaded via ``POST /api/files``, to show in the window."""

    file_id: str = Field(..., min_length=1)
    name: str = ""


@router.get("/instance")
def instance() -> dict:
    """Whether a native window is attached — how a second launch confirms that
    the port it found really belongs to a desktop instance, not to a bare web
    server (which cannot show anything)."""
    return {"desktop": _open_handler is not None}


@router.post("/open")
def open_in_window(req: OpenInWindow) -> dict:
    """Navigate the running window onto an already-uploaded file."""
    handler = _open_handler
    if handler is None:
        raise HTTPException(
            status_code=503,
            detail="No native window attached (web server mode).")
    try:
        handler(req.file_id, req.name)
    except Exception as exc:  # noqa: BLE001 — never take the window down for this
        logger.warning("open_in_window failed (%s): %s", type(exc).__name__, exc)
        raise HTTPException(status_code=500, detail="Could not reach the window.")
    logger.info("Handoff: window navigated onto %s (%s)", req.file_id, req.name or "—")
    return {"ok": True}
