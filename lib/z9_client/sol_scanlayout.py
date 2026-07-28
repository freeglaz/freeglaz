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

"""Link 5 — FREE (parametric) scanLayout generator for the native SOL channel.

Produces the text body of the `POST /Colorimetry/Scan` (cf `sol_native`) for a chart
of **ANY N patches / N columns** — no magic number, no 464
assumption. Carries the EMU semantics **verified 4/4 at milestone 3** (`scanlayout.py`
wrapper, reference):
  - FirstPatch = CENTER of the 1st patch (row 0, col 0), ABSOLUTE from the media
    edges (ZeroReference: MediaEdges) = placement offset + center in the image;
  - Delta = center-to-center step (x within the row, y between rows);
  - Spacing (= SkewMarksToPatch_X) = horizontal distance CENTER of skew mark
    -> CENTER of 1st patch = (CX_00 − mark_center) × mm/px (the field that
    `scanlayout_emu` of chart_geometry_refonte did not yet provide).

Source geometry = the canonical 300 dpi space of `chart.py` (the same as the 464
reference chart actually printed/scanned at milestone 2-3). **Anisotropic X scaling**
(target paper width) / native Y 300 dpi. Replicated constants (no heavy import),
identical to `chart.py` / to the wrapper.

GENERALIST spirit (cf feedback): `num_cols`/`patch_count` are PARAMETERS;
the composition (RGB) is not this module's concern. Output units = INCHES, format
`%g` (6 significant digits, native format).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ── Canonical 300 dpi space (chart.py / scanlayout.py wrapper) ──────────────
CX_00 = 263            # patch r0c0 center, x (px @300 dpi)
CY_00 = 221            # patch r0c0 center, y
PITCH_X = 177.20       # horizontal center-to-center step within a row
PITCH_Y = 153.80       # vertical step between rows
ODD_ROW_OFFSET = -89   # x offset of odd rows (bees-nest)
HEX_OBLIQUE_DX = 89    # hexagon half-width
HEX_HALF_HEIGHT = 99   # hexagon half-height
SKEW_WIDTH = 150       # width of the skew mark zone
SKEW_BAR_MIN_X = 1     # bar edges within the zone (center = (min+max)/2)
SKEW_BAR_MAX_X = 149
RIGHT_PAD = 24         # margin before the right skew

EMU_PER_MM = 36000.0
EMU_PER_INCH = 914400.0     # 25.4 mm × 36000
DPI_Y = 300.0

# Mechanical margins (printable-zone origin, for ZeroReference printable)
MECHANICAL_MARGINS_MM = {"MANUALFEED": {"top": 5.0, "left": 5.0},
                         "ROLL": {"top": 5.0, "left": 5.0}}

# Paper presets (printed target width + sheet + placement). PARAMETRIZABLE:
# the values can also be passed explicitly to scanlayout_emu.
PAPER_PRESETS = {
    # key : (target_width_mm, sheet_w_mm, sheet_h_mm|None, media_source, placement)
    "a4":     (190.0, 210.0, 297.0, "MANUALFEED", "centered"),
    "a3":     (277.0, 297.0, 420.0, "MANUALFEED", "centered"),
    "a2":     (400.0, 420.0, 594.0, "MANUALFEED", "centered"),
    "roll24": (585.0, 609.6, None,  "ROLL",       "topleft5"),
}


@dataclass
class ScanGeometry:
    num_cols: int
    patch_count: int
    nrows: int
    n_empty: int
    width_px: int
    height_px: int


def chart_scan_geometry(num_cols: int, patch_count: int) -> ScanGeometry:
    """Canonical geometry (px) replicating chart.py — PARAMETRIC (no fixed 464).
    width depends on num_cols; height/nrows on patch_count. width/height ROUNDED to
    the integer (like the wrapper -> this is what sets mm_per_px and thus the byte-exact)."""
    if num_cols < 1 or patch_count < 1:
        raise ValueError("num_cols and patch_count must be >= 1")
    nrows = math.ceil(patch_count / num_cols)
    last_odd_right = (CX_00 + ODD_ROW_OFFSET) + (num_cols - 1) * PITCH_X + HEX_OBLIQUE_DX
    width = int(round(last_odd_right + RIGHT_PAD + SKEW_WIDTH))
    last_cy = CY_00 + (nrows - 1) * PITCH_Y
    height = int(round(last_cy + HEX_HALF_HEIGHT))
    return ScanGeometry(num_cols, patch_count, nrows,
                        nrows * num_cols - patch_count, width, height)


def scanlayout_emu(num_cols: int, patch_count: int, *,
                   target_width_mm: float, sheet_w_mm: float,
                   sheet_h_mm: float | None, media_source: str,
                   placement: str, origin: str = "absolute",
                   offset_x_mm: float | None = None,
                   offset_y_mm: float | None = None) -> dict:
    """Full ScanLayout EMU (FirstPatch/Delta/Spacing), PARAMETRIC.

    :param origin: 'absolute' (media edges, ZeroReference MediaEdges) or 'printable'.
    :param offset_x/y_mm: placement override (sheet edge -> image edge).
    :return: dict {FirstPatch:(x,y), Delta:(x,y), Spacing:x, nrows, width_px,...} in EMU.
    """
    geo = chart_scan_geometry(num_cols, patch_count)
    mm_per_px_x = target_width_mm / geo.width_px      # anisotropic X (target width)
    mm_per_px_y = 25.4 / DPI_Y                         # native Y 300 dpi
    image_w_mm = geo.width_px * mm_per_px_x
    image_h_mm = geo.height_px * mm_per_px_y

    if offset_x_mm is None or offset_y_mm is None:
        if placement == "centered":
            auto_x = (sheet_w_mm - image_w_mm) / 2.0
            auto_y = ((sheet_h_mm - image_h_mm) / 2.0) if sheet_h_mm else 5.0
        else:  # topleft5 (economical roll)
            auto_x, auto_y = 5.0, 5.0
        offset_x_mm = auto_x if offset_x_mm is None else offset_x_mm
        offset_y_mm = auto_y if offset_y_mm is None else offset_y_mm

    first_x_mm = CX_00 * mm_per_px_x
    first_y_mm = CY_00 * mm_per_px_y
    delta_x_mm = PITCH_X * mm_per_px_x
    delta_y_mm = PITCH_Y * mm_per_px_y
    aligner_center_px = (SKEW_BAR_MIN_X + SKEW_BAR_MAX_X) / 2.0     # = 75
    spacing_x_mm = (CX_00 - aligner_center_px) * mm_per_px_x        # mark->patch

    if origin == "printable":
        m = MECHANICAL_MARGINS_MM[media_source]
        first_x_mm += offset_x_mm - m["left"]
        first_y_mm += offset_y_mm - m["top"]
    else:  # absolute
        first_x_mm += offset_x_mm
        first_y_mm += offset_y_mm

    def emu(v):
        return int(round(v * EMU_PER_MM))

    return {
        "num_cols": geo.num_cols, "patch_count": geo.patch_count, "nrows": geo.nrows,
        "n_empty": geo.n_empty, "width_px": geo.width_px, "height_px": geo.height_px,
        "image_mm": (image_w_mm, image_h_mm), "offset_mm": (offset_x_mm, offset_y_mm),
        "media_source": media_source,
        "FirstPatch": (emu(first_x_mm), emu(first_y_mm)),
        "Delta": (emu(delta_x_mm), emu(delta_y_mm)),
        "Spacing": emu(spacing_x_mm),
    }


def _inch(emu_value: int) -> str:
    """EMU -> inches, native format (%g, 6 significant digits)."""
    return f"{emu_value / EMU_PER_INCH:g}"


def _assemble_fields(*, num_cols: int, patch_count: int,
                     spacing_emu: int, fp_emu: tuple, delta_emu: tuple,
                     scan_measures: str, skew_marks_type: str,
                     color_stab_time: int, num_scans_per_patch: int,
                     grid_type: str) -> list:
    """Ordered list [(key, value_str)] of the POST /Colorimetry/Scan, from EMU.
    Shared by the legacy generator (chart.py) and the native-rework — the order and
    the field format are common; only the geometry SOURCE differs."""
    return [
        ("DistanceUnits", "Inches"),
        ("SkewMarksType", skew_marks_type),
        ("ZeroReference", "MediaEdges"),
        ("ColorStabTime", str(color_stab_time)),
        ("ScanMeasures", scan_measures),
        ("NumPatches", str(patch_count)),
        ("PatchesPerRow", str(num_cols)),
        ("NumScansPerPatch", str(num_scans_per_patch)),
        ("GridType", grid_type),
        ("SkewMarksToPatch_X", _inch(spacing_emu)),
        ("FirstPatch_X", _inch(fp_emu[0])),
        ("FirstPatch_Y", _inch(fp_emu[1])),
        ("ToNextPatch_X", _inch(delta_emu[0])),
        ("ToNextPatch_Y", _inch(delta_emu[1])),
    ]


def build_scan_fields_refonte(layout, *,
                              offset_x_mm: float | None = None,
                              offset_y_mm: float | None = None,
                              scan_measures: str = "Spectral",
                              skew_marks_type: str = "Both",
                              color_stab_time: int = 0,
                              num_scans_per_patch: int = 1,
                              grid_type: str = "HexagonalShiftFirst") -> list:
    """NATIVE-REWORK scanLayout — consistent BY CONSTRUCTION with what
    `chart_render_refonte.render_refonte` prints (decision B).

    Derives EVERYTHING from the same `Layout` (chart_geometry_refonte.compute_layout) and the
    SAME mark functions as the renderer -> if the mark geometry changes,
    the printed mark AND the declared Spacing move together. No more hardcoded 75 px.

    Spacing = first_patch_x − mark_width_mm(mark_delta_mm(cols, patch_w)) / 2
              (center of the bar group = block_x0(0) + mark_w/2).
    FirstPatch = ABSOLUTE (media edges) = placement offset + center in the chart.

    :param layout: chart_geometry_refonte.Layout (cols/patch/pitch/first_patch/...).
    :param offset_x/y_mm: placement media edge -> chart origin. Default: horizontal
        centering (= content_w derivation); vertical TOP-ANCHORED at the fixed head
        HEAD_MARGIN_MM (sheet AND roll, Route A). The orchestration (8b) passes the
        REAL print placement.
    """
    # Explicit coupling: SAME mark geometry source as the renderer
    # (chart_render_refonte) -> consistency by construction, no duplication.
    from .chart_render_refonte import mark_delta_mm, mark_width_mm

    fp_x, fp_y = layout.first_patch_mm
    delta_x_mm, delta_y_mm = layout.pitch_x_mm, layout.pitch_y_mm

    mark_w = mark_width_mm(delta_mm=mark_delta_mm(layout.cols, layout.patch_w_mm))
    mark_center_x = mark_w / 2.0                    # block_x0 = 0 on the left side
    spacing_x_mm = fp_x - mark_center_x

    media = layout.media
    if offset_x_mm is None:
        offset_x_mm = (media.width_mm - layout.chart_w_mm) / 2.0   # X centering
    if offset_y_mm is None:
        # Y TOP-ANCHORED: fixed head HEAD_MARGIN_MM for SHEET *AND* ROLL. The
        # roll goes through Route A (re-cut -> reload as a manual sheet ->
        # offline scan) -> same head margin (edge detector). -> FirstPatch_Y
        # independent of the height, consistent with the orchestration (sol_chart).
        from .chart_geometry_refonte import HEAD_MARGIN_MM
        offset_y_mm = HEAD_MARGIN_MM

    def emu(v):
        return int(round(v * EMU_PER_MM))

    return _assemble_fields(
        num_cols=layout.cols, patch_count=layout.patch_count,
        spacing_emu=emu(spacing_x_mm),
        fp_emu=(emu(offset_x_mm + fp_x), emu(offset_y_mm + fp_y)),
        delta_emu=(emu(delta_x_mm), emu(delta_y_mm)),
        scan_measures=scan_measures, skew_marks_type=skew_marks_type,
        color_stab_time=color_stab_time, num_scans_per_patch=num_scans_per_patch,
        grid_type=grid_type)


def build_scan_fields(num_cols: int, patch_count: int, *,
                      paper: str | None = None,
                      target_width_mm: float | None = None,
                      sheet_w_mm: float | None = None,
                      sheet_h_mm: float | None = None,
                      media_source: str = "MANUALFEED",
                      placement: str = "centered",
                      scan_measures: str = "Spectral",
                      skew_marks_type: str = "Both",
                      color_stab_time: int = 0,
                      num_scans_per_patch: int = 1,
                      grid_type: str = "HexagonalShiftFirst",
                      offset_x_mm: float | None = None,
                      offset_y_mm: float | None = None) -> list:
    """⚠️ LEGACY (chart.py geometry / mark center 75 px) — FROZEN EMU-PORT ORACLE,
    **NOT the print path**. Kept as a unit test of the conversion MECHANICS
    (EMU->inches, %g, field order): reproduces byte-exact the 464 POST
    validated live (milestone 2). To generate a real chart, use
    `build_scan_fields_refonte` (consistent with render_refonte — decision B).

    Fields of the POST /Colorimetry/Scan (exact order of the decoded protocol). Returns a
    list [(key, value_str)] -> `sol_native.build_scan_body`. Either `paper` (preset),
    or the explicit dimensions. 100% parametric.
    """
    if paper is not None:
        tw, sw, sh, ms, pl = PAPER_PRESETS[paper]
        target_width_mm = tw if target_width_mm is None else target_width_mm
        sheet_w_mm = sw if sheet_w_mm is None else sheet_w_mm
        sheet_h_mm = sh if sheet_h_mm is None else sheet_h_mm
        media_source = ms
        placement = pl
    if target_width_mm is None or sheet_w_mm is None:
        raise ValueError("Provide a `paper` preset OR target_width_mm + sheet_w_mm")

    s = scanlayout_emu(num_cols, patch_count,
                       target_width_mm=target_width_mm, sheet_w_mm=sheet_w_mm,
                       sheet_h_mm=sheet_h_mm, media_source=media_source,
                       placement=placement, origin="absolute",
                       offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm)

    return _assemble_fields(
        num_cols=num_cols, patch_count=patch_count,
        spacing_emu=s["Spacing"], fp_emu=s["FirstPatch"], delta_emu=s["Delta"],
        scan_measures=scan_measures, skew_marks_type=skew_marks_type,
        color_stab_time=color_stab_time, num_scans_per_patch=num_scans_per_patch,
        grid_type=grid_type)
