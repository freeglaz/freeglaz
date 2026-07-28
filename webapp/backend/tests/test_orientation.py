"""Landscape/portrait orientation tests (90/180/270 rotation) of the print path.

Three layers under test:
  1. ``oriented_dims`` (pure geometry) — swap w/h for 90/270 only.
  2. ``PrintJob.validate()`` — orientation enum.
  3. ``PrintOps._load_tiff_for_pdf`` — REAL buffer rotation (np.rot90),
     px dims transposed for 90/270, content effectively rotated.

Key invariant: the pixel rotation (lib) and the mm transposition (geometry)
stay coherent → MediaBox == PAPERLENGTH preserved, no firmware /Rotate.
"""
import numpy as np
import pytest
import tifffile

from lib.z9_client.client import Z9Client
from lib.z9_client.exceptions import Z9GeometryError, Z9PrintError
from lib.z9_client.printing import PrintJob, PrintOps
from webapp.backend.models import LoadedPaper, PrintParams
from webapp.backend.services.print_geometry import compute_geometry, oriented_dims


# ─── Layer 1: oriented_dims (pure) ──────────────────────────────────


@pytest.mark.parametrize("orient,expected", [
    (0,   (100.0, 150.0)),
    (180, (100.0, 150.0)),
    (90,  (150.0, 100.0)),
    (270, (150.0, 100.0)),
])
def test_oriented_dims_swap(orient, expected):
    assert oriented_dims(orient, 100.0, 150.0) == expected


def test_oriented_dims_changes_roll_footprint():
    """In economic ROLL, sheet_h follows the EFFECTIVE height: rotating a
    portrait image (100×150) to landscape must shorten the consumed sheet."""
    roll = LoadedPaper(
        id="MID_ROLL", short_id=0, name="Roll", category="paper",
        is_factory=False, media_source="ROLL",
        sheet_width_mm=None, sheet_height_mm=None, roll_width_mm=609.6,
    )
    params = PrintParams(gloss_enhancer="OFF", offset_y_mm=0.0)

    w0, h0 = oriented_dims(0, 100.0, 150.0)
    g0 = compute_geometry(roll, params, w0, h0)
    w90, h90 = oriented_dims(90, 100.0, 150.0)
    g90 = compute_geometry(roll, params, w90, h90)

    # sheet_h portrait (image_h=150) > sheet_h landscape (image_h=100)
    assert g0.sheet_height_mm > g90.sheet_height_mm
    assert g90.image_width_mm == 150.0 and g90.image_height_mm == 100.0


# ─── Layer 2: PrintJob.validate ─────────────────────────────────────


def _minimal_job(tiff_path, **kw):
    """Geometrically valid job (image well inside the margins).
    pdf_path used (existence not checked for a nonexistent TIFF) — we create
    an empty file to satisfy the TIFF existence check."""
    tiff_path.write_bytes(b"")  # existence is enough (validate does not read the content)
    base = dict(
        tiff_path=tiff_path, paper_id="2001", paper_name="P",
        media_source="MANUALFEED", sheet_w_mm=600.0, sheet_h_mm=900.0,
        image_w_mm=100.0, image_h_mm=150.0, offset_x_mm=50.0, offset_y_mm=50.0,
        gloss="OFF", quality="HIGH", rendermode="COLOR", max_detail="OFF",
        drytime="NORMAL", cutter="ON", copies=1, username="t",
    )
    base.update(kw)
    return PrintJob(**base)


def test_print_job_default_orientation_is_zero(tmp_path):
    job = _minimal_job(tmp_path / "x.tif")
    assert job.orientation == 0
    job.validate()  # does not raise


@pytest.mark.parametrize("orient", [90, 180, 270])
def test_print_job_valid_orientations(tmp_path, orient):
    job = _minimal_job(tmp_path / "x.tif", orientation=orient)
    job.validate()  # does not raise


@pytest.mark.parametrize("orient", [45, 1, 360, -90])
def test_print_job_rejects_bad_orientation(tmp_path, orient):
    job = _minimal_job(tmp_path / "x.tif", orientation=orient)
    with pytest.raises(Z9GeometryError) as exc:
        job.validate()
    assert any("orientation" in d for d in exc.value.details)


# ─── Layer 3: real buffer rotation ─────────────────────────────


def _make_tiff_16bit(path, px_w, px_h):
    """Asymmetric 16-bit RGB TIFF: top-left corner marked to track the
    rotation. No ICC (not required by _load_tiff_for_pdf, which returns
    icc=None; build_pdfx4 requires it, but we test _load alone)."""
    arr = np.zeros((px_h, px_w, 3), dtype=np.uint16)
    arr[0, 0] = (65535, 0, 0)   # red marker top-left
    tifffile.imwrite(path, arr, resolution=(300, 300), resolutionunit="INCH")
    return arr


def _make_ops():
    client = Z9Client.__new__(Z9Client)
    client.host = "127.0.0.1"
    client.admin_pwd = None
    return PrintOps(client)


def test_load_tiff_swaps_px_dims_for_90_270(tmp_path):
    p = tmp_path / "a.tif"
    _make_tiff_16bit(p, px_w=120, px_h=200)
    ops = _make_ops()

    _, w0, h0, _ = ops._load_tiff_for_pdf(p, 0)
    assert (w0, h0) == (120, 200)

    _, w90, h90, _ = ops._load_tiff_for_pdf(p, 90)
    assert (w90, h90) == (200, 120)           # transposed

    _, w180, h180, _ = ops._load_tiff_for_pdf(p, 180)
    assert (w180, h180) == (120, 200)         # unchanged

    _, w270, h270, _ = ops._load_tiff_for_pdf(p, 270)
    assert (w270, h270) == (200, 120)         # transposed


def test_load_tiff_rotates_content(tmp_path):
    """The content is REALLY rotated (np.rot90 CCW), not just the dims."""
    p = tmp_path / "a.tif"
    arr = _make_tiff_16bit(p, px_w=120, px_h=200)
    ops = _make_ops()

    raw, w, h, _ = ops._load_tiff_for_pdf(p, 90)
    got = np.frombuffer(raw, dtype=">u2").reshape(h, w, 3).astype(np.uint16)
    expected = np.rot90(arr, k=1)
    assert np.array_equal(got, expected)
    # The red marker (once top-left) has moved to bottom-left after CCW.
    assert tuple(got[h - 1, 0]) == (65535, 0, 0)


# ─── Layer 3b: bit-depth promotion ─────────────────────────────


def test_load_tiff_promotes_8bit_to_full_range_16bit(tmp_path):
    """An 8-bit TIFF is accepted and promoted with ×257 (0..255 → 0..65535
    exactly), NOT rejected and NOT a bare <<8 (which would cap white at 65280)."""
    p = tmp_path / "a8.tif"
    arr8 = np.zeros((2, 3, 3), dtype=np.uint8)
    arr8[0, 0] = (255, 128, 0)   # white / mid / black across the channels
    tifffile.imwrite(p, arr8, resolution=(300, 300), resolutionunit="INCH")

    raw, w, h, _ = _make_ops()._load_tiff_for_pdf(p, 0)
    got = np.frombuffer(raw, dtype=">u2").reshape(h, w, 3).astype(np.uint16)

    assert (w, h) == (3, 2)
    # 255*257 == 65535 (true full-range white), 128*257 == 32896, 0 stays 0.
    assert tuple(got[0, 0]) == (65535, 32896, 0)
    assert np.array_equal(got, arr8.astype(np.uint16) * 257)


def test_load_tiff_16bit_passes_through_unchanged(tmp_path):
    """A genuine 16-bit TIFF is NOT touched by the promotion branch."""
    p = tmp_path / "a16.tif"
    arr = _make_tiff_16bit(p, px_w=4, px_h=3)
    raw, w, h, _ = _make_ops()._load_tiff_for_pdf(p, 0)
    got = np.frombuffer(raw, dtype=">u2").reshape(h, w, 3).astype(np.uint16)
    assert np.array_equal(got, arr)


def test_load_tiff_rejects_float_dtype(tmp_path):
    """Neither 8- nor 16-bit integer (e.g. float32) is still rejected."""
    p = tmp_path / "af.tif"
    tifffile.imwrite(
        p, np.zeros((2, 2, 3), dtype=np.float32),
        resolution=(300, 300), resolutionunit="INCH",
    )
    with pytest.raises(Z9PrintError, match="8/16-bit"):
        _make_ops()._load_tiff_for_pdf(p, 0)
