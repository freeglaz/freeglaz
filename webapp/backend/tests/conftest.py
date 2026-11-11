"""Shared fixtures: generate tiny TIFF and PDF on the fly + isolate ROOT.

Also installs the NETWORK GUARD (tests/netguard.py) for the whole session, before any
test module is imported: no test may reach a non-local address, in-process or in a
Python subprocess. A test that attempts it fails, even if the error was swallowed."""
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image, ImageCms

from webapp.backend.tests import netguard

# ── network guard: installed at import (conftest loads before the test modules) ──
netguard.install()
_NETGUARD_SITE = str(Path(__file__).parent / "_netguard_site")
_fd, _NETGUARD_LOG = tempfile.mkstemp(prefix="freeglaz-netguard-", suffix=".log")
os.close(_fd)
os.environ[netguard.LOG_ENV] = _NETGUARD_LOG
os.environ["PYTHONPATH"] = os.pathsep.join(
    [_NETGUARD_SITE] + [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p])


def pytest_unconfigure(config):
    try:
        os.unlink(_NETGUARD_LOG)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _netguard_check():
    """Fails the test if it attempted any non-local network access (in-process or in a
    Python subprocess), even when the code under test swallowed NetworkAccessBlocked."""
    start, offset = len(netguard.ATTEMPTS), netguard.log_size()
    yield
    inproc = netguard.ATTEMPTS[start:]
    sub, _ = netguard.read_log_from(offset)
    if inproc or sub:
        del netguard.ATTEMPTS[start:]
        details = [f"{a['kind']} {a['target']}\n{a['stack']}" for a in inproc] + [f"subprocess: {l}" for l in sub]
        pytest.fail("non-local network access attempted by this test (blocked):\n" + "\n".join(details),
                    pytrace=False)


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
