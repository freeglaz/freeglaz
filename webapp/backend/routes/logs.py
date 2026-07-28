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

"""``/api/logs`` endpoints — Z9 firmware events (LEDM ProductLogsDyn).

Standalone feature. Exposes the last 100 firmware events with
severity, event code, timestamp, C++ source, firmware revision.
Read-only, no auth, 5s cache recommended on the caller side.
"""
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lib.z9_client import Z9Client, Z9Error
from webapp.backend.routes.status import get_z9

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/logs", tags=["logs"])


class LogEventDto(BaseModel):
    sequence_number: int
    event_code: str
    timestamp: str
    event_detail: str
    internal_error_code: int
    source_file: str
    source_line: int
    severity: str
    firmware_revision: str


def _require_z9(z9: Optional[Z9Client] = Depends(get_z9)) -> Z9Client:
    if z9 is None:
        raise HTTPException(503, detail="Z9 not configured (Z9_HOST missing)")
    return z9


@router.get("/recent", response_model=list[LogEventDto])
def get_recent_logs(
    z9: Z9Client = Depends(_require_z9),
    limit: int = Query(100, ge=1, le=100),
    severity: Optional[str] = Query(None),
    since: Optional[str] = Query(None),
    event_code: Optional[str] = Query(None),
) -> list[LogEventDto]:
    """Recent Z9 firmware events (LEDM ProductLogsDyn, max 100)."""
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(400, detail=f"Invalid date format: {since}")

    try:
        events = z9.logs.get_events(
            limit=limit,
            severity=severity,
            since=since_dt,
            event_code_prefix=event_code,
        )
    except Z9Error as e:
        logger.warning("get_recent_logs: %s", e)
        raise HTTPException(502, detail=f"Z9 logs fetch failed: {e}")

    return [LogEventDto(**ev.to_dict()) for ev in events]
