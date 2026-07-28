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

"""PROPOSAL (refonte-render-io branch) — render + IO 100% rework.

⚠️ NOT ADOPTED. Rendering path ENTIRELY based on the reworked geometry
(`chart_geometry_refonte`) — no dependency on the px constants / on the render of
`chart.py`. Goal: produce a printable chart whose geometry is 100%
reworked, so that the refinement pass really validates the new generator.

Path:  reworked geometry (mm, columns as input, patch/pitch derived)
       →  ISOTROPIC mm→px render (a single dpi, no more anisotropic DPI tag)
       →  16-bit TIFF IO + embedded resident ICC + sidecar (sample_id ↔ RGB).

INTENDED differences vs chart.py (the rest is superposable, ΔX ≤ 0.03 mm):
  - isotropic render (1 dpi X=Y) instead of the anisotropic DPI tag;
  - skew/fiducial marks at the OFFICIAL dimension (block width 12.80 mm,
    SolConstraints FiducialMarks) instead of ~12.05 mm compressed. The proven |\\|
    pattern (chart.py) is kept, just scaled to the official isotropic scale.
    ⚠️ The official fiducials are NOT physically validated (Phase B) — the
    pass serves as a first test. Bar thickness derived from the pattern ≈ 1.365 mm
    (official nominal 1.40; chart.py pattern ratio = official within ~2.5%).

The emitted sidecar follows the `patches_in_layout_order` format (index / sample_id /
rgb) — compatible with the SAMPLE_ID remap safeguard (garde-fou-remap branch).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import chart_geometry_refonte as G

# ── Render constants (copied, no import from the chart.py render) ──────────
PRIMARY_COLORS_OPTION_A = [
    (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (255, 255, 0), (255, 0, 255), (0, 255, 255),
    (85, 85, 85), (170, 170, 170),
]

# Pointy-top hexagon proportions (dimensionless, from chart.py: 56/99).
_HEX_VHALF_RATIO = 56.0 / 99.0     # v_half / half_height

# Primaries band: native height @300 dpi (never compressed) → mm.
PRIMARY_BAND_MM = 124.0 * 25.4 / 300.0     # ≈ 10.50 mm
PRIMARY_BAND_CY_MM = 61.0 * 25.4 / 300.0   # ≈ 5.16 mm (center y of the band marks)

# ── PARAMETRIC alignment mark (native specification) ──────────────────
# 4 bars: 2 vertical (outer) + 2 oblique (inner) with slope 4/1.
# Guide rules: thickness ε, height δ, mid-height separation τ = 1.5·ε + δ/8,
# center-to-center width of the outer bars Δ = 3·τ. Overall width = Δ + ε.
#
# ε = 1.78 mm: value CORROBORATED by the native geometry —
#   Aligner width 12.78 mm with δ 7.96 mm ⟹ ε = (12.78 − 3·δ/8)/5.5 ≈ 1.78.
#   ≥ 1.45 mm (guide minimum). 1.45 bare would give a ~11 mm mark (≠ native ref).
EPS_MM = 1.78
# δ: alignment mark height = ABSOLUTE (INVARIANT of the format).
#   CORRECTION (Phase 4). The old formula δ = 6 + σ·cols·w + margin made
#   δ GROW with the chart width — reading of the spec "δ ≥ 6 + σ·n·w" taken
#   as a VALUE whereas it is a MINIMUM. On a full-width roll (585 mm, 36 cols)
#   δ exploded to 11.37 mm (+48% vs ref) → the firmware validator rejects the mark
#   ("Invalid alignment mark size") → registration scan failed (OP_CANCELLED), while
#   the narrow A4 formats (δ≈8.2 mm) passed.
#   PROOF that δ is ABSOLUTE (Phase 2, measured): skew of the native 464 24"
#   roll chart ≈ 7.8 mm by the ruler — IDENTICAL to the narrow formats (ref 91 px@300 dpi
#   = 7.70 mm; native ref ≈ 7.93 mm) despite 585 mm of width. The mark is NOT
#   scaled with the width. → δ FIXED. (Old: SKEW_SIGMA=0.008, DELTA_BASE=6.0, margin=0.85.)
SKEW_HEIGHT_MM = 91 * 25.4 / 300.0   # ≈ 7.70 mm — ref 91 px@300 dpi (native 24" measured ≈7.8)
MARK_DELTA_FALLBACK_MM = SKEW_HEIGHT_MM   # consistency: the default == the fixed value
_OBLIQUE_SLOPE = 4.0                 # slope 4/1 (horizontal shift = δ/slope over the height δ)


def mark_delta_mm(cols: int, patch_w_mm: float) -> float:
    """Alignment mark height — ABSOLUTE, format-invariant (cf. SKEW_HEIGHT_MM).
    The params (cols, patch_w_mm) are kept for call compat but IGNORED: the
    HP native does not scale the mark with the width (24" roll measured ≈7.8 mm =
    narrow formats). Fixes the banding-independent OP_CANCELLED of the roll path."""
    return SKEW_HEIGHT_MM


def _px(mm: float, dpi: float) -> float:
    return mm * dpi / 25.4


def _hex_vertices_mm(cx: float, cy: float, half_w: float, half_h: float):
    """6 vertices (mm) of an irregular pointy-top hexagon centered at (cx, cy)."""
    v_half = half_h * _HEX_VHALF_RATIO
    return [
        (cx,          cy - half_h),
        (cx + half_w, cy - v_half),
        (cx + half_w, cy + v_half),
        (cx,          cy + half_h),
        (cx - half_w, cy + v_half),
        (cx - half_w, cy - v_half),
    ]


def mark_width_mm(eps_mm: float = EPS_MM, delta_mm: float = MARK_DELTA_FALLBACK_MM) -> float:
    """Overall width of a mark = Δ + ε = 3·(1.5ε + δ/8) + ε = 5.5ε + 3δ/8."""
    tau = 1.5 * eps_mm + delta_mm / 8.0
    return 3.0 * tau + eps_mm


def _draw_mark_block(draw, block_x0_mm: float, cy_mm: float, dpi: float,
                     eps_mm: float = EPS_MM, delta_mm: float = MARK_DELTA_FALLBACK_MM):
    """Conforming parametric alignment mark: 4 bars (2 vertical + 2
    oblique slope 4/1), thickness ε, height δ, separation τ=1.5ε+δ/8, Δ=3τ.
    ``block_x0_mm`` = LEFT overall edge of the mark; vertically centered at cy."""
    tau = 1.5 * eps_mm + delta_mm / 8.0
    y_top = cy_mm - delta_mm / 2.0
    y_bot = cy_mm + delta_mm / 2.0
    half = eps_mm / 2.0
    shift = delta_mm / _OBLIQUE_SLOPE / 2.0   # ± shift of the top/bottom center (total δ/slope)
    # centers (mid-height) of the 4 bars: left overall edge = block_x0
    centers = [block_x0_mm + half + i * tau for i in range(4)]
    for i, c in enumerate(centers):
        if i in (0, 3):   # outer bars: vertical
            draw.rectangle(
                [_px(c - half, dpi), _px(y_top, dpi), _px(c + half, dpi), _px(y_bot, dpi)],
                fill=(0, 0, 0))
        else:             # inner bars: oblique (/) slope 4/1
            ct, cb = c - shift, c + shift   # top center shifted left, bottom right
            draw.polygon(
                [(_px(ct - half, dpi), _px(y_top, dpi)),
                 (_px(ct + half, dpi), _px(y_top, dpi)),
                 (_px(cb + half, dpi), _px(y_bot, dpi)),
                 (_px(cb - half, dpi), _px(y_bot, dpi))],
                fill=(0, 0, 0))


def render_refonte(patches: list[tuple], layout: G.Layout, dpi: float = 300.0) -> np.ndarray:
    """8-bit RGB raster of the chart, 100% reworked geometry, ISOTROPIC render.

    :param patches: list of tuples (sample_id, r, g, b), LOGICAL order (row-major)
    :param layout: Layout from chart_geometry_refonte.compute_layout()
    :param dpi: isotropic resolution (X=Y)
    """
    n_expected = layout.cols * layout.nrows - layout.n_empty
    if len(patches) != n_expected:
        raise ValueError(
            f"render_refonte: {len(patches)} patches received, {n_expected} expected "
            f"({layout.cols}×{layout.nrows} − {layout.n_empty} empty)."
        )

    w_px = int(round(_px(layout.chart_w_mm, dpi)))
    h_px = int(round(_px(layout.chart_h_mm, dpi)))
    img = Image.new('RGB', (w_px, h_px), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)

    half_w = layout.patch_w_mm / 2.0
    half_h = layout.patch_h_mm / 2.0
    fp_x, fp_y = layout.first_patch_mm
    delta = mark_delta_mm(layout.cols, layout.patch_w_mm)  # δ = f(width), corr. 3
    mark_w = mark_width_mm(delta_mm=delta)      # overall width DERIVED (5.5ε+3δ/8)
    left_x0 = 0.0
    right_x0 = layout.chart_w_mm - mark_w

    # 1. Primaries band (top): 9 rects over the x span of the grid
    band_x_left = fp_x - half_w
    band_x_right = fp_x + layout._odd_off_mm + (layout.cols - 1) * layout.pitch_x_mm + half_w
    span = band_x_right - band_x_left
    for i, color in enumerate(PRIMARY_COLORS_OPTION_A):
        x0 = band_x_left + i * span / 9.0
        x1 = band_x_left + (i + 1) * span / 9.0
        draw.rectangle([_px(x0, dpi), 0, _px(x1, dpi) - 1, _px(PRIMARY_BAND_MM, dpi) - 1],
                       fill=color)

    # 2. Hexagons (bees-nest: odd rows shifted)
    idx = 0
    for row in range(layout.nrows):
        for col in range(layout.cols):
            if idx >= len(patches):
                break
            cx, cy = layout.patch_center_mm(row, col)
            _, r, g, b = patches[idx]
            verts = [(_px(x, dpi), _px(y, dpi)) for x, y in
                     _hex_vertices_mm(cx, cy, half_w, half_h)]
            draw.polygon(verts, fill=(int(r), int(g), int(b)))
            idx += 1

    # 3. Skew/fiducial marks.
    # Header (offline): marks at EXACTLY one row-pitch above the 1st
    # row (§7.5: "header→row 1 distance = distance of the other rows").
    # Fixes the old placement at PRIMARY_BAND_CY_MM (5.165) which was at 13.55 mm
    # from row 1 instead of pitch_y (13.02) → 0.52 mm too far.
    header_cy = fp_y - layout.pitch_y_mm
    _draw_mark_block(draw, left_x0, header_cy, dpi, delta_mm=delta)
    _draw_mark_block(draw, right_x0, header_cy, dpi, delta_mm=delta)
    for row in range(layout.nrows):
        cy = fp_y + row * layout.pitch_y_mm
        x0 = left_x0 if (row % 2 == 0) else right_x0
        _draw_mark_block(draw, x0, cy, dpi, delta_mm=delta)

    return np.array(img)


# TrueType fonts with full coverage (accents: FR paper names); fallback
# on the embedded Pillow font (portable, but limited glyphs).
_FOOTER_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
)


def _load_footer_font(size_px: int):
    for path in _FOOTER_FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size_px)
            except OSError:
                continue
    return ImageFont.load_default(size=size_px)   # portable (limited glyphs)


def render_footer_band(chart_arr8: np.ndarray, *, text: str, dpi: float = 300.0,
                       footer_h_mm: float = 10.0, clearance_mm: float = 2.0,
                       left_margin_mm: float = 6.0) -> np.ndarray:
    """Add a FOOTER band (horizontal text) BELOW the chart, to identify the
    sheet physically (offline workflow). PURE post-processing: the chart zone
    (patches/marks, pixels [0:h]) is left STRICTLY intact → scanLayout and
    placement (derived from the Layout, not the raster) unchanged.

    The footer lives in the low NON-scanned margin (the scanner measures the patches;
    the footer is after). Simple horizontal text (no vertical lines near the
    marks — the footer is outside the scanned zone). Scalable Pillow font (portable).
    """
    h, w = chart_arr8.shape[:2]
    footer_px = int(round(_px(footer_h_mm, dpi)))
    out = np.full((h + footer_px, w, 3), 255, dtype=np.uint8)
    out[:h] = chart_arr8                                  # chart INTACT
    img = Image.fromarray(out)
    draw = ImageDraw.Draw(img)

    # font: ~45% of the band height, shrunk if the text overflows in width
    left_px = int(round(_px(left_margin_mm, dpi)))
    max_text_w = w - 2 * left_px
    size = max(8, int(round(_px(footer_h_mm * 0.45, dpi))))
    font = _load_footer_font(size)
    while size > 8 and draw.textlength(text, font=font) > max_text_w:
        size -= 2
        font = _load_footer_font(size)

    clearance_px = int(round(_px(clearance_mm, dpi)))
    text_top = h + clearance_px
    draw.text((left_px, text_top), text, fill=(0, 0, 0), font=font)
    return np.array(img)


def save_tiff_refonte(arr8: np.ndarray, path: Path, reference_icc_path: Path,
                      dpi: float = 300.0) -> dict:
    """Write a 16-bit TIFF, ISOTROPIC dpi (X=Y), embedded resident ICC."""
    import tifffile
    reference_icc_path = Path(reference_icc_path)
    if not reference_icc_path.exists():
        raise FileNotFoundError(f"Reference ICC missing: {reference_icc_path}")
    icc = reference_icc_path.read_bytes()
    h, w = arr8.shape[:2]
    arr16 = arr8.astype(np.uint16) * 257
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(
        path, arr16, photometric='rgb',
        resolution=(dpi, dpi), resolutionunit='INCH',
        extratags=[(34675, 'B', len(icc), icc, False)],  # ICCProfile
        compression='none',
    )
    return {
        'path': str(path), 'tiff_size_bytes': os.path.getsize(path),
        'width_px': w, 'height_px': h, 'dpi_x': dpi, 'dpi_y': dpi,
        'width_mm': w * 25.4 / dpi, 'height_mm': h * 25.4 / dpi,
        'icc_path': str(reference_icc_path), 'icc_size_bytes': len(icc),
    }


def write_sidecar_refonte(patches: list[tuple], layout: G.Layout, output_path: Path,
                          dpi: float, extra: Optional[dict] = None) -> Path:
    """JSON sidecar in `patches_in_layout_order` format (index/sample_id/rgb)."""
    output_path = Path(output_path)
    sidecar_path = output_path.with_suffix('').as_posix() + '_sidecar.json'
    rows = []
    idx = 0
    for row in range(layout.nrows):
        for col in range(layout.cols):
            if idx >= len(patches):
                break
            sid, r, g, b = patches[idx]
            rows.append({'row': row, 'col': col, 'index': idx,
                         'sample_id': sid, 'rgb': [int(r), int(g), int(b)]})
            idx += 1
    meta = {
        'output': output_path.name,
        'generator': 'chart_render_refonte (100% rework, NOT adopted)',
        'render': 'isotropic',
        'dpi': dpi,
        'layout': {
            'cols': layout.cols, 'nrows': layout.nrows, 'n_empty': layout.n_empty,
            'media': layout.media.name, 'source': layout.media.source,
            'chart_w_mm': layout.chart_w_mm, 'chart_h_mm': layout.chart_h_mm,
            'pitch_x_mm': layout.pitch_x_mm, 'pitch_y_mm': layout.pitch_y_mm,
            'patch_w_mm': layout.patch_w_mm, 'patch_h_mm': layout.patch_h_mm,
            'first_patch_mm': list(layout.first_patch_mm),
            'fiducial_thick_mm': layout.fiducial_mm[0],
            'fiducial_width_mm': layout.fiducial_mm[1],
            'scanlayout_emu': layout.scanlayout_emu,
        },
        'patches_in_layout_order': rows,
    }
    if extra:
        meta.update(extra)
    Path(sidecar_path).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + '\n')
    return Path(sidecar_path)


@dataclass
class RefonteGenerateResult:
    tiff_path: str
    sidecar_path: str
    preview_path: str
    width_px: int
    height_px: int
    width_mm: float
    height_mm: float
    dpi: float
    cols: int
    nrows: int
    n_patches: int
    icc_path: str


def generate_refonte(*, patches: list[tuple], media_key: str, columns: int,
                     reference_icc_path: Path, output_tiff: Path,
                     dpi: float = 300.0, preview_max_px: int = 1400,
                     extra_sidecar: Optional[dict] = None,
                     footer_text: Optional[str] = None,
                     footer_h_mm: float = 10.0) -> RefonteGenerateResult:
    """Orchestrate reworked geometry → isotropic render → [ID footer] → TIFF+ICC → sidecar
    → PNG preview. `footer_text` (optional) = ID rendered in the footer (NON-scanned zone,
    below the patches); does NOT alter the patches/marks zone nor the scanLayout
    (derived from the Layout, not the raster)."""
    media = G.MEDIA[media_key]
    layout = G.compute_layout(media, patch_count=len(patches), columns=columns)
    raster = render_refonte(patches, layout, dpi=dpi)
    if footer_text:
        raster = render_footer_band(raster, text=footer_text, dpi=dpi,
                                    footer_h_mm=footer_h_mm)
    info = save_tiff_refonte(raster, Path(output_tiff), Path(reference_icc_path), dpi=dpi)
    sidecar = write_sidecar_refonte(patches, layout, Path(output_tiff), dpi, extra=extra_sidecar)

    # PNG preview (downscale) for the GUI / review
    img = Image.fromarray(raster)
    scale = min(1.0, preview_max_px / max(img.size))
    if scale < 1.0:
        img = img.resize((max(1, int(img.size[0] * scale)),
                          max(1, int(img.size[1] * scale))), Image.NEAREST)
    preview_path = Path(output_tiff).with_suffix('').as_posix() + '_preview.png'
    img.save(preview_path)

    return RefonteGenerateResult(
        tiff_path=info['path'], sidecar_path=str(sidecar), preview_path=preview_path,
        width_px=info['width_px'], height_px=info['height_px'],
        width_mm=info['width_mm'], height_mm=info['height_mm'], dpi=dpi,
        cols=layout.cols, nrows=layout.nrows, n_patches=len(patches),
        icc_path=info['icc_path'],
    )
