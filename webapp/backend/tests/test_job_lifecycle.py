"""Tests — job_lifecycle service (post-submit hooks)."""
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from lib.z9_client.exceptions import Z9ConnectionError
from webapp.backend.services import job_lifecycle, job_mapping, job_preview


@pytest.fixture(autouse=True)
def _isolate_data(tmp_path, monkeypatch):
    """Redirect the mapping + previews into tmp_path."""
    monkeypatch.setattr(job_mapping, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_mapping, "MAPPING_FILE", tmp_path / "job_mapping.json")
    monkeypatch.setattr(job_preview, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_preview, "PREVIEWS_DIR", tmp_path / "job_previews")
    yield


def _make_prn_with_jobacct5(path: Path, jobacct5: str = "A1B2C3D4-E5F6-4789-ABCD-EF1234567890") -> None:
    """Create a fake PRN with a PJL header containing JobAcct5."""
    header = (
        "\x1B%-12345X@PJL JOB NAME = \"test.tif (1 page) - freeglaz\"\n"
        '@PJL SET JOBATTR = "JobAcct1=user"\n'
        f'@PJL SET JOBATTR = "JobAcct5={jobacct5}"\n'
        '@PJL SET JOBATTR = "JobAcct6=freeglaz"\n'
    )
    # The real PRN has ~50 MB of binary after the header, we simulate just the header.
    path.write_bytes(header.encode("ascii") + b"\x00" * 1024)


def _make_tiff(path: Path) -> None:
    Image.new("RGB", (1200, 900), color=(100, 100, 100)).save(path, format="TIFF")


# ─── extract_jobacct5_from_prn ────────────────────────────────────────


def test_extract_jobacct5_from_prn_found(tmp_path):
    prn = tmp_path / "job.prn"
    _make_prn_with_jobacct5(prn, "A1B2C3D4-E5F6-4789-ABCD-EF1234567890")

    out = job_lifecycle.extract_jobacct5_from_prn(prn)
    assert out == "A1B2C3D4-E5F6-4789-ABCD-EF1234567890"


def test_extract_jobacct5_uppercase_normalized(tmp_path):
    prn = tmp_path / "job.prn"
    _make_prn_with_jobacct5(prn, "a1b2c3d4-e5f6-4789-abcd-ef1234567890")

    out = job_lifecycle.extract_jobacct5_from_prn(prn)
    assert out == "A1B2C3D4-E5F6-4789-ABCD-EF1234567890"


def test_extract_jobacct5_missing_returns_none(tmp_path):
    prn = tmp_path / "job.prn"
    prn.write_bytes(b"PRN without PJL JobAcct5")
    assert job_lifecycle.extract_jobacct5_from_prn(prn) is None


def test_extract_jobacct5_nonexistent_returns_none(tmp_path):
    assert job_lifecycle.extract_jobacct5_from_prn(tmp_path / "nope.prn") is None


# ─── detect_new_firmware_uuid ─────────────────────────────────────────


def _z9_with_jobs(jobs_uuids: list[str]):
    z9 = SimpleNamespace()
    z9.jobs = MagicMock()
    z9.jobs.get_jobs_snapshot.return_value = {
        "queue_status": "Paused",
        "number_of_jobs": len(jobs_uuids),
        "modification_number": 1,
        "timestamp": "2026-05-24T10:00:00Z",
        "jobs": [{"uuid": u} for u in jobs_uuids],
    }
    return z9


def test_detect_new_firmware_uuid_finds_new(tmp_path):
    z9 = _z9_with_jobs(["FW-OLD-1", "FW-OLD-2", "FW-NEW"])
    new = job_lifecycle.detect_new_firmware_uuid(
        z9, before_uuids={"FW-OLD-1", "FW-OLD-2"},
        poll_interval_s=0.01, timeout_s=0.1,
    )
    assert new == "FW-NEW"


def test_detect_new_firmware_uuid_returns_none_on_timeout():
    z9 = _z9_with_jobs(["FW-OLD-1"])  # nothing new
    out = job_lifecycle.detect_new_firmware_uuid(
        z9, before_uuids={"FW-OLD-1"},
        poll_interval_s=0.01, timeout_s=0.05,
    )
    assert out is None


def test_detect_new_firmware_uuid_resilient_to_z9_error():
    z9 = SimpleNamespace()
    z9.jobs = MagicMock()
    z9.jobs.get_jobs_snapshot.side_effect = [
        Z9ConnectionError("network down"),
        {"jobs": [{"uuid": "FW-OLD"}, {"uuid": "FW-NEW"}],
         "queue_status": "Paused", "number_of_jobs": 2, "modification_number": 1,
         "timestamp": ""},
    ]
    out = job_lifecycle.detect_new_firmware_uuid(
        z9, before_uuids={"FW-OLD"},
        poll_interval_s=0.01, timeout_s=0.2,
    )
    assert out == "FW-NEW"


# ─── snapshot_current_uuids ───────────────────────────────────────────


def test_snapshot_current_uuids_returns_set():
    z9 = _z9_with_jobs(["A", "B", "C"])
    assert job_lifecycle.snapshot_current_uuids(z9) == {"A", "B", "C"}


def test_snapshot_current_uuids_empty_on_z9_error():
    z9 = SimpleNamespace()
    z9.jobs = MagicMock()
    z9.jobs.get_jobs_snapshot.side_effect = Z9ConnectionError("down")
    assert job_lifecycle.snapshot_current_uuids(z9) == set()


# ─── finalize_submitted_job (end-to-end synchrone) ────────────────────


def test_finalize_creates_mapping_and_thumb(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src)
    prn = tmp_path / "source.prn"
    _make_prn_with_jobacct5(prn, "A1B2C3D4-E5F6-4789-ABCD-EF1234567890")

    z9 = _z9_with_jobs(["FW-OLD", "FW-NEW"])
    jobacct5, fw = job_lifecycle.finalize_submitted_job(
        z9, src, prn, before_uuids={"FW-OLD"},
    )

    # Patch the poll interval to avoid waiting
    assert jobacct5 == "A1B2C3D4-E5F6-4789-ABCD-EF1234567890"
    assert fw == "FW-NEW"

    # Mapping persisted
    assert job_mapping.lookup("FW-NEW") == "A1B2C3D4-E5F6-4789-ABCD-EF1234567890"
    # Thumb created
    thumb_path = job_preview.thumbnail_path("A1B2C3D4-E5F6-4789-ABCD-EF1234567890")
    assert thumb_path.exists()


def test_finalize_skips_when_no_jobacct5(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src)
    prn = tmp_path / "broken.prn"
    prn.write_bytes(b"No PJL header")

    z9 = _z9_with_jobs(["FW-OLD", "FW-NEW"])
    jobacct5, fw = job_lifecycle.finalize_submitted_job(
        z9, src, prn, before_uuids={"FW-OLD"},
    )
    assert jobacct5 is None
    assert fw is None
    # No mapping nor thumb
    assert job_mapping.all_entries() == {}


def test_finalize_with_composite_kwargs_renders_composite(tmp_path):
    """If composite_kwargs is passed, we should get a composite JPEG
    (with sheet proportions) rather than a simple thumb
    of the source image."""
    src = tmp_path / "source.tif"
    _make_tiff(src)
    prn = tmp_path / "source.prn"
    _make_prn_with_jobacct5(prn, "AABBCCDD-EEFF-4001-9000-000000000001")

    z9 = _z9_with_jobs(["FW-OLD", "FW-NEW"])
    composite_kwargs = {
        "sheet_w_mm": 210.0, "sheet_h_mm": 297.0,
        "image_w_mm": 180.0, "image_h_mm": 240.0,
        "image_x_mm": 15.0,  "image_y_mm": 28.5,
        "media_source": "SHEET",
        "gloss_enhancer": True,
    }
    jobacct5, fw = job_lifecycle.finalize_submitted_job(
        z9, src, prn, before_uuids={"FW-OLD"},
        composite_kwargs=composite_kwargs,
    )
    assert jobacct5 is not None and fw == "FW-NEW"

    # The generated file must have the paper ratio (A4 portrait ~ 1.41)
    # vs a thumb resize source (square 1200x900 -> ratio != 1.41)
    from PIL import Image
    with Image.open(job_preview.thumbnail_path(jobacct5)) as img:
        w, h = img.size
        canvas_ratio = w / h
        paper_ratio = 210 / 297
        # Tolerance because the canvas has a 20 px outer border
        assert abs(canvas_ratio - paper_ratio) < 0.10


def test_finalize_thumb_created_even_if_firmware_uuid_not_found(tmp_path, monkeypatch):
    """If the queue poll fails/times out, we keep the thumb (useful for
    debug or to replay the mapping later)."""
    monkeypatch.setattr(job_lifecycle, "_POLL_INTERVAL_S", 0.01)
    monkeypatch.setattr(job_lifecycle, "_POLL_TIMEOUT_S", 0.05)

    src = tmp_path / "source.tif"
    _make_tiff(src)
    prn = tmp_path / "source.prn"
    _make_prn_with_jobacct5(prn, "AABBCCDD-EEFF-4001-9000-000000000001")

    z9 = _z9_with_jobs(["FW-OLD"])  # nothing new -> timeout
    jobacct5, fw = job_lifecycle.finalize_submitted_job(
        z9, src, prn, before_uuids={"FW-OLD"},
    )
    assert jobacct5 == "AABBCCDD-EEFF-4001-9000-000000000001"
    assert fw is None
    assert job_preview.thumbnail_path(jobacct5).exists()
    assert job_mapping.all_entries() == {}
