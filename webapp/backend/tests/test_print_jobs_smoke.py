"""Smoke tests for jobs + SSE (mock worker, accelerated timing) + mocked run_real_job."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms
from fastapi.testclient import TestClient

from lib.z9_client.exceptions import Z9Error, Z9PreflightError
from webapp.backend.main import app
from webapp.backend.models import JobStage, PrintParams
from webapp.backend.routes.status import get_z9
from webapp.backend.services import file_storage
from webapp.backend.services.print_jobs import JobStore
from webapp.backend.services.print_worker import MockTiming, run_mock_job, run_real_job


FAST = MockTiming(0.01, 0.01, 0.01, 0.01, 0.01)


# ─── Helpers ──────────────────────────────────────────────────────────


def _srgb_icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def _make_tiff(path: Path) -> None:
    Image.new("RGB", (64, 64), color=(180, 90, 30)).save(
        path, format="TIFF", dpi=(300, 300), icc_profile=_srgb_icc(),
    )


def _make_tiff_16bit(path: Path, w_mm: float = 100.0, h_mm: float = 150.0,
                     dpi: int = 300) -> None:
    """16-bit sRGB TIFF compatible with the lib pipeline (build_pdfx4 requires uint16)."""
    px_w = round(w_mm / 25.4 * dpi)
    px_h = round(h_mm / 25.4 * dpi)
    arr = np.full((px_h, px_w, 3), 30000, dtype=np.uint16)
    icc = _srgb_icc()
    tifffile.imwrite(
        path, arr,
        resolution=(dpi, dpi),
        resolutionunit="INCH",
        extratags=[(34675, "B", len(icc), icc, False)],
    )


def _fake_z9_for_real(
    *,
    loaded: Optional[dict] = None,
    send_stages: Optional[list[str]] = None,
    send_raises: Optional[Exception] = None,
    resident_raises: Optional[Exception] = None,
):
    """Fake Z9Client for run_real_job. If send_raises, the exception is raised
    AFTER emitting the stages in send_stages (or immediately if the list is empty)."""
    dashboard = {
        "identification": {"ModelName": "Z9", "SerialNumber": "S", "FwReleaseName": "F"},
        "ink_levels": {}, "ink_warnings": [], "global_status": "Ready",
    }
    if loaded:
        dashboard.update(loaded)
    else:
        dashboard.update({"loaded_paper_id": None, "loaded_paper_name": None,
                          "loaded_paper_source": None, "loaded_paper_source_label": None,
                          "loaded_paper_width_mm": None, "loaded_paper_length_mm": None})

    class _Device:
        def status(self_inner): return dashboard
        def device_status(self_inner):
            return {"ActivitiesOverview": {"MostRelevantActivity": {"Name": "NoActivity"}}}

    class _Paper:
        def get(self_inner, _):
            return {"id": "P", "name": "FakePaper",
                    "category_id": "PHOTO", "is_factory": False}
        def capabilities(self_inner, _): return None

    class _Print:
        def send(self_inner, job, on_progress=None, **_):
            for st in (send_stages or []):
                if on_progress:
                    on_progress(st, {"output": Path("/tmp/fake.pdf")})
            if send_raises is not None:
                raise send_raises

    # run_real_job calls z9.jobs.get_jobs_snapshot() to
    # snapshot the set of firmware_uuid before submit. We expose an empty
    # fake snapshot — the post-submit hook will do nothing (no "prn" in
    # the callback data, so captured_prn_path stays None).
    class _Jobs:
        def get_jobs_snapshot(self_inner):
            return {
                "queue_status": "Running", "number_of_jobs": 0,
                "modification_number": 0, "timestamp": "", "jobs": [],
            }

    class _Soap:
        # run_real_job embeds the live resident profile → resolve_active_paper_icc_info
        # calls z9.soap.get_profile. Return valid ICC bytes by default; raise on
        # demand to exercise the block-franc path.
        def get_profile(self_inner, medium_id, gloss_enhancer="FULLPAGE",
                        color_space="PRINTER_RGB"):
            if resident_raises is not None:
                raise resident_raises
            return {"icc_bytes": _srgb_icc()}

    return SimpleNamespace(
        device=_Device(), paper=_Paper(), print=_Print(),
        jobs=_Jobs(), soap=_Soap(), host="fake",
    )


# ─── Async unit tests (worker + store + pub/sub) ───────────────────────


@pytest.mark.asyncio
async def test_run_mock_job_full_flow_publishes_all_stages():
    store = JobStore()
    state = store.create("J1", "F1", PrintParams())
    await run_mock_job("F1", state.params,
                       sink=lambda e: store.publish("J1", e), timing=FAST)
    final = store.get("J1")
    assert final.status == JobStage.DONE
    assert final.progress == 100
    stages = [e.stage for e in final.events]
    assert stages[0] == JobStage.PREPARING
    assert stages[-1] == JobStage.DONE
    assert stages.count(JobStage.SENDING) == 3  # 50%, 75%, 95%
    assert final.finished_at is not None


@pytest.mark.asyncio
async def test_run_mock_job_failure_at_sending():
    store = JobStore()
    state = store.create("J2", "F1", PrintParams())
    await run_mock_job("F1", state.params,
                       sink=lambda e: store.publish("J2", e),
                       timing=FAST, fail_at=JobStage.SENDING,
                       fail_code="MOCK-FAIL")
    final = store.get("J2")
    assert final.status == JobStage.ERROR
    last = final.events[-1]
    assert last.stage == JobStage.ERROR
    assert last.data["code"] == "MOCK-FAIL"


@pytest.mark.asyncio
async def test_stream_events_replays_history_when_job_done():
    store = JobStore()
    state = store.create("J3", "F1", PrintParams())
    await run_mock_job("F1", state.params,
                       sink=lambda e: store.publish("J3", e), timing=FAST)
    received = [e async for e in store.stream_events("J3")]
    assert received[-1].stage == JobStage.DONE
    assert len(received) == len(store.get("J3").events)


@pytest.mark.asyncio
async def test_stream_events_live_during_job():
    store = JobStore()
    state = store.create("J4", "F1", PrintParams())

    async def _producer():
        await run_mock_job("F1", state.params,
                           sink=lambda e: store.publish("J4", e),
                           timing=FAST)

    producer = asyncio.create_task(_producer())
    received = []
    async for evt in store.stream_events("J4"):
        received.append(evt)
        if evt.stage == JobStage.DONE:
            break
    await producer
    stages = [e.stage for e in received]
    assert stages[-1] == JobStage.DONE


@pytest.mark.asyncio
async def test_two_concurrent_jobs_do_not_interfere():
    """Two parallel jobs, two independent streams."""
    store = JobStore()
    store.create("A", "F1", PrintParams())
    store.create("B", "F2", PrintParams())

    async def _stream(job_id):
        return [e async for e in store.stream_events(job_id)]

    async def _producer(job_id):
        await run_mock_job(
            "Fx", PrintParams(),
            sink=lambda e: store.publish(job_id, e),
            timing=FAST,
        )

    # Launch producers + streams in parallel
    tasks = [
        asyncio.create_task(_stream("A")),
        asyncio.create_task(_stream("B")),
        asyncio.create_task(_producer("A")),
        asyncio.create_task(_producer("B")),
    ]
    stream_a_result, stream_b_result, _, _ = await asyncio.gather(*tasks)
    # Each stream ended on DONE and saw a similar number of events
    assert stream_a_result[-1].stage == JobStage.DONE
    assert stream_b_result[-1].stage == JobStage.DONE
    assert len(stream_a_result) >= 5
    assert len(stream_b_result) >= 5


# ─── HTTP tests (full stack via TestClient) ────────────────────────────


def test_http_start_then_sse_stream_yields_done_event(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    sample = tmp_path / "s.tif"
    _make_tiff(sample)

    with TestClient(app) as client:
        app.state.mock_timing = FAST
        app.state.use_mock_print = True  # do not hit the real Z9 in pytest
        with open(sample, "rb") as f:
            r = client.post("/api/files", files={"file": ("s.tif", f, "image/tiff")})
        assert r.status_code == 200
        fid = r.json()["file_id"]

        r = client.post("/api/print", json={"file_id": fid, "params": {}})
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        assert r.json()["status"] == "preparing"

        # SSE stream
        seen_stages = []
        current_event = None
        with client.stream("GET", f"/api/print/{job_id}/events") as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    seen_stages.append((current_event, payload["stage"]))
                    if current_event == "done":
                        break

        assert ("done", "done") in seen_stages
        assert any(name == "stage" for name, _ in seen_stages)


def test_http_post_twice_creates_two_independent_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    sample = tmp_path / "s.tif"
    _make_tiff(sample)

    with TestClient(app) as client:
        app.state.mock_timing = FAST
        app.state.use_mock_print = True
        with open(sample, "rb") as f:
            fid = client.post(
                "/api/files", files={"file": ("s.tif", f, "image/tiff")}
            ).json()["file_id"]

        body = {"file_id": fid, "params": {}}
        r1 = client.post("/api/print", json=body)
        r2 = client.post("/api/print", json=body)
        assert r1.status_code == 202 and r2.status_code == 202
        job1 = r1.json()["job_id"]
        job2 = r2.json()["job_id"]
        assert job1 != job2

        # Both job_ids must be queryable
        assert client.get(f"/api/print/{job1}").status_code == 200
        assert client.get(f"/api/print/{job2}").status_code == 200


def test_http_get_unknown_job_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    with TestClient(app) as client:
        fake = "00000000-0000-4000-8000-000000000000"
        assert client.get(f"/api/print/{fake}").status_code == 404
        assert client.get(f"/api/print/{fake}/events").status_code == 404


def test_http_start_with_unknown_file_id_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    with TestClient(app) as client:
        fake = "00000000-0000-4000-8000-000000000000"
        r = client.post("/api/print", json={"file_id": fake, "params": {}})
        assert r.status_code == 404


# ─── POST /api/print/{job_id}/cancel tests ─────────────────────────


def test_http_cancel_unknown_job_returns_404(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    with TestClient(app) as client:
        fake = "00000000-0000-4000-8000-000000000000"
        r = client.post(f"/api/print/{fake}/cancel")
        assert r.status_code == 404


def test_http_cancel_mid_job_publishes_cancelled_event(tmp_path, monkeypatch):
    """Cancel while the mock worker runs -> CANCELLED event broadcast."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    sample = tmp_path / "s.tif"
    _make_tiff(sample)

    # Timing slow enough to have time to cancel before DONE
    slow = MockTiming(0.2, 0.2, 0.2, 0.2, 0.2)
    with TestClient(app) as client:
        app.state.mock_timing = slow
        app.state.use_mock_print = True
        with open(sample, "rb") as f:
            r = client.post("/api/files", files={"file": ("s.tif", f, "image/tiff")})
        fid = r.json()["file_id"]

        r = client.post("/api/print", json={"file_id": fid, "params": {}})
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        # Cancel ASAP — the worker is in PREPARING or PREFLIGHT
        r = client.post(f"/api/print/{job_id}/cancel")
        assert r.status_code == 202, r.text
        assert r.json()["ok"] is True
        assert r.json()["status"] == "cancelling"

        # Stream and wait for the CANCELLED event (or ERROR if cancel failed)
        seen_terminal = None
        with client.stream("GET", f"/api/print/{job_id}/events") as resp:
            assert resp.status_code == 200
            current_event = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    if payload["stage"] in ("cancelled", "done", "error"):
                        seen_terminal = payload["stage"]
                        break
        assert seen_terminal == "cancelled", (
            f"Expected CANCELLED, got {seen_terminal}"
        )


def test_http_cancel_after_done_idle_is_silent_ok(tmp_path, monkeypatch):
    """Worker finished DONE and Z9 idle -> silent 202 with already_terminal."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    sample = tmp_path / "s.tif"
    _make_tiff(sample)

    with TestClient(app) as client:
        app.state.mock_timing = FAST
        app.state.use_mock_print = True
        with open(sample, "rb") as f:
            r = client.post("/api/files", files={"file": ("s.tif", f, "image/tiff")})
        fid = r.json()["file_id"]
        job_id = client.post("/api/print", json={"file_id": fid, "params": {}}).json()["job_id"]

        # Wait for DONE via stream
        with client.stream("GET", f"/api/print/{job_id}/events") as resp:
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    if payload["stage"] == "done":
                        break

        # Subscriber idle (mocked Z9 -> NoActivity)
        r = client.post(f"/api/print/{job_id}/cancel")
        assert r.status_code == 202
        assert r.json() == {"ok": True, "already_terminal": True, "status": "done"}


def test_http_cancel_done_but_z9_busy_returns_409(tmp_path, monkeypatch):
    """Worker DONE but Z9 still active -> 409 post_send_no_remote_cancel."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    sample = tmp_path / "s.tif"
    _make_tiff(sample)

    # Fake subscriber that claims the Z9 is Drying
    class _FakeSub:
        def current_snapshot(self_inner):
            return {"activity_name": "Drying"}

    with TestClient(app) as client:
        app.state.mock_timing = FAST
        app.state.use_mock_print = True
        app.state.z9_status_subscriber = _FakeSub()
        try:
            with open(sample, "rb") as f:
                r = client.post("/api/files", files={"file": ("s.tif", f, "image/tiff")})
            fid = r.json()["file_id"]
            job_id = client.post("/api/print", json={"file_id": fid, "params": {}}).json()["job_id"]

            # Wait for worker DONE
            with client.stream("GET", f"/api/print/{job_id}/events") as resp:
                for line in resp.iter_lines():
                    if line.startswith("data:"):
                        payload = json.loads(line[len("data:"):].strip())
                        if payload["stage"] == "done":
                            break

            r = client.post(f"/api/print/{job_id}/cancel")
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["code"] == "post_send_no_remote_cancel"
            assert "front panel" in detail["message"].lower()
            assert detail["z9_activity"] == "Drying"
        finally:
            app.state.z9_status_subscriber = None


# ─── run_real_job tests (PrintOps.send mocked) ─────────────────────────


@pytest.mark.asyncio
async def test_run_real_job_emits_mapped_stages(tmp_path, monkeypatch):
    """Maps the 6 PrintOps callback stages -> JobStage + progress %."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    fid, dir_path = file_storage.new_storage()
    _make_tiff_16bit(dir_path / "source.tif")

    store = JobStore()
    state = store.create("J6", fid, PrintParams())
    fake_z9 = _fake_z9_for_real(
        loaded={
            "loaded_paper_id": "P", "loaded_paper_name": "FakePaper",
            "loaded_paper_source": "MANUAL_FEED",
            "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 297.0,
        },
        send_stages=["validate", "pdf", "preflight", "prn", "send", "done"],
    )

    await run_real_job(
        fid, state.params,
        sink=lambda e: store.publish("J6", e),
        z9=fake_z9, capabilities_cache={},
    )

    final = store.get("J6")
    assert final.status == JobStage.DONE
    assert final.progress == 100

    stages_progress = [(e.stage, e.progress) for e in final.events]
    assert (JobStage.PREPARING, 2) in stages_progress      # init webapp
    assert (JobStage.PREPARING, 5) in stages_progress      # validate
    assert (JobStage.RENDERING, 20) in stages_progress     # pdf
    assert (JobStage.PREFLIGHT, 35) in stages_progress     # preflight
    assert (JobStage.RENDERING, 60) in stages_progress     # prn
    assert (JobStage.SENDING, 85) in stages_progress       # send
    assert (JobStage.DONE, 100) in stages_progress         # done


@pytest.mark.asyncio
async def test_run_real_job_z9_preflight_error_to_event(tmp_path, monkeypatch):
    """PrintOps.send raises Z9PreflightError -> ERROR event with code = class name."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    fid, dir_path = file_storage.new_storage()
    _make_tiff_16bit(dir_path / "source.tif")

    store = JobStore()
    state = store.create("J7", fid, PrintParams())
    fake_z9 = _fake_z9_for_real(
        loaded={
            "loaded_paper_id": "P", "loaded_paper_name": "FakePaper",
            "loaded_paper_source": "MANUAL_FEED",
            "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 297.0,
        },
        send_stages=["validate", "pdf"],
        send_raises=Z9PreflightError("PDF not compliant"),
    )

    await run_real_job(
        fid, state.params,
        sink=lambda e: store.publish("J7", e),
        z9=fake_z9, capabilities_cache={},
    )

    final = store.get("J7")
    assert final.status == JobStage.ERROR
    last = final.events[-1]
    assert last.stage == JobStage.ERROR
    assert last.data["code"] == "Z9PreflightError"
    assert "PDF not compliant" in last.message


@pytest.mark.asyncio
async def test_run_real_job_no_paper_loaded_setup_fails(tmp_path, monkeypatch):
    """Setup phase: no paper -> ERROR, no call to send."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    fid, dir_path = file_storage.new_storage()
    _make_tiff_16bit(dir_path / "source.tif")

    store = JobStore()
    state = store.create("J8", fid, PrintParams())
    send_called = {"flag": False}

    fake_z9 = _fake_z9_for_real(loaded=None)
    original_send = fake_z9.print.send
    def _spy(*a, **kw):
        send_called["flag"] = True
        return original_send(*a, **kw)
    fake_z9.print.send = _spy

    await run_real_job(
        fid, state.params,
        sink=lambda e: store.publish("J8", e),
        z9=fake_z9, capabilities_cache={},
    )

    final = store.get("J8")
    assert final.status == JobStage.ERROR
    assert any("paper" in e.message.lower() for e in final.events)
    assert send_called["flag"] is False


@pytest.mark.asyncio
async def test_run_real_job_resident_unavailable_blocks(tmp_path, monkeypatch):
    """Resident ICC not retrievable → block franc (ERROR event), send NOT called,
    no silent fallback to the file's own profile."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    fid, dir_path = file_storage.new_storage()
    _make_tiff_16bit(dir_path / "source.tif")

    store = JobStore()
    state = store.create("J9", fid, PrintParams())
    send_called = {"flag": False}
    fake_z9 = _fake_z9_for_real(
        loaded={
            "loaded_paper_id": "P", "loaded_paper_name": "FakePaper",
            "loaded_paper_source": "MANUAL_FEED",
            "loaded_paper_width_mm": 210.0, "loaded_paper_length_mm": 297.0,
        },
        send_stages=["validate", "pdf", "preflight", "prn", "send", "done"],
        resident_raises=Z9Error("SOAP getProfile failed"),
    )
    original_send = fake_z9.print.send
    def _spy(*a, **kw):
        send_called["flag"] = True
        return original_send(*a, **kw)
    fake_z9.print.send = _spy

    await run_real_job(
        fid, state.params,
        sink=lambda e: store.publish("J9", e),
        z9=fake_z9, capabilities_cache={},
    )

    final = store.get("J9")
    assert final.status == JobStage.ERROR
    assert any("resident" in e.message.lower() for e in final.events)
    assert send_called["flag"] is False       # blocked before send


def test_http_use_mock_print_flag_forces_mock_worker(tmp_path, monkeypatch):
    """FREEGLAZ_MOCK_PRINT=1 -> mock worker even if a real Z9Client is available."""
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", tmp_path / "storage",
    )
    monkeypatch.setenv("FREEGLAZ_MOCK_PRINT", "1")
    sample = tmp_path / "s.tif"
    _make_tiff(sample)

    with TestClient(app) as client:
        # Lifespan read the env var and must have enabled the flag
        assert app.state.use_mock_print is True
        app.state.mock_timing = FAST
        # Override z9 so it is NOT None — demonstrates the flag takes precedence
        app.dependency_overrides[get_z9] = lambda: SimpleNamespace()

        with open(sample, "rb") as f:
            fid = client.post(
                "/api/files", files={"file": ("s.tif", f, "image/tiff")}
            ).json()["file_id"]

        r = client.post("/api/print", json={"file_id": fid, "params": {}})
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        # Verify we followed the mock path (which produces 3 SENDING events)
        seen_send_progress = []
        with client.stream("GET", f"/api/print/{job_id}/events") as resp:
            current_event = None
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    if payload["stage"] == "sending":
                        seen_send_progress.append(payload["progress"])
                    if current_event == "done":
                        break
        # Mock emits 3 SENDING events (50, 75, 95). Real worker emits only one (85).
        assert sorted(seen_send_progress) == [50, 75, 95]

    app.dependency_overrides.clear()
