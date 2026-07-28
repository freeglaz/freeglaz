"""Shared fixtures: generate tiny TIFF and PDF on the fly + isolate ROOT."""
from pathlib import Path

import pytest
from PIL import Image, ImageCms


def _srgb_icc_bytes() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


@pytest.fixture
def sample_tiff_path(tmp_path: Path) -> Path:
    p = tmp_path / "sample.tif"
    img = Image.new("RGB", (64, 64), color=(180, 90, 30))
    img.save(p, format="TIFF", dpi=(300, 300), icc_profile=_srgb_icc_bytes())
    return p


@pytest.fixture
def sample_pdf_path(tmp_path: Path) -> Path:
    p = tmp_path / "sample.pdf"
    img = Image.new("RGB", (595, 842), color=(255, 255, 255))  # ≈ A4 @ 72 dpi
    img.save(p, format="PDF")
    return p


@pytest.fixture(autouse=True)
def isolated_upload_storage(tmp_path: Path, monkeypatch) -> Path:
    """Isolate file_storage.ROOT per test (otherwise /tmp/freeglaz-webapp is shared)."""
    storage = tmp_path / "storage"
    monkeypatch.setattr(
        "webapp.backend.services.file_storage.ROOT", storage
    )
    return storage


@pytest.fixture(autouse=True)
def _profile_build_sync(monkeypatch):
    """Profile build = background job; in tests we run it INLINE (deterministic, no thread)
    + reset the singleton between tests (persistent module globals -> otherwise concurrency pollutes)."""
    monkeypatch.setenv("FREEGLAZ_PROFILE_BUILD_SYNC", "1")
    from webapp.backend.services import chart_profile_job
    chart_profile_job.reset()


@pytest.fixture(autouse=True)
def _no_z9_subscribers(monkeypatch):
    """Z9 ANTI-HAMMERING: NO test starts the subscribers (status/jobs) that
    poll the real `192.168.1.50` via the lifespan -> HERMETIC tests (no more stray
    poller, and end of the desktop test flakiness that depended on real network state)."""
    monkeypatch.setenv("FREEGLAZ_DISABLE_Z9_SUBSCRIBERS", "1")
