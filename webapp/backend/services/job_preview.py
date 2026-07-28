# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""freeglaz thumbnail generation for printed jobs.

Inc 14 Phase 3 — Part 2 (thumbnails). One JPEG thumbnail ~20-80 KB per
job, stored in ``webapp/data/job_previews/<jobacct5>.jpg``.

Why on the freeglaz side rather than the Z9 firmware side:
- The Z9 firmware exposes a preview thumbnail via REST PIWS, but it is
  purged when the job goes to ``Deleted``. So it is impossible to
  retrieve the preview of a cancelled/finished job for reprint.
- The freeglaz thumb is generated from the **original user source file**
  (TIFF / PDF), not from the post-hack placement PDF. Visually more
  faithful to what the user submitted.
- Hardlink possible on reprint (0 disk space, clean semantics).

Source: the file_id stored by ``file_storage`` (TIFF or PDF). The thumb
is generated just after the upload (before submit), so as not to slow
any critical step of the print pipeline.

Produced format:
- JPEG quality 75
- max 512 px on the long side (ratio preserved)
- RGB mode (not RGBA, not P)
- typically 20-80 KB depending on content

Known limitations:
- multi-page PDF: we take the first page only (freeglaz does not handle
  multi-pages on the printing side anyway — cf.
  ``Settings/NumberOfPagesPerCopy = 1`` always).
- 16-bit TIFF: converted to 8-bit for the JPEG. The thumb is
  illustrative, not a faithful 16-bit preview.
- multi-page TIFF: we take the first page.
"""
import io
import logging
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# Default path of the thumbnails folder, monkeypatchable in tests.
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
PREVIEWS_DIR = DATA_DIR / "job_previews"

MAX_SIDE_PX = 512
JPEG_QUALITY = 75

# ── Composite render constants (P3.G) ─────────────────────────────────
COMPOSITE_MAX_PAPER_DIM_PX = 512  # long side of the sheet in pixels
COMPOSITE_PAPER_MARGIN_PX  = 20   # gray border around the sheet
COMPOSITE_BG_COLOR         = (240, 240, 240)  # light gray = off-sheet
COMPOSITE_PAPER_COLOR      = (255, 255, 255)  # white = sheet
COMPOSITE_PAPER_BORDER     = (204, 204, 204)  # light gray = sheet edge
COMPOSITE_GE_COLOR         = (0, 150, 214)    # HP Blue for GE overlay
COMPOSITE_GE_ALPHA         = 0.40             # opacity 40 %
COMPOSITE_MARGIN_HINT      = (204, 204, 204)  # dashed margin line
# Fixed firmware margins — used for the dashed line visualizing the
# printable area on MANUAL_FEED jobs (17.4 mm bottom asymmetry).
# Cf. memory #14 / lib/z9_client/printing.py.
MANUAL_FEED_BOTTOM_MARGIN_MM = 17.4


def _ensure_dir() -> None:
    """Create ``PREVIEWS_DIR`` if absent. Idempotent."""
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)


def thumbnail_path(jobacct5: str) -> Path:
    """Expected path for the thumb of a given ``jobacct5``.

    The caller checks ``.exists()`` to know if it was generated.
    The ``jobacct5`` is sanitized on the caller side (uppercase UUID4
    with dashes) — no additional validation here.
    """
    return PREVIEWS_DIR / f"{jobacct5}.jpg"


def _load_first_page(source: Path) -> Optional[Image.Image]:
    """Load the 1st page of a source file into an RGB ``PIL.Image``.

    - TIFF / TIF: Pillow direct (native TIFF mode). 8-bit conversion via
      ``.convert("RGB")`` which handles 16-bit → 8-bit.
    - PDF: ``pypdfium2`` rasterizes the 1st page at ~150 DPI (enough for
      a thumb 512 px max).
    - Other formats: returns None (case not supposed to happen — the
      upload already filters on ``.tif/.tiff/.pdf``).
    """
    suffix = source.suffix.lower()
    if suffix in (".tif", ".tiff"):
        try:
            img = Image.open(source)
            # ``.convert("RGB")`` also applies the EXIF rotation if needed
            # (but mainly: 16-bit → 8-bit, RGBA → RGB, P → RGB).
            return img.convert("RGB")
        except Exception:  # noqa: BLE001 — we log and return None
            logger.exception("job_preview: impossible de lire TIFF %s", source)
            return None
    if suffix == ".pdf":
        try:
            import pypdfium2 as pdfium
        except ImportError:
            logger.warning("pypdfium2 indisponible — pas de thumb PDF")
            return None
        try:
            pdf = pdfium.PdfDocument(source)
            if len(pdf) == 0:
                return None
            page = pdf[0]
            # ~150 DPI = scale 150/72 ≈ 2.08. Enough for 512 px max.
            bitmap = page.render(scale=2.0)
            return bitmap.to_pil().convert("RGB")
        except Exception:  # noqa: BLE001
            logger.exception("job_preview: unable to render PDF %s", source)
            return None
    logger.warning("job_preview: unsupported suffix %s", suffix)
    return None


def generate_thumbnail(
    source: Path,
    jobacct5: str,
    *,
    max_side_px: int = MAX_SIDE_PX,
    quality: int = JPEG_QUALITY,
) -> Optional[Path]:
    """Generate the JPEG thumbnail for a source file + jobacct5.

    :param source: path of the user source file (TIFF / PDF)
    :param jobacct5: ``JobAcct5`` UUID4 of the job (thumb file name)
    :return: ``Path`` of the created thumb, or ``None`` if generation
             failed (logged, but does not interrupt the print pipeline —
             the thumb is best-effort).
    """
    if not source.exists():
        logger.warning("job_preview: source %s introuvable", source)
        return None

    img = _load_first_page(source)
    if img is None:
        return None

    # Resize preserving the ratio (Image.thumbnail modifies inplace).
    img.thumbnail((max_side_px, max_side_px), Image.Resampling.LANCZOS)

    _ensure_dir()
    out_path = thumbnail_path(jobacct5)
    try:
        # ``optimize=True`` tries a 2nd Huffman pass to gain ~5-10 % of
        # weight. Negligible CPU cost for a 512 px image.
        img.save(out_path, format="JPEG", quality=quality, optimize=True)
    except OSError:
        logger.exception("job_preview: JPEG write failed for %s", out_path)
        return None
    logger.info(
        "job_preview: thumb %s generated (%d bytes)",
        out_path.name, out_path.stat().st_size,
    )
    return out_path


def render_page_composite(
    source: Path,
    jobacct5: str,
    *,
    sheet_w_mm: float,
    sheet_h_mm: float,
    image_w_mm: float,
    image_h_mm: float,
    image_x_mm: float,
    image_y_mm: float,
    media_source: str,
    gloss_enhancer: bool = False,
    max_paper_dim_px: int = COMPOSITE_MAX_PAPER_DIM_PX,
    jpeg_quality: int = 80,
) -> Optional[Path]:
    """Generate a JPEG composite page render (Inc 14 P3.G).

    Instead of a simple thumb of the source image, we render the **final
    printed page** as it will come out of the machine:

    - Gray background ~20 px of external margin (= off-sheet area)
    - White rectangle at the **real sheet dimensions** (sheet_*_mm)
    - Source image resized + pasted at the **absolute position** in the
      sheet (image_x_mm / image_y_mm, ratio preserved)
    - Dashed HP Blue overlay at 40 % on the image area if
      ``gloss_enhancer=True`` (selective freeglaz GE area)
    - Gray dashed line at the bottom if ``media_source="MANUAL_FEED"`` to
      visualize the 17.4 mm asymmetry (informative only)

    All dimensions are in mm — computed by
    ``print_geometry.compute_geometry`` **before** the placement hack.
    The composite therefore reflects what the user submitted, not the
    post-hack PDF sent to the firmware.

    If loading the source image fails, we return None (the caller logs a
    warning but does not crash the print pipeline).

    :return: Path of the created JPEG, or None on failure.
    """
    if not source.exists():
        logger.warning("render_page_composite: source %s introuvable", source)
        return None
    if sheet_w_mm <= 0 or sheet_h_mm <= 0:
        logger.warning(
            "render_page_composite: dims feuille invalides (%.1f × %.1f mm)",
            sheet_w_mm, sheet_h_mm,
        )
        return None

    img = _load_first_page(source)
    if img is None:
        return None

    # Scale: sheet long side → max_paper_dim_px
    scale = max_paper_dim_px / max(sheet_w_mm, sheet_h_mm)
    paper_w_px = max(1, round(sheet_w_mm * scale))
    paper_h_px = max(1, round(sheet_h_mm * scale))
    canvas_w = paper_w_px + 2 * COMPOSITE_PAPER_MARGIN_PX
    canvas_h = paper_h_px + 2 * COMPOSITE_PAPER_MARGIN_PX

    # RGBA canvas to allow the alpha-composite of the GE overlay
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (*COMPOSITE_BG_COLOR, 255))

    # White "sheet" rectangle with a thin gray edge
    draw = ImageDraw.Draw(canvas)
    paper_x0 = COMPOSITE_PAPER_MARGIN_PX
    paper_y0 = COMPOSITE_PAPER_MARGIN_PX
    paper_x1 = paper_x0 + paper_w_px - 1
    paper_y1 = paper_y0 + paper_h_px - 1
    draw.rectangle(
        [paper_x0, paper_y0, paper_x1, paper_y1],
        fill=(*COMPOSITE_PAPER_COLOR, 255),
        outline=(*COMPOSITE_PAPER_BORDER, 255),
        width=1,
    )

    # Source image: resize + paste at absolute position in the sheet
    img_w_px = max(1, round(image_w_mm * scale))
    img_h_px = max(1, round(image_h_mm * scale))
    img_resized = img.resize((img_w_px, img_h_px), Image.Resampling.LANCZOS)
    img_x = paper_x0 + round(image_x_mm * scale)
    img_y = paper_y0 + round(image_y_mm * scale)
    canvas.paste(img_resized.convert("RGBA"), (img_x, img_y))

    # GE area overlay (freeglaz selective on the image)
    if gloss_enhancer:
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        _draw_dashed_rect(
            ImageDraw.Draw(overlay),
            (img_x, img_y, img_x + img_w_px - 1, img_y + img_h_px - 1),
            color=(*COMPOSITE_GE_COLOR, round(COMPOSITE_GE_ALPHA * 255)),
            dash=4, gap=3, width=1,
        )
        canvas = Image.alpha_composite(canvas, overlay)

    # "Printable area edge" dashed line for MANUAL_FEED only
    # (the 17.4 mm bottom asymmetry is the main educational info;
    # for ROLL it is 5 mm everywhere = too thin to stand out).
    if media_source == "MANUAL_FEED":
        bottom_limit_y = paper_y0 + round(
            (sheet_h_mm - MANUAL_FEED_BOTTOM_MARGIN_MM) * scale
        )
        if paper_y0 < bottom_limit_y < paper_y1:
            _draw_dashed_hline(
                ImageDraw.Draw(canvas),
                x0=paper_x0, x1=paper_x1, y=bottom_limit_y,
                color=(*COMPOSITE_MARGIN_HINT, 255),
                dash=4, gap=4, width=1,
            )

    _ensure_dir()
    out_path = thumbnail_path(jobacct5)
    try:
        canvas.convert("RGB").save(
            out_path, format="JPEG", quality=jpeg_quality, optimize=True,
        )
    except OSError:
        logger.exception("render_page_composite: JPEG write failed %s", out_path)
        return None
    logger.info(
        "render_page_composite: %s generated (%d bytes, sheet %.0f×%.0f mm, "
        "image @%.1f,%.1f %.0f×%.0f mm, gloss=%s, source=%s)",
        out_path.name, out_path.stat().st_size,
        sheet_w_mm, sheet_h_mm,
        image_x_mm, image_y_mm, image_w_mm, image_h_mm,
        gloss_enhancer, media_source,
    )
    return out_path


def _draw_dashed_hline(draw, *, x0, x1, y, color, dash, gap, width):
    """Horizontal dashed line from (x0, y) to (x1, y)."""
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=width)
        x += dash + gap


def _draw_dashed_vline(draw, *, x, y0, y1, color, dash, gap, width):
    """Vertical dashed line from (x, y0) to (x, y1)."""
    y = y0
    while y < y1:
        draw.line([(x, y), (x, min(y + dash, y1))], fill=color, width=width)
        y += dash + gap


def _draw_dashed_rect(draw, bbox, *, color, dash, gap, width):
    """4 dashed sides of a rectangle (top, bottom, left, right)."""
    x0, y0, x1, y1 = bbox
    _draw_dashed_hline(draw, x0=x0, x1=x1, y=y0, color=color,
                       dash=dash, gap=gap, width=width)
    _draw_dashed_hline(draw, x0=x0, x1=x1, y=y1, color=color,
                       dash=dash, gap=gap, width=width)
    _draw_dashed_vline(draw, x=x0, y0=y0, y1=y1, color=color,
                       dash=dash, gap=gap, width=width)
    _draw_dashed_vline(draw, x=x1, y0=y0, y1=y1, color=color,
                       dash=dash, gap=gap, width=width)


def delete_thumbnail(jobacct5: str) -> bool:
    """Delete the thumb if it exists. Return True if deleted.

    No error if the thumb does not exist — idempotent for the CLI
    cleanup.
    """
    path = thumbnail_path(jobacct5)
    if path.exists():
        path.unlink()
        return True
    return False


def list_thumbnails() -> list[Path]:
    """List all .jpg files in ``PREVIEWS_DIR``.

    Used by the CLI cleanup for stats and the --older-than scan.
    Returns ``[]`` if the folder does not exist yet.
    """
    if not PREVIEWS_DIR.exists():
        return []
    return sorted(PREVIEWS_DIR.glob("*.jpg"))
