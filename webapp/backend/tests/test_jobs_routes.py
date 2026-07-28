"""Tests Phase 2 — backend Z9JobsSubscriber + routes /api/jobs."""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from lib.z9_client.exceptions import Z9AuthError, Z9ConnectionError, Z9RESTError
from webapp.backend.main import app
from webapp.backend.routes.status import get_z9
from webapp.backend.services import job_mapping, job_preview, preview_cache
from webapp.backend.services.z9_jobs_subscriber import Z9JobsSubscriber


# Example snapshot that JobQueueOps.get_jobs_snapshot would return
_SAMPLE_SNAPSHOT = {
    "queue_status": "Paused",
    "number_of_jobs": 2,
    "modification_number": 56,
    "timestamp": "2026-05-23T13:44:36Z",
    "jobs": [
        {"uuid": "JOB-A", "name": "a.tif (1 page) - freeglaz", "user": "user",
         "status": "WaitingToPrint", "media_source": "ManualSheet",
         "media_type_id": "X", "print_quality": "Best", "max_detail": True,
         "copies_requested": 1, "number_of_pages": 1,
         "page_size_mm": {"width": 100.0, "height": 150.0},
         "progress_percentage": 0.0, "preview_uri": "/.../Page/1/resources/preview",
         "submission_timestamp": "2026-05-23T14:49:45Z",
         "completion_timestamp": "", "completion_status": "OK",
         "hold_reason": "None", "source": "Application", "pdl_name": "PDF"},
    ],
}


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure that dependency_overrides do not leak between tests."""
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect the mapping + previews to tmp_path to avoid polluting
    the prod ``webapp/data/`` directory during tests."""
    monkeypatch.setattr(job_mapping, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_mapping, "MAPPING_FILE", tmp_path / "job_mapping.json")
    monkeypatch.setattr(job_preview, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_preview, "PREVIEWS_DIR", tmp_path / "job_previews")
    # P3.H: clear the module-level cache before each test for isolation.
    preview_cache.clear()
    yield
    preview_cache.clear()


def _make_z9_mock(get_snapshot_returns=None, get_snapshot_raises=None,
                  pause_raises=None, resume_raises=None,
                  cancel_returns=True, cancel_raises=None,
                  remove_returns=True, remove_raises=None,
                  preview_returns=b"\x89PNG\r\n\x1a\n...",
                  preview_raises=None,
                  reprint_returns=None, reprint_raises=None,
                  find_new_returns=None):
    """Build a mock Z9Client with a configurable .jobs sub-mock."""
    jobs = MagicMock()
    if get_snapshot_raises is not None:
        jobs.get_jobs_snapshot.side_effect = get_snapshot_raises
    else:
        jobs.get_jobs_snapshot.return_value = get_snapshot_returns or _SAMPLE_SNAPSHOT
    jobs.pause_queue.side_effect = pause_raises
    jobs.resume_queue.side_effect = resume_raises
    if cancel_raises is not None:
        jobs.cancel_job.side_effect = cancel_raises
    else:
        jobs.cancel_job.return_value = cancel_returns
    if remove_raises is not None:
        jobs.remove_job.side_effect = remove_raises
    else:
        jobs.remove_job.return_value = remove_returns
    if preview_raises is not None:
        jobs.get_job_preview.side_effect = preview_raises
    else:
        jobs.get_job_preview.return_value = preview_returns
    if reprint_raises is not None:
        jobs.reprint_job.side_effect = reprint_raises
    else:
        jobs.reprint_job.return_value = reprint_returns or {
            "queue_uuid": "Q", "original_uuid": "J",
            "status": "SuccessfullySubmitted",
        }
    jobs.find_new_reprint_job.return_value = find_new_returns
    return SimpleNamespace(jobs=jobs, host="192.168.1.50")


# ─── Subscriber ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_subscriber_returns_empty_snapshot_before_first_tick():
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=10.0)  # long interval, we test only the initial state
    snap = sub.current_snapshot()
    assert snap["queue_status"] == "Unknown"
    assert snap["jobs"] == []
    assert snap["_meta"]["consecutive_failures"] == 0
    assert snap["_meta"]["last_success_at"] is None


@pytest.mark.asyncio
async def test_subscriber_updates_snapshot_after_first_tick():
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=0.01)
    await sub.start()
    # Let 2-3 ticks pass
    await asyncio.sleep(0.05)
    snap = sub.current_snapshot()
    await sub.stop()

    assert snap["queue_status"] == "Paused"
    assert snap["number_of_jobs"] == 2
    assert len(snap["jobs"]) == 1
    assert snap["jobs"][0]["uuid"] == "JOB-A"
    assert snap["_meta"]["consecutive_failures"] == 0
    assert snap["_meta"]["last_success_at"] == "2026-05-23T13:44:36Z"


@pytest.mark.asyncio
async def test_subscriber_keeps_last_snapshot_on_z9_error():
    """If the Z9 becomes unreachable mid-session, keep the last known
    snapshot and increment consecutive_failures."""
    z9 = _make_z9_mock()
    # First success, then successive errors
    z9.jobs.get_jobs_snapshot.side_effect = [
        _SAMPLE_SNAPSHOT,
        Z9ConnectionError("network down"),
        Z9ConnectionError("still down"),
    ]
    sub = Z9JobsSubscriber(z9, poll_interval=0.01)
    # Neutralize the backoff (5/10/30/60 s) to stay fast in test.
    sub.BACKOFF_STEPS_SECONDS = (0.005, 0.005, 0.005, 0.005)
    await sub.start()
    await asyncio.sleep(0.10)  # several ticks
    snap = sub.current_snapshot()
    await sub.stop()

    # The snapshot stays the one from the first success
    assert snap["queue_status"] == "Paused"
    assert snap["number_of_jobs"] == 2
    # Error counter visible
    assert snap["_meta"]["consecutive_failures"] >= 1


@pytest.mark.asyncio
async def test_subscriber_resets_failure_counter_on_recovery():
    z9 = _make_z9_mock()
    # Pad the success side so side_effect is not exhausted during the ticks
    # (MagicMock raises StopIteration on an exhausted side_effect list, which
    # would be counted as a new failure)
    z9.jobs.get_jobs_snapshot.side_effect = (
        [Z9ConnectionError("down"), Z9ConnectionError("down")]
        + [_SAMPLE_SNAPSHOT] * 100
    )
    sub = Z9JobsSubscriber(z9, poll_interval=0.01)
    # Neutralize the exponential backoff to stay fast in test.
    sub.BACKOFF_STEPS_SECONDS = (0.005, 0.005, 0.005, 0.005)
    await sub.start()
    await asyncio.sleep(0.10)
    snap = sub.current_snapshot()
    await sub.stop()

    # After the successful recovery, counter reset to 0
    assert snap["_meta"]["consecutive_failures"] == 0


def test_polling_backoff_on_repeated_failures():
    """Bug regression: on consecutive failures, the interval between
    ticks must follow the exponential backoff 5/10/30/60 s. Reset to
    normal poll_interval on the next success."""
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=3.0)

    # 0 failures → normal poll_interval
    assert sub._consecutive_failures == 0
    assert sub._next_sleep_seconds() == 3.0

    # 1 failure → 5 s
    sub._consecutive_failures = 1
    assert sub._next_sleep_seconds() == 5.0

    # 2 failures → 10 s
    sub._consecutive_failures = 2
    assert sub._next_sleep_seconds() == 10.0

    # 3 failures → 30 s
    sub._consecutive_failures = 3
    assert sub._next_sleep_seconds() == 30.0

    # 4 failures → 60 s
    sub._consecutive_failures = 4
    assert sub._next_sleep_seconds() == 60.0

    # 5 failures → 120 s (anti-hammering cap: let a down Z9 breathe)
    sub._consecutive_failures = 5
    assert sub._next_sleep_seconds() == 120.0

    # 10 failures → still 120 s (clamped on the last step)
    sub._consecutive_failures = 10
    assert sub._next_sleep_seconds() == 120.0


@pytest.mark.asyncio
async def test_subscriber_stop_is_idempotent():
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=0.01)
    await sub.start()
    await sub.stop()
    await sub.stop()  # 2nd stop does not raise


# ─── Routes ───────────────────────────────────────────────────────────


def _client_with(z9_mock, subscriber=None):
    """Build a TestClient with a mocked Z9 injected via dependency
    override + a custom subscriber set on app.state AFTER the lifespan
    start."""
    app.dependency_overrides[get_z9] = lambda: z9_mock
    ctx = TestClient(app)
    # The TestClient as a context manager triggers the lifespan which resets
    # app.state. We configure the subscriber afterwards with __enter__.
    return ctx, subscriber


def test_get_jobs_returns_subscriber_snapshot():
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=1.0)
    sub._snapshot = _SAMPLE_SNAPSHOT
    sub._last_success_at = "2026-05-23T13:44:36Z"
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.get("/api/jobs")
    assert r.status_code == 200
    body = r.json()
    assert body["queue_status"] == "Paused"
    assert body["number_of_jobs"] == 2
    assert len(body["jobs"]) == 1
    assert body["_meta"]["subscriber_started"] is True


def test_get_jobs_returns_empty_when_no_subscriber():
    """Subscriber absent → /api/jobs returns a coherent empty snapshot."""
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = None
        r = client.get("/api/jobs")
    assert r.status_code == 200
    body = r.json()
    assert body["jobs"] == []
    assert body["_meta"]["subscriber_started"] is False


def test_pause_queue_returns_202():
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post("/api/jobs/queue/pause")
    assert r.status_code == 202
    assert r.json() == {"ok": True, "action": "pause"}
    z9.jobs.pause_queue.assert_called_once()


def test_resume_queue_returns_202():
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post("/api/jobs/queue/resume")
    assert r.status_code == 202
    z9.jobs.resume_queue.assert_called_once()


# ─── DELETE /api/jobs/queue — clear the queue (Patch 3) ────────────────


def test_clear_queue_returns_removed_and_failed_counts():
    """DELETE /api/jobs/queue returns {removed_count, failed_count}
    after pivoting to an individual loop (25/05/2026)."""
    z9 = _make_z9_mock()
    z9.jobs.clear_all.return_value = (5, 0)
    z9.jobs.queue_uuid = "QUEUE-UUID"
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/jobs/queue")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "ok": True, "removed_count": 5, "failed_count": 0,
        "queue_uuid": "QUEUE-UUID",
    }


def test_clear_queue_partial_failure_returns_200_with_counts():
    """Best-effort: if N jobs failed, we still return 200 with the
    exact counts. The frontend decides what to display."""
    z9 = _make_z9_mock()
    z9.jobs.clear_all.return_value = (3, 2)  # 3 OK, 2 failed
    z9.jobs.queue_uuid = "QUEUE-UUID"
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/jobs/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["removed_count"] == 3
    assert body["failed_count"] == 2


def test_clear_queue_empty_returns_zero_zero():
    z9 = _make_z9_mock()
    z9.jobs.clear_all.return_value = (0, 0)
    z9.jobs.queue_uuid = "QUEUE-UUID"
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/jobs/queue")
    assert r.status_code == 200
    body = r.json()
    assert body["removed_count"] == 0
    assert body["failed_count"] == 0


def test_clear_queue_returns_502_on_pre_loop_error():
    """Error OUTSIDE the loop (e.g. snapshot fail) → 502. Errors INSIDE
    the loop are already captured as failed_count."""
    from lib.z9_client.exceptions import Z9ConnectionError
    z9 = _make_z9_mock()
    z9.jobs.clear_all.side_effect = Z9ConnectionError("network")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.delete("/api/jobs/queue")
    assert r.status_code == 502


def test_clear_queue_returns_503_when_z9_not_configured():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as client:
        r = client.delete("/api/jobs/queue")
    assert r.status_code == 503


def test_pause_queue_502_on_z9_error():
    z9 = _make_z9_mock(pause_raises=Z9ConnectionError("down"))
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post("/api/jobs/queue/pause")
    assert r.status_code == 502


def test_cancel_job_returns_no_op_false_on_acceptance():
    z9 = _make_z9_mock(cancel_returns=True)
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{fake_uuid}/cancel")
    assert r.status_code == 202
    assert r.json() == {"ok": True, "no_op": False, "action": "cancel"}


def test_cancel_job_returns_no_op_true_on_502_silently():
    """When the firmware replies 502 (job already Deleted), the lib returns
    False → the API exposes no_op:true as a success."""
    z9 = _make_z9_mock(cancel_returns=False)
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{fake_uuid}/cancel")
    assert r.status_code == 202
    assert r.json() == {"ok": True, "no_op": True, "action": "cancel"}


def test_remove_job_passes_uuid():
    z9 = _make_z9_mock(remove_returns=True)
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.post(f"/api/jobs/{fake_uuid}/remove")
    assert r.status_code == 202
    z9.jobs.remove_job.assert_called_once_with(fake_uuid)


def test_invalid_uuid_returns_422():
    """The strict UUID pattern rejects arbitrary ids (security against
    path injection). FastAPI replies 422 on a regex mismatch of a Path
    parameter."""
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post("/api/jobs/not-a-uuid/cancel")
    assert r.status_code == 422


def test_jobs_route_returns_503_when_z9_not_configured():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as client:
        r = client.post("/api/jobs/queue/pause")
    assert r.status_code == 503


def test_preview_returns_png_with_correct_content_type():
    z9 = _make_z9_mock(preview_returns=b"\x89PNG\r\n\x1a\nfakepng")
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNG\r\n\x1a\nfakepng"


def test_preview_returns_jpeg_with_correct_content_type():
    z9 = _make_z9_mock(preview_returns=b"\xff\xd8\xff\xe0jpegbytes")
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_preview_returns_401_on_z9_auth_error():
    z9 = _make_z9_mock(preview_raises=Z9AuthError("admin pwd missing"))
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 401


def test_preview_returns_410_when_job_deleted():
    """Z9 replies 500 on preview of a Deleted job → API exposes 410 Gone."""
    z9 = _make_z9_mock(preview_raises=Z9RESTError(500, "/preview", "deleted"))
    app.dependency_overrides[get_z9] = lambda: z9
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 410


# ─── P2.D: local-first preview + preview_source field ───────────────


def _make_local_thumb(jobacct5: str, bytes_payload: bytes = b"\xff\xd8\xff\xe0local-jpeg") -> None:
    """Create a synthetic JPEG file on the job_preview side for the mapping test."""
    job_preview.PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    (job_preview.PREVIEWS_DIR / f"{jobacct5}.jpg").write_bytes(bytes_payload)


def test_preview_firmware_uses_cache_on_second_call():
    """P3.H: 2 successive calls on the same firmware preview trigger
    only 1 Z9 fetch (cache TTL)."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    z9 = _make_z9_mock(preview_returns=b"\xff\xd8\xff\xe0firmware")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.get(f"/api/jobs/{fake_uuid}/preview")
        r2 = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.content == r2.content
    # The Z9 is called only once — the 2nd hit comes from the cache
    assert z9.jobs.get_job_preview.call_count == 1


def test_preview_firmware_refresh_query_forces_refetch():
    """?refresh=1 invalidates the cache → 2 distinct Z9 fetches."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    z9 = _make_z9_mock(preview_returns=b"\xff\xd8\xff\xe0v1")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        client.get(f"/api/jobs/{fake_uuid}/preview")  # MISS, fetches v1
        z9.jobs.get_job_preview.return_value = b"\xff\xd8\xff\xe0v2"
        r2 = client.get(f"/api/jobs/{fake_uuid}/preview?refresh=1")
    assert r2.status_code == 200
    assert r2.content == b"\xff\xd8\xff\xe0v2"
    assert z9.jobs.get_job_preview.call_count == 2


def test_preview_firmware_error_not_cached():
    """If the firmware replies with an error, the cache stays empty — the
    next call retries (case where it was a transient error)."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    z9 = _make_z9_mock(preview_raises=Z9RESTError(500, "/p", "deleted"))
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.get(f"/api/jobs/{fake_uuid}/preview")
        r2 = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r1.status_code == 410 and r2.status_code == 410
    # 2 Z9 calls (no caching of errors)
    assert z9.jobs.get_job_preview.call_count == 2


def test_preview_local_does_not_consult_firmware_cache():
    """Local hit → does not touch the firmware cache (is not even
    called)."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    job_mapping.register(fake_uuid, "JA5-LOCAL")
    _make_local_thumb("JA5-LOCAL", b"\xff\xd8\xff\xe0LOCAL")

    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.get(f"/api/jobs/{fake_uuid}/preview")
        r2 = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r1.content == r2.content == b"\xff\xd8\xff\xe0LOCAL"
    z9.jobs.get_job_preview.assert_not_called()
    # The firmware cache stays empty
    assert preview_cache.get(fake_uuid) is None


def test_remove_job_invalidates_preview_cache():
    """Remove a job → its preview cache entry disappears (consistency)."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    z9 = _make_z9_mock(preview_returns=b"\xff\xd8\xff\xe0cached")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        # 1. Fetch preview → cache HIT
        client.get(f"/api/jobs/{fake_uuid}/preview")
        assert preview_cache.get(fake_uuid) is not None
        # 2. Remove → cache invalidated
        client.post(f"/api/jobs/{fake_uuid}/remove")
        assert preview_cache.get(fake_uuid) is None


def test_preview_serves_local_when_mapping_exists():
    """If we have a firmware_uuid→jobacct5 mapping and the local thumb
    exists on disk, we serve it (without touching the firmware)."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    job_mapping.register(fake_uuid, "JA5-LOCAL")
    _make_local_thumb("JA5-LOCAL", b"\xff\xd8\xff\xe0LOCAL_BYTES")

    z9 = _make_z9_mock()  # the mock firmware will not be called
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8\xff\xe0LOCAL_BYTES"
    z9.jobs.get_job_preview.assert_not_called()


def test_preview_falls_back_to_firmware_when_no_mapping():
    """No mapping → firmware fallback (jobs submitted by another application)."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    z9 = _make_z9_mock(preview_returns=b"\x89PNG\r\nfirmware-bytes")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    z9.jobs.get_job_preview.assert_called_once_with(fake_uuid, page=1)


def test_preview_falls_back_when_local_file_missing():
    """If the mapping exists but the thumb file was deleted,
    automatic firmware fallback."""
    fake_uuid = "12345678-1234-4234-8234-123456789012"
    job_mapping.register(fake_uuid, "JA5-PURGED")
    # We do NOT create the thumb file (CLI cleanup went through here)
    z9 = _make_z9_mock(preview_returns=b"\xff\xd8\xff\xe0FW_FALLBACK")
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{fake_uuid}/preview")
    assert r.status_code == 200
    assert r.content == b"\xff\xd8\xff\xe0FW_FALLBACK"


def test_snapshot_enriches_jobs_with_preview_source():
    """The preview_source field is added to each job in the snapshot."""
    sample = {
        "queue_status": "Paused", "number_of_jobs": 3,
        "modification_number": 1, "timestamp": "",
        "jobs": [
            {"uuid": "FW-LOCAL",   "name": "freeglaz.tif", "preview_uri": "/some/uri"},
            {"uuid": "FW-EXTERNAL","name": "hp-click.tif","preview_uri": "/other/uri"},
            {"uuid": "FW-NONE",    "name": "no-preview.tif", "preview_uri": ""},
        ],
    }
    # Job 1: mapping + local thumb → "local"
    job_mapping.register("FW-LOCAL", "JA5-A")
    _make_local_thumb("JA5-A")
    # Job 2: no mapping, but firmware preview_uri → "firmware"
    # Job 3: neither mapping nor preview_uri → None

    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=1.0)
    sub._snapshot = sample
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.get("/api/jobs")
    assert r.status_code == 200
    jobs = r.json()["jobs"]
    assert jobs[0]["preview_source"] == "local"
    assert jobs[1]["preview_source"] == "firmware"
    assert jobs[2]["preview_source"] is None


# ─── P3: reprint endpoint + can_reprint flag ─────────────────────────


_FAKE_UUID_ORIGINAL = "11111111-1111-4111-8111-111111111111"
_FAKE_UUID_NEW      = "22222222-2222-4222-8222-222222222222"


def _subscriber_with_job(status: str, uuid: str = _FAKE_UUID_ORIGINAL) -> Z9JobsSubscriber:
    """Build a subscriber with 1 job at the given status, ready to be
    set on ``app.state.z9_jobs_subscriber``."""
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=1.0)
    sub._snapshot = {
        "queue_status": "Paused", "number_of_jobs": 1,
        "modification_number": 50, "timestamp": "",
        "jobs": [{"uuid": uuid, "status": status, "preview_uri": ""}],
    }
    return sub


def test_reprint_returns_200_with_new_uuid():
    z9 = _make_z9_mock(
        get_snapshot_returns={
            "queue_status": "Paused", "number_of_jobs": 1,
            "modification_number": 50, "timestamp": "",
            "jobs": [{"uuid": _FAKE_UUID_ORIGINAL, "status": "Completed"}],
        },
        reprint_returns={
            "queue_uuid": "Q", "original_uuid": _FAKE_UUID_ORIGINAL,
            "status": "SuccessfullySubmitted",
        },
        find_new_returns=_FAKE_UUID_NEW,
    )
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 200
    body = r.json()
    assert body["original_uuid"] == _FAKE_UUID_ORIGINAL
    assert body["new_uuid"] == _FAKE_UUID_NEW


def test_reprint_returns_202_when_new_job_not_visible_yet():
    z9 = _make_z9_mock(
        get_snapshot_returns={
            "queue_status": "Paused", "number_of_jobs": 1,
            "modification_number": 50, "timestamp": "",
            "jobs": [{"uuid": _FAKE_UUID_ORIGINAL, "status": "Completed"}],
        },
        find_new_returns=None,  # timeout
    )
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    # FastAPI status_code=200 by decorator, but we returned a body
    # with new_uuid:null + warning → the UI handles this specifically.
    assert r.status_code == 200
    body = r.json()
    assert body["new_uuid"] is None
    assert "warning" in body


def test_reprint_returns_422_on_deleted_job():
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Deleted")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 422
    assert "Deleted" in r.text


def test_reprint_returns_422_on_non_completed_status():
    """A WaitingToPrint / Paused / Processing job cannot be reprinted
    (it is still in the queue, the user can just wait or cancel)."""
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("WaitingToPrint")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 422


def test_reprint_returns_502_on_z9_error():
    from lib.z9_client.exceptions import Z9ConnectionError
    z9 = _make_z9_mock(
        reprint_raises=Z9ConnectionError("Z9 down during reprint"),
    )
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 502


def test_reprint_hardlinks_thumbnail_via_mapping_inheritance():
    """If the original has a mapping → thumb, the new one inherits via
    job_mapping.register (both point to the same file physically present
    on disk)."""
    job_mapping.register(_FAKE_UUID_ORIGINAL, "JA5-ORIGINAL")
    _make_local_thumb("JA5-ORIGINAL")

    z9 = _make_z9_mock(find_new_returns=_FAKE_UUID_NEW)
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 200
    # The new one's mapping points to the same thumb as the original
    assert job_mapping.lookup(_FAKE_UUID_NEW) == "JA5-ORIGINAL"
    # The new one's thumb is servable (same physical file)
    with TestClient(app) as client:
        r = client.get(f"/api/jobs/{_FAKE_UUID_NEW}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_reprint_unexpected_exception_returns_structured_json_500():
    """Live bug regression 24/05: if find_new_reprint_job (or any
    other code after the firmware call) raises a non-Z9Error exception,
    we must return a structured JSON 500 (not FastAPI's default
    text/plain). The frontend must be able to display a contextual
    message and the log file must contain the traceback."""
    z9 = _make_z9_mock(
        get_snapshot_returns={
            "queue_status": "Paused", "number_of_jobs": 1,
            "modification_number": 50, "timestamp": "",
            "jobs": [{"uuid": _FAKE_UUID_ORIGINAL, "status": "Completed"}],
        },
    )
    # The firmware accepts the reprint without error...
    z9.jobs.reprint_job.return_value = {
        "queue_uuid": "Q", "original_uuid": _FAKE_UUID_ORIGINAL,
        "status": "SuccessfullySubmitted",
    }
    # ...then find_new_reprint_job crashes with an unexpected exception
    # (KeyError, IOError, etc. — here we simulate a KeyError typical of a
    # malformed snapshot).
    z9.jobs.find_new_reprint_job.side_effect = KeyError("modification_number")

    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")

    # 500 but with structured JSON, not text/plain
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json"), (
        f"Content-Type doit être JSON, pas {r.headers['content-type']!r}"
    )
    body = r.json()
    assert body["error"] == "reprint_failed"
    assert body["stage"] == "find_new_uuid"
    # firmware_succeeded lets the UI say "Reprint sent to the Z9 but
    # identification of the new job failed" rather than a generic
    # "Error" message.
    assert body["firmware_succeeded"] is True
    assert "KeyError" in body["detail"]
    assert body["original_uuid"] == _FAKE_UUID_ORIGINAL


def test_reprint_crash_in_mapping_register_returns_structured_500():
    """Variant: crash injected into job_mapping.register (thumb_inheritance
    step). The stage must reflect this precise step."""
    z9 = _make_z9_mock(
        get_snapshot_returns={
            "queue_status": "Paused", "number_of_jobs": 1,
            "modification_number": 50, "timestamp": "",
            "jobs": [{"uuid": _FAKE_UUID_ORIGINAL, "status": "Completed"}],
        },
        find_new_returns=_FAKE_UUID_NEW,
    )
    # The original has a mapping → we enter thumb_inheritance
    job_mapping.register(_FAKE_UUID_ORIGINAL, "JA5-ORIGINAL")

    # We inject a crash into job_mapping.register to simulate, for
    # example, an OSError on disk write.
    import unittest.mock as _mock
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with _mock.patch(
        "webapp.backend.services.job_mapping.register",
        side_effect=OSError("disk full"),
    ):
        with TestClient(app) as client:
            app.state.z9_jobs_subscriber = sub
            r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")

    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    body = r.json()
    assert body["stage"] == "thumb_inheritance"
    assert body["firmware_succeeded"] is True
    assert body["new_uuid"] == _FAKE_UUID_NEW
    assert "OSError" in body["detail"]


def test_reprint_no_thumb_inheritance_when_original_has_none():
    """If the original has no mapping, neither does the new one. The thumb
    did not exist for the original, so it is normal it does not exist for
    the reprint."""
    z9 = _make_z9_mock(find_new_returns=_FAKE_UUID_NEW)
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Completed")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 200
    assert job_mapping.lookup(_FAKE_UUID_NEW) is None


def test_snapshot_includes_can_reprint_flag():
    """The enriched snapshot exposes ``can_reprint`` per job — strict
    whitelist on firmware statuses (post-patch 25/05/2026)."""
    sample = {
        "queue_status": "Paused", "number_of_jobs": 9,
        "modification_number": 1, "timestamp": "",
        "jobs": [
            # ─ True: statuses where the resource is still present firmware-side
            {"uuid": "C", "status": "Completed"},
            {"uuid": "X", "status": "Cancelled"},
            # ─ False: everything else
            {"uuid": "D", "status": "Deleted"},          # purged firmware
            {"uuid": "W", "status": "WaitingToPrint"},   # still waiting
            {"uuid": "P", "status": "Paused"},           # still waiting (queue paused)
            {"uuid": "A", "status": "Active"},           # in progress
            {"uuid": "PR", "status": "Processing"},      # in progress
            {"uuid": "H", "status": "Held"},             # waiting (hold reason)
            {"uuid": "?",  "status": ""},                # empty status
        ],
    }
    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=1.0)
    sub._snapshot = sample
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.get("/api/jobs")
    jobs = r.json()["jobs"]
    flags = {j["uuid"]: j["can_reprint"] for j in jobs}
    assert flags == {
        "C": True,  "X": True,
        "D": False, "W": False, "P": False, "A": False,
        "PR": False, "H": False, "?": False,
    }


def test_reprint_returns_422_on_pending_job():
    """Post-validation regression 25/05/2026: a ``Pending`` job must
    NEVER be reprinted, even if the Z9 firmware observed it once with
    that status. Backend-side, explicit 422 refusal before any firmware
    call (no side effect on the source job's status)."""
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Pending")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 422
    # The firmware was NOT called (no side effect)
    z9.jobs.reprint_job.assert_not_called()


def test_reprint_returns_422_on_paused_job():
    """Paused (queue paused, job not yet started) → 422 refusal."""
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("Paused")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 422
    z9.jobs.reprint_job.assert_not_called()


def test_reprint_returns_422_on_unknown_status_defensive():
    """Unknown firmware status (future HP addition) → defensive 422
    refusal. The warning is logged for traceability."""
    z9 = _make_z9_mock()
    app.dependency_overrides[get_z9] = lambda: z9
    sub = _subscriber_with_job("SomeFutureStatus")
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.post(f"/api/jobs/{_FAKE_UUID_ORIGINAL}/reprint")
    assert r.status_code == 422
    z9.jobs.reprint_job.assert_not_called()


def test_snapshot_preview_source_local_requires_file_on_disk():
    """Mapping present but file deleted → "firmware" fallback if
    preview_uri OK, otherwise None. Avoids promising "local" to the UI
    when the file will not be servable."""
    sample = {
        "queue_status": "Paused", "number_of_jobs": 1,
        "modification_number": 1, "timestamp": "",
        "jobs": [{"uuid": "FW-1", "preview_uri": "/uri"}],
    }
    job_mapping.register("FW-1", "JA5-PURGED")
    # No thumb file on disk

    z9 = _make_z9_mock()
    sub = Z9JobsSubscriber(z9, poll_interval=1.0)
    sub._snapshot = sample
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        app.state.z9_jobs_subscriber = sub
        r = client.get("/api/jobs")
    assert r.json()["jobs"][0]["preview_source"] == "firmware"
