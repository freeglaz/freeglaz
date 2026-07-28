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

"""ORCHESTRATION of the FREE chart (generation + descriptor).

Single entry point that chains the full pipeline, in the GENERALIST spirit (any N
patches / N columns, no fixed composition):

  .ti1 (arbitrary) → read_ti1 → list of (sample_id,r,g,b)
    → compute_layout(patch_count, columns)        (reworked geometry)
    → [feasibility: mark↔patch gap > 0]
    → generate_refonte (16-bit TIFF + conforming marks + sidecar + preview)
    → build_scan_fields_refonte (COHERENT scanLayout, same placement as the print)
    → chart.json (persistent descriptor) in <root>/charts/<chart_id>/

⚠️ This link GENERATES (chart + descriptor + TIFF). **No Z9**: the actual print
and the scan are SEPARATE physical acts (with GO + chart loaded).

CRITICAL placement (continuation of 8a): the centering offset is computed ONCE
here and passed to `build_scan_fields_refonte` AND stored in the descriptor →
the scanLayout (ZeroReference: MediaEdges) declares the SAME placement as the one
that will print the TIFF. The print step MUST honor `scanLayout.placement`.

Reused building blocks (modules we control): `chart.read_ti1`,
`chart_geometry_refonte.compute_layout`, `chart_render_refonte.generate_refonte`
(+ mark functions), `sol_scanlayout.build_scan_fields_refonte`, `cache`.
"""
from __future__ import annotations

import json
import logging
import string
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import cache
from .chart import read_ti1
from . import chart_geometry_refonte as G
from .chart_render_refonte import (
    generate_refonte, mark_delta_mm, mark_width_mm,
)
from .printing import MECHANICAL_MARGINS_MM
from .sol_scanlayout import build_scan_fields_refonte, EMU_PER_MM

logger = logging.getLogger(__name__)

# ⚠️ NO sRGB default: the Z9 firmware CONSUMES the source profile for its
# device→ink conversion (source=OutputIntent NECESSARY but NOT sufficient). The tag MUST be
# the RESIDENT profile of the
# slot, read live. The live resolution (export_icc) lives in the CLI (cmd_chart_create),
# like chart generate; orchestrate_free_chart consumes the already-resolved resident.

# Footer (ID): band height + clearance, in mm (unscanned bottom margin)
FOOTER_H_MM = 10.0
FOOTER_CLEARANCE_MM = 2.0
FOOTER_MIN_H_MM = 6.0

# RECOMMENDED columns per format (gap ~1-2 mm, cf Calage_Scan_..._Recoupe). The
# HARD guard stays gap > 0 (generalist); this is only an indicative bound.
RECOMMENDED_MAX_COLS = {"a4": 12, "a3": 18, "a2": 26, "roll24": 38, "roll17": 26}

# Reference columns for the communicable CAPACITY (comfortable 1-2 mm gap;
# A3=18 = the validated 464). Used to compute max_patches per format (headline). The
# per-chart guard stays geometric (gap>0 AND height), so NOT auto-limited:
# more columns can be packed, alignment allows it as long as gap>0.
CAPACITY_COLS = {"a4": 11, "a3": 18, "a2": 25, "roll24": 36, "roll44": 70, "roll17": 24}
# Sheet order from smallest to largest (helps inverse smallest_format_for).
SHEET_ORDER = ["a4", "a3", "a2"]
# SCAN bottom margin of a sheet (SolConstraints) + PRINT bottom margin
# (MANUALFEED, non-printable). TOP-ANCHORED placement: the chart starts at
# HEAD_MARGIN_MM (fixed head), the grid must leave at the bottom the MAX of the two margins
# (scan on the last row; print+footer on the bottom of the raster).
SHEET_SCAN_BOTTOM_MM = G.SCAN_MARGINS_MM["sheet"]["bottom"]      # 27 mm (scan)
PRINT_BOTTOM_MM = MECHANICAL_MARGINS_MM["MANUALFEED"]["bottom"]  # 17.4 mm (non-printable)
# required bottom (raster top→bottom edge) = max(scan, print + footer); the footer lives below
# the grid → it is the one touching the print margin.
SHEET_BOTTOM_REQUIRED_MM = max(SHEET_SCAN_BOTTOM_MM, PRINT_BOTTOM_MM + FOOTER_H_MM)  # 27.4

DESCRIPTOR_FILENAME = "chart.json"
SCHEMA_VERSION = 1
_B36 = string.digits + string.ascii_uppercase


@dataclass
class Feasibility:
    ok: bool
    gap_mm: float                 # right mark ↔ left edge of the 1st patch
    reason: str
    recommended_max_cols: int | None
    height_ok: bool = True            # does the height fit (sheet)?
    max_patches: int | None = None    # format capacity (None = roll, free)


@dataclass
class FreeChartResult:
    chart_id: str
    chart_dir: str
    descriptor_path: str
    tiff_path: str
    sidecar_path: str
    preview_path: str
    scan_fields: list             # [(key, value)] of the POST /Colorimetry/Scan
    feasibility: Feasibility
    n_patches: int
    cols: int
    nrows: int


# ─── Capacity per format (computed once, from the real geometry) ─────────────
def _media_key(media: G.MediaSpec) -> str | None:
    for k, v in G.MEDIA.items():
        if v is media or v.name == media.name:
            return k
    return media.proven_key


def _max_rows_sheet(media: G.MediaSpec) -> int:
    """Max rows on a sheet (FIXED-PITCH model), TOP-ANCHORED placement: fixed head
    HEAD_MARGIN_MM + grid + required bottom (max scan / print+footer) ≤ effective height.
    Y is fixed (cols-independent): chart_h(nrows) = FIRST_PATCH_Y_MM +
    (nrows−1)·PITCH_Y_MM + PATCH_H_MM/2 → solved directly (no more
    columns=1 loop, which no longer makes sense since cols is DERIVED)."""
    eff_h = media.height_mm - G.SHEET_HEIGHT_ALLOWANCE_MM
    limit = eff_h - G.HEAD_MARGIN_MM - SHEET_BOTTOM_REQUIRED_MM
    n = int((limit - G.FIRST_PATCH_Y_MM - G.PATCH_H_MM / 2.0) / G.PITCH_Y_MM) + 1
    return max(0, n)


def format_capacity(media_or_key) -> dict:
    """Conforming capacity of a format (from the real geometry):
    max_cols (comfortable reference) × max_rows (usable height). For the roll,
    the height is free → we give rows/m and the REAL LIMIT = scannable
    length (untested), not the geometry."""
    media = G.MEDIA[media_or_key] if isinstance(media_or_key, str) else media_or_key
    key = media_or_key if isinstance(media_or_key, str) else _media_key(media)
    cols = G.compute_layout(media, patch_count=2).cols   # DERIVED (fixed-pitch model)
    pitch_y = G.PITCH_Y_MM
    rows_per_m = int(1000.0 / pitch_y)
    if media.height_mm is None:                       # roll: free length
        return {"format": key, "is_roll": True, "max_cols": cols,
                "max_rows": None, "max_patches": None, "rows_per_m": rows_per_m,
                "note": "actual limit = offline scannable length (not tested)"}
    max_rows = _max_rows_sheet(media)
    return {"format": key, "is_roll": False, "max_cols": cols,
            "max_rows": max_rows, "max_patches": cols * max_rows,
            "rows_per_m": rows_per_m, "note": ""}


def smallest_format_for(n_patches: int) -> str:
    """Inverse helper: the smallest SHEET that fits n patches (otherwise roll)."""
    for k in SHEET_ORDER:
        cap = format_capacity(k)
        if cap["max_patches"] and cap["max_patches"] >= n_patches:
            return k
    return "roll24"   # roll: free length (limit = scannable length)


# ─── Feasibility (width: gap; height: the chart fits on the sheet) ───────────
def check_feasibility(layout: G.Layout) -> Feasibility:
    """GENERALIST guard by the real geometry (not a fixed format):
      - WIDTH: gap = (left edge of 1st patch) − (mark width) > 0 (no
        mark↔patch overlap);
      - HEIGHT (sheet, TOP-ANCHORED): head HEAD_MARGIN_MM + grid + required bottom
        (max scan / print+footer) ≤ effective height (nominal→measured allowance),
        otherwise the last rows fall outside the scannable/printable zone."""
    mark_w = mark_width_mm(delta_mm=mark_delta_mm(layout.cols, layout.patch_w_mm))
    gap = (layout.first_patch_mm[0] - layout.patch_w_mm / 2.0) - mark_w
    media = layout.media
    rec = RECOMMENDED_MAX_COLS.get(_media_key(media) or "")
    cap = format_capacity(media)

    if gap <= 0:
        reason = (f"mark↔patch overlap (gap={gap:.2f} mm <= 0): "
                  f"{layout.cols} columns too many for {media.name}")
        return Feasibility(False, gap, reason, rec, height_ok=True,
                           max_patches=cap["max_patches"])

    if media.height_mm is not None:
        eff_h = media.height_mm - G.SHEET_HEIGHT_ALLOWANCE_MM
        available = eff_h - G.HEAD_MARGIN_MM - SHEET_BOTTOM_REQUIRED_MM
        if layout.chart_h_mm > available:
            sugg = smallest_format_for(layout.patch_count)
            reason = (f"overflows in height: {layout.nrows} rows (grid "
                      f"{layout.chart_h_mm:.1f} mm > {available:.1f} mm available = "
                      f"{eff_h:.0f}−head {G.HEAD_MARGIN_MM:.0f}−bottom "
                      f"{SHEET_BOTTOM_REQUIRED_MM:.1f}). Max ≈ {cap['max_patches']} "
                      f"patches on {media.name} ({cap['max_cols']}×{cap['max_rows']})."
                      f" Use '{sugg}' or reduce the number of patches.")
            return Feasibility(False, gap, reason, rec, height_ok=False,
                               max_patches=cap["max_patches"])

    reason = f"OK (gap={gap:.2f} mm)"
    if rec and layout.cols > rec:
        reason += f" — beyond the recommended limit ({rec} cols for this format)"
    return Feasibility(True, gap, reason, rec, height_ok=True,
                       max_patches=cap["max_patches"])


def generate_chart_id(now: datetime | None = None) -> str:
    """CHT-<YYYYMMDD>-<HHMM>-<2 base36 chars> (readable, sortable, per-minute anti-collision)."""
    now = now or datetime.now()
    n = (now.second * 1_000_000 + now.microsecond) % (36 * 36)
    suffix = _B36[n // 36] + _B36[n % 36]
    return f"CHT-{now:%Y%m%d}-{now:%H%M}-{suffix}"


# ─── VISUAL order of patches (FREE chart only) ──────────────────────────────
def _disperse_runs(seq: list[tuple]) -> None:
    """Breaks IN PLACE the runs of strictly identical RGB: inserts, at the conflicting
    position, the nearest patch of a different color AHEAD (rotation →
    relative order preserved, duplicate kept in its zone of the gradient). Deterministic.

    BIDIRECTIONAL pass (forward, mirror, forward, mirror): the forward pass alone
    cannot break a TAIL run (nothing after it to insert); the mirror turns
    that tail into a head, which the forward pass handles by borrowing from the neighbors just before
    (e.g. alignment whites interleaved with the lightest grays). A residual run
    remains only if a color is too frequent to be separated (> half the
    patches) — does not happen with the targen alignment duplicates."""
    def rgb(p):
        return p[1], p[2], p[3]

    def _forward():
        i = 1
        while i < len(seq):
            if rgb(seq[i]) == rgb(seq[i - 1]):
                j = i + 1
                while j < len(seq) and rgb(seq[j]) == rgb(seq[i - 1]):
                    j += 1
                if j >= len(seq):
                    break                   # tail run → left to the mirror pass
                seq.insert(i, seq.pop(j))   # insert the nearest different neighbor
            i += 1

    _forward()                              # fixes head + middle
    seq.reverse()
    _forward()                              # fixes the old tail (now the head)
    seq.reverse()


def order_patches_serpentine(patches: list[tuple]) -> list[tuple]:
    """Reorders the patches of a FREE chart for VISUAL APPEAL — continuous
    serpentine gradient, no monochrome block. PURELY aesthetic: the order has
    NO colorimetric stake (edge contamination not detectable at worst case
    white↔black; we never profile on a badly printed chart → no banding
    to "average out" by dispersion). Deterministic, reproducible, zero randomness.

    Two passes:
      1. BOUSTROPHEDON over (R,G,B) device: R increasing (outer); direction of G alternated
         at each R; direction of B alternated at each (R,G) → serpentine, neighbors always
         close, no "seam" (unlike strict lexicographic order which
         snaps B back to 0 at each line).
      2. ANTI-RUN: locally disperses the consecutive strictly identical RGB
         (targen alignment duplicates: whites/blacks) by inserting the nearest
         neighbor → no monochrome block, without taking the color out of its gradient zone.

    ⚠️ Only alters the placement (list order), NOT the set of values
    (targen grammar R≥G≥B preserved on the whole set). Apply ONCE, upstream
    (single source of order) → render row-major / sidecar / positional scanLayout /
    ti3 (paired by position via sample_id) follow by construction.

    Reserved for FREE charts (orchestrate_free_chart). `chart generate` 464
    (chart.py legacy, validated cube+121 structure) does NOT call this function.
    """
    if len(patches) < 3:
        return list(patches)
    by_r: dict = defaultdict(lambda: defaultdict(list))
    for p in patches:
        by_r[p[1]][p[2]].append(p)
    ordered: list[tuple] = []
    for i, r in enumerate(sorted(by_r)):
        gs = sorted(by_r[r], reverse=(i % 2 == 1))           # G alternated per R
        for j, g in enumerate(gs):
            grp = sorted(by_r[r][g], key=lambda p: p[3],
                         reverse=((i + j) % 2 == 1))          # B alternated per (R,G)
            ordered.extend(grp)
    _disperse_runs(ordered)
    return ordered


# ─── Orchestration ──────────────────────────────────────────────────────────
def orchestrate_free_chart(
    ti1_path: str | Path,
    media_key: str,
    columns: int,
    *,
    reference_icc_path: str | Path,
    gloss_enhancer: str | None = None,
    tag_source: str = "resident-live",
    source: str | None = None,
    paper: dict | None = None,
    dpi: float = 300.0,
    charts_root: str | Path | None = None,
    now: datetime | None = None,
) -> FreeChartResult:
    """Generates a complete free chart (TIFF + descriptor), WITHOUT printing.

    ⚠️ COLOR (profiling): `reference_icc_path` MUST be the RESIDENT profile of the
    target slot (read live), NOT a generic sRGB. The Z9 firmware consumes the
    source profile for its device→ink conversion; source=OutputIntent=RESIDENT
    is the only "raw device values" path (validated ΔE 0.60, cf
    Brief_ClaudeCode_Validation_Tag_Resident_Live). No default → resident required.

    :param ti1_path: any .ti1 (composition = upstream user choice).
    :param media_key: MEDIA key (a4/a3/a2/roll24/roll17).
    :param columns: desired number of columns (parametric).
    :param reference_icc_path: RESIDENT profile of the slot (MANDATORY, no sRGB).
    :param gloss_enhancer: GE state of the tagged slot (FULLPAGE/OFF) — traced to the descriptor.
    :param tag_source: provenance of the tag ('resident-live' by default).
    :param source: free origin metadata (e.g. "targen -d2 -f200").
    :param paper: target paper metadata {name, media_id?, serial?}.
    :raises ValueError: resident missing, invalid .ti1, unknown media, feasibility failed.
    """
    ti1_path = Path(ti1_path)
    if media_key not in G.MEDIA:
        raise ValueError(f"media inconnu : {media_key!r} (connus : {list(G.MEDIA)})")
    if not reference_icc_path:
        raise ValueError(
            "RESIDENT profile required (reference_icc_path) — no default sRGB in "
            "profiling: the firmware consumes the source profile. Provide the slot "
            "resident (read live)."
        )
    ref_icc = Path(reference_icc_path)
    if not ref_icc.exists():
        raise FileNotFoundError(f"Resident profile missing: {ref_icc}")

    # 1. .ti1 → list of patches (generalist: any N)
    patches = read_ti1(ti1_path)
    if not patches:
        raise ValueError(f".ti1 empty: {ti1_path}")
    # 1b. VISUAL ORDER (serpentine + anti-run) — single source of order, purely
    #     aesthetic (no measurement stake). Everything downstream follows by construction.
    patches = order_patches_serpentine(patches)

    # 2. geometry + 3. FEASIBILITY (before any render)
    media = G.MEDIA[media_key]
    layout = G.compute_layout(media, patch_count=len(patches), columns=columns)
    feas = check_feasibility(layout)
    if not feas.ok:
        raise ValueError(f"Generation refused — {feas.reason}")

    # 4. library folder + chart_id + ID text (before render → footer)
    chart_id = generate_chart_id(now)
    created = (now or datetime.now()).isoformat(timespec="seconds")
    # per-serial WRITE: home = explicit charts_root OR charts/<serial> derived
    # from the serial carried by `paper` (set by the caller via store.get_serial(client)).
    if charts_root is not None:
        root = Path(charts_root)
    else:
        root = cache.charts_dir((paper or {}).get("serial") or "")
    chart_dir = root / chart_id
    chart_dir.mkdir(parents=True, exist_ok=True)
    printed_text = _printed_id_text(chart_id, created, paper, len(patches),
                                    layout.cols, media_key)

    # 4b. does the footer fit in the UNSCANNED bottom margin? (sheet = finite;
    #     roll = room along the length → always OK)
    footer_h = FOOTER_H_MM
    footer_rendered = True
    if media.height_mm is not None:
        bottom = (media.height_mm - layout.chart_h_mm) / 2.0
        usable = bottom - FOOTER_CLEARANCE_MM
        if usable < FOOTER_MIN_H_MM:
            footer_rendered = False
            logger.warning("ID footer not rendered: bottom margin %.1f mm insufficient "
                           "(< %.1f mm)", bottom, FOOTER_MIN_H_MM + FOOTER_CLEARANCE_MM)
        elif usable < footer_h:
            footer_h = usable

    # 5. reworked render (16-bit TIFF + conforming marks + [ID footer] + sidecar + preview)
    gen = generate_refonte(
        patches=patches, media_key=media_key, columns=columns,
        reference_icc_path=ref_icc, output_tiff=chart_dir / "chart.tif", dpi=dpi,
        footer_text=printed_text if footer_rendered else None, footer_h_mm=footer_h,
    )

    # 6. PLACEMENT (single source): X centered; Y TOP-ANCHORED (fixed head). The fixed
    #    head makes FirstPatch_Y independent of the measured height (robust to
    #    nominal vs measured + reload). This is what the TIFF will print.
    offset_x_mm = (media.width_mm - layout.chart_w_mm) / 2.0
    if media.height_mm is not None:
        offset_y_mm = G.HEAD_MARGIN_MM            # sheet: fixed head (top-anchored)
        placement_mode = "top_anchored"
    else:
        # Roll: SAME head as the sheets. In Path A, the roll is ALWAYS
        # recut then reloaded as a MANUAL SHEET to scan offline → the
        # edge detector requires the white head margin (27.5). The "online" head 5
        # (scan without unloading) is not used by freeglaz.
        offset_y_mm = G.HEAD_MARGIN_MM
        placement_mode = "top_anchored_roll"

    # 7. COHERENT scanLayout (same offset as the placement above)
    scan_fields = build_scan_fields_refonte(
        layout, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm)

    # 8. chart.json descriptor (persistent authority)
    spacing_emu = round((layout.first_patch_mm[0]
                         - mark_width_mm(delta_mm=mark_delta_mm(layout.cols,
                                                                layout.patch_w_mm)) / 2.0)
                        * EMU_PER_MM)
    descriptor = _build_descriptor(
        chart_id=chart_id, created_at=created,
        source=source or f"ti1: {ti1_path.name}",
        media_key=media_key, media=media, dpi=dpi, paper=paper,
        layout=layout, patches=patches, scan_fields=scan_fields,
        placement={"offset_x_mm": offset_x_mm, "offset_y_mm": offset_y_mm,
                   "mode": placement_mode,
                   "note": "print the TOP of the raster at this offset (the footer "
                           "occupies the bottom margin); do NOT recenter the widened TIFF"},
        spacing_emu=spacing_emu, feasibility=feas,
        printed_text=printed_text, footer_rendered=footer_rendered,
        color_management=_color_management_meta(ref_icc, tag_source, gloss_enhancer),
        files={"tiff": Path(gen.tiff_path).name,
               "sidecar": Path(gen.sidecar_path).name,
               "preview": Path(gen.preview_path).name},
    )
    descriptor_path = save_chart_descriptor(chart_dir, descriptor)

    return FreeChartResult(
        chart_id=chart_id, chart_dir=str(chart_dir),
        descriptor_path=str(descriptor_path), tiff_path=gen.tiff_path,
        sidecar_path=gen.sidecar_path, preview_path=gen.preview_path,
        scan_fields=scan_fields, feasibility=feas,
        n_patches=len(patches), cols=layout.cols, nrows=layout.nrows,
    )


def _printed_id_text(chart_id, created_at, paper, n_patches, cols, media_key) -> str:
    """ID text printed in the footer (= shown in list_charts → visual pairing)."""
    return (f"freeglaz  {chart_id}   {created_at[:10]}   "
            f"{(paper or {}).get('name', '—')}   "
            f"{n_patches} patches / {cols} col   {media_key.upper()}")


def _color_management_meta(ref_icc: Path, tag_source: str,
                           gloss_enhancer: str | None) -> dict:
    """Color traceability of the resident tag embedded in the TIFF (= source =
    OutputIntent at print time). Reads the profile name for verification."""
    try:
        from .printing import _get_icc_profile_description
        icc_name = _get_icc_profile_description(Path(ref_icc).read_bytes())
    except Exception:                                # noqa: BLE001 (best-effort traceability)
        icc_name = "?"
    return {
        "tag_source": tag_source,            # 'resident-live' (profiling) or other
        "gloss_enhancer": gloss_enhancer,    # tagged slot (GE) — must match the print
        "icc_name": icc_name,                # name of the embedded resident profile
        "note": ("source = OutputIntent = RESIDENT profile of the slot (firmware consumes "
                 "the source profile) → raw device values. NO sRGB."),
    }


def _build_descriptor(*, chart_id, created_at, source, media_key, media, dpi,
                      paper, layout, patches, scan_fields, placement,
                      spacing_emu, feasibility, printed_text, footer_rendered,
                      color_management, files) -> dict:
    rows = []
    idx = 0
    for row in range(layout.nrows):
        for col in range(layout.cols):
            if idx >= len(patches):
                break
            sid, r, g, b = patches[idx]
            rows.append({"row": row, "col": col, "index": idx,
                         "sample_id": sid, "rgb": [int(r), int(g), int(b)]})
            idx += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "chart_id": chart_id,
        "created_at": created_at,
        "source": source,
        "media": {
            "format_key": media_key, "source": media.source,
            "width_mm": media.width_mm, "height_mm": media.height_mm, "dpi": dpi,
        },
        "paper": paper or {},
        "geometry": {
            "cols": layout.cols, "nrows": layout.nrows, "n_empty": layout.n_empty,
            "patch_count": layout.patch_count,
            "chart_w_mm": layout.chart_w_mm, "chart_h_mm": layout.chart_h_mm,
            "pitch_x_mm": layout.pitch_x_mm, "pitch_y_mm": layout.pitch_y_mm,
            "patch_w_mm": layout.patch_w_mm, "patch_h_mm": layout.patch_h_mm,
            "first_patch_mm": list(layout.first_patch_mm),
            "fiducial_thick_mm": layout.fiducial_mm[0],
            "fiducial_width_mm": layout.fiducial_mm[1],
        },
        "scanLayout": {
            "fields": {k: v for k, v in scan_fields},
            "emu": {**layout.scanlayout_emu, "Spacing": spacing_emu},
            "placement": placement,
        },
        "feasibility": {
            "ok": feasibility.ok, "gap_mm": round(feasibility.gap_mm, 3),
            "recommended_max_cols": feasibility.recommended_max_cols,
        },
        "color_management": color_management,
        "patches_in_layout_order": rows,
        "printed_id": {
            "text": printed_text,
            "fields_shown": ["chart_id", "date", "paper", "n_patches", "cols",
                             "format"],
            "rendered_on_tiff": footer_rendered,   # footer render
        },
        "files": files,
    }


# ─── Chart library ──────────────────────────────────────────────────────────
def save_chart_descriptor(chart_dir: str | Path, descriptor: dict) -> Path:
    """Writes the chart.json descriptor (authority) into the chart's folder."""
    chart_dir = Path(chart_dir)
    chart_dir.mkdir(parents=True, exist_ok=True)
    path = chart_dir / DESCRIPTOR_FILENAME
    path.write_text(json.dumps(descriptor, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def load_chart(chart_id: str, charts_root: str | Path | None = None) -> dict:
    """Loads a chart's descriptor by ID (start of the scan, no manual input).

    per-serial READ: if ``charts_root`` is provided (a ``<serial>`` folder),
    we look there directly; otherwise we locate the chart by FS enumeration
    (``charts/<serial>/<chart_id>/``), without knowing the serial — chart_id is unique."""
    if charts_root is not None:
        path = Path(charts_root) / chart_id / DESCRIPTOR_FILENAME
    else:
        d = cache.locate_chart_dir(chart_id)
        path = (d / DESCRIPTOR_FILENAME) if d else cache.charts_root() / chart_id / DESCRIPTOR_FILENAME
    if not path.exists():
        raise FileNotFoundError(f"charte inconnue : {chart_id} ({path})")
    return json.loads(path.read_text(encoding="utf-8"))


def list_charts(charts_root: str | Path | None = None) -> list[dict]:
    """Summary of all charts (for the SELECTION LIST at scan time).

    Sorted by descending date. "scanned" status = presence of measurements/*.ti3.

    per-serial READ: by default enumerates ALL charts of all known Z9s
    (``charts/<serial>/<chart_id>/``), client-free — V1 mono = a single
    serial, zero mixing. If ``charts_root`` is provided (a ``<serial>`` folder),
    we restrict to that one.
    """
    if charts_root is not None:
        chart_dirs = [d for d in Path(charts_root).iterdir() if d.is_dir()] \
            if Path(charts_root).exists() else []
    else:
        base = cache.charts_root()
        chart_dirs = [d for sdir in base.iterdir() if sdir.is_dir()
                      for d in sdir.iterdir() if d.is_dir()] \
            if base.exists() else []
    out = []
    for d in chart_dirs:
        desc_path = d / DESCRIPTOR_FILENAME
        if not desc_path.is_file():
            continue
        try:
            desc = json.loads(desc_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        meas = d / "measurements"
        scanned = meas.is_dir() and any(meas.glob("*.ti3"))
        out.append({
            "chart_id": desc.get("chart_id"),
            "created_at": desc.get("created_at"),
            "paper": (desc.get("paper") or {}).get("name"),
            "paper_media_id": (desc.get("paper") or {}).get("media_id"),
            "format": (desc.get("media") or {}).get("format_key"),
            "patch_count": (desc.get("geometry") or {}).get("patch_count"),
            "cols": (desc.get("geometry") or {}).get("cols"),
            "source": desc.get("source"),
            "printed_at": desc.get("printed_at"),     # set on successful print
            "printed": bool(desc.get("printed_at")),
            "scanned": scanned,
            "purpose": desc.get("purpose") or "profiling",   # profiling | validation
            "lightened": bool(desc.get("lightened")),        # chart (TIFF) purged, ti3 kept
        })
    out.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return out
