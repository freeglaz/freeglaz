"""Strict webapp upload gate: only a single-page RGB 8/16-bit TIFF with an
embedded ICC is accepted; everything else is rejected cleanly (by content, not
extension). Covers the helper directly and the POST /api/files route."""
import os
from pathlib import Path

import numpy as np
import pytest
import tifffile
from fastapi.testclient import TestClient
from PIL import Image, ImageCms

from webapp.backend.main import app
from webapp.backend.services.input_guard import (
    WebappInputRejected, validate_tiff_upload,
)


def _icc() -> bytes:
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


# ─── Fixtures: one small TIFF (or not) per contract case ────────────


def _tiff_rgb(path, dtype, with_icc=True):
    arr = np.zeros((3, 4, 3), dtype=dtype)
    kw = {"photometric": "rgb"}
    if with_icc:
        kw["iccprofile"] = _icc()
    tifffile.imwrite(path, arr, **kw)


def _tiff_multipage(path):
    with tifffile.TiffWriter(path) as tw:
        tw.write(np.zeros((3, 4, 3), np.uint16), photometric="rgb", iccprofile=_icc())
        tw.write(np.zeros((3, 4, 3), np.uint16), photometric="rgb")


def _tiff_cmyk(path):
    Image.new("CMYK", (4, 3)).save(path, format="TIFF")


def _tiff_rgba(path):
    Image.new("RGBA", (4, 3)).save(path, format="TIFF", icc_profile=_icc())


def _tiff_rgba16_assoc(path):
    """RGBA 16-bit with ASSOCIATED (premultiplied) alpha + ICC — the exact
    Affinity export shape that used to be a false positive."""
    arr = np.zeros((3, 4, 4), dtype=np.uint16)
    tifffile.imwrite(path, arr, photometric="rgb",
                     extrasamples=1, iccprofile=_icc())  # 1 = ASSOCALPHA


def _tiff_5channel(path):
    """RGB + two extra channels (SamplesPerPixel == 5) — outside the single
    alpha tolerance, must stay rejected. Non-3 leading dims + contig planarconfig
    so tifffile does not read the array as a 3-plane planar RGB."""
    arr = np.zeros((6, 8, 5), dtype=np.uint16)
    tifffile.imwrite(path, arr, photometric="rgb", planarconfig="contig",
                     extrasamples=(0, 0), iccprofile=_icc())  # 0 = UNSPECIFIED


# ─── The helper, case by case ───────────────────────────────────────


def test_accepts_rgb16_with_icc(tmp_path):
    p = tmp_path / "ok16.tif"
    _tiff_rgb(p, np.uint16)
    validate_tiff_upload(p)  # does not raise


def test_accepts_rgb8_with_icc(tmp_path):
    p = tmp_path / "ok8.tif"
    _tiff_rgb(p, np.uint8)
    validate_tiff_upload(p)  # does not raise (promoted downstream)


def test_accepts_rgba16_associated_alpha(tmp_path):
    """The Affinity case: RGBA 16-bit, associated (premultiplied) alpha, ICC.
    Accepted — freeglaz ignores the alpha (build keeps arr[..., :3])."""
    p = tmp_path / "rgba16.tif"
    _tiff_rgba16_assoc(p)
    validate_tiff_upload(p)  # does not raise


def test_accepts_rgba8_with_icc(tmp_path):
    p = tmp_path / "rgba8.tif"
    _tiff_rgba(p)  # PIL RGBA 8-bit + ICC
    validate_tiff_upload(p)  # does not raise (alpha ignored, 8→16 promoted)


def test_accepts_real_editor_export():
    """Regression on the real file that triggered the false positive (RGBA
    16-bit, associated opaque alpha, ICC). Skipped unless the fixture path is
    provided via FREEGLAZ_EDITOR_FIXTURE."""
    fixture = os.environ.get("FREEGLAZ_EDITOR_FIXTURE", "")
    p = Path(fixture)
    if not fixture or not p.exists():
        pytest.skip("real editor-export fixture not present (set FREEGLAZ_EDITOR_FIXTURE)")
    validate_tiff_upload(p)  # does not raise


@pytest.mark.parametrize("make,expected_code", [
    (_tiff_cmyk,                                   "not_rgb"),
    (lambda p: _tiff_rgb(p, np.uint16, with_icc=False), "no_icc"),
    (_tiff_5channel,                               "channels"),
    (_tiff_multipage,                              "multipage"),
    (lambda p: _tiff_rgb(p, np.float32),           "bad_depth"),
])
def test_rejects_nonconforming_tiff(tmp_path, make, expected_code):
    p = tmp_path / "bad.tif"
    make(p)
    with pytest.raises(WebappInputRejected) as exc:
        validate_tiff_upload(p)
    assert exc.value.code == expected_code
    assert exc.value.message  # a user-facing sentence is always present


def test_rejects_pdf_renamed_tif(tmp_path):
    """Magic-byte detection, not extension: a PDF renamed .tif is refused."""
    p = tmp_path / "sneaky.tif"
    p.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
    with pytest.raises(WebappInputRejected) as exc:
        validate_tiff_upload(p)
    assert exc.value.code == "not_tiff"


def test_rejects_arbitrary_non_image(tmp_path):
    p = tmp_path / "note.tif"
    p.write_bytes(b"hello world, not an image at all")
    with pytest.raises(WebappInputRejected) as exc:
        validate_tiff_upload(p)
    assert exc.value.code == "not_tiff"


# ─── The route ──────────────────────────────────────────────────────


def _client() -> TestClient:
    return TestClient(app)


def test_route_accepts_valid_tiff(tmp_path):
    p = tmp_path / "ok.tif"
    _tiff_rgb(p, np.uint16)
    r = _client().post(
        "/api/files",
        files={"file": ("ok.tif", p.read_bytes(), "image/tiff")},
    )
    assert r.status_code == 200
    assert r.json()["file_id"]


@pytest.mark.parametrize("make,name,expected_code", [
    (_tiff_cmyk, "photo.tif", "not_rgb"),
    (None,       "doc.tif",   "not_tiff"),   # PDF bytes under a .tif name
])
def test_route_rejects_with_structured_code(tmp_path, make, name, expected_code):
    if make is None:
        data = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    else:
        p = tmp_path / "src"
        make(p)
        data = p.read_bytes()
    r = _client().post("/api/files", files={"file": (name, data, "image/tiff")})
    assert r.status_code == 415
    assert r.json()["detail"]["code"] == expected_code
    assert r.json()["detail"]["message"]


def test_cli_entrypoint_does_not_reference_the_gate():
    """The lock is a webapp-layer decision; the CLI must not import it (proves
    the CLI print path stays unaffected)."""
    cli = Path(__file__).resolve().parents[3] / "freeglaz"
    text = cli.read_text(encoding="utf-8")
    assert "input_guard" not in text
    assert "validate_tiff_upload" not in text
