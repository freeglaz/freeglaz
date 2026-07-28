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

"""App-level settings endpoints.

GET /api/settings → full state (defaults + overrides).
PUT /api/settings → patch dict, persisted.

Preparation for gamut_reference used by the 3D view.
"""
import logging

from fastapi import APIRouter, HTTPException

from webapp.backend.services import settings_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings")
def get_settings() -> dict:
    """Return the full settings dict (defaults + overrides)."""
    return settings_store.get_all()


@router.put("/settings", status_code=200)
def update_settings(patch: dict) -> dict:
    """Apply a patch (nested form). Validate against the schema.

    Returns:
        dict of the effective settings after application.

    Raises:
        HTTPException 422 if a value is invalid for a known key.
    """
    try:
        return settings_store.update(patch)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
