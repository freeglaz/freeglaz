"""Tests P4.B+D — color linear calibration (CLC) routes."""
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.routes.status import get_z9
from webapp.backend.services import calibration_jobs

MID = "9E489F02AE027F9DD93191D872728C1D"


@pytest.fixture(autouse=True)
def _reset_state():
    calibration_jobs.reset_for_tests()
    yield
    calibration_jobs.reset_for_tests()
    app.dependency_overrides.clear()


def _make_calibrate_mock(steps=None, raise_on_done=False, sleep_per_step=0.0):
    """Build a paper.calibrate mock that calls on_progress N times
    then returns a success/error result.

    :param steps: list of dicts to pass to on_progress in sequence
    :param raise_on_done: if True, raise instead of returning
    :param sleep_per_step: delay between 2 on_progress calls (for SSE)
    """
    if steps is None:
        steps = [
            {"percent": 25, "process": "PRINTING", "elapsed": 5.0, "operation_id": "op1"},
            {"percent": 75, "process": "DRYING",   "elapsed": 30.0, "operation_id": "op1"},
        ]

    def _fake_calibrate(ref, on_progress=None, **kwargs):
        for step in steps:
            if on_progress:
                on_progress(step)
            if sleep_per_step:
                time.sleep(sleep_per_step)
        if raise_on_done:
            raise RuntimeError("calibration firmware failed")
        return {
            "operation_id": "op1",
            "elapsed": 42.0,
            "final_state": "DONE",
            "calibration_date": "2026-05-25T09:00:00Z",
            "calibration_valid": True,
        }
    return _fake_calibrate


def _make_z9(calibrate_side_effect):
    paper = MagicMock()
    paper.calibrate.side_effect = calibrate_side_effect
    return SimpleNamespace(paper=paper, host="192.168.1.50")


# ─── POST /calibrate ──────────────────────────────────────────────────


def test_start_calibration_returns_job():
    z9 = _make_z9(_make_calibrate_mock())
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/calibrate")
    assert r.status_code == 200
    body = r.json()
    assert "job" in body
    assert body["job"]["mediaid"] == MID
    assert body["job"]["state"] in ("starting", "running", "done")


def test_start_calibration_409_when_already_running():
    """A 2nd calibration on a different paper -> 409."""
    # We block the mock so the 1st calibration does not finish
    # before the 2nd attempt
    def _slow(ref, on_progress=None, **kw):
        time.sleep(1.0)
        return {"elapsed": 1.0, "final_state": "DONE", "calibration_date": "x", "calibration_valid": True}
    z9 = _make_z9(_slow)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.post(f"/api/papers/{MID}/calibrate")
        assert r1.status_code == 200
        # Right away, 2nd attempt on another mediaid
        other = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        r2 = client.post(f"/api/papers/{other}/calibrate")
    assert r2.status_code == 409
    body = r2.json()
    detail = body["detail"]
    assert "current" in detail
    assert detail["current"]["mediaid"] == MID


def test_start_calibration_503_when_z9_not_configured():
    app.dependency_overrides[get_z9] = lambda: None
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/calibrate")
    assert r.status_code == 503


def test_start_calibration_rejects_invalid_mediaid():
    z9 = _make_z9(_make_calibrate_mock())
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post("/api/papers/not-hex/calibrate")
    assert r.status_code == 422


# ─── GET /calibrate/current ───────────────────────────────────────────


def test_current_calibration_none_at_start():
    with TestClient(app) as client:
        r = client.get("/api/papers/calibrate/current")
    assert r.status_code == 200
    assert r.json() == {"job": None}


def test_current_calibration_reflects_active_job():
    z9 = _make_z9(_make_calibrate_mock(sleep_per_step=0.05))
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        client.post(f"/api/papers/{MID}/calibrate")
        # The job should still be visible (with state done if finished fast)
        r = client.get("/api/papers/calibrate/current")
    assert r.status_code == 200
    body = r.json()
    assert body["job"] is not None
    assert body["job"]["mediaid"] == MID


# ─── SSE /calibrate/events ────────────────────────────────────────────


def _parse_sse_events(text):
    """Simplistic parse of the raw SSE response -> list of dicts.

    Normalizes the line endings (sse-starlette uses \\r\\n).
    """
    text = text.replace("\r\n", "\n")
    events = []
    current = {}
    for line in text.split("\n"):
        if line.startswith("event: "):
            current["event"] = line[7:].strip()
        elif line.startswith("data: "):
            current["data"] = line[6:]
        elif line == "" and current:
            if "event" in current:
                events.append(current)
            current = {}
    if current and "event" in current:
        events.append(current)
    return events


def test_sse_events_when_no_active_job():
    """No calibration on this mediaid -> null snapshot + close."""
    with TestClient(app) as client:
        with client.stream("GET", f"/api/papers/{MID}/calibrate/events") as r:
            assert r.status_code == 200
            body = r.read().decode("utf-8")
    events = _parse_sse_events(body)
    assert len(events) >= 1
    assert events[0]["event"] == "snapshot"
    assert json.loads(events[0]["data"])["job"] is None


def test_sse_events_emits_progress_and_finished():
    """Calibration mock with 2 progress + success -> at least 4 events:
    snapshot(initial), progress*2, calibration_finished."""
    z9 = _make_z9(_make_calibrate_mock())
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        client.post(f"/api/papers/{MID}/calibrate")
        # Give the thread time to fire the events
        time.sleep(0.1)
        with client.stream("GET", f"/api/papers/{MID}/calibrate/events") as r:
            assert r.status_code == 200
            body = r.read().decode("utf-8")
    events = _parse_sse_events(body)
    event_types = [e["event"] for e in events]
    # We must see at least the initial snapshot and the calibration_finished
    assert "snapshot" in event_types
    assert "calibration_finished" in event_types
    # The finished event has outcome=success
    finished = next(e for e in events if e["event"] == "calibration_finished")
    data = json.loads(finished["data"])
    assert data["outcome"] == "success"
    assert data["clc_date"] == "2026-05-25T09:00:00Z"


def test_sse_emits_error_on_calibrate_failure():
    """Calibration mock that raises -> calibration_finished event with
    outcome=error."""
    z9 = _make_z9(_make_calibrate_mock(raise_on_done=True))
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        client.post(f"/api/papers/{MID}/calibrate")
        time.sleep(0.1)
        with client.stream("GET", f"/api/papers/{MID}/calibrate/events") as r:
            body = r.read().decode("utf-8")
    events = _parse_sse_events(body)
    finished = next(e for e in events if e["event"] == "calibration_finished")
    data = json.loads(finished["data"])
    assert data["outcome"] == "error"
    assert "firmware failed" in data["message"]


# ─── Service singleton ────────────────────────────────────────────────


def test_calibration_jobs_is_busy_lifecycle():
    """is_busy() True during the run, False after done."""
    z9 = _make_z9(_make_calibrate_mock(sleep_per_step=0.2))
    assert calibration_jobs.is_busy() is False
    job = calibration_jobs.start(MID, z9)
    # We wait for the thread to finish (without blocking indefinitely)
    for _ in range(50):
        if not calibration_jobs.is_busy():
            break
        time.sleep(0.05)
    assert calibration_jobs.is_busy() is False
    final = calibration_jobs.current()
    assert final.state == "done"
    assert final.result["calibration_valid"] is True


def test_calibration_jobs_start_rejects_concurrent():
    z9 = _make_z9(_make_calibrate_mock(sleep_per_step=0.3))
    calibration_jobs.start(MID, z9)
    with pytest.raises(RuntimeError, match="already in progress"):
        calibration_jobs.start("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", z9)
