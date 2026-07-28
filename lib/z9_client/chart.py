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

"""
464-patch profiling chart generator for the Z9.

Native migration of the Scripts/chart_render.py script into the lib.
Validated pixel-perfect at 96.32% against the native canonical chart.

Architecture:
    - Geometric constants (CX_00, PITCH_X, etc.) — invariants @ 300 dpi
    - Pure functions (compute_layout, render, hex_polygon, etc.)
    - ChartOps class: public API, generate() method and utilities

Dependencies: numpy, Pillow, tifffile.

The source ICC profile of the PDF/X-4 is CRITICAL:
the Z9 firmware reads its mathematical content and applies different
transformations depending on that content. The custom paper donor (retrievable
via paper.donor_export) is the reference profile producing the expected
rendering for that paper.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw


# ============================================================================
# INVARIANT geometric constants (300 dpi)
# Validated empirically on the Z9.
# ============================================================================

# Canonical position of patch r0c0 (center, in cropped PPM)
CX_00 = 263
CY_00 = 221

# Irregular pointy-top hexagon
HEX_V_HALF      = 56   # vertical half side
HEX_OBLIQUE_DX  = 89   # x component of the oblique side = half width
HEX_OBLIQUE_DY  = 43   # y component of the oblique side
HEX_HALF_HEIGHT = 99   # = HEX_V_HALF + HEX_OBLIQUE_DY

# Grid pitch (fractional)
PITCH_X = 177.20
PITCH_Y = 153.80
ODD_ROW_OFFSET = -89   # odd rows shifted to the left

# Primaries band
PRIMARY_BAND_HEIGHT = 124       # px (y ∈ [0, 124))
PRIMARY_BAND_CY = 61            # center y for the band skews

# Skew marks (pattern | \ \ |, height 91 px)
SKEW_LEFT_BARS = [
    ('vertical', 1,   17,  1,   17),
    ('oblique',  34,  49,  57,  72),
    ('oblique',  78,  93,  101, 116),
    ('vertical', 133, 149, 133, 149),
]
SKEW_WIDTH = 150

# Colors of the primaries band (canonical option A)
PRIMARY_COLORS_OPTION_A = [
    (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (255, 255, 0), (255, 0, 255), (0, 255, 255),
    (85, 85, 85), (170, 170, 170),
]

TOTAL_PATCHES = 464


# ============================================================================
# Paper presets for scaling toward the Native Z9 geometry
# ============================================================================
# The scaling is applied via the TIFF X DPI (anisotropic) without modifying
# the raster. Y stays at native 300 dpi — anisotropy CRITICAL so the
# Z9 finds the skew marks again in scan-only mode.

PAPER_TARGET_WIDTH_MM = {
    'a3':      277.0,   # A3 manual feed    : 0.948 × 292 mm (native geometry)
    '24inch':  585.0,   # 24" roll          : 0.941 × 622 mm (native geometry)
    'native':  None,    # no scaling, keeps 300 DPI (native dimensions)
}

# Mapping --cols → default paper preset
COLS_TO_PAPER_DEFAULT = {
    18: 'a3',
    40: '24inch',
}


# ============================================================================
# Pure functions — geometry and rendering
# ============================================================================


def compute_layout(num_cols: int) -> dict:
    """Compute chart dimensions and positions for a number of columns.

    :param num_cols: 18 for A3, 40 for 24" roll
    :return: dict with width, height, num_cols, nrows, n_empty, etc.
    """
    nrows = math.ceil(TOTAL_PATCHES / num_cols)
    n_empty = nrows * num_cols - TOTAL_PATCHES
    last_col_present = (TOTAL_PATCHES - 1) % num_cols

    # Width: aligned on the right edge of the last ODD hex + gap + right skew
    # (even rows have no skew on the right, so their position only determines
    # the useless right margin; W is fixed by the odd rows).
    last_odd_hex_right_edge = (
        (CX_00 + ODD_ROW_OFFSET) + (num_cols - 1) * PITCH_X + HEX_OBLIQUE_DX
    )
    width = int(round(last_odd_hex_right_edge + 24 + SKEW_WIDTH))

    # Primaries band bounds (aligned with the first and last hex of row 0)
    primary_band_x_left = CX_00 - HEX_OBLIQUE_DX
    primary_band_x_right = int(round(
        (CX_00 + ODD_ROW_OFFSET) + (num_cols - 1) * PITCH_X + HEX_OBLIQUE_DX
    ))

    # Right skew: translation = width - SKEW_WIDTH
    skew_right_translation = width - SKEW_WIDTH

    # Height: primaries_band + nrows × pitch_y + bottom margin
    last_cy = CY_00 + (nrows - 1) * PITCH_Y
    height = int(round(last_cy + HEX_HALF_HEIGHT))

    return {
        'num_cols': num_cols,
        'nrows': nrows,
        'n_empty': n_empty,
        'last_col_present': last_col_present,
        'width': width,
        'height': height,
        'primary_band_x_left': primary_band_x_left,
        'primary_band_x_right': primary_band_x_right,
        'skew_right_translation': skew_right_translation,
    }


def hex_polygon(cx: float, cy: float) -> list[tuple[int, int]]:
    """Return the 6 vertices of an irregular pointy-top hexagon."""
    return [
        (cx,                          cy - HEX_HALF_HEIGHT),
        (cx + HEX_OBLIQUE_DX,         cy - HEX_V_HALF),
        (cx + HEX_OBLIQUE_DX,         cy + HEX_V_HALF),
        (cx,                          cy + HEX_HALF_HEIGHT),
        (cx - HEX_OBLIQUE_DX,         cy + HEX_V_HALF),
        (cx - HEX_OBLIQUE_DX,         cy - HEX_V_HALF),
    ]


def patch_center(row: int, col: int) -> tuple[float, float]:
    """Center (x, y) of a patch according to its grid position."""
    x_off = ODD_ROW_OFFSET if (row % 2 == 1) else 0
    return CX_00 + col * PITCH_X + x_off, CY_00 + row * PITCH_Y


def patch_layout_iter(num_cols: int, nrows: int):
    """Iterate over the patches in logical row-major order.

    Skips positions beyond TOTAL_PATCHES (empty cells at the end
    of the last row).
    """
    for row in range(nrows):
        for col in range(num_cols):
            idx = row * num_cols + col
            if idx >= TOTAL_PATCHES:
                continue
            yield row, col, idx


def _draw_primary_band(draw, layout: dict, primary_colors: list) -> None:
    n = 9
    x_left = layout['primary_band_x_left']
    x_right = layout['primary_band_x_right']
    span = x_right - x_left + 1
    rect_w = span / n
    for i, color in enumerate(primary_colors):
        x0 = int(round(x_left + i * rect_w))
        x1 = int(round(x_left + (i + 1) * rect_w))
        draw.rectangle([x0, 0, x1 - 1, PRIMARY_BAND_HEIGHT - 1], fill=color)


def _draw_skew_marks_at_y(draw, layout: dict, cy: int, side: str) -> None:
    y_top = cy - 45
    y_bot = cy + 45
    translation = 0 if side == 'left' else layout['skew_right_translation']
    for bar_type, xtm, xtM, xbm, xbM in SKEW_LEFT_BARS:
        x_top_min = xtm + translation
        x_top_max = xtM + translation
        x_bot_min = xbm + translation
        x_bot_max = xbM + translation
        if bar_type == 'vertical':
            draw.rectangle([x_top_min, y_top, x_top_max, y_bot], fill=(0, 0, 0))
        else:
            draw.polygon(
                [(x_top_min, y_top), (x_top_max, y_top),
                 (x_bot_max, y_bot), (x_bot_min, y_bot)],
                fill=(0, 0, 0),
            )


def _draw_all_skew_marks(draw, layout: dict) -> None:
    _draw_skew_marks_at_y(draw, layout, PRIMARY_BAND_CY, 'left')
    _draw_skew_marks_at_y(draw, layout, PRIMARY_BAND_CY, 'right')
    for row in range(layout['nrows']):
        cy = CY_00 + row * PITCH_Y
        side = 'left' if (row % 2 == 0) else 'right'
        _draw_skew_marks_at_y(draw, layout, cy, side)


def render(
    patches: list[tuple],
    layout: dict,
    primary_colors: list = PRIMARY_COLORS_OPTION_A,
) -> np.ndarray:
    """Generate the 8-bit RGB raster of the chart.

    :param patches: list of TOTAL_PATCHES tuples (sample_id, r, g, b)
    :param layout: dict produced by compute_layout()
    :param primary_colors: colors of the primaries band (top of chart)

    :return: uint8 ndarray (H, W, 3) — RGB raster
    :raises ValueError: if patches does not have exactly TOTAL_PATCHES elements
    """
    if len(patches) != TOTAL_PATCHES:
        raise ValueError(
            f"Need {TOTAL_PATCHES} patches, got {len(patches)}"
        )
    img = Image.new('RGB', (layout['width'], layout['height']),
                    color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    _draw_primary_band(draw, layout, primary_colors)
    for row, col, idx in patch_layout_iter(layout['num_cols'], layout['nrows']):
        cx, cy = patch_center(row, col)
        _, r, g, b = patches[idx]
        draw.polygon(hex_polygon(cx, cy), fill=(r, g, b))
    _draw_all_skew_marks(draw, layout)
    return np.array(img)


# ============================================================================
# IO PPM / TIFF / ti1 / sidecar
# ============================================================================


def load_ppm(path: str | Path) -> np.ndarray:
    """Read a PPM P6 8-bit RGB → ndarray (H, W, 3)."""
    with open(path, 'rb') as f:
        f.readline()                      # P6
        line = f.readline()
        while line.startswith(b'#'):
            line = f.readline()
        w, h = map(int, line.split())
        f.readline()                      # 255
        return np.frombuffer(f.read(), dtype=np.uint8).reshape(h, w, 3)


def save_ppm(arr: np.ndarray, path: str | Path) -> None:
    """Write a raster as PPM P6 8-bit."""
    h, w = arr.shape[:2]
    with open(path, 'wb') as f:
        f.write(f"P6\n{w} {h}\n255\n".encode())
        f.write(np.ascontiguousarray(arr).tobytes())


def read_ti1(path: str | Path) -> list[tuple]:
    """Read an Argyll .ti1 file → list (sample_id, r8, g8, b8).

    The ti1 contains the RGB in percent (0..100) — we convert to
    8-bit (0..255) with correct rounding.

    :raises ValueError: if the file is malformed or COLOR_REP != RGB
    """
    with open(path, 'r', encoding='utf-8') as f:
        lines = [ln.rstrip('\n') for ln in f]

    fmt_start = fmt_end = data_start = data_end = None
    color_rep = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith('COLOR_REP') and color_rep is None:
            color_rep = s.split()[-1].strip('"')
        elif s == 'BEGIN_DATA_FORMAT' and fmt_start is None:
            fmt_start = i + 1
        elif s == 'END_DATA_FORMAT' and fmt_end is None:
            fmt_end = i
        elif s == 'BEGIN_DATA' and data_start is None:
            data_start = i + 1
        elif s == 'END_DATA' and data_end is None:
            data_end = i

    if None in (fmt_start, fmt_end, data_start, data_end):
        raise ValueError(".ti1: BEGIN/END sections missing")
    if color_rep is None or 'RGB' not in color_rep.upper():
        raise ValueError(f".ti1: COLOR_REP must be RGB (read: {color_rep})")

    header = []
    for ln in lines[fmt_start:fmt_end]:
        header.extend(ln.split())
    idx_s = header.index('SAMPLE_ID')
    idx_r = header.index('RGB_R')
    idx_g = header.index('RGB_G')
    idx_b = header.index('RGB_B')

    samples = []
    for ln in lines[data_start:data_end]:
        toks = ln.split()
        if not toks or toks[0].startswith('#'):
            continue
        try:
            sid = toks[idx_s]
            r = int(round(float(toks[idx_r]) * 2.55))
            g = int(round(float(toks[idx_g]) * 2.55))
            b = int(round(float(toks[idx_b]) * 2.55))
        except (IndexError, ValueError):
            continue
        samples.append((sid, max(0, min(255, r)),
                              max(0, min(255, g)),
                              max(0, min(255, b))))
    return samples


def demo_patches(canonical: Optional[np.ndarray] = None, num_cols: int = 18) -> list[tuple]:
    """Generate a set of 464 demo patches (cube 7³ + ramp).

    :param canonical: canonical PPM to sample the last 121 patches
                      in the flesh/ochre tones (HP mode). If None, synthetic
                      linear R=G=B ramp 0..255.
    :param num_cols: for the canonical sampling (only 18 cols supported)
    """
    patches = []
    levels = [0, 42, 85, 127, 170, 212, 255]
    for r in levels:
        for g in levels:
            for b in levels:
                patches.append((f"cube_{len(patches)+1}", r, g, b))

    if canonical is not None and num_cols == 18:
        H, W = canonical.shape[:2]
        for row in range(19, 26):
            for col in range(18):
                idx = row * 18 + col
                if idx < 343 or idx >= TOTAL_PATCHES:
                    continue
                x_off = ODD_ROW_OFFSET if (row % 2 == 1) else 0
                cx = CX_00 + col * PITCH_X + x_off
                cy = CY_00 + row * PITCH_Y
                cxi, cyi = int(round(cx)), int(round(cy))
                if not (4 <= cxi < W-4 and 4 <= cyi < H-4):
                    patches.append((f"ramp_{len(patches)+1}", 255, 255, 255))
                    continue
                patch = canonical[cyi-4:cyi+5, cxi-4:cxi+5]
                color = tuple(int(c) for c in np.round(patch.mean(axis=(0, 1))))
                patches.append((f"ramp_{len(patches)+1}", *color))
    else:
        for i in range(121):
            v = round(i * 255 / 120)
            patches.append((f"ramp_{i+1}", v, v, v))

    return patches[:TOTAL_PATCHES]


def write_sidecar_json(
    patches: list[tuple],
    output_path: str | Path,
    layout: dict,
) -> Path:
    """Write the JSON sidecar next to the produced TIFF/PPM.

    The sidecar contains the logical index → row/col/RGB mapping used
    later by remap_ti3 (post-scan, to re-inject the actual RGB
    sent into the firmware ti3).

    :return: path of the written sidecar
    """
    output_path = Path(output_path)
    sidecar = output_path.with_suffix('').as_posix() + '_sidecar.json'
    meta = {
        'output': output_path.name,
        'layout': layout,
        'patches_in_layout_order': [
            {
                'row': row, 'col': col, 'index': idx,
                'sample_id': patches[idx][0],
                'rgb': [patches[idx][1], patches[idx][2], patches[idx][3]],
            }
            for row, col, idx in patch_layout_iter(layout['num_cols'], layout['nrows'])
        ],
        'geometry_constants_at_300dpi': {
            'patch_center_r0c0': {'x': CX_00, 'y': CY_00},
            'pitch_x_px': PITCH_X,
            'pitch_y_px': PITCH_Y,
            'odd_row_offset_px': ODD_ROW_OFFSET,
            'hex_v_half': HEX_V_HALF,
            'hex_oblique_dx': HEX_OBLIQUE_DX,
            'hex_oblique_dy': HEX_OBLIQUE_DY,
        },
    }
    with open(sidecar, 'w') as f:
        json.dump(meta, f, indent=2)
    return Path(sidecar)


def save_tiff(
    arr8: np.ndarray,
    path: str | Path,
    reference_icc_path: str | Path,
    paper_preset: str = 'native',
    also_ppm: bool = False,
) -> dict:
    """Write a 16-bit freeglaz-ready TIFF.

    The generated TIFF is consumed directly by `freeglaz print`:
      - DPI computed to encode the scaling toward the native Z9 geometry without touching the raster
      - source ICC embedded (given as a parameter — typically the paper
        donor, retrieved via paper.donor_export)
      - 16-bit pixels (×257 from 8-bit, full range 0..65535 preserved)
      - No pixel reinterpolation: 1 px PPM = 1 px TIFF

    The freeglaz pipeline embeds this ICC as BOTH the image colorspace and the
    OutputIntent, so the APPE stays transparent. The RGB arrive intact because
    this embedded profile IS the resident (device passthrough: the firmware
    decodes device→ink via the OutputIntent profile) — not merely because
    image == OutputIntent.

    Anisotropic DPI: X encodes the Native Z9 scaling, Y stays at native 300.
    This anisotropy is CRITICAL so the Z9 finds the skew marks
    of the chart again in scan-only mode.

    :param arr8: uint8 ndarray (H, W, 3) — native PPM raster
    :param path: output path (.tif)
    :param reference_icc_path: path to the source ICC profile to embed
    :param paper_preset: 'a3' / '24inch' / 'native'
    :param also_ppm: if True, also save an 8-bit PPM next to it for comparison

    :return: dict with dpi_x, dpi_y, width_mm, height_mm, icc_size_bytes,
             tiff_size_bytes
    :raises FileNotFoundError: if reference_icc_path does not exist
    :raises ImportError: if tifffile is not installed
    """
    try:
        import tifffile
    except ImportError as e:
        raise ImportError(
            "tifffile is required to produce a TIFF. "
            "Install it: pip install tifffile"
        ) from e

    reference_icc_path = Path(reference_icc_path)
    if not reference_icc_path.exists():
        raise FileNotFoundError(
            f"Reference ICC profile missing: {reference_icc_path}"
        )

    with open(reference_icc_path, 'rb') as f:
        icc = f.read()

    h, w = arr8.shape[:2]

    # Upscale 8 → 16 bits by ×257 (preserves full range 0..65535)
    arr16 = arr8.astype(np.uint16) * 257

    # Anisotropic DPI: X encodes the Native Z9 scaling, Y stays at native 300
    target_w_mm = PAPER_TARGET_WIDTH_MM.get(paper_preset)
    if target_w_mm is None:
        dpi_x = 300.0
    else:
        dpi_x = w * 25.4 / target_w_mm
    dpi_y = 300.0

    width_mm = w * 25.4 / dpi_x
    height_mm = h * 25.4 / dpi_y

    tifffile.imwrite(
        path,
        arr16,
        photometric='rgb',
        resolution=(dpi_x, dpi_y),
        resolutionunit='INCH',
        extratags=[(34675, 'B', len(icc), icc, False)],  # ICCProfile tag
        compression='none',
    )

    info = {
        'path': str(path),
        'tiff_size_bytes': os.path.getsize(path),
        'width_px': w,
        'height_px': h,
        'dpi_x': dpi_x,
        'dpi_y': dpi_y,
        'width_mm': width_mm,
        'height_mm': height_mm,
        'paper_preset': paper_preset,
        'icc_path': str(reference_icc_path),
        'icc_size_bytes': len(icc),
    }

    if also_ppm:
        ppm_path = str(path).rsplit('.', 1)[0] + '.ppm'
        save_ppm(arr8, ppm_path)
        info['ppm_path'] = ppm_path
        info['ppm_size_bytes'] = os.path.getsize(ppm_path)

    return info


# ============================================================================
# ChartOps class — public API
# ============================================================================


@dataclass
class ChartGenerateResult:
    """Result of ChartOps.generate()."""
    output_path: str
    sidecar_path: str
    width_px: int
    height_px: int
    width_mm: float
    height_mm: float
    dpi_x: float
    dpi_y: float
    num_cols: int
    nrows: int
    n_empty: int
    paper_preset: str
    icc_path: str
    icc_size_bytes: int
    output_format: str
    n_patches: int


class ChartOps:
    """
    Operations on the Z9 profiling charts (464 patches).

    Usable in two ways:
      - directly (without Z9Client) for purely local operations
        (offline generation from a ti1, without printer access)
      - via Z9Client for integrated workflows (uses the Z9Client.cache
        donor cache to auto-resolve the source ICC profile)
    """

    def __init__(self, z9_client=None):
        """
        :param z9_client: Z9Client instance (optional). Gives access
                          to the donor cache to resolve --source auto.
        """
        self._client = z9_client

    # ─── Main method ─────────────────────────────────────────

    def generate(
        self,
        *,
        ti1_path: Optional[str | Path] = None,
        output_path: str | Path,
        reference_icc_path: str | Path,
        num_cols: int = 18,
        paper_preset: str = 'auto',
        output_format: str = 'tiff',
        demo_mode: str = 'auto',
        canonical_ppm_path: Optional[str | Path] = None,
        write_sidecar: bool = True,
        also_ppm: bool = False,
        on_step=None,
    ) -> ChartGenerateResult:
        """Generate a 464-patch chart + JSON sidecar.

        :param ti1_path: path to an Argyll .ti1 file (optional).
                        If None, uses demo_patches() (cube 7³ + ramp).
        :param output_path: output path (.tif or .ppm depending on format)
        :param reference_icc_path: source ICC profile embedded in the TIFF.
                        TYPICALLY the paper donor. No default
                        value — must be provided.
        :param num_cols: 18 (A3) or 40 (24" roll) usually
        :param paper_preset: 'auto' | 'a3' | '24inch' | 'native'
                        If 'auto': deduced from num_cols (18→a3, 40→24inch)
        :param output_format: 'tiff' (default) or 'ppm'
        :param demo_mode: 'auto' | 'linear' | 'canonical' — only
                        used if ti1_path=None
        :param canonical_ppm_path: canonical PPM to sample the ramp in
                        demo mode (optional, num_cols=18 only)
        :param write_sidecar: if True, write the JSON sidecar
        :param also_ppm: if True and format=tiff, also write a debug PPM
        :param on_step: callback(step, total, label, **details) for tracing

        :return: ChartGenerateResult with all the info of the produced file

        :raises FileNotFoundError: if reference_icc_path or ti1 absent
        :raises ValueError: if invalid format, malformed ti1, or num_cols
                            incompatible with canonical
        """
        def _step(n, total, label, **details):
            if on_step:
                on_step(n, total, label, **details)

        output_path = Path(output_path)
        reference_icc_path = Path(reference_icc_path)

        if not reference_icc_path.exists():
            raise FileNotFoundError(
                f"Reference ICC profile missing: {reference_icc_path}"
            )

        if output_format not in ('tiff', 'ppm'):
            raise ValueError(
                f"invalid output_format: {output_format!r} "
                f"(expected 'tiff' or 'ppm')"
            )

        # 1. Resolve paper_preset auto
        if paper_preset == 'auto':
            paper_preset = COLS_TO_PAPER_DEFAULT.get(num_cols, 'native')

        # 2. Compute the layout
        layout = compute_layout(num_cols)
        _step(1, 4, 'layout-computed',
              num_cols=num_cols, nrows=layout['nrows'],
              n_empty=layout['n_empty'],
              width=layout['width'], height=layout['height'])

        # 3. Patch selection (ti1 provided vs demo)
        if ti1_path is not None:
            ti1_path = Path(ti1_path)
            if not ti1_path.exists():
                raise FileNotFoundError(f"ti1 not found: {ti1_path}")
            patches = read_ti1(ti1_path)
            if len(patches) != TOTAL_PATCHES:
                raise ValueError(
                    f"The .ti1 must have {TOTAL_PATCHES} patches, "
                    f"found {len(patches)} in {ti1_path}"
                )
            _step(2, 4, 'ti1-loaded',
                  ti1_path=str(ti1_path), n_patches=len(patches))
        else:
            canonical = None
            if demo_mode == 'canonical':
                if canonical_ppm_path is None or not Path(canonical_ppm_path).exists():
                    raise ValueError(
                        f"demo_mode='canonical' requested but canonical_ppm_path "
                        f"missing or not found: {canonical_ppm_path}"
                    )
                canonical = load_ppm(canonical_ppm_path)
            elif demo_mode == 'auto':
                if canonical_ppm_path and Path(canonical_ppm_path).exists() and num_cols == 18:
                    canonical = load_ppm(canonical_ppm_path)
            # demo_mode='linear': canonical stays None → synthetic ramp
            patches = demo_patches(canonical=canonical, num_cols=num_cols)
            _step(2, 4, 'demo-patches',
                  demo_mode=demo_mode,
                  used_canonical=(canonical is not None),
                  n_patches=len(patches))

        # 4. Raster rendering
        raster = render(patches, layout)
        _step(3, 4, 'rendered',
              shape=raster.shape)

        # 5. Writing
        if output_format == 'tiff':
            info = save_tiff(
                raster, output_path,
                reference_icc_path=reference_icc_path,
                paper_preset=paper_preset,
                also_ppm=also_ppm,
            )
        else:
            save_ppm(raster, output_path)
            h, w = raster.shape[:2]
            info = {
                'path': str(output_path),
                'tiff_size_bytes': os.path.getsize(output_path),  # PPM in fact
                'width_px': w,
                'height_px': h,
                'dpi_x': 300.0,
                'dpi_y': 300.0,
                'width_mm': w * 25.4 / 300.0,
                'height_mm': h * 25.4 / 300.0,
                'paper_preset': paper_preset,
                'icc_path': str(reference_icc_path),
                'icc_size_bytes': reference_icc_path.stat().st_size,
            }

        sidecar_path = None
        if write_sidecar:
            sidecar_path = write_sidecar_json(patches, output_path, layout)

        _step(4, 4, 'written',
              output_path=str(output_path),
              size_bytes=info['tiff_size_bytes'],
              sidecar_path=str(sidecar_path) if sidecar_path else None)

        return ChartGenerateResult(
            output_path=str(output_path),
            sidecar_path=str(sidecar_path) if sidecar_path else '',
            width_px=info['width_px'],
            height_px=info['height_px'],
            width_mm=info['width_mm'],
            height_mm=info['height_mm'],
            dpi_x=info['dpi_x'],
            dpi_y=info['dpi_y'],
            num_cols=num_cols,
            nrows=layout['nrows'],
            n_empty=layout['n_empty'],
            paper_preset=paper_preset,
            icc_path=info['icc_path'],
            icc_size_bytes=info['icc_size_bytes'],
            output_format=output_format,
            n_patches=len(patches),
        )

    # ─── Helpers exposed for external uses ───────────────────────

    @staticmethod
    def compute_layout(num_cols: int) -> dict:
        """Wrapper on compute_layout(). See module-level."""
        return compute_layout(num_cols)

    @staticmethod
    def read_ti1(path: str | Path) -> list[tuple]:
        """Wrapper on read_ti1(). See module-level."""
        return read_ti1(path)

    @staticmethod
    def load_ppm(path: str | Path) -> np.ndarray:
        """Wrapper on load_ppm(). See module-level."""
        return load_ppm(path)

    @staticmethod
    def save_ppm(arr: np.ndarray, path: str | Path) -> None:
        """Wrapper on save_ppm(). See module-level."""
        return save_ppm(arr, path)

    @staticmethod
    def demo_patches(
        canonical: Optional[np.ndarray] = None,
        num_cols: int = 18,
    ) -> list[tuple]:
        """Wrapper on demo_patches(). See module-level."""
        return demo_patches(canonical=canonical, num_cols=num_cols)
