"""Tests P5.A — ICC profiling routes (Print & Scan, Scan only, Print only).

Mirror of the P4 pattern (test_papers_calibration.py) — FastAPI TestClient +
Z9Client mock (paper.profile blocks in a daemon thread, we emulate it).

Critical guardrails covered:
- The 3 workflows (PRINT_AND_SCAN, SCAN_ONLY, PRINT_ONLY) are exposed
  and accepted by the Pydantic contract. SCAN_ONLY = custom freeglaz
  chart pipeline. PRINT_ONLY = native firmware
  fallback (firmware profiling path).
- profile_name validation (XML-safe, length)
- 400 if gloss_enhancer=True on unsupported paper
- 409 if paper not loaded (MEDIAID mismatch)
- 409 if a profiling is already running (singleton)
- parsable SSE events + coherent transitions
- /profile/current 204-like (job=null) if no job
"""
import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import app
from webapp.backend.routes.status import get_z9
from webapp.backend.services import profile_jobs

MID = "9E489F02AE027F9DD93191D872728C1D"
OTHER_MID = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture(autouse=True)
def _reset_state():
    profile_jobs.reset_for_tests()
    yield
    profile_jobs.reset_for_tests()
    app.dependency_overrides.clear()


def _make_profile_mock(steps=None, raise_on_done=False, sleep_per_step=0.0):
    """Build a paper.profile mock that calls on_progress N times
    then returns a success/error result.

    The mock also captures the received kwargs to verify the
    bool->string mapping on the backend side (gloss_enhancer, max_detail).
    """
    if steps is None:
        steps = [
            {"percent": 10, "process": "PRINTING",  "elapsed":  60.0, "operation_id": "op1"},
            {"percent": 50, "process": "DRYING",    "elapsed": 240.0, "operation_id": "op1"},
            {"percent": 80, "process": "SCANNING",  "elapsed": 480.0, "operation_id": "op1"},
        ]

    captured = {}

    def _fake_profile(ref, on_progress=None, **kwargs):
        captured["ref"] = ref
        captured["kwargs"] = kwargs
        for step in steps:
            if on_progress:
                on_progress(step)
            if sleep_per_step:
                time.sleep(sleep_per_step)
        if raise_on_done:
            raise RuntimeError("profiling firmware failed: SCANNING phase")
        return {
            "operation_id": "op1",
            "elapsed": 540.0,
            "final_state": "DONE",
            "profile_name": "HP Z9 Test Profile",
            "profile_uuid": "uuid-1234",
            "profile_icc_name": "freeglaz_Test_GEON",
            "profile_date": "2026-05-25T11:00:00Z",
            "gloss_enhancer": "FULLPAGE",
            "color_space": "RGB",
            "paper_name": "Test Paper",
            "paper_id": ref,
        }

    return _fake_profile, captured


def _make_z9(profile_side_effect, *, ge_supported=True, loaded_mediaid=MID):
    """Build a minimal Z9Client mock for the profile route tests.

    - ``paper.profile`` : side_effect from the test
    - ``paper.details(mediaid)`` : returns capabilities GE supported/not
    - ``device.status()`` : returns loaded_paper_id for the MEDIAID check
    """
    paper = MagicMock()
    paper.profile.side_effect = profile_side_effect
    paper.details.return_value = {
        "capabilities": {
            "GlossEnhancerSupported": "1" if ge_supported else "0",
            "MaxDetailSupported": "1",
        },
    }
    device = MagicMock()
    device.status.return_value = {"loaded_paper_id": loaded_mediaid}
    return SimpleNamespace(paper=paper, device=device, host="192.168.1.50")


def _payload(**overrides) -> dict:
    """Default ProfileRequest payload, overridable."""
    base = {
        "workflow": "PRINT_AND_SCAN",
        "profile_name": "freeglaz_Test_GEON",
        "gloss_enhancer": True,
        "quality": "BEST",
        "max_detail": False,
        "color_space": "RGB",
    }
    base.update(overrides)
    return base


# ─── POST /profile — success ───────────────────────────────────────────


def test_start_profile_returns_job_id_and_eta():
    profile_fn, _ = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/profile", json=_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert "job_id" in body
    assert body["estimated_duration_s"] == 600  # PRINT_AND_SCAN ETA


def test_scan_only_accepted():
    """SCAN_ONLY is now the core of the freeglaz pipeline (custom
    chart + scan), validated live on 30/05/2026 (resident-tag,
    delta-E 0.60 vs HP). Pydantic contract ``Literal[..., "SCAN_ONLY"]``
    accepts the payload — the returned ETA is 420 s (~7 min).

    Non-restriction guarantee: this test protects against an
    accidental re-restriction of the API to PRINT_AND_SCAN only.
    """
    profile_fn, captured = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            f"/api/papers/{MID}/profile",
            json=_payload(workflow="SCAN_ONLY"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "job_id" in body
        assert body["estimated_duration_s"] == 420  # SCAN_ONLY ETA
        # Wait for the daemon thread to start then finish, to
        # avoid any ghost job across tests (reset_for_tests would do it
        # anyway, but we stay explicit).
        for _ in range(50):
            if captured:
                break
            time.sleep(0.02)
        assert captured.get("ref") == MID


def test_start_profile_maps_gloss_enhancer_bool_to_fullpage():
    """Guardrail: ``gloss_enhancer=True`` on the webapp side must
    arrive as ``"FULLPAGE"`` in the lib (not ``"ON"``, which would be
    refused by the firmware)."""
    profile_fn, captured = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/profile", json=_payload(gloss_enhancer=True))
    assert r.status_code == 200
    # Wait for the daemon thread to start and capture the kwargs
    for _ in range(50):
        if captured:
            break
        time.sleep(0.02)
    assert captured.get("kwargs", {}).get("gloss_enhancer") == "FULLPAGE"


def test_start_profile_max_detail_bool_mapped_to_lib_string():
    profile_fn, captured = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            f"/api/papers/{MID}/profile", json=_payload(max_detail=True),
        )
    assert r.status_code == 200
    for _ in range(50):
        if captured:
            break
        time.sleep(0.02)
    assert captured.get("kwargs", {}).get("max_detail") == "ON"


# ─── POST /profile — guardrails ──────────────────────────────────────


def test_workflow_print_only_allowed():
    """PRINT_ONLY is intentionally exposed as a fallback:
    it allows driving the chart printing via the native firmware
    workflowKind, if the freeglaz pipeline is unavailable.
    Rarely used in practice, but
    essential as a resilience guarantee.

    Pydantic contract ``Literal[..., "PRINT_ONLY"]`` accepts the
    payload — the returned ETA is 120 s (~2 min, printing only).

    Non-restriction guarantee: this test protects against the
    accidental removal of this fallback.
    """
    profile_fn, captured = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            f"/api/papers/{MID}/profile", json=_payload(workflow="PRINT_ONLY"),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "job_id" in body
        assert body["estimated_duration_s"] == 120  # PRINT_ONLY ETA
        for _ in range(50):
            if captured:
                break
            time.sleep(0.02)
        assert captured.get("ref") == MID


def test_ge_true_on_unsupported_paper_returns_400():
    """Critical GE guardrail: Hahnemühle Photo Rag 308g does not support
    the Gloss Enhancer. A forged request ``gloss_enhancer=True``
    must be rejected by the backend (the frontend disables the
    checkbox, but we also harden the API side)."""
    profile_fn, _ = _make_profile_mock()
    z9 = _make_z9(profile_fn, ge_supported=False)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/profile", json=_payload(gloss_enhancer=True))
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["code"] == "ge_not_supported"


def test_ge_false_on_unsupported_paper_is_allowed():
    """GE=False is valid on any paper — no useless check."""
    profile_fn, _ = _make_profile_mock()
    z9 = _make_z9(profile_fn, ge_supported=False)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/profile", json=_payload(gloss_enhancer=False))
    assert r.status_code == 200


def test_mediaid_not_loaded_returns_409():
    """If the requested paper is not physically loaded, we reject
    with 409 and a clear message (consistent with the firmware which
    would refuse otherwise)."""
    profile_fn, _ = _make_profile_mock()
    z9 = _make_z9(profile_fn, loaded_mediaid=OTHER_MID)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(f"/api/papers/{MID}/profile", json=_payload())
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "paper_not_loaded"
    assert detail["loaded_mediaid"] == OTHER_MID


def test_profile_name_with_xml_chars_returns_400():
    """profile_name validation: no angle brackets, ampersand,
    unescaped quotes (consistent with SOAP newProfile)."""
    profile_fn, _ = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        for bad in ["bad<name", "with&amp", 'has"quote', "has'quote", "with>chev"]:
            r = client.post(
                f"/api/papers/{MID}/profile", json=_payload(profile_name=bad),
            )
            assert r.status_code == 400, f"Expected 400 for {bad!r}, got {r.status_code}"


def test_profile_name_too_long_returns_422():
    """Pydantic ``max_length=64`` must reject names that are too long."""
    profile_fn, _ = _make_profile_mock()
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r = client.post(
            f"/api/papers/{MID}/profile",
            json=_payload(profile_name="x" * 65),
        )
    assert r.status_code == 422


def test_two_profiles_simultaneously_returns_409():
    """Singleton guaranteed: only one profiling at a time. We use the
    same MID on both sides to short-circuit the
    ``paper_not_loaded`` check (which fires before the singleton) — it is
    indeed the ``profile_busy`` code we want to test here."""
    # 1st profiling: we block the mock so it does not finish
    # quickly, giving time to fire the 2nd POST.
    profile_fn_slow, _ = _make_profile_mock(sleep_per_step=0.5)
    z9 = _make_z9(profile_fn_slow)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.post(f"/api/papers/{MID}/profile", json=_payload())
        assert r1.status_code == 200
        # 2nd immediate attempt (same paper, already loaded -> only the
        # singleton blocks).
        r2 = client.post(f"/api/papers/{MID}/profile", json=_payload())
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    assert detail["code"] == "profile_busy"


def test_two_profiles_different_mids_other_unloaded_returns_409_loaded():
    """Neighboring case: the 2nd attempt on a non-loaded paper is also
    rejected but with a different code (``paper_not_loaded``). Keep
    this distinction frontend-side to differentiate the message."""
    profile_fn_slow, _ = _make_profile_mock(sleep_per_step=0.5)
    z9 = _make_z9(profile_fn_slow)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.post(f"/api/papers/{MID}/profile", json=_payload())
        assert r1.status_code == 200
        r2 = client.post(f"/api/papers/{OTHER_MID}/profile", json=_payload())
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "paper_not_loaded"


# ─── GET /profile/current ─────────────────────────────────────────────


def test_current_profile_returns_null_when_no_job():
    with TestClient(app) as client:
        r = client.get("/api/papers/profile/current")
    assert r.status_code == 200
    assert r.json() == {"job": None}


def test_current_profile_returns_active_job():
    """The global badge polls this endpoint — it must see the job as soon
    as it is started (not wait for the 1st progress)."""
    profile_fn, _ = _make_profile_mock(sleep_per_step=0.3)
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.post(f"/api/papers/{MID}/profile", json=_payload())
        assert r1.status_code == 200
        r2 = client.get("/api/papers/profile/current")
    assert r2.status_code == 200
    job = r2.json()["job"]
    assert job is not None
    assert job["mediaid"] == MID
    assert job["workflow"] == "PRINT_AND_SCAN"
    assert job["state"] in ("starting", "running", "done")


# ─── GET /profile/events SSE ──────────────────────────────────────────


def _parse_sse(text: str) -> list[dict]:
    """Parse an SSE blob into a list of events {event, data}."""
    events = []
    current = {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if not line:
            if current:
                events.append(current)
                current = {}
            continue
        if line.startswith("event:"):
            current["event"] = line[len("event:"):].strip()
        elif line.startswith("data:"):
            payload = line[len("data:"):].strip()
            try:
                current["data"] = json.loads(payload) if payload else None
            except json.JSONDecodeError:
                current["data"] = payload
    if current:
        events.append(current)
    return events


def test_sse_events_for_unknown_mediaid_empty_snapshot():
    """If no active profiling on this paper, the SSE returns a null
    snapshot + closes."""
    with TestClient(app) as client:
        r = client.get(f"/api/papers/{MID}/profile/events")
    assert r.status_code == 200
    events = _parse_sse(r.text)
    assert len(events) >= 1
    assert events[0]["event"] == "snapshot"
    assert events[0]["data"] == {"job": None}


def test_sse_events_streams_progress_and_finished():
    """During a profiling: initial snapshot + N progress events +
    profile_finished. The SSE must terminate cleanly."""
    profile_fn, _ = _make_profile_mock(
        steps=[
            {"percent": 10, "process": "PRINTING", "elapsed":  60.0, "operation_id": "op1"},
            {"percent": 90, "process": "SCANNING", "elapsed": 480.0, "operation_id": "op1"},
        ],
        sleep_per_step=0.05,
    )
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.post(f"/api/papers/{MID}/profile", json=_payload())
        assert r1.status_code == 200
        # Small delay to let the daemon thread generate a few events
        time.sleep(0.3)
        r2 = client.get(f"/api/papers/{MID}/profile/events")
    events = _parse_sse(r2.text)
    types = [e["event"] for e in events]
    assert "snapshot" in types
    # The result depends on timing: depending on whether the thread has
    # already finished or not before the SSE opens, we may have only the
    # final snapshot (with state="done") OR snapshot + progress + finished.
    # Both scenarios are valid; we just check that we terminated
    # cleanly.
    has_progress_or_done = (
        "progress" in types
        or "profile_finished" in types
        or any(
            e["event"] == "snapshot" and e["data"]["job"] and e["data"]["job"]["state"] == "done"
            for e in events
        )
    )
    assert has_progress_or_done, f"Got events: {types}"


def test_sse_events_on_error_emits_finished_with_error_outcome():
    profile_fn, _ = _make_profile_mock(raise_on_done=True, sleep_per_step=0.02)
    z9 = _make_z9(profile_fn)
    app.dependency_overrides[get_z9] = lambda: z9
    with TestClient(app) as client:
        r1 = client.post(f"/api/papers/{MID}/profile", json=_payload())
        assert r1.status_code == 200
        time.sleep(0.5)  # let the thread crash
        r2 = client.get("/api/papers/profile/current")
    job = r2.json()["job"]
    assert job["state"] == "error"
    assert "failed" in job["error"].lower()
