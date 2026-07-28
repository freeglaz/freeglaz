"""Tests for PJL JOB NAME unique + UUID + ASCII sanitize.

Verifies that the PRN send pipeline generates identifiers unique
per print (JobAcct5) and stable per session (JobAcct16), avoiding
the silent overwrite of jobs in the Z9 queue when 2 consecutive PRN
share the same name.
"""
import re
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import tifffile
from PIL import Image, ImageCms

from lib.z9_client.client import Z9Client
from lib.z9_client.printing import (
    PrintJob,
    _APP_INSTANCE_UUID,
    _FREEGLAZ_VERSION,
    sanitize_for_pjl,
)


# ─── Sanitize helper ──────────────────────────────────────────────────


def test_sanitize_for_pjl_passes_through_safe_ascii():
    assert sanitize_for_pjl("canson_photolustre.tif") == "canson_photolustre.tif"
    assert sanitize_for_pjl("photo 1.pdf") == "photo 1.pdf"
    assert sanitize_for_pjl("test-2026.tif") == "test-2026.tif"


def test_sanitize_for_pjl_strips_non_ascii_accents():
    # Accents -> equivalent ASCII character lost (encode 'ignore'),
    # only the skeleton remains -> invalid characters replaced by _
    out = sanitize_for_pjl("Müller Frères côté est.tif")
    # 'ü' / 'è' / 'ê' / 'ô' dropped via encode('ascii', 'ignore'),
    # the rest passes the whitelist as is.
    assert "M" in out and "ller" in out and "est.tif" in out
    # No non-ASCII character
    assert all(ord(c) < 128 for c in out)


def test_sanitize_for_pjl_replaces_special_chars_with_underscore():
    assert sanitize_for_pjl("foto/album_2026.tif") == "foto_album_2026.tif"
    assert sanitize_for_pjl("name#with@chars!.tif") == "name_with_chars_.tif"


def test_sanitize_for_pjl_keeps_parens_replaced():
    # Whitelist excludes ( and ) -> replaced. Note : PJL tolerates
    # literal parentheses, but we stay conservative (cf. known 'ü'
    # Z9 firmware bug) for values
    # coming from user file names.
    assert "_" in sanitize_for_pjl("photo (final).pdf")


def test_sanitize_for_pjl_collapses_spaces_only():
    """Multiple spaces -> a single one. Other whitespace (tab, etc.)
    falls into the whitelist by default and becomes '_'."""
    assert sanitize_for_pjl("a   b   c") == "a b c"
    # Tab/newline are not whitelisted -> '_' (not collapsed into a space)
    assert sanitize_for_pjl("a\tb\nc") == "a_b_c"


def test_sanitize_for_pjl_truncates():
    long = "x" * 200
    assert len(sanitize_for_pjl(long, max_len=60)) == 60


def test_sanitize_for_pjl_empty_returns_untitled():
    assert sanitize_for_pjl("") == "untitled"
    assert sanitize_for_pjl("///###") == "untitled" or sanitize_for_pjl("///###").strip("_") == ""


# ─── App instance UUID + version constants ────────────────────────────


def test_app_instance_uuid_format():
    """JobAcct16 — UUID4 in braces, lowercase (standard PJL format)."""
    assert _APP_INSTANCE_UUID.startswith("{") and _APP_INSTANCE_UUID.endswith("}")
    inner = _APP_INSTANCE_UUID[1:-1]
    # standard UUID4 : 8-4-4-4-12 hex chars (lowercase)
    assert re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", inner)


def test_freeglaz_version_not_empty():
    """JobAcct7 — non-empty version (may be 'dev' in source mode)."""
    assert _FREEGLAZ_VERSION and isinstance(_FREEGLAZ_VERSION, str)


# ─── Build PRN + PJL header extraction ────────────────────────────────


@pytest.fixture
def sample_print_job(tmp_path):
    """Minimal PrintJob + 16-bit RGB TIFF so a PDF/X-4 can be generated."""
    # 16-bit TIFF (prerequisite of the build_pdfx4 pipeline)
    px_w, px_h = 1181, 1772  # ~100×150 mm @ 300 dpi
    arr = np.full((px_h, px_w, 3), 30000, dtype=np.uint16)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    tiff_path = tmp_path / "test_a.tif"
    tifffile.imwrite(
        tiff_path, arr,
        resolution=(300, 300),
        resolutionunit="INCH",
        extratags=[(34675, "B", len(icc), icc, False)],
    )
    job = PrintJob(
        tiff_path=tiff_path,
        paper_id="2001",
        paper_name="HP Premium",
        media_source="MANUALFEED",
        sheet_w_mm=210.0, sheet_h_mm=297.0,
        image_w_mm=100.0, image_h_mm=150.0,
        offset_x_mm=55.0, offset_y_mm=73.5,
        gloss="OFF", quality="HIGH",
        rendermode="COLOR", max_detail="OFF",
        drytime="NORMAL", cutter="ON", copies=1,
        username="testuser",
    )
    return job


def _extract_pjl_header(prn_path: Path) -> str:
    """Read the first ~10 KB and decode the textual PJL header."""
    raw = prn_path.read_bytes()[:10000]
    # UEL escape ASCII, the rest is pure UTF-8/ASCII up to @PJL ENTER LANGUAGE=PDF
    return raw.decode("utf-8", errors="replace")


def _parse_jobacct(header_text: str, key: str) -> str:
    """Extract a JobAcctN value from the PJL header."""
    m = re.search(rf'@PJL SET JOBATTR = "JobAcct{key}=([^"]*)"', header_text)
    return m.group(1) if m else ""


def _parse_jobname(header_text: str) -> str:
    m = re.search(r'@PJL JOB NAME="([^"]*)"', header_text)
    return m.group(1) if m else ""


def _make_print_ops(tmp_path):
    """Build a minimal Z9Client without opening a real connection.

    We do not touch send_raw — only build_pdfx4 + build_prn
    (purely local, no network I/O).
    """
    client = Z9Client.__new__(Z9Client)
    client.host = "127.0.0.1"
    client.admin_pwd = None
    # The constructor initializes sub-clients (rest, soap, etc.).
    # We mock them to avoid any network attempt.
    from lib.z9_client.printing import PrintOps
    ops = PrintOps(client)
    return ops


def test_jobacct5_unique_per_build_prn(sample_print_job, tmp_path):
    """2 consecutive build_prn with the same PrintJob produce 2 different
    JobAcct5 (fresh UUID4 per print). Without this, the Z9 silently
    overwrites the previous one."""
    ops = _make_print_ops(tmp_path)
    # Build the PDF/X-4 only once (deterministic)
    pdf_path = tmp_path / "src.pdf"
    ops.build_pdfx4(sample_print_job, pdf_path)

    prn1 = tmp_path / "out1.prn"
    prn2 = tmp_path / "out2.prn"
    ops.build_prn(sample_print_job, pdf_path, prn1)
    ops.build_prn(sample_print_job, pdf_path, prn2)

    h1 = _extract_pjl_header(prn1)
    h2 = _extract_pjl_header(prn2)
    uuid1 = _parse_jobacct(h1, "5")
    uuid2 = _parse_jobacct(h2, "5")
    assert uuid1 and uuid2, f"JobAcct5 absent : {uuid1=} {uuid2=}"
    assert uuid1 != uuid2, "JobAcct5 doit être différent à chaque tirage"
    # UUID4 format (uppercase, dashes, no braces — standard PJL format)
    assert re.match(
        r"^[0-9A-F]{8}-[0-9A-F]{4}-4[0-9A-F]{3}-[89AB][0-9A-F]{3}-[0-9A-F]{12}$", uuid1,
    ), f"Format JobAcct5 inattendu : {uuid1}"


def test_jobacct16_stable_across_builds(sample_print_job, tmp_path):
    """JobAcct16 — app instance UUID — is stable across multiple build_prn
    in the same process (consistent with the 'backend session' semantics)."""
    ops = _make_print_ops(tmp_path)
    pdf_path = tmp_path / "src.pdf"
    ops.build_pdfx4(sample_print_job, pdf_path)

    prn1 = tmp_path / "out1.prn"
    prn2 = tmp_path / "out2.prn"
    ops.build_prn(sample_print_job, pdf_path, prn1)
    ops.build_prn(sample_print_job, pdf_path, prn2)

    h1 = _extract_pjl_header(prn1)
    h2 = _extract_pjl_header(prn2)
    inst1 = _parse_jobacct(h1, "16")
    inst2 = _parse_jobacct(h2, "16")
    assert inst1 == inst2 == _APP_INSTANCE_UUID
    assert inst1.startswith("{") and inst1.endswith("}")


def test_jobname_includes_sanitized_filename(tmp_path):
    """JOB NAME includes the sanitized source file name + format
    '(1 page) - freeglaz' (standard PJL format)."""
    # Create a TIFF with a name containing a non-ASCII / special character
    px_w, px_h = 600, 900
    arr = np.full((px_h, px_w, 3), 30000, dtype=np.uint16)
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    # Name containing a character to sanitize
    tiff_path = tmp_path / "foto_album_2026.tif"  # underscore instead of / (pathlib rejects '/')
    tifffile.imwrite(
        tiff_path, arr, resolution=(300, 300), resolutionunit="INCH",
        extratags=[(34675, "B", len(icc), icc, False)],
    )
    job = PrintJob(
        tiff_path=tiff_path, paper_id="2001", paper_name="HP Premium",
        media_source="MANUALFEED",
        sheet_w_mm=210.0, sheet_h_mm=297.0,
        image_w_mm=50.0, image_h_mm=76.0,
        offset_x_mm=80.0, offset_y_mm=110.5,
        gloss="OFF", quality="HIGH", rendermode="COLOR",
        max_detail="OFF", drytime="NORMAL", cutter="ON", copies=1,
        username="t",
    )
    ops = _make_print_ops(tmp_path)
    pdf_path = tmp_path / "src.pdf"
    prn_path = tmp_path / "out.prn"
    ops.build_pdfx4(job, pdf_path)
    ops.build_prn(job, pdf_path, prn_path)

    h = _extract_pjl_header(prn_path)
    name = _parse_jobname(h)
    assert "foto_album_2026.tif" in name
    assert "(1 page) - freeglaz" in name


def test_jobacct6_and_7_set_to_freeglaz(sample_print_job, tmp_path):
    """JobAcct6 = 'freeglaz' (application name in JobAcct6)
    and JobAcct7 = freeglazcli version (e.g. '0.1.0' or 'dev')."""
    ops = _make_print_ops(tmp_path)
    pdf_path = tmp_path / "src.pdf"
    prn_path = tmp_path / "out.prn"
    ops.build_pdfx4(sample_print_job, pdf_path)
    ops.build_prn(sample_print_job, pdf_path, prn_path)

    h = _extract_pjl_header(prn_path)
    assert _parse_jobacct(h, "6") == "freeglaz"
    assert _parse_jobacct(h, "7") == _FREEGLAZ_VERSION
