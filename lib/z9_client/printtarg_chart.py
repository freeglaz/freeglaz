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

"""Charts for an EXTERNAL spectrophotometer: ArgyllCMS printtarg, composed for the roll
or for a single sheet (one block of strips, within what the sheet takes centred).

Second chart path, independent from the honeycomb + built-in spectrophotometer path
(``sol_chart``)::

    .ti1 (targen or provided) → printtarg → .ti2 + page TIFFs
      → checks (ti1↔ti2, pages, patch positions) → pages composed into ONE TIFF
      → chart.json (layout "printtarg", no scanLayout)

The chart is later read with ArgyllCMS ``chartread``, which only needs the ``.ti2``.

printtarg OWNS the layout: patch size, spacers, strip order, strip and patch labels.
freeglaz never lays patches out itself. It runs printtarg, checks what it wrote, and
composes its pages for the roll (pixel copy, no resampling). The original pages and
the ``.ti2`` are kept.

Roll composition ("roll_layout"):
  - ``transverse`` (default): printtarg draws its strips side by side across the page
    WIDTH and runs each strip down the page HEIGHT. freeglaz asks it for a page just
    wide enough to hold all the strips (found by quick PostScript probes, no TIFF),
    then turns that page by 90° clockwise: each strip now crosses the roll, and the
    strips follow one another as the paper advances. Nothing is cropped. Above
    ``MAX_PAGE_LENGTH_MM`` the strips are split evenly over several pages, each turned
    and stacked along the roll. The page width only changes printtarg's pagination:
    with the same random start, the .ti2 data rows are the same at any width.
  - ``pages`` (previous behaviour, kept to go back): printtarg's nominal pages stacked
    top to bottom, unturned.

Options added to the printtarg command, each for a measured reason:
  - ``-C`` (no TIFF compression): printtarg compresses with LZW by default, which
    tifffile cannot decode without the optional ``imagecodecs`` package;
  - ``-R <n>`` (random start): without it the patch placement changes on every run,
    so the command line alone would not reproduce the chart;
  - ``-v``: printtarg reports its layout (patches per strip, pages), kept for audit;
  - ``-M 6`` (transverse only): printtarg includes its own 6 mm page margin in the
    TIFF, so that once turned the first strip does not sit at the cut edge of the roll.

printtarg pads the last strip with patches whose SAMPLE_ID is ``0``, all of one colour:
the ``INDEX 0`` row of the ``.ti1``'s DENSITY_EXTREME_VALUES table (white for a plain
targen run, another colour for a preconditioned one, ``targen -c``). They are recorded as
padding; a ``.ti1`` that already uses SAMPLE_ID ``0`` is refused.

Offline only: no printer, no instrument, no network. Errors carry stable codes.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from . import cache
from . import chart_geometry_refonte as G
from .printing import MECHANICAL_MARGINS_MM

logger = logging.getLogger(__name__)

LAYOUT = "printtarg"

# printtarg's own page margin (``-m``, default 6 mm). Without -M the page TIFF is the
# page MINUS this margin on each side; with -M it is the whole page. Checked against the
# real TIFF size.
PRINTTARG_MARGIN_MM = 6.0

ROLL_LAYOUT_TRANSVERSE = "transverse"
ROLL_LAYOUT_PAGES = "pages"
ROLL_LAYOUTS = (ROLL_LAYOUT_TRANSVERSE, ROLL_LAYOUT_PAGES)
# Clockwise: printtarg's label reads upright, strip A comes first (top) and the strips
# follow in label order; strip letters and patch 1 end up on the right-hand side.
ROTATIONS = {"cw": -1, "ccw": 1}               # numpy rot90 k
DEFAULT_ROTATION = "cw"
# Longest printtarg page (= longest block on the roll) in transverse mode. Resource
# guard only (printtarg rasterises a whole page: ~0.4 GB per metre at 300 dpi, 500 mm).
MAX_PAGE_LENGTH_MM = 1000
# printtarg refuses custom pages somewhere between 4000 and 4500 mm (3.5.0).
_PROBE_MAX_PAGE_MM = 4000
DEFAULT_DPI = 300
PRINTTARG_TIMEOUT_S = 300.0
WHITE16 = 65535

# Only the default index patterns are understood (freeglaz never passes -x / -y).
STRIP_INDEX_PATTERN = "A-Z, A-Z"
PATCH_INDEX_PATTERN = "0-9,@-9,@-9;1-999"
INDEX_ORDER = "STRIP_THEN_PATCH"
PADDING_SAMPLE_ID = "0"

# printtarg -T rewrites the .ti1 RGB in the .ti2 quantized to 16 bits (the values it
# renders). Allowed gap: half a 16-bit step plus the 4-5 digits printed (percent).
_RGB_TOL_16 = 50.0 / WHITE16 + 1e-4
_SAFE_BASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LOC = re.compile(r"^([A-Z]+)([0-9]+)$")


@dataclass(frozen=True)
class InstrumentSpec:
    key: str                      # stable freeglaz key
    name: str                     # instrument name (log / descriptor, not UI text)
    selector: str                 # printtarg -i value
    ti2_target_instrument: str    # TARGET_INSTRUMENT printtarg writes in the .ti2


# Whitelist: only the instruments actually studied. Nothing else is offered.
INSTRUMENTS: dict[str, InstrumentSpec] = {
    # printtarg 3.4.1 and 3.5.0 write the i1 Pro family name for -i3p.
    "i1pro3plus": InstrumentSpec("i1pro3plus", "X-Rite i1Pro 3 Plus", "3p",
                                 "GretagMacbeth i1 Pro"),
    "colormunki": InstrumentSpec("colormunki", "X-Rite ColorMunki", "CM",
                                 "X-Rite ColorMunki"),
}


@dataclass(frozen=True)
class LayoutPreset:
    page_w_mm: float
    page_h_mm: float
    unlimited_strip: bool         # printtarg -P (strip length follows the page height)


def is_sheet(media_key: str) -> bool:
    return G.MEDIA[media_key].source == "sheet"


def _margins(media_key: str) -> dict:
    """Mechanical margins of the source the media is printed from (sheet = manual feed)."""
    return MECHANICAL_MARGINS_MM["MANUALFEED" if is_sheet(media_key) else "ROLL"]


def sheet_chart_limits(media_key: str) -> dict:
    """Largest chart a SHEET can take with the print pipeline's placement (centred on the
    sheet, print_geometry.compute_geometry), the sheet being taken as the Z9 measures it
    (nominal height minus G.SHEET_HEIGHT_ALLOWANCE_MM): centred, the image keeps
    max(top, bottom) on both sides vertically and max(left, right) horizontally.
    The print route still checks the sheet actually loaded (the hard backstop)."""
    media = G.MEDIA[media_key]
    m = _margins(media_key)
    length = media.height_mm - G.SHEET_HEIGHT_ALLOWANCE_MM
    return {"sheet_w_mm": media.width_mm, "sheet_h_mm": media.height_mm,
            "assumed_sheet_h_mm": length,
            "max_w_mm": round(media.width_mm - 2 * max(m["left"], m["right"]), 3),
            "max_h_mm": round(length - 2 * max(m["top"], m["bottom"]), 3)}


def _sheet_block_mm(media_key: str) -> float:
    """Longest block of strips a sheet takes (whole mm): where the transverse search starts
    and stops on a sheet. Derived from the sheet and its margins, never typed in."""
    return float(math.floor(sheet_chart_limits(media_key)["max_h_mm"]))


# Default page per (instrument, media): the page width (where the transverse search starts)
# and the page height, which becomes the STRIP LENGTH once the page is turned. Evidence:
#  - ColorMunki on an A4 sheet (manual feed): 190 mm strips across the sheet. printtarg
#    3.4.1 and 3.5.0 lay 8 ColorMunki patches on them (9 from 194 mm), the same patch size
#    as on the roll; 8 strips need a 241 mm block, one block within sheet_chart_limits():
#    64 patches = 8 strips of 8. READ for real once (Z9 24in, manual feed, 8 x 8, strips read
#    right to left; private notes 153-156). Larger counts are refused (one block per sheet).
#  - ColorMunki on the 24" roll: 450 mm strips (26 patches per strip), not read for real yet.
#  - i1Pro 3 Plus on the 24" roll: -P and a 500 mm page (20 patches per strip). The real scan
#    length on the ruler (X-Rite: 515 mm max) must be proven by a reading. Not offered on a
#    sheet: its -P 500 mm strips cannot cross an A4.
# A combination missing here is refused (unsupported_media), never approximated.
PRESETS: dict[tuple[str, str], LayoutPreset] = {
    ("i1pro3plus", "roll24"): LayoutPreset(610.0, 500.0, True),
    ("colormunki", "roll24"): LayoutPreset(610.0, 450.0, False),
    ("colormunki", "a4"): LayoutPreset(_sheet_block_mm("a4"), 190.0, False),
}
SUPPORTED_MEDIA = ("roll24", "a4")

# Loaded media accepted for a chart laid out for a given format (print route). A sheet chart
# goes on a sheet of its format, a roll chart on a roll; the print pipeline's printable-area
# check stays the hard backstop. Same tolerances as the interface (measurementMethod.js).
SHEET_WIDTH_TOL_MM = 3.0
SHEET_LENGTH_SHORT_MM = 10.0       # the Z9 measures sheets shorter than nominal (A4: ~293 mm)
_SHEET_SOURCES = ("MANUAL_FEED", "SHEET")


def loaded_media_mismatch(media_key: Optional[str], *, media_source: Optional[str],
                          width_mm: Optional[float], height_mm: Optional[float]) -> Optional[str]:
    """Why the loaded media cannot take a chart laid out for ``media_key``, or None.

    - ``chart_format_unknown``: the chart names no format this version knows (never guessed).
    - A SHEET chart goes only on a sheet the printer identifies: a sheet source (MANUAL_FEED,
      SHEET) with both dimensions known and matching the format. Otherwise ``source`` (a roll),
      ``source_unknown`` (no source, or one freeglaz does not know), ``sheet_unidentified``
      (dimensions missing) or ``sheet_format`` (another size). A second roll (the Z9 Pro's
      ROLL2) is left to the print pipeline, whose own guard refuses it.
    - A ROLL chart: refused on a sheet source (``source``); unknown or ROLL2 sources and the roll
      width stay with the print pipeline and the interface, as before."""
    media = G.MEDIA.get(media_key) if isinstance(media_key, str) else None
    if media is None:
        return "chart_format_unknown"
    src = (media_source or "").strip().upper()
    if media.source == "roll":
        return "source" if src in _SHEET_SOURCES else None
    if src.startswith("ROLL") and src != "ROLL":
        return None                    # ROLL2: the print pipeline's Z9 Pro guard refuses it
    if src == "ROLL":
        return "source"
    if src not in _SHEET_SOURCES:
        return "source_unknown"
    if not (isinstance(width_mm, (int, float)) and isinstance(height_mm, (int, float))
            and width_mm > 0 and height_mm > 0):
        return "sheet_unidentified"
    if (abs(width_mm - media.width_mm) > SHEET_WIDTH_TOL_MM
            or height_mm > media.height_mm + SHEET_WIDTH_TOL_MM
            or media.height_mm - height_mm > SHEET_LENGTH_SHORT_MM):
        return "sheet_format"
    return None


def supported_options() -> dict:
    """What the generator accepts, derived from INSTRUMENTS, PRESETS and SUPPORTED_MEDIA
    (the single source: routes and UI never keep their own copy). Static: no binary is
    run, no device is touched."""
    return {
        "layout": LAYOUT,
        "media": list(SUPPORTED_MEDIA),
        "instruments": [
            {"key": spec.key, "name": spec.name, "printtarg_selector": spec.selector,
             "ti2_target_instrument": spec.ti2_target_instrument,
             "presets": [{"media_key": media, "page_w_mm": p.page_w_mm, "page_h_mm": p.page_h_mm,
                          "unlimited_strip": p.unlimited_strip}
                         for (inst, media), p in PRESETS.items()
                         if inst == spec.key and media in SUPPORTED_MEDIA]}
            for spec in INSTRUMENTS.values()],
        # The API always composes with the default; "pages" stays a library option.
        "roll_layout": {"default": ROLL_LAYOUT_TRANSVERSE, "selectable": False,
                        "library_modes": list(ROLL_LAYOUTS)},
        "rotation": DEFAULT_ROTATION,
        "dpi": DEFAULT_DPI,
        "max_page_length_mm": MAX_PAGE_LENGTH_MM,
    }


class PrinttargChartError(Exception):
    """Generation refused or failed. ``code`` is stable; ``detail`` is for logs."""

    def __init__(self, code: str, detail: str = "", **info):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.info = info


# ─── Small helpers ──────────────────────────────────────────────────────────
def _fmt_mm(v: float) -> str:
    s = f"{float(v):.1f}"
    return s[:-2] if s.endswith(".0") else s


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _px(mm: float, dpi: float) -> float:
    return mm / 25.4 * dpi


def _mm(px: float, dpi: float) -> float:
    return px * 25.4 / dpi


def strip_labels(count: int) -> list[str]:
    """Strip labels of printtarg's default pattern "A-Z, A-Z": A…Z, AA…AZ, BA…ZZ."""
    letters = [chr(65 + i) for i in range(26)]
    if count > 26 + 26 * 26:
        raise PrinttargChartError("too_many_strips", f"{count} strips")
    out = letters[:count]
    for i in range(26, count):
        j = i - 26
        out.append(letters[j // 26] + letters[j % 26])
    return out


def default_random_start(chart_id: str, n_patches: int) -> int:
    """Deterministic printtarg random start derived from the chart id (recorded)."""
    digest = hashlib.sha256(chart_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % max(1, n_patches)


# ─── CGATS (.ti1 / .ti2) ────────────────────────────────────────────────────
def _read_cgats(path: Path) -> tuple[dict, list[str], list[list[str]]]:
    from .profiling import parse_ti3
    try:
        header, fmt, rows = parse_ti3(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (ValueError, IndexError, OSError) as e:
        raise PrinttargChartError("cgats_unreadable", f"{Path(path).name}: {e}")
    kw = {}
    for line in header:
        m = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s+"(.*)"\s*$', line)
        if m:
            kw.setdefault(m.group(1), m.group(2))
    rows = [[c.strip('"') for c in r] for r in rows]
    return kw, fmt, rows


def _rgb_index(fmt: list[str], what: str) -> tuple[int, int, int, int]:
    try:
        return (fmt.index("SAMPLE_ID"), fmt.index("RGB_R"), fmt.index("RGB_G"),
                fmt.index("RGB_B"))
    except ValueError:
        raise PrinttargChartError(f"{what}_fields_missing",
                                  f"SAMPLE_ID/RGB_R/RGB_G/RGB_B required, got {fmt}")


def read_ti1_patches(path: str | Path) -> list[tuple[str, tuple[float, float, float]]]:
    """Patches of a .ti1 in file order: [(sample_id, (r, g, b) percent)].

    Refuses what printtarg would turn into an ambiguous chart: no RGB, values out of
    0..100, duplicated SAMPLE_ID, or SAMPLE_ID "0" (printtarg's padding id)."""
    kw, fmt, rows = _read_cgats(Path(path))
    rep = (kw.get("COLOR_REP") or "").upper()
    if "RGB" not in rep:
        raise PrinttargChartError("ti1_not_rgb", f"COLOR_REP={rep or '?'}")
    i_s, i_r, i_g, i_b = _rgb_index(fmt, "ti1")
    out, seen = [], set()
    for r in rows:
        try:
            sid = r[i_s]
            rgb = (float(r[i_r]), float(r[i_g]), float(r[i_b]))
        except (IndexError, ValueError):
            raise PrinttargChartError("ti1_row_invalid", " ".join(r)[:80])
        if sid == PADDING_SAMPLE_ID:
            raise PrinttargChartError("ti1_sample_id_reserved",
                                      "SAMPLE_ID 0 is printtarg's padding id")
        if sid in seen:
            raise PrinttargChartError("ti1_duplicate_sample_id", sid)
        if not all(0.0 <= v <= 100.0 for v in rgb):
            raise PrinttargChartError("ti1_rgb_out_of_range", f"{sid}: {rgb}")
        seen.add(sid)
        out.append((sid, rgb))
    if not out:
        raise PrinttargChartError("ti1_empty")
    return out


_WHITE = (100.0, 100.0, 100.0)


def read_ti1_padding_rgb(path: str | Path) -> tuple[float, float, float]:
    """The colour printtarg pads the last strip with: the ``INDEX 0`` row of the .ti1's
    DENSITY_EXTREME_VALUES table, white when the table is absent. Established with
    printtarg 3.5.0 for -iCM and -i3p (2026-11-22): editing that row moves the padding with
    it, and a preconditioned .ti1 (targen -c) does not pad with white."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    i = text.find("DENSITY_EXTREME_VALUES")
    if i < 0:
        return _WHITE
    block = text[i:]
    fmt_m = re.search(r"BEGIN_DATA_FORMAT\s+(.*?)\s+END_DATA_FORMAT", block, re.S)
    data_m = re.search(r"BEGIN_DATA[ \t]*\n(.*?)\n\s*END_DATA", block, re.S)
    if not (fmt_m and data_m):
        return _WHITE
    fmt = fmt_m.group(1).split()
    try:
        ii, ir, ig, ib = (fmt.index(c) for c in ("INDEX", "RGB_R", "RGB_G", "RGB_B"))
    except ValueError:
        return _WHITE
    rows = [ln.split() for ln in data_m.group(1).splitlines() if ln.strip()]
    row = next((r for r in rows if len(r) > ii and r[ii] == "0"), None)
    if row is None:
        return _WHITE
    try:
        return (float(row[ir]), float(row[ig]), float(row[ib]))
    except (ValueError, IndexError):
        return _WHITE


def check_ti1_tables(path: str | Path) -> int:
    """printtarg needs the 2 or 3 tables targen writes (patches, then density extremes
    and device combinations, used for the spacers). A single-table .ti1, accepted by
    the honeycomb path, is refused here: freeglaz does not make those tables up."""
    n = sum(1 for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
            if line.strip() == "BEGIN_DATA")
    if not 2 <= n <= 3:
        raise PrinttargChartError("ti1_missing_targen_tables",
                                  f"{n} table(s); printtarg needs the 2 or 3 written by targen")
    return n


@dataclass
class Ti2Check:
    keywords: dict
    steps_in_pass: int
    passes_in_strips: list[int]
    labels: list[str]
    patches: list[dict]           # strip/patch order: sample_id, sample_loc, strip, patch, page, rgb
    padding_locations: list[str]

    @property
    def total_strips(self) -> int:
        return sum(self.passes_in_strips)

    @property
    def n_pages(self) -> int:
        return len(self.passes_in_strips)


def check_ti2(ti1_patches: list, ti2_path: str | Path, *, instrument: str,
              padding_rgb: Optional[tuple] = None) -> Ti2Check:
    """Check the .ti2 printtarg wrote against the .ti1 it was given.

    Every .ti1 SAMPLE_ID appears exactly once with the same RGB (equal once quantized
    to 16 bits: printtarg -T writes the rendered values); the only extra rows are padding
    with SAMPLE_ID 0 in the colour printtarg pads with (``padding_rgb``, see
    read_ti1_padding_rgb; white by default); every strip × patch slot is used exactly once;
    the strip count per page and the index patterns are the ones freeglaz understands."""
    spec = INSTRUMENTS[instrument]
    kw, fmt, rows = _read_cgats(Path(ti2_path))
    i_s, i_r, i_g, i_b = _rgb_index(fmt, "ti2")
    if "SAMPLE_LOC" not in fmt:
        raise PrinttargChartError("ti2_fields_missing", "SAMPLE_LOC")
    i_l = fmt.index("SAMPLE_LOC")

    def _kw(name, expected=None):
        v = kw.get(name)
        if v is None:
            raise PrinttargChartError("ti2_keyword_missing", name)
        if expected is not None and v != expected:
            raise PrinttargChartError("ti2_keyword_unexpected", f"{name}={v!r}, expected {expected!r}")
        return v

    _kw("TARGET_INSTRUMENT", spec.ti2_target_instrument)
    _kw("STRIP_INDEX_PATTERN", STRIP_INDEX_PATTERN)
    _kw("PATCH_INDEX_PATTERN", PATCH_INDEX_PATTERN)
    _kw("INDEX_ORDER", INDEX_ORDER)
    try:
        steps = int(_kw("STEPS_IN_PASS"))
        passes = [int(x) for x in _kw("PASSES_IN_STRIPS2").split(",")]
    except ValueError as e:
        raise PrinttargChartError("ti2_keyword_unexpected", str(e))
    if steps <= 0 or not passes or min(passes) <= 0:
        raise PrinttargChartError("ti2_keyword_unexpected", f"steps={steps} passes={passes}")
    total = sum(passes)
    if len(rows) != steps * total:
        raise PrinttargChartError("ti2_set_count", f"{len(rows)} rows != {steps} × {total} strips")

    labels = strip_labels(total)
    label_idx = {lab: i for i, lab in enumerate(labels)}
    page_of_strip = [p for p, n in enumerate(passes) for _ in range(n)]
    ti1_rgb = dict(ti1_patches)

    slots, seen_ids, patches, padding = set(), set(), [], []
    for r in rows:
        sid, loc = r[i_s], r[i_l]
        m = _LOC.match(loc)
        if not m or m.group(1) not in label_idx:
            raise PrinttargChartError("ti2_location_invalid", loc)
        strip, patch = label_idx[m.group(1)], int(m.group(2))
        if not 1 <= patch <= steps:
            raise PrinttargChartError("ti2_location_invalid", f"{loc}: patch > {steps}")
        if (strip, patch) in slots:
            raise PrinttargChartError("ti2_location_duplicate", loc)
        slots.add((strip, patch))
        try:
            rgb = (float(r[i_r]), float(r[i_g]), float(r[i_b]))
        except ValueError:
            raise PrinttargChartError("ti2_row_invalid", " ".join(r)[:80])
        if sid == PADDING_SAMPLE_ID:
            if _q16(rgb) != _q16(padding_rgb or _WHITE):
                raise PrinttargChartError("ti2_padding_unexpected",
                                          f"{loc}: {rgb}, expected {padding_rgb or _WHITE}")
            padding.append(loc)
        else:
            if sid not in ti1_rgb:
                raise PrinttargChartError("ti2_unknown_sample_id", sid)
            if sid in seen_ids:
                raise PrinttargChartError("ti2_duplicate_sample_id", sid)
            # printtarg -T writes the values it renders, i.e. quantized to 16 bits.
            if _q16(rgb) != _q16(ti1_rgb[sid]) or any(
                    abs(a - b) > _RGB_TOL_16 for a, b in zip(rgb, ti1_rgb[sid])):
                raise PrinttargChartError("ti2_rgb_mismatch", f"{sid}: {rgb} != {ti1_rgb[sid]}")
            seen_ids.add(sid)
        patches.append({"sample_id": sid, "sample_loc": loc, "strip": labels[strip],
                        "strip_index": strip, "patch": patch,
                        "page": page_of_strip[strip] + 1, "rgb": list(rgb)})
    missing = [sid for sid, _ in ti1_patches if sid not in seen_ids]
    if missing:
        raise PrinttargChartError("ti2_missing_sample_id", ", ".join(missing[:10]),
                                  count=len(missing))
    if len(padding) != len(rows) - len(ti1_patches):
        raise PrinttargChartError("ti2_padding_count",
                                  f"{len(padding)} padding rows for "
                                  f"{len(rows) - len(ti1_patches)} expected")
    patches.sort(key=lambda p: (p["strip_index"], p["patch"]))
    return Ti2Check(keywords=kw, steps_in_pass=steps, passes_in_strips=passes,
                    labels=labels, patches=patches, padding_locations=padding)


# ─── printtarg ──────────────────────────────────────────────────────────────
def build_printtarg_argv(printtarg_path: str, *, instrument: str, page_w_mm: float,
                         page_h_mm: float, unlimited_strip: bool, dpi: Optional[int],
                         random_start: int, base: str,
                         margin_in_tiff: bool = False) -> list[str]:
    """printtarg argv (never a shell). ``base`` is a bare file name, run in its folder.

    ``dpi=None`` builds a layout PROBE: no TIFF (printtarg writes PostScript), same
    layout options otherwise. ``margin_in_tiff`` adds ``-M <margin>``."""
    if instrument not in INSTRUMENTS:
        raise PrinttargChartError("unsupported_instrument", str(instrument))
    if not _SAFE_BASE.match(base or ""):
        raise PrinttargChartError("invalid_base_name", repr(base))
    if not (0 < page_w_mm <= _PROBE_MAX_PAGE_MM and 0 < page_h_mm <= _PROBE_MAX_PAGE_MM):
        raise PrinttargChartError("invalid_page_size", f"{page_w_mm}x{page_h_mm}")
    if dpi is not None and (int(dpi) != dpi or not 50 <= dpi <= 1200):
        raise PrinttargChartError("invalid_dpi", str(dpi))
    if int(random_start) < 0:
        raise PrinttargChartError("invalid_random_start", str(random_start))
    argv = [printtarg_path, "-v", f"-i{INSTRUMENTS[instrument].selector}", "-L"]
    if unlimited_strip:
        argv.append("-P")
    if margin_in_tiff:
        argv += ["-M", _fmt_mm(PRINTTARG_MARGIN_MM)]
    if dpi is not None:
        argv.append("-C")
    argv += ["-R", str(int(random_start))]
    if dpi is not None:
        argv.append(f"-T{int(dpi)}")
    argv += [f"-p{_fmt_mm(page_w_mm)}x{_fmt_mm(page_h_mm)}", base]
    return argv


_PROGRESS_LINE = re.compile(r"^\s*\d+%\s*$")


def printtarg_failure_message(output: str, returncode: int) -> str:
    """Readable reason of a failed printtarg run: its ``Error`` line when it wrote one, otherwise
    its exit status (a crash is named as such) and its last meaningful line. The percentage
    progress lines printtarg writes are never the reason."""
    lines = [ln.strip() for ln in (output or "").splitlines()
             if ln.strip() and not _PROGRESS_LINE.match(ln)]
    err = next((ln for ln in lines if "Error" in ln), None)
    if err:
        return err
    crashed = returncode < 0 or returncode >= 128
    head = f"printtarg {'crashed' if crashed else 'failed'} (exit status {returncode})"
    if any("-nan" in ln or " nan" in ln for ln in lines):
        head += "; invalid (NaN) colour values in the .ti1"
    return f"{head}: {lines[-1]}" if lines else head


class _LayoutProber:
    """Ask printtarg how many strips fit on a page of a given width (``Rows per page``
    in its -v report), without rendering a TIFF. Each probe is a throw-away run in its
    own folder; results are cached and logged for the descriptor."""

    def __init__(self, exe: str, folder: Path, ti1: Path, *, instrument: str,
                 page_h_mm: float, unlimited_strip: bool, random_start: int,
                 margin_in_tiff: bool, timeout: float):
        self.exe, self.folder, self.ti1 = exe, folder, ti1
        self.kw = dict(instrument=instrument, page_h_mm=page_h_mm,
                       unlimited_strip=unlimited_strip, random_start=random_start,
                       margin_in_tiff=margin_in_tiff)
        self.timeout = timeout
        self.cache: dict[int, int] = {}
        self.log: list[dict] = []
        self.total_strips: Optional[int] = None
        self.last_error = ""

    def rows(self, page_w_mm: int) -> int:
        if page_w_mm in self.cache:
            return self.cache[page_w_mm]
        base = f"probe{len(self.log):02d}"
        shutil.copyfile(self.ti1, self.folder / f"{base}.ti1")
        argv = build_printtarg_argv(self.exe, page_w_mm=page_w_mm, dpi=None, base=base,
                                    **self.kw)
        try:
            proc = subprocess.run(argv, cwd=str(self.folder), capture_output=True,
                                  text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise PrinttargChartError("printtarg_timeout", f"probe > {self.timeout:.0f} s")
        except OSError as e:
            raise PrinttargChartError("printtarg_not_runnable", str(e))
        out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        m = re.search(r"Rows per page\s*=\s*(\d+)", out)
        rows = int(m.group(1)) if (m and proc.returncode == 0) else 0
        if rows == 0:
            self.last_error = printtarg_failure_message(out, proc.returncode)
        elif self.total_strips is None and (self.folder / f"{base}.ti2").is_file():
            kw, _, _ = _read_cgats(self.folder / f"{base}.ti2")
            try:
                self.total_strips = sum(int(x) for x in kw["PASSES_IN_STRIPS2"].split(","))
            except (KeyError, ValueError):
                pass
        self.cache[page_w_mm] = rows
        self.log.append({"page_w_mm": page_w_mm, "rows_per_page": rows})
        return rows

    def smallest_width(self, strips: int, lo: int, hi: int) -> int:
        """Smallest page width (whole mm) holding ``strips`` strips, by bisection.
        ``hi`` must already hold them; ``lo`` must not (or be a trivial bound)."""
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.rows(mid) >= strips:
                hi = mid
            else:
                lo = mid
        return hi


def plan_transverse_pages(prober: _LayoutProber, start_w_mm: int, *,
                          max_page_mm: int = MAX_PAGE_LENGTH_MM) -> dict:
    """Page width and page count for the transverse composition.

    All the strips on ONE page when it is at most ``max_page_mm`` long; otherwise the
    strips are spread evenly over k pages (ceil(S / k) per page), so that no page is
    left nearly empty. Every width returned has been checked by a printtarg probe."""
    floor_w = math.ceil(2 * PRINTTARG_MARGIN_MM)      # a page of margins holds no strip
    r0 = prober.rows(start_w_mm)
    total = prober.total_strips
    if r0 == 0 or not total:
        raise PrinttargChartError("printtarg_failed", prober.last_error or "layout probe failed")
    if r0 >= total:
        return {"strips_total": total, "pages": 1, "strips_per_page": total,
                "page_w_mm": prober.smallest_width(total, floor_w, start_w_mm)}
    # Estimated width for all strips (the pitch estimate includes the label: an upper
    # bound in practice), checked by a probe before it is used as a bound.
    pitch = (start_w_mm - 2 * PRINTTARG_MARGIN_MM) / r0
    guess = min(max_page_mm, math.ceil(start_w_mm + (total - r0) * pitch * 1.1 + 2 * pitch))
    if prober.rows(guess) >= total:
        return {"strips_total": total, "pages": 1, "strips_per_page": total,
                "page_w_mm": prober.smallest_width(total, start_w_mm, guess)}
    r_max = prober.rows(max_page_mm)
    if r_max >= total:
        return {"strips_total": total, "pages": 1, "strips_per_page": total,
                "page_w_mm": prober.smallest_width(total, guess, max_page_mm)}
    pages = math.ceil(total / r_max)
    per_page = math.ceil(total / pages)
    lo = start_w_mm if r0 < per_page else floor_w
    hi = max_page_mm if r0 < per_page else start_w_mm
    return {"strips_total": total, "pages": pages, "strips_per_page": per_page,
            "page_w_mm": prober.smallest_width(per_page, lo, hi)}


def printtarg_version(printtarg_path: str) -> Optional[str]:
    """Version from printtarg's usage banner (no argument: prints usage, touches nothing)."""
    try:
        proc = subprocess.run([printtarg_path], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"Version\s+([0-9][0-9A-Za-z._-]*)", (proc.stderr or "") + (proc.stdout or ""))
    return m.group(1) if m else None


def _expected_page_files(workdir: Path, base: str, n_pages: int) -> list[Path]:
    """printtarg names a single page ``<base>.tif`` and several ``<base>_NN.tif``."""
    if n_pages == 1:
        return [workdir / f"{base}.tif"]
    return [workdir / f"{base}_{i:02d}.tif" for i in range(1, n_pages + 1)]


def run_printtarg(workdir: Path, base: str, argv: list[str], *,
                  timeout: float = PRINTTARG_TIMEOUT_S) -> dict:
    """Run printtarg in ``workdir`` (where ``<base>.ti1`` sits). Returns {argv, stdout,
    stderr, returncode}. Raises if it fails or writes no .ti2."""
    try:
        proc = subprocess.run(argv, cwd=str(workdir), capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        raise PrinttargChartError("printtarg_timeout", f"> {timeout:.0f} s")
    except OSError as e:
        raise PrinttargChartError("printtarg_not_runnable", str(e))
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0 or not (workdir / f"{base}.ti2").is_file():
        msg = printtarg_failure_message(err + "\n" + out, proc.returncode)
        raise PrinttargChartError("printtarg_failed", msg.strip(), returncode=proc.returncode)
    logger.info("printtarg executed: %s", " ".join(argv))
    return {"argv": list(argv), "stdout": out, "stderr": err, "returncode": proc.returncode}


def _verbose_layout(stdout: str) -> dict:
    """Layout figures printtarg reports with -v (cross-checked against the .ti2)."""
    out = {}
    for key, pat in (("patches_per_strip", r"Test patches per row\s*=\s*(\d+)"),
                     ("pages", r"Total pages needed\s*=\s*(\d+)")):
        m = re.search(pat, stdout or "")
        if m:
            out[key] = int(m.group(1))
    return out


# ─── Page TIFFs ─────────────────────────────────────────────────────────────
def _page_meta(path: Path) -> dict:
    import tifffile
    with tifffile.TiffFile(path) as tif:
        p = tif.pages[0]
        unit = int(p.tags[296].value) if 296 in p.tags else 2
        xres = p.tags[282].value if 282 in p.tags else None
        dpi = None
        if xres:
            v = xres[0] / xres[1] if isinstance(xres, tuple) else float(xres)
            dpi = v * 2.54 if unit == 3 else v
        return {"path": path, "height_px": int(p.shape[0]), "width_px": int(p.shape[1]),
                "samples": int(p.samplesperpixel), "dtype": str(p.dtype),
                "photometric": int(p.photometric), "compression": int(p.compression),
                "resolution_unit": {1: "NONE", 2: "INCH", 3: "CENTIMETER"}.get(unit, str(unit)),
                "dpi": dpi, "n_ifd": len(tif.pages)}


def _open_page(path: Path):
    """Pixels of a page TIFF, memory-mapped when possible (no full copy in RAM)."""
    import tifffile
    try:
        return tifffile.memmap(path, mode="r")
    except (ValueError, OSError):
        return tifffile.imread(path)


def _check_pages(pages: list[Path], *, page_w_mm: float, page_h_mm: float, dpi: int,
                 margin_in_tiff: bool = False) -> list[dict]:
    """Real page TIFF sizes against the nominal page (minus printtarg's margin unless
    -M put it in the TIFF)."""
    cut = 0.0 if margin_in_tiff else 2 * PRINTTARG_MARGIN_MM
    exp_w = _px(page_w_mm - cut, dpi)
    exp_h = _px(page_h_mm - cut, dpi)
    metas = []
    for p in pages:
        m = _page_meta(p)
        if m["dtype"] != "uint16" or m["samples"] != 3 or m["photometric"] != 2:
            raise PrinttargChartError("page_format_unexpected",
                                      f"{p.name}: {m['dtype']} spp={m['samples']} "
                                      f"photometric={m['photometric']}")
        if m["compression"] != 1:
            raise PrinttargChartError("page_compressed", f"{p.name}: {m['compression']}")
        if abs(m["width_px"] - exp_w) > 1 or abs(m["height_px"] - exp_h) > 1:
            raise PrinttargChartError("page_size_unexpected",
                                      f"{p.name}: {m['width_px']}x{m['height_px']} px, "
                                      f"expected {exp_w:.1f}x{exp_h:.1f}")
        if m["dpi"] is None or abs(m["dpi"] - dpi) > 0.01:
            raise PrinttargChartError("page_resolution_unexpected", f"{p.name}: {m['dpi']}")
        metas.append(m)
    if len({m["width_px"] for m in metas}) != 1:
        raise PrinttargChartError("page_widths_differ")
    return metas


def _q16(rgb_percent) -> tuple[int, int, int]:
    """16-bit pixel values of an RGB in percent, as printtarg -T renders them."""
    return tuple(min(WHITE16, max(0, int(math.floor(v / 100.0 * WHITE16 + 0.5))))
                 for v in rgb_percent)


def _key16(rgb_percent) -> int:
    """Packed 16-bit RGB key (R << 32 | G << 16 | B) of an RGB in percent."""
    r, g, b = _q16(rgb_percent)
    return (r << 32) | (g << 16) | b


def _color_regions(page, step: int) -> dict:
    """Per distinct colour of a sub-sampled page: sample count, bounding box and
    centroid (full-resolution pixels). Patches are flat fills, so one colour = one
    patch when its value is unique in the chart."""
    sub = np.ascontiguousarray(page[::step, ::step, :3])
    w = sub.shape[1]
    keys = ((sub[..., 0].astype(np.uint64) << np.uint64(32))
            | (sub[..., 1].astype(np.uint64) << np.uint64(16))
            | sub[..., 2].astype(np.uint64)).ravel()
    order = np.argsort(keys, kind="stable")
    sk = keys[order]
    starts = np.flatnonzero(np.r_[True, sk[1:] != sk[:-1]])
    ys = (order // w).astype(np.int64)
    xs = (order % w).astype(np.int64)
    counts = np.diff(np.r_[starts, sk.size])
    return {
        "keys": sk[starts], "counts": counts,
        "ymin": np.minimum.reduceat(ys, starts) * step,
        "ymax": np.maximum.reduceat(ys, starts) * step,
        "xmin": np.minimum.reduceat(xs, starts) * step,
        "xmax": np.maximum.reduceat(xs, starts) * step,
        "cy": np.add.reduceat(ys, starts) / counts * step,
        "cx": np.add.reduceat(xs, starts) / counts * step,
    }


def verify_page_patches(page, page_patches: list[dict], page_labels: list[str], *,
                        steps: int, dpi: int, multiplicity: dict, step: int = 4) -> dict:
    """Check, from the pixels, that the page holds each patch where the .ti2 says.

    ``page`` is seen in printtarg's own orientation: strips are columns, left to right
    in label order, and patch 1 is at the TOP of its strip. Anything else (a mirror, a
    turn, a transposition) is refused, so a composed chart viewed through the inverse
    of its declared rotation must pass exactly like the original page.

    A patch whose RGB is unique in the chart is located by its exact 16-bit colour.
    Its column must be the strip its SAMPLE_LOC names and, within a strip, positions
    must follow the patch numbers on one regular pitch. Patches sharing their RGB with
    another patch, with the page background or with a spacer cannot be located: they
    are counted, not guessed."""
    reg = _color_regions(page, step)
    keys = reg["keys"]
    bg = int(keys[int(np.argmax(reg["counts"]))])
    if bg != (WHITE16 << 32 | WHITE16 << 16 | WHITE16):
        raise PrinttargChartError("page_background_not_white", hex(bg))

    missing, cands, skipped_dup, skipped_bg = [], [], 0, 0
    for p in page_patches:
        k = _key16(p["rgb"])
        i = int(np.searchsorted(keys, k))
        if i >= len(keys) or int(keys[i]) != k:
            missing.append(p["sample_loc"])
            continue
        if k == bg:
            skipped_bg += 1
        elif multiplicity.get(k, 0) > 1:
            skipped_dup += 1
        else:
            cands.append((p, i))
    if missing:
        raise PrinttargChartError("patch_missing_on_page", ", ".join(missing[:10]),
                                  count=len(missing))
    if not cands:
        raise PrinttargChartError("patch_map_no_reference", "no locatable patch on page")

    bw = np.array([reg["xmax"][i] - reg["xmin"][i] + step for _, i in cands], float)
    bh = np.array([reg["ymax"][i] - reg["ymin"][i] + step for _, i in cands], float)
    cnt = np.array([reg["counts"][i] for _, i in cands], float)
    mw, mh, mc = np.median(bw), np.median(bh), np.median(cnt)
    ok = (bw <= 1.5 * mw) & (bh <= 1.5 * mh) & (cnt >= 0.5 * mc) & (cnt <= 1.5 * mc)
    located = [(p, float(reg["cx"][i]), float(reg["cy"][i]))
               for (p, i), good in zip(cands, ok) if good]
    ambiguous = int((~ok).sum())

    # Columns: split the x-sorted centroids where the gap exceeds half a patch width.
    located.sort(key=lambda t: t[1])
    columns, cur = [], [located[0]]
    for prev, item in zip(located, located[1:]):
        if item[1] - prev[1] > 0.5 * mw:
            columns.append(cur)
            cur = []
        cur.append(item)
    columns.append(cur)
    if len(columns) != len(page_labels):
        raise PrinttargChartError("patch_map_strip_count",
                                  f"{len(columns)} columns found, {len(page_labels)} strips expected")
    for j, col in enumerate(columns):
        wrong = [p["sample_loc"] for p, _, _ in col if p["strip"] != page_labels[j]]
        if wrong:
            raise PrinttargChartError("patch_map_wrong_strip",
                                      f"column {j + 1} ({page_labels[j]}): {', '.join(wrong[:5])}")

    # Within a strip: y follows the patch number on one common pitch.
    slopes, fits = [], []
    for col in columns:
        ks = np.array([p["patch"] for p, _, _ in col], float)
        ys = np.array([cy for _, _, cy in col], float)
        if len(set(ks)) >= 2:
            slopes.append(np.polyfit(ks, ys, 1)[0])
        fits.append((ks, ys))
    if not slopes:
        raise PrinttargChartError("patch_map_no_reference", "no strip with two locatable patches")
    pitch = float(np.median(slopes))
    if pitch <= 0:
        raise PrinttargChartError("patch_map_orientation",
                                  "patch 1 is not at the top of its strip (mirrored or turned)")
    if any(s * pitch <= 0 or abs(s - pitch) > 0.05 * abs(pitch) for s in slopes):
        raise PrinttargChartError("patch_map_pitch_inconsistent",
                                  f"slopes {min(slopes):.1f}..{max(slopes):.1f} px")
    intercepts = []
    for j, (ks, ys) in enumerate(fits):
        a = float(np.median(ys - pitch * (ks - 1)))
        resid = np.abs(ys - (a + pitch * (ks - 1)))
        if resid.max() > 0.25 * abs(pitch):
            raise PrinttargChartError("patch_map_order",
                                      f"strip {page_labels[j]}: off by {resid.max():.0f} px")
        intercepts.append(a)

    col_x = [float(np.mean([cx for _, cx, _ in col])) for col in columns]
    if len(col_x) > 1 and min(np.diff(col_x)) <= 0.5 * mw:
        raise PrinttargChartError("patch_map_strip_count", "strip columns overlap")
    a0 = float(np.median(intercepts))
    top = a0 - mh / 2
    bottom = a0 + pitch * (steps - 1) + mh / 2
    return {
        "strips": len(page_labels),
        "patches_on_page": len(page_patches),
        "located": len(located),
        "not_located_duplicate_rgb": skipped_dup,
        "not_located_white": skipped_bg,
        "not_located_ambiguous": ambiguous,
        "patch_1_at": "top",
        "patch_pitch_mm": round(_mm(abs(pitch), dpi), 2),
        "strip_pitch_mm": (round(_mm(float(np.median(np.diff(col_x))), dpi), 2)
                           if len(col_x) > 1 else None),
        "patch_w_mm": round(_mm(mw, dpi), 1),
        "patch_h_mm": round(_mm(mh, dpi), 1),
        "patch_area_top_mm": round(_mm(top, dpi), 1),
        "patch_area_bottom_mm": round(_mm(bottom, dpi), 1),
        "strip_patch_span_mm": round(_mm(bottom - top, dpi), 1),
        "sampling_step_px": step,
        # page pixels, for the composed-chart geometry (not rounded)
        "_strip_centers_px": col_x,
        "_strip_pitch_px": float(np.median(np.diff(col_x))) if len(col_x) > 1 else mw,
        "_patch_area_px": [top, bottom],
    }


# ─── Composition ────────────────────────────────────────────────────────────
def rotated_view(arr, rotation: Optional[str]):
    """The page turned by 90° (``cw`` / ``ccw``) as a numpy VIEW: no copy, no
    resampling, every pixel kept. ``None`` returns the page as is."""
    if rotation is None:
        return arr
    if rotation not in ROTATIONS:
        raise PrinttargChartError("invalid_rotation", str(rotation))
    return np.rot90(arr, k=ROTATIONS[rotation], axes=(0, 1))


def _rotated_rows_reference(src, rotation: Optional[str], r0: int, r1: int):
    """Rows r0:r1 of the turned page, by explicit indexing (independent of np.rot90):
    clockwise, out[i, j] = src[H-1-j, i]; counter-clockwise, out[i, j] = src[j, W-1-i]."""
    if rotation is None:
        return src[r0:r1, :, :3]
    if rotation == "cw":
        return src[::-1, r0:r1, :3].transpose(1, 0, 2)
    w = src.shape[1]
    return src[:, w - r1:w - r0, :3][:, ::-1].transpose(1, 0, 2)


def stack_pages(pages: list[Path], out_path: Path, *, dpi: int, gap_px: int,
                icc_bytes: bytes, rotation: Optional[str] = None,
                chunk_bytes: int = 64 << 20) -> dict:
    """Compose page TIFFs top to bottom into ONE 16-bit TIFF (pixel copy).

    Each page is optionally turned by 90° (``rotation``), then copied as is, in
    printtarg's order, with ``gap_px`` white rows between pages. No resampling, no
    cropping, no colour change. The output carries an isotropic resolution in inches
    and the given ICC profile (pixels untouched). Written through a memory map in
    blocks, then re-read and compared to the pages with an independent formula."""
    import tifffile
    out_path = Path(out_path)
    if out_path.exists():
        raise PrinttargChartError("output_exists", str(out_path))
    srcs = [_open_page(p) for p in pages]
    if any(s.ndim != 3 or s.shape[2] < 3 or s.dtype != np.uint16 for s in srcs):
        raise PrinttargChartError("page_format_unexpected", "pages are not 16-bit RGB")
    blocks = [rotated_view(s, rotation) for s in srcs]
    width = blocks[0].shape[1]
    if any(b.shape[1] != width for b in blocks):
        raise PrinttargChartError("page_format_unexpected", "pages differ in size")
    height = sum(b.shape[0] for b in blocks) + gap_px * (len(blocks) - 1)
    rows_per_chunk = max(1, chunk_bytes // (width * 6))

    out = tifffile.memmap(
        out_path, shape=(height, width, 3), dtype=np.uint16, photometric="rgb",
        resolution=(dpi, dpi), resolutionunit="INCH",
        extratags=[(34675, "B", len(icc_bytes), icc_bytes, False)])
    offsets, y = [], 0
    try:
        for i, blk in enumerate(blocks):
            h = blk.shape[0]
            for r0 in range(0, h, rows_per_chunk):
                r1 = min(h, r0 + rows_per_chunk)
                out[y + r0:y + r1] = blk[r0:r1, :, :3]
            offsets.append(y)
            y += h
            if i < len(blocks) - 1:
                out[y:y + gap_px] = WHITE16
                y += gap_px
        out.flush()
    finally:
        del out

    chk = tifffile.memmap(out_path, mode="r")
    try:
        for src, blk, y0 in zip(srcs, blocks, offsets):
            h = blk.shape[0]
            for r0 in range(0, h, rows_per_chunk):
                r1 = min(h, r0 + rows_per_chunk)
                if not np.array_equal(chk[y0 + r0:y0 + r1],
                                      _rotated_rows_reference(src, rotation, r0, r1)):
                    raise PrinttargChartError("stack_verify_failed", f"block rows {r0}-{r1}")
        for y0, blk in zip(offsets[:-1], blocks[:-1]):
            g0 = y0 + blk.shape[0]
            if not bool((chk[g0:g0 + gap_px] == WHITE16).all()):
                raise PrinttargChartError("stack_verify_failed", "gap not white")
    finally:
        del chk
    with tifffile.TiffFile(out_path) as tif:
        tag = tif.pages[0].tags.get(34675)
        if tag is None or bytes(tag.value) != icc_bytes:
            raise PrinttargChartError("stack_verify_failed", "ICC profile not embedded")
    return {"width_px": width, "height_px": height, "offsets_px": offsets,
            "block_heights_px": [int(b.shape[0]) for b in blocks],
            "page_shapes_px": [[int(s.shape[1]), int(s.shape[0])] for s in srcs]}


def verify_composed_chart(chart, blocks: list[dict], pages_patches: list[list[dict]],
                          pages_labels: list[list[str]], *, steps: int, dpi: int,
                          multiplicity: dict) -> list[dict]:
    """Check the FINAL chart, block by block: each block seen through the inverse of
    its declared rotation (a view) must hold every patch where the .ti2 says, in
    printtarg's orientation. A mirrored, transposed or wrongly turned block fails."""
    reports = []
    for blk, patches, labels in zip(blocks, pages_patches, pages_labels):
        sub = chart[blk["y_px"]:blk["y_px"] + blk["h_px"]]
        inv = {"cw": "ccw", "ccw": "cw"}.get(blk["rotation"])
        reports.append(verify_page_patches(rotated_view(sub, inv), patches, labels,
                                           steps=steps, dpi=dpi, multiplicity=multiplicity))
    return reports


def strips_in_chart(blocks: list[dict], page_reports: list[dict],
                    pages_labels: list[list[str]], *, page_h_px: list[int],
                    dpi: int) -> list[dict]:
    """Where each strip lies in the composed chart (mm from the chart's top-left),
    from the located patches of each page (no edge detection). Turned clockwise, a page
    column x becomes chart row y0 + x and a page row r becomes chart column H-1-r."""
    out = []
    for blk, rep, labels, hp in zip(blocks, page_reports, pages_labels, page_h_px):
        half = rep["_strip_pitch_px"] / 2
        top, bottom = rep["_patch_area_px"]
        for lab, cx in zip(labels, rep["_strip_centers_px"]):
            if blk["rotation"] == "cw":
                y, x0, x1 = blk["y_px"] + cx, hp - 1 - bottom, hp - 1 - top
                side = "right"
            elif blk["rotation"] == "ccw":
                y = blk["y_px"] + blk["h_px"] - 1 - cx
                x0, x1, side = top, bottom, "left"
            else:
                y, x0, x1, side = None, cx - half, cx + half, "top"
            entry = {"label": lab, "block": blk["page"], "patch_1_side": side}
            if y is not None:
                entry.update(y_mm=[round(_mm(y - half, dpi), 2), round(_mm(y + half, dpi), 2)],
                             x_mm=[round(_mm(x0, dpi), 2), round(_mm(x1, dpi), 2)])
            else:
                entry.update(x_mm=[round(_mm(x0, dpi), 2), round(_mm(x1, dpi), 2)],
                             y_mm=[round(_mm(blk["y_px"] + top, dpi), 2),
                                   round(_mm(blk["y_px"] + bottom, dpi), 2)])
            out.append(entry)
    return out


def write_preview(tiff_path: Path, out_png: Path, max_width: int = 1200) -> None:
    """Small 8-bit PNG of the stacked chart (nearest-neighbour sub-sampling)."""
    from PIL import Image
    arr = _open_page(Path(tiff_path))
    s = max(1, math.ceil(arr.shape[1] / max_width))
    small = (np.asarray(arr[::s, ::s, :3]) >> 8).astype(np.uint8)
    Image.fromarray(small).save(out_png)


# ─── Orchestration ──────────────────────────────────────────────────────────
@dataclass
class PrinttargChartResult:
    chart_id: str
    chart_dir: str
    descriptor_path: str
    tiff_path: str
    n_patches: int
    n_pages: int
    steps_in_pass: int
    total_strips: int
    tiff_w_mm: float
    tiff_h_mm: float
    roll_layout: str = ROLL_LAYOUT_TRANSVERSE
    warnings: list = field(default_factory=list)


def printable_width_mm(media_key: str) -> float:
    """Media width the chart may cross: the nominal width minus the mechanical side margins
    of its source (roll), or what a chart centred on the sheet may take (sheet)."""
    if is_sheet(media_key):
        return sheet_chart_limits(media_key)["max_w_mm"]
    media = G.MEDIA[media_key]
    m = _margins(media_key)
    return media.width_mm - m["left"] - m["right"]


def _public(report: dict) -> dict:
    return {k: v for k, v in report.items() if not k.startswith("_")}


def orchestrate_printtarg_chart(
    ti1_path: str | Path,
    *,
    instrument: str,
    media_key: str,
    reference_icc_path: str | Path,
    gloss_enhancer: Optional[str] = None,
    tag_source: str = "resident-live",
    source: Optional[str] = None,
    paper: Optional[dict] = None,
    dpi: int = DEFAULT_DPI,
    page_h_mm: Optional[float] = None,
    unlimited_strip: Optional[bool] = None,
    random_start: Optional[int] = None,
    roll_layout: str = ROLL_LAYOUT_TRANSVERSE,
    rotation: str = DEFAULT_ROTATION,
    max_page_length_mm: int = MAX_PAGE_LENGTH_MM,
    charts_root: str | Path | None = None,
    now: Optional[datetime] = None,
    printtarg_path: str = "printtarg",
    timeout: float = PRINTTARG_TIMEOUT_S,
) -> PrinttargChartResult:
    """Generate an external-instrument chart (printtarg + composed TIFF + chart.json).

    No printer is contacted: ``reference_icc_path`` is the resident profile the caller
    already exported (embedded in the TIFF exactly like the honeycomb chart). The
    chart folder is new; on any failure it is removed, so no half chart is left."""
    from .argyll import ArgyllNotFound, resolve_argyll_binary
    from .sol_chart import (SCHEMA_VERSION, _color_management_meta, generate_chart_id,
                            save_chart_descriptor)

    if instrument not in INSTRUMENTS:
        raise PrinttargChartError("unsupported_instrument", str(instrument))
    if media_key not in SUPPORTED_MEDIA or (instrument, media_key) not in PRESETS:
        raise PrinttargChartError("unsupported_media", str(media_key))
    if roll_layout not in ROLL_LAYOUTS:
        raise PrinttargChartError("invalid_roll_layout", str(roll_layout))
    if rotation not in ROTATIONS:
        raise PrinttargChartError("invalid_rotation", str(rotation))
    ref_icc = Path(reference_icc_path) if reference_icc_path else None
    if ref_icc is None or not ref_icc.is_file():
        raise PrinttargChartError("resident_profile_missing", str(reference_icc_path))
    transverse = roll_layout == ROLL_LAYOUT_TRANSVERSE
    sheet = is_sheet(media_key)
    if sheet and not transverse:
        raise PrinttargChartError("invalid_roll_layout", "a sheet takes the transverse layout only")
    limits = sheet_chart_limits(media_key) if sheet else None
    if sheet:
        # one block, no longer than the sheet takes: the page-length cap is the sheet's
        max_page_length_mm = min(int(max_page_length_mm), int(_sheet_block_mm(media_key)))
    preset = PRESETS[(instrument, media_key)]
    nominal_w = preset.page_w_mm
    page_h = float(page_h_mm) if page_h_mm is not None else preset.page_h_mm
    unlimited = preset.unlimited_strip if unlimited_strip is None else bool(unlimited_strip)

    # What crosses the roll: the strip length (page height, margins included by -M)
    # once turned, or the nominal page width minus its margins when stacked unturned.
    printable_w = printable_width_mm(media_key)
    across_mm = page_h if transverse else nominal_w - 2 * PRINTTARG_MARGIN_MM
    if across_mm > printable_w:
        raise PrinttargChartError("page_too_wide",
                                  f"{across_mm:.1f} mm > printable {printable_w:.1f} mm")

    ti1_path = Path(ti1_path)
    ti1_patches = read_ti1_patches(ti1_path)
    check_ti1_tables(ti1_path)
    try:
        exe = resolve_argyll_binary(printtarg_path)
    except ArgyllNotFound as e:
        raise PrinttargChartError("printtarg_not_found", str(e))

    chart_id = generate_chart_id(now)
    created = (now or datetime.now()).isoformat(timespec="seconds")
    root = Path(charts_root) if charts_root is not None else cache.charts_dir(
        (paper or {}).get("serial") or "")
    chart_dir = root / chart_id
    try:
        chart_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        raise PrinttargChartError("chart_dir_exists", str(chart_dir))

    try:
        work = chart_dir / "printtarg"
        work.mkdir()
        base = chart_id
        shutil.copyfile(ti1_path, work / f"{base}.ti1")
        seed = default_random_start(chart_id, len(ti1_patches)) if random_start is None \
            else int(random_start)

        # 1. Page width: the nominal one, or (transverse) the smallest printtarg page
        #    holding all the strips (or an even share of them), found by probes.
        layout_search = None
        page_w = nominal_w
        if transverse:
            probe_dir = work / "probes"
            probe_dir.mkdir()
            prober = _LayoutProber(exe, probe_dir, work / f"{base}.ti1",
                                   instrument=instrument, page_h_mm=page_h,
                                   unlimited_strip=unlimited, random_start=seed,
                                   margin_in_tiff=True, timeout=timeout)
            plan = plan_transverse_pages(prober, min(int(round(nominal_w)), int(max_page_length_mm)),
                                         max_page_mm=int(max_page_length_mm))
            if sheet and plan["pages"] != 1:
                raise PrinttargChartError(
                    "too_many_strips_for_sheet",
                    f"{plan['strips_total']} strips need {plan['pages']} blocks; a sheet takes one "
                    f"of at most {limits['max_h_mm']:.1f} mm: fewer patches")
            page_w = float(plan["page_w_mm"])
            rows_at_w = prober.rows(plan["page_w_mm"])
            layout_search = {**plan, "rows_per_page": rows_at_w,
                             "start_page_w_mm": nominal_w,
                             "max_page_length_mm": max_page_length_mm,
                             "probes": prober.log}
            shutil.rmtree(probe_dir)

        # 2. The printtarg run that counts (recorded argv reproduces it).
        argv = build_printtarg_argv(exe, instrument=instrument, page_w_mm=page_w,
                                    page_h_mm=page_h, unlimited_strip=unlimited, dpi=dpi,
                                    random_start=seed, base=base, margin_in_tiff=transverse)
        run = run_printtarg(work, base, argv, timeout=timeout)
        padding_rgb = read_ti1_padding_rgb(ti1_path)
        ti2 = check_ti2(ti1_patches, work / f"{base}.ti2", instrument=instrument,
                        padding_rgb=padding_rgb)

        reported = _verbose_layout(run["stdout"])
        if reported.get("patches_per_strip") not in (None, ti2.steps_in_pass) or \
                reported.get("pages") not in (None, ti2.n_pages):
            raise PrinttargChartError("printtarg_report_mismatch", str(reported))
        if transverse:
            expected = math.ceil(ti2.total_strips / layout_search["rows_per_page"])
            if ti2.total_strips != layout_search["strips_total"] or ti2.n_pages != expected:
                raise PrinttargChartError("layout_plan_mismatch",
                                          f"{ti2.passes_in_strips} for {layout_search}")
            layout_search["pages"] = ti2.n_pages

        pages = _expected_page_files(work, base, ti2.n_pages)
        absent = [p.name for p in pages if not p.is_file()]
        produced = sorted(p.name for p in work.glob(f"{base}*.tif"))
        if absent or produced != sorted(p.name for p in pages):
            raise PrinttargChartError("printtarg_outputs_unexpected",
                                      f"expected {[p.name for p in pages]}, got {produced}")
        metas = _check_pages(pages, page_w_mm=page_w, page_h_mm=page_h, dpi=dpi,
                             margin_in_tiff=transverse)

        # 3. Patch positions on each ORIGINAL page (RGB unique in the chart → locatable).
        multiplicity: dict = {}
        for p in ti2.patches:
            k = _key16(p["rgb"])
            multiplicity[k] = multiplicity.get(k, 0) + 1
        offset, per_page, reports, pages_labels, pages_patches = 0, [], [], [], []
        for pi, (pg, n) in enumerate(zip(pages, ti2.passes_in_strips)):
            labels = ti2.labels[offset:offset + n]
            on_page = [p for p in ti2.patches if p["page"] == pi + 1]
            report = verify_page_patches(_open_page(pg), on_page, labels,
                                         steps=ti2.steps_in_pass, dpi=dpi,
                                         multiplicity=multiplicity)
            reports.append(report)
            pages_labels.append(labels)
            pages_patches.append(on_page)
            per_page.append({"page": pi + 1, "file": f"printtarg/{pg.name}",
                             "strip_labels": [labels[0], labels[-1]], **_public(report)})
            offset += n

        # 4. Composition for the roll (turned pages, or the previous unturned stack).
        rot = rotation if transverse else None
        gap_px = 0 if transverse else math.ceil(_px(2 * PRINTTARG_MARGIN_MM, dpi) - 1e-9)
        icc_bytes = ref_icc.read_bytes()
        tiff_path = chart_dir / "chart.tif"
        stacked = stack_pages(pages, tiff_path, dpi=dpi, gap_px=gap_px, icc_bytes=icc_bytes,
                              rotation=rot)
        blocks = [{"page": i + 1, "file": f"printtarg/{pg.name}", "rotation": rot,
                   "y_px": y0, "h_px": h, "y_mm": round(_mm(y0, dpi), 3),
                   "h_mm": round(_mm(h, dpi), 3)}
                  for i, (pg, y0, h) in enumerate(zip(pages, stacked["offsets_px"],
                                                      stacked["block_heights_px"]))]
        tiff_w_mm = _mm(stacked["width_px"], dpi)
        tiff_h_mm = _mm(stacked["height_px"], dpi)
        if tiff_w_mm > printable_w:
            raise PrinttargChartError("page_too_wide",
                                      f"{tiff_w_mm:.2f} mm > printable {printable_w:.1f} mm")
        if sheet and tiff_h_mm > limits["max_h_mm"]:
            raise PrinttargChartError("too_many_strips_for_sheet",
                                      f"{tiff_h_mm:.2f} mm > {limits['max_h_mm']:.1f} mm on the sheet")

        # 5. The FINAL chart, checked in its own pixels (orientation, order, presence).
        chart = _open_page(tiff_path)
        final = verify_composed_chart(chart, blocks, pages_patches, pages_labels,
                                      steps=ti2.steps_in_pass, dpi=dpi,
                                      multiplicity=multiplicity)
        del chart
        if [f["located"] for f in final] != [r["located"] for r in reports]:
            raise PrinttargChartError("stack_verify_failed", "patches lost in composition")
        strips = strips_in_chart(blocks, reports, pages_labels,
                                 page_h_px=[m["height_px"] for m in metas], dpi=dpi)
        ys = [st["y_mm"][0] for st in strips]
        if transverse and (ys != sorted(ys) or ys[0] < 0
                           or max(st["y_mm"][1] for st in strips) > tiff_h_mm + 0.5
                           or min(st["x_mm"][0] for st in strips) < 0
                           or max(st["x_mm"][1] for st in strips) > tiff_w_mm + 0.5):
            raise PrinttargChartError("strip_geometry_unexpected", "strips out of order or bounds")

        preview = chart_dir / "chart_preview.png"
        write_preview(tiff_path, preview)

        spec = INSTRUMENTS[instrument]
        margins = _margins(media_key)
        located = sum(p["located"] for p in per_page)
        warnings = []
        if located < len(ti1_patches):
            warnings.append(f"{len(ti1_patches) - located} patch(es) not located from pixels "
                            "(duplicate or white RGB); their place is printtarg's.")
        descriptor = {
            "schema_version": SCHEMA_VERSION,
            "chart_id": chart_id,
            "created_at": created,
            "source": source or f"ti1: {ti1_path.name}",
            "layout": LAYOUT,
            "roll_layout": roll_layout,
            "instrument": {"key": spec.key, "name": spec.name,
                           "printtarg_selector": spec.selector,
                           "ti2_target_instrument": spec.ti2_target_instrument},
            "media": {"format_key": media_key, "source": G.MEDIA[media_key].source,
                      "width_mm": G.MEDIA[media_key].width_mm,
                      "height_mm": G.MEDIA[media_key].height_mm if sheet else None,
                      "dpi": dpi, "printable_width_mm": printable_w,
                      "mechanical_margins_mm": dict(margins),
                      **({"sheet_limits": limits} if sheet else {})},
            "paper": paper or {},
            "printtarg": {
                **run,
                "version": printtarg_version(exe),
                "page_w_mm": page_w, "page_h_mm": page_h, "nominal_page_w_mm": nominal_w,
                "margin_mm": PRINTTARG_MARGIN_MM, "margin_in_tiff": transverse,
                "unlimited_strip": unlimited,
                "dpi": dpi, "random_start_requested": seed,
                "random_start": int(ti2.keywords.get("RANDOM_START", seed)),
                "ti2_keywords": {k: ti2.keywords[k] for k in (
                    "TARGET_INSTRUMENT", "PAPER_SIZE", "RANDOM_START", "STEPS_IN_PASS",
                    "PASSES_IN_STRIPS2", "STRIP_INDEX_PATTERN", "PATCH_INDEX_PATTERN",
                    "INDEX_ORDER", "CREATED") if k in ti2.keywords},
            },
            "layout_search": layout_search,
            "geometry": {
                "patch_count": len(ti1_patches),
                "sets": len(ti2.patches),
                "padding_count": len(ti2.padding_locations),
                "steps_in_pass": ti2.steps_in_pass,
                "passes_in_strips": ti2.passes_in_strips,
                "total_strips": ti2.total_strips,
                "n_pages": ti2.n_pages,
                "page_tiff_px": [[m["width_px"], m["height_px"]] for m in metas],
                "page_resolution_unit": metas[0]["resolution_unit"],
                "tiff_w_px": stacked["width_px"], "tiff_h_px": stacked["height_px"],
                "tiff_w_mm": round(tiff_w_mm, 3), "tiff_h_mm": round(tiff_h_mm, 3),
                # a sheet is not cut to length: no roll consumed
                "roll_length_mm": None if sheet else round(tiff_h_mm + margins["top"]
                                                           + margins["bottom"], 1),
            },
            "composition": {
                "roll_layout": roll_layout, "rotation": rot,
                "strips_cross_the_roll": transverse,
                "strip_order": "top_to_bottom" if transverse else "left_to_right",
                "patch_1_side": strips[0]["patch_1_side"],
                "gap_px": gap_px, "gap_mm": round(_mm(gap_px, dpi), 3),
                "blocks": blocks,
            },
            "strips": strips,
            "patch_map": {
                "method": "exact 16-bit colour of each patch with a unique RGB",
                "located": located, "patch_count": len(ti1_patches),
                "per_page": per_page,
                "final_chart": [{"block": b["page"], "located": f["located"],
                                 "patch_1_at_in_page_orientation": f["patch_1_at"]}
                                for b, f in zip(blocks, final)],
            },
            "padding": {"sample_id": PADDING_SAMPLE_ID, "count": len(ti2.padding_locations),
                        "locations": ti2.padding_locations, "rgb": list(padding_rgb),
                        "rgb_source": "ti1 DENSITY_EXTREME_VALUES INDEX 0"},
            "patches_by_location": [
                {k: p[k] for k in ("sample_id", "sample_loc", "strip", "patch", "page", "rgb")}
                for p in ti2.patches],
            "color_management": _color_management_meta(ref_icc, tag_source, gloss_enhancer),
            "provenance": {
                "ti1_sha256": _sha256(work / f"{base}.ti1"),
                "ti2_sha256": _sha256(work / f"{base}.ti2"),
                "pages_sha256": [_sha256(p) for p in pages],
                "tiff_sha256": _sha256(tiff_path),
                "icc_sha256": hashlib.sha256(icc_bytes).hexdigest(),
            },
            "files": {"tiff": tiff_path.name, "preview": preview.name,
                      "ti1": f"printtarg/{base}.ti1", "ti2": f"printtarg/{base}.ti2",
                      "pages": [f"printtarg/{p.name}" for p in pages]},
        }
        descriptor_path = save_chart_descriptor(chart_dir, descriptor)
    except BaseException:
        shutil.rmtree(chart_dir, ignore_errors=True)
        raise

    return PrinttargChartResult(
        chart_id=chart_id, chart_dir=str(chart_dir), descriptor_path=str(descriptor_path),
        tiff_path=str(tiff_path), n_patches=len(ti1_patches), n_pages=ti2.n_pages,
        steps_in_pass=ti2.steps_in_pass, total_strips=ti2.total_strips,
        tiff_w_mm=tiff_w_mm, tiff_h_mm=tiff_h_mm, roll_layout=roll_layout,
        warnings=warnings)
