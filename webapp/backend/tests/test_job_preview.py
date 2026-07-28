"""Tests 14 P2.B — job_preview service (thumbnail generation)."""
import io
from pathlib import Path

import pytest
from PIL import Image

from webapp.backend.services import job_preview


@pytest.fixture(autouse=True)
def _isolate_previews(tmp_path, monkeypatch):
    """Redirect PREVIEWS_DIR to tmp_path per test."""
    monkeypatch.setattr(job_preview, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_preview, "PREVIEWS_DIR", tmp_path / "job_previews")
    yield


def _make_tiff(path: Path, w: int = 2400, h: int = 1800, mode: str = "RGB") -> None:
    """Create a synthetic TIFF on disk."""
    img = Image.new(mode, (w, h), color=(120, 90, 60))
    img.save(path, format="TIFF")


def test_thumbnail_path_returns_expected_filename():
    p = job_preview.thumbnail_path("ABC-123")
    assert p.name == "ABC-123.jpg"
    assert p.parent == job_preview.PREVIEWS_DIR


def test_generate_thumbnail_from_tiff_succeeds(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src, w=2400, h=1800)

    out = job_preview.generate_thumbnail(src, "JA5-1")
    assert out is not None
    assert out.exists()
    assert out.name == "JA5-1.jpg"


def test_generated_thumbnail_respects_max_side(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src, w=2400, h=1800)

    out = job_preview.generate_thumbnail(src, "JA5-1", max_side_px=512)
    with Image.open(out) as img:
        # The largest side must not exceed 512
        assert max(img.size) <= 512
        # The ratio must be preserved (4:3 here)
        assert abs(img.size[0] / img.size[1] - 2400 / 1800) < 0.05


def test_generated_thumbnail_is_reasonable_size(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src, w=2400, h=1800)

    out = job_preview.generate_thumbnail(src, "JA5-1")
    size = out.stat().st_size
    # Indicative 20-80 KB target — flat colors, little detail = rather small
    assert size < 100_000, f"thumb {size} bytes > 100 KB"


def test_generated_thumbnail_opens_as_jpeg(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src, w=2400, h=1800)

    out = job_preview.generate_thumbnail(src, "JA5-1")
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert img.mode == "RGB"


def test_generate_handles_missing_source(tmp_path):
    out = job_preview.generate_thumbnail(tmp_path / "nope.tif", "JA5-1")
    assert out is None


def test_generate_handles_unsupported_format(tmp_path):
    bad = tmp_path / "source.exe"
    bad.write_bytes(b"\x00")
    out = job_preview.generate_thumbnail(bad, "JA5-1")
    assert out is None


def test_generate_tiff_16bit_converts_to_8bit(tmp_path):
    """16-bit TIFF must be cleanly converted to 8-bit JPEG (no crash)."""
    src = tmp_path / "source.tif"
    img = Image.new("I;16", (2000, 1500), color=42000)
    img.save(src, format="TIFF")

    out = job_preview.generate_thumbnail(src, "JA5-1")
    assert out is not None
    with Image.open(out) as o:
        assert o.mode == "RGB"


def test_delete_thumbnail_idempotent(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src)
    job_preview.generate_thumbnail(src, "JA5-1")

    assert job_preview.delete_thumbnail("JA5-1") is True
    # 2nd delete does not crash
    assert job_preview.delete_thumbnail("JA5-1") is False


def test_list_thumbnails_returns_empty_when_dir_absent(tmp_path):
    # Not generated yet -> dir does not exist
    assert job_preview.list_thumbnails() == []


def test_list_thumbnails_returns_existing_jpg(tmp_path):
    src = tmp_path / "source.tif"
    _make_tiff(src)
    job_preview.generate_thumbnail(src, "JA5-A")
    job_preview.generate_thumbnail(src, "JA5-B")

    names = [p.name for p in job_preview.list_thumbnails()]
    assert sorted(names) == ["JA5-A.jpg", "JA5-B.jpg"]


# ─── render_page_composite (P3.G) ────────────────────────────────────


def _composite_args(**overrides) -> dict:
    """Helper : standard composite parameter set (A4 portrait, centered
    image). Tests just pass the relevant overrides."""
    defaults = {
        "sheet_w_mm":   210.0,
        "sheet_h_mm":   297.0,
        "image_w_mm":   180.0,
        "image_h_mm":   240.0,
        "image_x_mm":   15.0,
        "image_y_mm":   28.5,
        "media_source": "SHEET",
        "gloss_enhancer": False,
    }
    defaults.update(overrides)
    return defaults


def test_render_composite_a4_portrait_proportions(tmp_path):
    """A4 portrait (210 × 297 mm) -> paper ratio ~1.41. The final canvas
    must have a close ratio, with ~20 px of external margin."""
    src = tmp_path / "src.tif"
    _make_tiff(src, w=1800, h=2400)

    out = job_preview.render_page_composite(src, "JA5-A4", **_composite_args())
    assert out is not None and out.exists()
    with Image.open(out) as img:
        assert img.format == "JPEG"
        # Large side ~= max_paper_dim + 2*margin = 512 + 40 = 552 px
        # Small side = (210/297) * 512 + 40 = ~ 402 px
        # Check within tolerance that canvas ratio ≈ paper ratio
        w, h = img.size
        paper_ratio = 210.0 / 297.0
        # canvas includes the gray borders ; the ratio stays close
        canvas_ratio = w / h
        assert abs(canvas_ratio - paper_ratio) < 0.08


def test_render_composite_roll_landscape(tmp_path):
    """Roll 610 × 200 mm (sheet height auto = image_h + 10 mm
    to save paper). Landscape ratio expected."""
    src = tmp_path / "src.tif"
    _make_tiff(src, w=3000, h=1000)
    out = job_preview.render_page_composite(
        src, "JA5-ROLL",
        **_composite_args(
            sheet_w_mm=610.0, sheet_h_mm=200.0,
            image_w_mm=600.0, image_h_mm=190.0,
            image_x_mm=5.0,   image_y_mm=5.0,
            media_source="ROLL",
        ),
    )
    with Image.open(out) as img:
        w, h = img.size
        assert w > h  # landscape


def test_render_composite_output_size_below_100kb(tmp_path):
    src = tmp_path / "src.tif"
    _make_tiff(src, w=2000, h=2800)
    out = job_preview.render_page_composite(src, "JA5-S", **_composite_args())
    assert out.stat().st_size < 100_000


def test_render_composite_with_gloss_enhancer_differs_from_without(tmp_path):
    """With GE we draw a dashed HP Blue overlay on the image area
    -> the resulting image must have different pixels at least on
    the edges of the image area."""
    src = tmp_path / "src.tif"
    _make_tiff(src, w=1200, h=1600)

    out_no_ge = job_preview.render_page_composite(
        src, "JA5-noge", **_composite_args(gloss_enhancer=False),
    )
    out_ge = job_preview.render_page_composite(
        src, "JA5-ge", **_composite_args(gloss_enhancer=True),
    )
    # The JPEG files must differ
    assert out_no_ge.read_bytes() != out_ge.read_bytes()


def test_render_composite_manual_feed_draws_bottom_margin_hint(tmp_path):
    """MANUAL_FEED -> bottom dashed line to visualize the 17.4 mm
    asymmetry. We check the composite differs vs ROLL (same sheet
    dims -> only the margin overlay changes)."""
    src = tmp_path / "src.tif"
    _make_tiff(src, w=2000, h=2800)

    out_roll = job_preview.render_page_composite(
        src, "JA5-roll", **_composite_args(media_source="ROLL"),
    )
    out_mf = job_preview.render_page_composite(
        src, "JA5-mf", **_composite_args(media_source="MANUAL_FEED"),
    )
    assert out_roll.read_bytes() != out_mf.read_bytes()


def test_render_composite_handles_missing_source(tmp_path):
    out = job_preview.render_page_composite(
        tmp_path / "nope.tif", "JA5-x", **_composite_args(),
    )
    assert out is None


def test_render_composite_handles_zero_paper_dims(tmp_path):
    src = tmp_path / "src.tif"
    _make_tiff(src)
    out = job_preview.render_page_composite(
        src, "JA5-zero", **_composite_args(sheet_w_mm=0, sheet_h_mm=0),
    )
    assert out is None


def test_render_composite_image_position_respected(tmp_path):
    """The image must be placed at the absolute position, not centered
    automatically. Checked indirectly by comparing pixels when
    image_x changes without changing the rest."""
    src = tmp_path / "src.tif"
    _make_tiff(src, w=600, h=600)

    out_left = job_preview.render_page_composite(
        src, "JA5-L", **_composite_args(image_x_mm=5.0),
    )
    out_right = job_preview.render_page_composite(
        src, "JA5-R", **_composite_args(image_x_mm=25.0),
    )
    assert out_left.read_bytes() != out_right.read_bytes()


def test_generate_thumbnail_from_pdf_succeeds(tmp_path):
    """Single-page PDF rendered via pypdfium2."""
    import pypdfium2 as pdfium

    src = tmp_path / "source.pdf"
    # Generate a blank 1-page A4 PDF via pikepdf (already in deps)
    import pikepdf
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(595, 842))  # A4 in points
    pdf.save(src)
    pdf.close()

    out = job_preview.generate_thumbnail(src, "JA5-PDF")
    assert out is not None
    assert out.exists()
    with Image.open(out) as img:
        assert img.format == "JPEG"
        assert max(img.size) <= 512
