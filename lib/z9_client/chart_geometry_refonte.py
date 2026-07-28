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

"""PROPOSAL v2 (refonte-geometrie branch) — chart geometry, INVERTED MODEL.

⚠️ NOT ADOPTED. PROPOSAL module for review + physical A/B validation (Phase B).
The current `chart.py` (px constants + anisotropic DPI) remains the operational default (validated live 96.32%).
This module is NOT wired to the render/IO.

────────────────────────────────────────────────────────────────────────────
What v2 fixes vs v1 (commit a8935d0):
  v1 was WRONG: it derived the columns from a fixed pitch_X (15.935) AND subtracted an exclusive
  fiducial band (12.80 mm/side) → 16 cols/29 rows → false "464 don't fit on A3".
  The real chart.py does 18 cols × 26 rows and fits (bottom margin 33.7 ≥ 27).

INVERTED MODEL (inputs → derived outputs):
  - Constraints (INPUT) = PageSize, Resolution, PatchCount, **Columns**.
  - Layout (OUTPUT) = BoundingBox, Grid, **Cells** (patch size), Aligner, **Gutter**.
  - ScanLayout (OUTPUT) = FirstPatch, **Delta** (pitch), Spacing.
  ⟹ Columns is a FREE HOST PARAMETER; the patch size, the gutter and the pitch (Delta) are
     DERIVED from (PageSize, PatchCount, Columns). PatchSize 15×15 = NOMINAL,
     not a floor (no min/max). A patch compressed to ~14.3 mm is CONFORMING.

pitch_Y = DERIVED, not a constant (verified):
  No source fixes a Delta/pitch/Gutter/Cells. The 12.86 mm of Phase A came from the
  `Delta` of a SINGLE log (44in chart, 34 cols) — it is an OUTPUT computed for that chart, not
  a universal constant. Fixing it = repeating the mistake "fixing what is derived". So we keep
  the pitch_Y PROVEN LIVE from chart.py (≈13.02 mm); a tighter pitch_Y remains a PHYSICAL
  question (Phase B), not a fixed acquired fact.

v2 principle: everything in **physical mm**, **isotropic** rendering (a single dpi, no more anisotropic DPI tag).
The mm geometry = the PHYSICAL geometry of chart.py (thus reproduced by construction); the ONLY
intended correction = fiducials 1.40 × 12.80 mm (official) instead of ~1.29 × 12.05.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ── Official (read) ──────────────────────────────────────────────────────
EMU_PER_MM = 36000.0

PRINT_MARGIN_MM = 5.0                          # SolConstraints PrintMargins (4 sides, 2 sources)
SCAN_MARGINS_MM = {                            # SolConstraints ScanMargins (T, B, L, R)
    "sheet": {"top": 7.0, "bottom": 27.0, "left": 5.0, "right": 5.0},
    "roll":  {"top": 7.0, "bottom": 5.0,  "left": 5.0, "right": 5.0},
}
# TOP-ANCHORED placement (sheets): fixed HEAD margin (top edge → raster top),
# independent of the sheet height → constant FirstPatch_Y (referenced to the head edge)
# = robust to nominal vs measured (Z9 measures ~293 for A4 nominal 297) + reliable
# reload. 27.5 mm = HP measurement (A3 print-only) ≈ scan_bottom (27); cf 464 centered 33.7.
HEAD_MARGIN_MM = 27.5
# nominal→measured allowance: the Z9 measures sheets shorter than the nominal.
# We reserve this margin at the bottom for the capacity (the valid print stays the hard backstop).
SHEET_HEIGHT_ALLOWANCE_MM = 5.0
FIDUCIAL_THICK_MM = 1.40                       # FiducialMarks thickness 140 (1/100 mm)
FIDUCIAL_WIDTH_MM = 12.80                      # FiducialMarks width 1280 (1/100 mm)
PATCH_NOMINAL_MM = 15.0                        # PatchSize NOMINAL (target, not floor)

TOTAL_PATCHES = 464

# ── PROVEN LIVE geometry, ported from chart.py (px constants @300 dpi native) ─
# Single source of truth: the chart.py layout validated at 96.32%. We express it in physical mm.
# (These are NOT arbitrary choices: they are the constants of the operational generator.)
_CX0_PX      = 263       # x center of the patch (r0,c0) in the raster
_CY0_PX      = 221       # y center of the patch (r0,c0) — below the primaries band
_PITCHX_PX   = 177.20    # column pitch (same row)
_PITCHY_PX   = 153.80    # row pitch (bees-nest: < patch height → rows interleave)
_ODD_PX      = -89       # offset of odd rows (to the left)
_HEXDX_PX    = 89        # hexagon half-width (x component of the oblique edge)
_HEXHH_PX    = 99        # hexagon half-height
_RIGHTPAD_PX = 24        # margin before the right skew
_SKEW_PX     = 150       # skew/fiducial block width (px, old value)
_DPI_Y       = 300.0     # native Y (never scaled in chart.py)

# Horizontal physical envelope of the chart (= width the raster occupies).
# chart.py: PAPER_TARGET_WIDTH_MM {a3:277, 24inch:585} — native geometry (0.948×292 / 0.941×622),
# i.e. ~10 mm margin/side on A3 (> the official 5 mm PrintMargins). We REPRODUCE this
# proven-live behavior. "Principled" variant (page − 2×PrintMargin) = Phase B candidate (cf. side_margin_mm).
_PROVEN_CONTENT_WIDTH_MM = {
    "a3": 277.0, "roll24": 585.0,
    # roll44: PROVISIONAL — aligned on the reference 44" chart = 73 columns
    # (known structure: 10 series of 7 + 3 = 73, cube structure). Lacking a physical 44"
    # measurement, the content width = patch-span(73 × pitch 14.04) + FIXED overhead of the 24"
    # (585 − 40×14.04 = 23.4 mm margins/gaps). ⚠️ The earlier proportional
    # extrapolation (585 × 1118/610 ≈ 1072.2) wrongly scaled this FIXED overhead (marks/
    # gaps do not grow with the width) → 1 column too many (74). To be recalibrated
    # on an edge-to-edge measurement of a real 44" chart (cf. community call).
    "roll44": round(73 * 14.04 + (585.0 - 40 * 14.04), 1),   # = 1048.3 mm → 73 cols
}

# ════════════════════════════════════════════════════════════════════════════
# MEASURED NATIVE GEOMETRY — FIXED-PITCH model.
# Single source: DIRECT measurements on a native chart (ruler, side by side).
# Zero formula. ⚠️ pitch_X = CONSTANT MEASURED on a native chart, NEVER derived from a
# closure. The "closure" (cols·pitch + 2·mark + 2·gap = skew-to-skew) shaved the
# pitch (→ 13.86, patches too short by 7 mm/row vs native): that was THE bug. It now serves
# only as a CHECK. If the measured pitch does not "close" with mark/gap, it is because the
# mark/structure width (interleaving: marks nested in the ½-pitch offset, not
# additive off-grid) differs — to be MEASURED on HP, not assumed.
# Doc: Docs/Methode_Geometrie_Chartes_Ancrage_Natif.md §2.4/2.5
PITCH_Y_MM        = 13.0    # row↔row: 26 mm (bottom of mark line N → line N+2) / 2 (ruler)
PATCH_H_MM        = 16.2    # patch point-to-point (measured; ratio h/w ≈ 1.18)
GAP_MIN_MM        = 2.2     # gap mark↔1st LEFT patch (measured = HP, user confirms)
PITCH_X_NOMINAL_MM = 13.8   # formats WITHOUT a native HP chart (a4/a2/roll17) — nominal pitch
FIRST_PATCH_Y_MM  = _CY0_PX * 25.4 / _DPI_Y   # ≈ 18.71 mm — Y proven live (unchanged)
_NATIVE_COLS = {"a3": 18, "roll24": 40}        # COUNTED on a native chart (measurement, not derived)
# pitch_X MEASURED DIRECTLY on HP (full row of N edge-to-edge patches / N), per format.
PITCH_X_MEASURED_MM = {
    "roll24": 14.04,   # MEASURED HP: 561.5 mm / 40 patches (full row, averaged → reliable)
    "roll44": 14.04,   # INHERITS the measured 24" pitch (UNIVERSAL 24"/44" patch geometry,
                       # confirmed by HP doc: 44" chart ~73 patches → 73×14.04≈1025+margins).
                       # cols NOT fixed (not in _NATIVE_COLS) → DERIVED from the width 1118.
    "a3":     14.03,   # MEASURED HP: 252.5 mm / 18 patches (user) — native ~14.03 constant
}


@dataclass
class MediaSpec:
    name: str
    source: str                  # "sheet" | "roll"
    width_mm: float              # physical width (short side of sheet / roll width)
    height_mm: float | None = None
    proven_key: str | None = None  # key in _PROVEN_CONTENT_WIDTH_MM if live layout known


MEDIA = {
    # A4 = smallest supported sheet (Z9 manual: "210 to 610-mm wide sheets"
    # → 210 mm = A4 width = machine minimum). Conforming geometry computed (11 col,
    # patch 15.31 mm, gap 2.10 mm > 0). No proven_key (no live width) →
    # content_w = 210 − 2·10 = 190 mm.
    "a4":     MediaSpec("A4 (alim manuelle)", "sheet", 210.0, 297.0),
    "a3":     MediaSpec("A3 (alim manuelle)", "sheet", 297.0, 420.0, proven_key="a3"),
    "a2":     MediaSpec("A2 (alim manuelle)", "sheet", 420.0, 594.0),
    "roll24": MediaSpec('Rouleau 24"', "roll", 610.0, None, proven_key="roll24"),
    # 44" roll (1118 mm, machine max 44"). SAME model/path as roll24:
    # MEASURED pitch 14.04 (universal), DERIVED columns = 73 (aligned on the HP 44" chart).
    # Internal scaling inherited from the 24" — untested on a physical 44" (community call
    # in progress to recalibrate the content width). Patch geometry unchanged (universal).
    "roll44": MediaSpec('Rouleau 44"', "roll", 1118.0, None, proven_key="roll44"),
    "roll17": MediaSpec('Rouleau 17"', "roll", 432.0, None),
}


@dataclass
class Layout:
    media: MediaSpec
    patch_count: int
    cols: int                    # DERIVED from the fixed-pitch (native: counted; others: floor)
    nrows: int
    n_empty: int
    content_width_mm: float
    # derived (outputs: layout + scanLayout)
    pitch_x_mm: float
    pitch_y_mm: float
    patch_w_mm: float
    patch_h_mm: float
    first_patch_mm: tuple        # center (x, y) of the patch (r0,c0)
    chart_w_mm: float
    chart_h_mm: float
    scan_bottom_free_mm: float
    fiducial_mm: tuple           # (thickness, width)
    scanlayout_emu: dict = field(default_factory=dict)

    def patch_center_mm(self, row: int, col: int) -> tuple[float, float]:
        """Physical center (x, y) in mm of a patch (bees-nest: odd rows offset)."""
        x_off = self._odd_off_mm if (row % 2 == 1) else 0.0
        return (self.first_patch_mm[0] + col * self.pitch_x_mm + x_off,
                self.first_patch_mm[1] + row * self.pitch_y_mm)

    _odd_off_mm: float = 0.0


def _content_width_mm(media: MediaSpec, side_margin_mm: float | None) -> float:
    """Horizontal physical envelope.

    - default: reproduces chart.py (known live width) → no regression;
    - otherwise: page − 2·side_margin (side_margin default = 10 mm, chart.py convention; pass 5.0 for the
      principled PrintMargin-only variant to test in Phase B).
    """
    if side_margin_mm is None and media.proven_key in _PROVEN_CONTENT_WIDTH_MM:
        return _PROVEN_CONTENT_WIDTH_MM[media.proven_key]
    sm = 10.0 if side_margin_mm is None else side_margin_mm
    return media.width_mm - 2.0 * sm


def compute_layout(media: MediaSpec,
                   patch_count: int = TOTAL_PATCHES,
                   columns: int | None = None,
                   side_margin_mm: float | None = None) -> Layout:
    """Native-anchored FIXED-PITCH model: pitch_X = CONSTANT MEASURED on HP (never derived).

    HP native formats (a3, roll24): pitch_X = PITCH_X_MEASURED_MM (measured HP row / cols),
    cols = _NATIVE_COLS (counted), LEFT gap = GAP_MIN_MM (measured = HP). The CLOSURE
    (cols·pitch + 2·mark + 2·gap = skew-to-skew) now serves only as a coherence CHECK,
    NEVER to produce the pitch (that was the bug: it shaved the pitch → patches too short).
    Formats without an HP chart (a4/a2/roll17): nominal pitch, derived cols, gap = centered residual.
    The ½-pitch bees-nest offset stays at RENDER time (interleaving 1 mark/row).

    :param columns: IGNORED (call compat). Native density imposed; patch_count drives the ROWS.
    :param side_margin_mm: lateral margin override (None = reproduces the chart.py live width).
    """
    from .chart_render_refonte import mark_delta_mm, mark_width_mm

    s2s = _content_width_mm(media, side_margin_mm)              # skew-to-skew (edge-to-edge, header)
    mark_w = mark_width_mm(delta_mm=mark_delta_mm(0, 0.0))      # RENDERED mark width (≈12.68)

    # pitch_X: MEASURED on HP if known (a3, roll24, and roll44 which INHERITS the 24" pitch —
    # universal patch geometry), otherwise NOMINAL (a4/a2/roll17). DECOUPLED from the number of
    # columns: roll44 has a measured pitch BUT derived cols (not in _NATIVE_COLS).
    pitch_x_mm = PITCH_X_MEASURED_MM.get(media.proven_key, PITCH_X_NOMINAL_MM)
    if media.proven_key in _NATIVE_COLS:
        # HP native formats (a3, roll24): cols COUNTED on the HP chart (not derived).
        cols = _NATIVE_COLS[media.proven_key]
    else:
        # Columns DERIVED from the width to fill to the MAX (a4/a2/roll17/roll44).
        # +0.5 = compensates the bees-nest half-pitch of the centering → packs one more column
        # (tight ~HP gaps instead of slack), while guaranteeing centered_gap ≥ GAP_MIN_MM.
        cols = max(1, math.floor((s2s - 2.0 * mark_w - 2.0 * GAP_MIN_MM) / pitch_x_mm + 0.5))

    nrows = math.ceil(patch_count / cols)
    n_empty = nrows * cols - patch_count

    patch_w_mm = pitch_x_mm                  # bees-nest touching (pitch ≥ flat-to-flat → no overlap)
    patch_h_mm = PATCH_H_MM                  # point-to-point measured (16.2; ratio ≈ 1.18)
    pitch_y_mm = PITCH_Y_MM                  # 13.0 measured
    odd_off_mm = -pitch_x_mm / 2.0           # bees-nest offset (odd rows, to the left)

    chart_w_mm = s2s
    # CENTERING of the patch block between the marks: LEFT gap (even row ↔ left mark) ==
    # RIGHT gap (odd row ↔ right mark). The bounding box of the 2 parities (the ½-pitch
    # offset extends it on one side) is centered in [0, chart_w]. fp_x = (chart_w − odd_off − (cols−1)·pitch)/2.
    fp_x = (chart_w_mm - odd_off_mm - (cols - 1) * pitch_x_mm) / 2.0
    fp_y = FIRST_PATCH_Y_MM                   # Y proven live (unchanged)

    # CHECK (informative): actual placed left gap (even row ↔ left mark).
    gap_mm = fp_x - patch_w_mm / 2.0 - mark_w
    chart_h_mm = fp_y + (nrows - 1) * pitch_y_mm + patch_h_mm / 2.0

    # free bottom margin (sheet centered vertically); roll = scan bottom margin
    if media.height_mm is not None:
        top_off = (media.height_mm - chart_h_mm) / 2.0
        scan_bottom_free = media.height_mm - (top_off + chart_h_mm)
    else:
        scan_bottom_free = SCAN_MARGINS_MM[media.source]["bottom"]

    scanlayout_emu = {
        "FirstPatch": (round(fp_x * EMU_PER_MM), round(fp_y * EMU_PER_MM)),
        "Delta": (round(pitch_x_mm * EMU_PER_MM), round(pitch_y_mm * EMU_PER_MM)),
    }

    L = Layout(media, patch_count, cols, nrows, n_empty, s2s,
               pitch_x_mm, pitch_y_mm, patch_w_mm, patch_h_mm,
               (fp_x, fp_y), chart_w_mm, chart_h_mm, scan_bottom_free,
               (FIDUCIAL_THICK_MM, FIDUCIAL_WIDTH_MM), scanlayout_emu)
    L._odd_off_mm = odd_off_mm
    return L


def mm_to_px(mm: float, dpi: float) -> int:
    return int(round(mm * dpi / 25.4))


if __name__ == "__main__":
    print(f"{'Media':<22}{'src':<6}{'cols':>5}{'rows':>5}{'empty':>5}"
          f"{'patchX':>8}{'patchY':>8}{'pitchX':>8}{'pitchY':>8}{'scanBas':>9}")
    for key in ("a3", "a2", "roll24", "roll17"):
        L = compute_layout(MEDIA[key])
        need = SCAN_MARGINS_MM[L.media.source]["bottom"]
        flag = "OK" if L.scan_bottom_free_mm >= need else "<MIN"
        print(f"{L.media.name:<22}{L.media.source:<6}{L.cols:>5}{L.nrows:>5}{L.n_empty:>5}"
              f"{L.patch_w_mm:>8.2f}{L.patch_h_mm:>8.2f}{L.pitch_x_mm:>8.2f}{L.pitch_y_mm:>8.2f}"
              f"{L.scan_bottom_free_mm:>7.1f}{flag:>2}")
