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

"""Endpoint /api/wake — wake the Z9 from freeglaz.

The user should no longer have to leave the app and open Safari
to visit https://{Z9_HOST}/ when the Z9 goes to sleep. The frontend
"Wake the Z9" button (StatusBar) triggers this endpoint.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from lib.z9_client import Z9Client

from webapp.backend.routes.status import get_z9
from webapp.backend.services.wake_service import wake_z9

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["wake"])


@router.post("/wake")
async def wake_endpoint(z9: Optional[Z9Client] = Depends(get_z9)) -> dict:
    """Wake the Z9 by visiting its HTTPS root + PIWS polling.

    Responses (always HTTP 200, except 503 if Z9_HOST missing):

    - ``{"status": "awake", "elapsed_seconds": 12.3}`` — success, the
      REST/SOAP services are available again.
    - ``{"status": "timeout", "elapsed_seconds": 30, "detail": "..."}``
      — Apache responds but the REST services have not restarted
      within the allotted time (30 s).
    - ``{"status": "unreachable", "detail": "..."}`` — TCP/HTTPS
      fails on the root, Z9 probably off or off-network.

    The operation blocks up to 30 s. On the route side, it is async and the
    HTTP fetches are dispatched via ``asyncio.to_thread`` → the event
    loop stays free to serve other routes during the wait.
    """
    if z9 is None:
        raise HTTPException(
            503, detail="Z9 not configured (Z9_HOST missing)",
        )
    logger.info("wake: triggered by user — host=%s", z9.host)
    result = await wake_z9(z9.host)
    logger.info("wake: done status=%r elapsed=%.1fs",
                result.get("status"), result.get("elapsed_seconds", -1))
    return result
