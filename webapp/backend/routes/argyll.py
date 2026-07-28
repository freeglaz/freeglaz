# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Argyll settings endpoints — GET/PUT the Argyll root, persisted to the TOML.

Dedicated (NOT /api/settings, which targets the app-level settings.json the
resolution cascade ignores). Validation reuses check_argyll's predicates; a
successful PUT re-probes and refreshes app.state.argyll so the banner + status
reflect the change without a restart.
"""
import logging

from fastapi import APIRouter, HTTPException, Request

from webapp.backend.services import argyll_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


def _payload(request: Request) -> dict:
    # Re-probe live so GET/PUT always reflect reality (and keep the banner fresh).
    status = argyll_settings.probe()
    request.app.state.argyll = status
    return {
        "status": status,
        "root": argyll_settings.read_root(),
        "env_override": argyll_settings.env_override_active(),
    }


@router.get("/argyll")
def get_argyll(request: Request) -> dict:
    """Current Argyll state (probe) + the TOML root + env-override flag."""
    return _payload(request)


@router.put("/argyll")
def put_argyll(body: dict, request: Request) -> dict:
    """Validate a candidate ``root`` then persist it to ~/.freeglazrc.toml.

    Refuses an invalid root (missing binaries or ref) WITHOUT writing anything.
    On success: writes [argyll].root (other TOML sections preserved), re-probes,
    refreshes app.state.argyll.

    An EMPTY root is a valid intent = "reset to auto-detection": we remove the
    custom root from the TOML (other sections preserved) and re-probe → the
    cascade falls back to the system/Homebrew Argyll. Only a NON-empty but
    invalid root is rejected (422).
    """
    raw = (body or {}).get("root")
    root = raw.strip() if isinstance(raw, str) else ""
    if not root:
        argyll_settings.clear_root()
        logger.info("Argyll root cleared via GUI → auto-detection")
        return _payload(request)
    result = argyll_settings.validate_root(root)
    if not result["ok"]:
        raise HTTPException(
            status_code=422,
            detail={"code": "argyll_root_invalid",
                    "message": ("Invalid Argyll root — missing: "
                                f"{', '.join(result['missing'])}."),
                    "missing": result["missing"]},
        )
    argyll_settings.write_root(root)
    logger.info("Argyll root set via GUI: %s", root)
    return _payload(request)
