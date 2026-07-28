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

"""30s cache of the LEDM snapshot ``/Calibration/Calibration.xml``.

P3.A2. Avoids a Z9 HTTPS fetch on each ``GET /api/papers``
(which is called frequently on the UI side). The LEDM only changes on
rare events (calibration finished, paper created), so 30s of
cache is generous.

Manual invalidation exposed for the ``calibration_jobs`` service:
when a CLC finishes, it calls ``invalidate()`` so that the
next ``GET /api/papers`` sees the fresh status (goes from
``running`` → ``valid``/``stale``).

Module-level singleton pattern (lock + cache dict), consistent with
``calibration_jobs.py`` P4.B.
"""
import logging
import threading
import time
from typing import Optional

from lib.z9_client.calibration_ledm import CalibrationLEDMReader
from lib.z9_client.exceptions import Z9Error

logger = logging.getLogger(__name__)

# Cache TTL. The LEDM is globally stable — a freshly created custom
# paper appears within a few seconds on the firmware side. 30s
# is a reasonable compromise between freshness and fetch economy.
CACHE_TTL_SECONDS = 30.0

_lock = threading.RLock()
_cache: Optional[dict[str, dict]] = None
_cached_at: float = 0.0
# Z9 host learned on the first call (from the passed Z9Client). In practice
# the host does not change during a backend session.
_reader: Optional[CalibrationLEDMReader] = None


def _ensure_reader(z9_client) -> CalibrationLEDMReader:
    """Lazy init of the reader with the host of the current Z9Client."""
    global _reader
    with _lock:
        if _reader is None or _reader.host != getattr(z9_client, "host", None):
            _reader = CalibrationLEDMReader(
                host=z9_client.host,
                timeout=10,
            )
        return _reader


def get_snapshot(z9_client, force_refresh: bool = False) -> dict[str, dict]:
    """Return the LEDM colorLinearization snapshot ``{mediaid: {status, timestamp}}``,
    from cache or fresh.

    On LEDM network error (timeout, 404 if the endpoint disappears
    after a firmware update), returns the last valid snapshot
    from cache, or ``{}`` if none. The caller (``transform_all``) handles
    the SOAP fallback via its ``ledm_clc=None`` parameter.

    :param force_refresh: ignore the TTL and refetch immediately
    """
    global _cache, _cached_at
    with _lock:
        age = time.time() - _cached_at
        if not force_refresh and _cache is not None and age < CACHE_TTL_SECONDS:
            return _cache

    reader = _ensure_reader(z9_client)
    try:
        snapshot = reader.color_linearization_by_mediaid()
    except Z9Error as e:
        logger.info("LEDM Calibration fetch failed: %s — fallback cache/empty", e)
        with _lock:
            return _cache if _cache is not None else {}

    with _lock:
        _cache = snapshot
        _cached_at = time.time()
        logger.debug("LEDM Calibration cache refreshed: %d entries", len(snapshot))
        return snapshot


def invalidate() -> None:
    """Force the next ``get_snapshot`` to refetch. To be called when we
    know the LEDM state has changed (end of CLC, custom creation).
    """
    global _cache, _cached_at
    with _lock:
        _cache = None
        _cached_at = 0.0
        logger.debug("LEDM Calibration cache invalidated")
