"""Tests for preview_cache TTL on firmware fetches."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from webapp.backend.services import preview_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the cache before each test (module-level global state)."""
    preview_cache.clear()
    yield
    preview_cache.clear()


# ─── get / set / invalidate basics ────────────────────────────────────


def test_get_returns_none_on_miss():
    assert preview_cache.get("UUID-X") is None


def test_set_then_get_returns_stored_payload():
    preview_cache.set("UUID-X", b"\xff\xd8\xff\xe0jpeg", "image/jpeg")
    out = preview_cache.get("UUID-X")
    assert out == (b"\xff\xd8\xff\xe0jpeg", "image/jpeg")


def test_invalidate_removes_entry():
    preview_cache.set("UUID-X", b"bytes", "image/jpeg")
    assert preview_cache.invalidate("UUID-X") is True
    assert preview_cache.get("UUID-X") is None


def test_invalidate_returns_false_when_absent():
    assert preview_cache.invalidate("UUID-NOTHING") is False


def test_clear_resets_all_entries():
    preview_cache.set("A", b"a", "image/jpeg")
    preview_cache.set("B", b"b", "image/jpeg")
    preview_cache.clear()
    assert preview_cache.size() == 0


# ─── TTL ──────────────────────────────────────────────────────────────


def test_entry_expires_after_ttl(monkeypatch):
    """Mock the monotonic time: the entry must disappear after TTL_SECONDS."""
    fake_time = [1000.0]
    monkeypatch.setattr(preview_cache, "_now", lambda: fake_time[0])
    monkeypatch.setattr(preview_cache, "TTL_SECONDS", 600)

    preview_cache.set("UUID-X", b"bytes", "image/jpeg")
    assert preview_cache.get("UUID-X") is not None

    # +599s: still valid
    fake_time[0] += 599
    assert preview_cache.get("UUID-X") is not None

    # +602s: expired
    fake_time[0] += 3
    assert preview_cache.get("UUID-X") is None


def test_purge_expired_called_on_get(monkeypatch):
    """Expired entries are cleaned up on the next access."""
    fake_time = [1000.0]
    monkeypatch.setattr(preview_cache, "_now", lambda: fake_time[0])
    monkeypatch.setattr(preview_cache, "TTL_SECONDS", 10)

    preview_cache.set("A", b"a", "image/jpeg")
    fake_time[0] += 20
    # The entry is not yet physically removed
    preview_cache.get("A")  # triggers purge
    assert preview_cache.size() == 0


# ─── MAX_ENTRIES eviction ─────────────────────────────────────────────


def test_evicts_oldest_when_max_exceeded(monkeypatch):
    """Beyond MAX_ENTRIES, the oldest entry is evicted."""
    fake_time = [0.0]
    monkeypatch.setattr(preview_cache, "_now", lambda: fake_time[0])
    monkeypatch.setattr(preview_cache, "MAX_ENTRIES", 3)

    for i, uuid in enumerate(["A", "B", "C"]):
        fake_time[0] = float(i)
        preview_cache.set(uuid, f"data-{uuid}".encode(), "image/jpeg")

    assert preview_cache.size() == 3
    # Insert D -> evict A (the oldest)
    fake_time[0] = 10.0
    preview_cache.set("D", b"data-D", "image/jpeg")
    assert preview_cache.size() == 3
    assert preview_cache.get("A") is None
    assert preview_cache.get("D") is not None


# ─── fetch_or_cache ───────────────────────────────────────────────────


def test_fetch_or_cache_miss_calls_fetcher_and_stores():
    fetcher = MagicMock(return_value=(b"firmware-bytes", "image/jpeg"))
    data, ct = preview_cache.fetch_or_cache("UUID-X", fetcher)
    assert data == b"firmware-bytes"
    assert ct == "image/jpeg"
    fetcher.assert_called_once()
    # 2nd call: HIT, fetcher not called
    fetcher.reset_mock()
    preview_cache.fetch_or_cache("UUID-X", fetcher)
    fetcher.assert_not_called()


def test_fetch_or_cache_force_refresh_invalidates_first():
    fetcher = MagicMock(return_value=(b"v1", "image/jpeg"))
    preview_cache.fetch_or_cache("UUID-X", fetcher)
    fetcher.return_value = (b"v2", "image/jpeg")
    # Without force_refresh: HIT v1
    data, _ = preview_cache.fetch_or_cache("UUID-X", fetcher)
    assert data == b"v1"
    # With force_refresh=True: invalidate then fetch v2
    data, _ = preview_cache.fetch_or_cache(
        "UUID-X", fetcher, force_refresh=True,
    )
    assert data == b"v2"


def test_fetch_or_cache_does_not_store_on_error():
    """If the fetcher raises, nothing is cached."""
    def fetcher_err():
        raise RuntimeError("firmware down")

    with pytest.raises(RuntimeError):
        preview_cache.fetch_or_cache("UUID-X", fetcher_err)
    assert preview_cache.size() == 0


def test_concurrent_misses_dedupe_fetcher_calls():
    """2 concurrent threads on the same UUID on a cache miss -> ideally
    a single fetch. With our lock: one thread fetches outside the lock,
    the other sees MISS too and fetches too, BUT each stores and both
    receive a valid response.

    We accept that in the worst case, 2 concurrent fetches occur when the
    1st releases the lock to fetch — this is the acceptable tradeoff
    (holding the lock during the fetch would block the whole app). We
    just verify it does not crash and that both threads see a consistent
    response."""
    call_count = [0]
    fetch_event = threading.Event()
    release_event = threading.Event()

    def slow_fetcher():
        call_count[0] += 1
        fetch_event.set()
        # Brief wait to let the other thread reach fetch_or_cache
        release_event.wait(timeout=1.0)
        return (b"data", "image/jpeg")

    results = []

    def worker():
        try:
            data, _ = preview_cache.fetch_or_cache("UUID-X", slow_fetcher)
            results.append(data)
        except Exception as e:
            results.append(e)

    t1 = threading.Thread(target=worker)
    t1.start()
    fetch_event.wait(timeout=1.0)  # t1 is inside the fetcher

    t2 = threading.Thread(target=worker)
    t2.start()

    # Release t1, t1 stores then t2 looks up -> HIT
    release_event.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert len(results) == 2
    assert results[0] == b"data" and results[1] == b"data"
    # call_count == 1 is the ideal (t2 saw the cache HIT after t1 stored).
    # call_count == 2 is acceptable if t2 reached the cache check before
    # t1 finished storing.
    assert call_count[0] in (1, 2)
