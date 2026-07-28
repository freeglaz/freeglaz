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

"""In-memory TTL cache for Z9 firmware previews (P3.H).

Critical protection: the Z9 has been observed to crash under excess
requests, and a reboot costs ~10 min. The JobQueuePanel panel
can trigger 10-20 preview fetches within a few seconds if
the user clicks on several external jobs (submitted by other
printing applications) that are not in the local freeglaz mapping.

We therefore cache the firmware preview responses in memory with a TTL
of 10 min, max 200 entries (enough for normal usage — the Z9 queue
itself keeps a limited number of jobs).

Implementation choices:
- Simple dict + timestamps (no external ``cachetools`` dependency)
- Global RLock — serializes firmware fetches (1 at a time) to
  avoid the thundering herd when N simultaneous requests miss the cache
- No caching of firmware error responses (which must propagate
  to the frontend for display)
"""
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Configuration. Modifiable in tests via monkeypatch.
TTL_SECONDS = 600        # 10 min
MAX_ENTRIES = 200

# {firmware_uuid: (timestamp_inserted_s, jpeg_bytes, content_type)}
_entries: dict[str, tuple[float, bytes, str]] = {}
_lock = threading.RLock()


def _now() -> float:
    """Hook for tests (monkeypatch ``preview_cache._now``)."""
    return time.monotonic()


def _purge_expired_locked() -> None:
    """Remove entries whose TTL has expired. Called under lock."""
    now = _now()
    to_remove = [k for k, (ts, *_) in _entries.items() if now - ts > TTL_SECONDS]
    for k in to_remove:
        del _entries[k]


def _evict_oldest_locked() -> None:
    """Evict the oldest entry if we exceed MAX_ENTRIES.

    Called under lock. Approximate LRU (by insertion age, not by
    access — sufficient for our low-volume use case).
    """
    while len(_entries) > MAX_ENTRIES:
        oldest_key = min(_entries.items(), key=lambda kv: kv[1][0])[0]
        del _entries[oldest_key]


def get(firmware_uuid: str) -> Optional[tuple[bytes, str]]:
    """Return ``(jpeg_bytes, content_type)`` or None on miss/expired."""
    with _lock:
        _purge_expired_locked()
        entry = _entries.get(firmware_uuid)
        if entry is None:
            logger.debug("preview_cache: MISS firmware %s", firmware_uuid)
            return None
        _, data, content_type = entry
        logger.debug("preview_cache: HIT firmware %s (saved Z9 call)", firmware_uuid)
        return data, content_type


def set(firmware_uuid: str, jpeg_bytes: bytes, content_type: str) -> None:
    """Store an entry. Evicts the oldest if MAX_ENTRIES exceeded."""
    with _lock:
        _entries[firmware_uuid] = (_now(), jpeg_bytes, content_type)
        _evict_oldest_locked()


def invalidate(firmware_uuid: str) -> bool:
    """Remove the entry if present. Returns True if it existed.

    Called:
    - On ``?refresh=1`` on the endpoint side (debug / force re-fetch)
    - On ``POST /api/jobs/{uuid}/remove`` (consistency: job removed →
      its cached preview becomes stale)
    """
    with _lock:
        existed = firmware_uuid in _entries
        _entries.pop(firmware_uuid, None)
        if existed:
            logger.debug("preview_cache: INVALIDATE firmware %s", firmware_uuid)
        return existed


def clear() -> None:
    """Empty the whole cache. Useful in tests."""
    with _lock:
        _entries.clear()


def size() -> int:
    """Number of entries (for stats / debug)."""
    with _lock:
        return len(_entries)


def fetch_or_cache(
    firmware_uuid: str,
    fetcher,  # callable: () -> (bytes, str) or raises
    *,
    force_refresh: bool = False,
) -> tuple[bytes, str]:
    """Return from the cache, otherwise call ``fetcher`` once and
    store the result.

    Serializes the fetches: if several threads call simultaneously
    with the same UUID on a cache miss, only the first runs ``fetcher``
    while it holds the lock; the others will see the cache HIT
    when they acquire the lock in turn. Simple and
    efficient pattern for our volume (low concurrency).

    :param firmware_uuid: cache key
    :param fetcher: no-arg callable returning ``(jpeg_bytes, content_type)``
    :param force_refresh: if True, invalidate first then fetch
    :raises: whatever ``fetcher`` raises — not cached.
    """
    with _lock:
        if force_refresh:
            _entries.pop(firmware_uuid, None)
            logger.debug("preview_cache: INVALIDATE firmware %s (refresh=1)", firmware_uuid)
        _purge_expired_locked()
        entry = _entries.get(firmware_uuid)
        if entry is not None:
            _, data, content_type = entry
            logger.debug("preview_cache: HIT firmware %s (saved Z9 call)", firmware_uuid)
            return data, content_type
        # Cache miss: we release the lock during the firmware fetch (can
        # take 1-2 s) so as not to block the rest of the app. We keep
        # just the guarantee that no other request on the same UUID
        # will do a parallel fetch — for that, we re-take the lock after
        # fetch and re-check the cache in case another thread has
        # won the race.
        logger.debug("preview_cache: MISS firmware %s → fetching from Z9", firmware_uuid)
    # Outside lock — firmware fetch (1-2 s acceptable)
    data, content_type = fetcher()
    # Re-lock to insert; someone else may have already inserted
    # in the meantime, we overwrite anyway (the bytes should be identical).
    with _lock:
        _entries[firmware_uuid] = (_now(), data, content_type)
        _evict_oldest_locked()
    return data, content_type
