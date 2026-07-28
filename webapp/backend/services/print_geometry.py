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

"""Pure geometry computations for /api/print/preview — no Z9 I/O.

The functions here take an already-resolved ``LoadedPaper`` and
``PrintParams``, and produce a ``GeometryResult`` + lists of
``blocking_issues`` / ``warnings``. They do no firmware validation
(cf. ``PrintJob.validate()`` reused at increment 5 for the real send
pipeline).
"""
import hashlib
import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.printing import MECHANICAL_MARGINS_MM, _get_icc_profile_description
from webapp.backend.services.icc_identity import icc_color_hash

from webapp.backend.models import GeometryResult, LoadedPaper, PrintParams

logger = logging.getLogger(__name__)

# Z9 API source (with underscore) → MECHANICAL_MARGINS_MM key (without underscore)
_LIB_KEY_BY_API = {
    "MANUAL_FEED": "MANUALFEED",
    "SHEET":       "MANUALFEED",  # same on the mechanical margins side (case not observed live)
    "ROLL":        "ROLL",
    "MANUALFEED":  "MANUALFEED",  # tolerance for lib-style override
}

ROLL_ECON_BOTTOM_PAD_MM = 10.0
ROLL_ECON_MARGIN_MM = 5.0


def _resolve_media_source_api(loaded: LoadedPaper, override: Optional[str]) -> str:
    """Return the media_source for the API ('MANUAL_FEED' | 'ROLL' | 'SHEET')."""
    if override and override != "AUTO":
        return "MANUAL_FEED" if override == "MANUALFEED" else override
    src = (loaded.media_source or "").upper()
    return src if src in ("MANUAL_FEED", "ROLL", "SHEET") else "MANUAL_FEED"


def _lib_margin_key(media_source_api: str) -> str:
    return _LIB_KEY_BY_API.get(media_source_api.upper(), "MANUALFEED")


def oriented_dims(orientation: int, image_w_mm: float, image_h_mm: float) -> Tuple[float, float]:
    """EFFECTIVE printed dimensions according to the content orientation.

    90/270 ⇒ the image is rotated a quarter turn (np.rot90 on the lib
    side), so its width and height swap. Pure function, called *upstream*
    of ``compute_geometry`` (which stays orientation-agnostic) by the
    image printing sites. Charts never orient (orientation = 0).
    """
    if orientation in (90, 270):
        return image_h_mm, image_w_mm
    return image_w_mm, image_h_mm


def compute_geometry(
    loaded: LoadedPaper,
    params: PrintParams,
    image_w_mm: float,
    image_h_mm: float,
) -> GeometryResult:
    """Image position + printable area + directional overflows.

    Three default positioning modes, each justified:
    - **MANUAL_FEED / SHEET**: centering on the physical sheet + user
      offsets. Natural UX (the user sees the image in the middle of the
      sheet).
    - **economical ROLL** (no ``sheet_height_mm_override``): top-left
      corner + 5 mm. Historical economical mode to minimize the paper
      consumed on the roll.
    - **virtual document ROLL** (``sheet_height_mm_override`` provided):
      centering on the virtual sheet + user offsets.

    The printable area is computed from the lib ``MECHANICAL_MARGINS_MM``.
    The directional overflows are computed from the image bounding box
    (at its absolute position) vs this printable area.
    """
    media_source = _resolve_media_source_api(loaded, params.media_source_override)
    is_roll = media_source == "ROLL"

    if is_roll:
        sheet_w = params.sheet_width_mm_override or loaded.roll_width_mm or 0.0
        if params.sheet_height_mm_override is not None:
            sheet_h = params.sheet_height_mm_override
            is_virtual = True
        else:
            # economical ROLL: the sheet height (PDF MediaBox) MUST
            # include the user vertical offset. Otherwise the MediaBox
            # ignores offset_y while build_prn already produces
            # PAPERLENGTH = effective_top + image_h + bottom (5mm ROLL) →
            # PDF shorter than the announced paper length → dropout as
            # soon as offset_y > 0 (legitimate use: shift to avoid the
            # start of the roll, curvature/flatness). We re-align the
            # MediaBox on PAPERLENGTH via the EFFECTIVE TOP (auto margin +
            # user delta = image_y), NOT a copy of the CLI formula
            # (CLI offset_y = absolute, webapp offset_y_mm = delta).
            # offset_y_mm=0 → image_h+10 (unchanged).
            roll_bottom = MECHANICAL_MARGINS_MM["ROLL"]["bottom"]   # 5mm, == BOTTOMMARGIN_PJL_BY_SOURCE["ROLL"]
            effective_top = ROLL_ECON_MARGIN_MM + params.offset_y_mm   # = image_y in economical ROLL
            sheet_h = effective_top + image_h_mm + roll_bottom
            is_virtual = False
    else:
        sheet_w = params.sheet_width_mm_override or loaded.sheet_width_mm or 0.0
        sheet_h = params.sheet_height_mm_override or loaded.sheet_height_mm or 0.0
        is_virtual = False

    if is_roll and not is_virtual:
        auto_x = ROLL_ECON_MARGIN_MM
        auto_y = ROLL_ECON_MARGIN_MM
        centered_x = centered_y = False
    else:
        auto_x = (sheet_w - image_w_mm) / 2
        auto_y = (sheet_h - image_h_mm) / 2
        centered_x = centered_y = True

    image_x = auto_x + params.offset_x_mm
    image_y = auto_y + params.offset_y_mm

    # Printable area from the mechanical margins
    margins = MECHANICAL_MARGINS_MM.get(
        _lib_margin_key(media_source), MECHANICAL_MARGINS_MM["MANUALFEED"],
    )
    printable_x = float(margins["left"])
    printable_y = float(margins["top"])
    printable_w = max(0.0, sheet_w - margins["left"] - margins["right"])
    printable_h = max(0.0, sheet_h - margins["top"] - margins["bottom"])

    # Directional overflows (image bounding box vs printable area)
    img_right  = image_x + image_w_mm
    img_bottom = image_y + image_h_mm
    pr_right   = printable_x + printable_w
    pr_bottom  = printable_y + printable_h

    overflow_left   = max(0.0, printable_x - image_x)
    overflow_top    = max(0.0, printable_y - image_y)
    overflow_right  = max(0.0, img_right   - pr_right)
    overflow_bottom = max(0.0, img_bottom  - pr_bottom)

    return GeometryResult(
        sheet_width_mm=sheet_w, sheet_height_mm=sheet_h,
        image_width_mm=image_w_mm, image_height_mm=image_h_mm,
        image_x_mm=image_x, image_y_mm=image_y,
        # Auto anchors = EXACT values used for the placement (before user
        # delta). Single source consumed by the front (approach B).
        auto_x_mm=auto_x,
        auto_y_mm=auto_y,
        margin_left_mm=image_x,
        margin_top_mm=image_y,
        margin_right_mm=sheet_w - image_x - image_w_mm,
        margin_bottom_mm=sheet_h - image_y - image_h_mm,
        media_source=media_source,
        centered_x=(centered_x and params.offset_x_mm == 0.0),
        centered_y=(centered_y and params.offset_y_mm == 0.0),
        printable_x_mm=printable_x, printable_y_mm=printable_y,
        printable_w_mm=printable_w, printable_h_mm=printable_h,
        overflow_left_mm=overflow_left, overflow_top_mm=overflow_top,
        overflow_right_mm=overflow_right, overflow_bottom_mm=overflow_bottom,
    )


def detect_geometry_issues(
    geometry: GeometryResult, media_source_api: str,
) -> Tuple[list[str], list[str]]:
    """Return (blocking_issues, warnings).

    Distinguishes 3 causes per axis for an actionable diagnosis (B13
    follow-up):

    1. **Image too large**: `image_size > printable_size`. The cause is
       the file size, not the position. The user must resize or change
       paper.
    2. **Position below the margin**: `image_xy < printable_xy`. The
       image fits, but it is positioned out of the area on the left or
       top side. The user must increase Position X / Y.
    3. **Position above the max**: `image_xy + image_size >
       printable_xy + printable_size`. The image fits, but it overflows
       on the right or bottom side. We indicate the acceptable max.

    Tolerance 0.01 mm to avoid false positives on float rounding.
    """
    blocking: list[str] = []
    warnings: list[str] = []

    # ─── X axis ───────────────────────────────────────────────────
    too_wide = geometry.image_width_mm > geometry.printable_w_mm + 0.01
    if too_wide:
        blocking.append(
            f"Image too wide for the printable area: "
            f"{geometry.image_width_mm:.0f} mm > {geometry.printable_w_mm:.0f} mm "
            f"(usable width = sheet − mechanical margins)"
        )
    else:
        if geometry.image_x_mm < geometry.printable_x_mm - 0.01:
            blocking.append(
                f"Position X ({geometry.image_x_mm:.1f} mm) below the "
                f"left margin ({geometry.printable_x_mm:.0f} mm)"
            )
        max_x = (
            geometry.printable_x_mm + geometry.printable_w_mm
            - geometry.image_width_mm
        )
        if geometry.image_x_mm > max_x + 0.01:
            blocking.append(
                f"Image overflows on the right: Position X max = {max_x:.1f} mm "
                f"(current {geometry.image_x_mm:.1f} mm)"
            )

    # ─── Y axis ───────────────────────────────────────────────────
    too_tall = geometry.image_height_mm > geometry.printable_h_mm + 0.01
    if too_tall:
        blocking.append(
            f"Image too tall for the printable area: "
            f"{geometry.image_height_mm:.0f} mm > {geometry.printable_h_mm:.0f} mm "
            f"(usable height = sheet − mechanical margins)"
        )
    else:
        if geometry.image_y_mm < geometry.printable_y_mm - 0.01:
            blocking.append(
                f"Position Y ({geometry.image_y_mm:.1f} mm) below the "
                f"top margin ({geometry.printable_y_mm:.0f} mm)"
            )
        max_y = (
            geometry.printable_y_mm + geometry.printable_h_mm
            - geometry.image_height_mm
        )
        if geometry.image_y_mm > max_y + 0.01:
            blocking.append(
                f"Image overflows at the bottom: Position Y max = {max_y:.1f} mm "
                f"(current {geometry.image_y_mm:.1f} mm)"
            )

    return blocking, warnings


# ─────────────────────────────────────────────────────────────────────────
# Active paper ICC — uses the firmware-authoritative API get_profile
# ─────────────────────────────────────────────────────────────────────────

def _color_space_for(rendermode: str) -> str:
    return "PRINTER_GRAYSCALE" if rendermode == "GRAYSCALE" else "PRINTER_RGB"


def _pick_profile_metadata_name(
    z9: Z9Client, paper_id: str, gloss_enhancer: str,
) -> Optional[str]:
    """Fallback: name from ``paper.details()["profiles"]`` (firmware ProfilingTicket).

    Used when extraction of the ``desc`` tag from the ICC bytes fails
    (case of HP custom profiles that store their name in the
    ProfilingTicket rather than inside the ICC file). Selection: matches
    the requested GE, prefers ``custom=True`` (the user's calibration).
    """
    try:
        details = z9.paper.details(paper_id)
    except (Z9Error, AttributeError) as e:
        # AttributeError tolerance: allows lightweight test stubs
        # (mock Z9 without the full paper surface). In prod, paper.details()
        # is always present (real Z9Client).
        logger.info("paper.details(%s) fallback failed: %s", paper_id, e)
        return None
    if not details:
        return None
    profiles = details.get("profiles", []) or []
    matching = [p for p in profiles if (p.get("gloss_enhancer") or "OFF") == gloss_enhancer]
    if not matching:
        matching = profiles
    if not matching:
        return None
    custom = [p for p in matching if p.get("custom")]
    pick = (custom or matching)[0]
    return pick.get("icc_name")


@dataclass(frozen=True)
class PaperIccInfo:
    """Active ICC profile of a Z9 paper, as resolved on the firmware side.

    ``color_hash`` (colour tags only, metadata excluded) is the value used for
    matching — robust to name/date-only byte differences. ``md5`` is the exact
    full-file hash (debug). ``name`` is readable but can be empty/divergent for
    HP custom profiles. Any of them can be None if get_profile failed or returns
    no bytes.
    """
    name: Optional[str] = None
    md5: Optional[str] = None
    color_hash: Optional[str] = None


def resolve_active_paper_icc_info(
    z9: Z9Client,
    paper_id: str,
    gloss_enhancer: str,
    rendermode: str,
    cache: dict,
) -> PaperIccInfo:
    """Get name + MD5 of the ICC profile actually active for this paper + GE.

    2-step strategy:
    1. SOAP ``get_profile`` (authoritative on the firmware side) → ICC
       bytes of the profile the Z9 will really use. MD5 computed on the
       bytes, name extracted from the ``desc`` tag.
    2. If the ``desc`` tag is empty (typical of HP custom profiles),
       fallback on ``paper.details()["profiles"]`` filtered by GE — the
       firmware metadata carries the readable name. The MD5 stays that of
       the bytes.

    Result cached by (paper_id, GE, color_space) — each get_profile
    pulls hundreds of KB of ICC bytes, and each details a SOAP
    getMediumList.
    """
    color_space = _color_space_for(rendermode)
    key = (paper_id, gloss_enhancer, color_space)
    if key in cache:
        return cache[key]

    try:
        result = z9.soap.get_profile(
            medium_id=paper_id,
            gloss_enhancer=gloss_enhancer,
            color_space=color_space,
        )
    except Z9Error as e:
        logger.info("get_profile(%s, %s, %s) failed: %s",
                    paper_id, gloss_enhancer, color_space, e)
        info = PaperIccInfo()
        cache[key] = info
        return info

    icc_bytes = result.get("icc_bytes")
    if not icc_bytes:
        # No bytes — we still try the metadata to have a displayable name
        # (and signal the mismatch via the name fallback if possible).
        name = _pick_profile_metadata_name(z9, paper_id, gloss_enhancer)
        info = PaperIccInfo(name=name, md5=None)
        cache[key] = info
        return info

    md5 = hashlib.md5(icc_bytes).hexdigest()
    color_hash = icc_color_hash(icc_bytes)
    name = _get_icc_profile_description(icc_bytes)
    if not name or name == "Unknown ICC Profile":
        name = _pick_profile_metadata_name(z9, paper_id, gloss_enhancer)
    info = PaperIccInfo(name=name or None, md5=md5, color_hash=color_hash)
    cache[key] = info
    return info


def _normalize_icc_name(name: str) -> str:
    """Normalize an ICC name for fallback comparison: lower + compact spaces.

    Preserves the significant characters (digits, separators like
    underscore/comma) to avoid artificially matching
    "Canson Photolustre 2025" and "Canson Photolustre 2026".
    """
    return " ".join(name.lower().split())


def icc_match_status(
    file_icc_name: Optional[str],
    file_icc_color_hash: Optional[str],
    paper_icc_name: Optional[str],
    paper_icc_color_hash: Optional[str],
) -> Tuple[str, str]:
    """Compare the file profile and the paper one. Return (status, reason).

    Statuses produced:
    - ``match``    : profiles confirmed identical (colour-hash priority, name fallback)
    - ``mismatch`` : profiles confirmed different → critical alert
    - ``unknown``  : paper not resolved (SOAP fails) or partial data →
                     alert without blocking
    - ``none``     : file without embedded ICC profile. Marginal case in
                     2026 (Affinity / Lightroom / Darktable / Photoshop
                     embed the ICC by default), but we distinguish it from
                     ``unknown`` because the absence of ICC on the file
                     side is deterministic info and not a failure — the
                     corresponding badge is neutral (gray) rather than an
                     alert (amber).

    Priority:
    1. If both COLORIMETRIC hashes are available → compare them (colour tags
       only; robust to name/copyright/date-only byte differences that a
       full-file MD5 would wrongly flag — verified live: a file's embedded ICC
       vs the live get_profile differed ONLY in the ``desc`` name tag).
    2. Otherwise if both names are non-empty → comparison on the normalized
       name (safety net when a colour hash is missing, e.g. unparseable ICC).
    3. Otherwise → none (file without ICC) OR unknown (paper not resolved).
    """
    if file_icc_color_hash and paper_icc_color_hash:
        if file_icc_color_hash == paper_icc_color_hash:
            return "match", "Same colour tables (metadata-independent hash)"
        return "mismatch", "Different colour tables"

    if file_icc_name and paper_icc_name:
        if _normalize_icc_name(file_icc_name) == _normalize_icc_name(paper_icc_name):
            return "match", "Identical ICC names (colour hash unavailable on one side)"
        return "mismatch", "Different ICC names (colour hash unavailable on one side)"

    if not file_icc_color_hash and not file_icc_name:
        # Absence of ICC on the file side is deterministic, not a failure.
        return "none", "File without embedded ICC profile"
    if not paper_icc_color_hash and not paper_icc_name:
        return "unknown", "Paper ICC profile not resolved (Z9 unavailable?)"
    return "unknown", "Insufficient data to compare the ICC profiles"
