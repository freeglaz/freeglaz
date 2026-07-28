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

"""TIFF/PDF metadata extraction for /api/files/{id}/info."""
import hashlib
import logging
from io import BytesIO
from pathlib import Path
from typing import Optional

import pikepdf
from PIL import ImageCms
from tifffile import TiffFile

from lib.z9_client.exceptions import Z9PrintError
from lib.z9_client.printing import TiffInfo

from webapp.backend.models import FileInfo
from webapp.backend.services.icc_identity import icc_color_hash

logger = logging.getLogger(__name__)

PT_PER_MM = 72.0 / 25.4


def _extract_icc_description(icc_bytes: bytes) -> str:
    """Read the ``desc`` tag of an ICC profile via Pillow ImageCms.

    Handles v2 profiles (ASCII ``desc`` tag) AND v4 (``desc`` tag in
    ``mluc`` multi-localized unicode format). The historical custom parser
    ``lib.z9_client.printing._get_icc_profile_description`` parses the mluc
    layout wrongly (reads ``rec_len``/``rec_offset`` at the header offsets
    +12..16 instead of the first record fields +20..28) and returns ``""``
    silently for v4 profiles — typically the custom HP Z9 profiles
    embedded in exports (bug B3, cf.
    ``Docs/freeglaz_Webapp_Roadmap.md``). Pillow relies on lcms which
    covers both formats without a blind spot.

    Returns ``""`` if the extraction fails or if the desc tag is empty.
    """
    try:
        profile = ImageCms.ImageCmsProfile(BytesIO(icc_bytes))
        return ImageCms.getProfileDescription(profile).strip()
    except Exception as e:
        logger.info("ImageCms.getProfileDescription failed: %s", e)
        return ""


def _detect_kind(path: Path) -> Optional[str]:
    ext = path.suffix.lower()
    if ext in (".tif", ".tiff"):
        return "tiff"
    if ext == ".pdf":
        return "pdf"
    return None


def _read_tiff_extras(
    path: Path,
) -> tuple[Optional[int], Optional[str], Optional[str], Optional[str]]:
    """Read bits_per_sample (258) + icc_name + icc_md5 + icc_color_hash (34675)
    without loading the pixels.

    ``icc_md5`` = full-file MD5 (exact identity, kept for debug). ``icc_color_hash``
    = hash of the colour-defining tags only (see icc_identity) — the value used
    for matching, robust to name/copyright/date-only differences between the
    editor's export and the Z9 firmware.
    """
    bits: Optional[int] = None
    icc_name: Optional[str] = None
    icc_md5: Optional[str] = None
    icc_color: Optional[str] = None
    try:
        with TiffFile(path) as tif:
            page = tif.pages[0]
            if 258 in page.tags:
                bps = page.tags[258].value
                bits = int(bps[0]) if isinstance(bps, tuple) else int(bps)
            if 34675 in page.tags:
                icc_raw = page.tags[34675].value
                if isinstance(icc_raw, (bytes, bytearray)) and icc_raw:
                    icc_bytes = bytes(icc_raw)
                    icc_name = _extract_icc_description(icc_bytes)
                    icc_md5 = hashlib.md5(icc_bytes).hexdigest()
                    icc_color = icc_color_hash(icc_bytes)
    except Exception as e:
        logger.info("TIFF extras read failed for %s: %s", path, e)
    return bits, icc_name, icc_md5, icc_color


def _inspect_tiff(path: Path) -> dict:
    try:
        ti = TiffInfo.from_path(path)
    except Z9PrintError as e:
        return {
            "kind": "tiff",
            "width_mm": 0.0, "height_mm": 0.0,
            "dpi": None, "bits_per_sample": None,
            "has_icc": False, "icc_name": None, "icc_md5": None,
            "icc_color_hash": None,
            "blocking_issues": [str(e)],
            "warnings": [],
        }

    bits, icc_name, icc_md5, icc_color = _read_tiff_extras(path)
    dpi = round((ti.xdpi + ti.ydpi) / 2)

    blocking: list[str] = []
    warnings: list[str] = []

    if dpi < 72:
        blocking.append(f"DPI too low ({dpi} dpi)")
    if bits is not None and bits < 16:
        warnings.append(f"TIFF {bits}-bit, promoted to 16-bit (16-bit source preferred)")
    if not ti.has_icc:
        warnings.append("No embedded ICC profile")

    return {
        "kind": "tiff",
        "width_mm": ti.width_mm, "height_mm": ti.height_mm,
        "dpi": dpi, "bits_per_sample": bits,
        "has_icc": ti.has_icc, "icc_name": icc_name, "icc_md5": icc_md5,
        "icc_color_hash": icc_color,
        "blocking_issues": blocking,
        "warnings": warnings,
    }


def _read_pdf_string(node) -> Optional[str]:
    """Get a str from a pikepdf node (String, bytes, or unicode)."""
    if node is None:
        return None
    try:
        s = str(node).strip()
        return s or None
    except Exception:
        return None


def _inspect_pdf(path: Path) -> dict:
    try:
        with pikepdf.open(str(path)) as pdf:
            page = pdf.pages[0]
            media = page.mediabox
            width_pt = float(media[2]) - float(media[0])
            height_pt = float(media[3]) - float(media[1])
            width_mm = width_pt / PT_PER_MM
            height_mm = height_pt / PT_PER_MM

            has_icc = False
            icc_name: Optional[str] = None
            icc_md5: Optional[str] = None
            icc_color: Optional[str] = None
            try:
                oi = pdf.Root.get("/OutputIntents")
            except Exception:
                oi = None

            if oi is not None and len(oi) > 0:
                oi_entry = oi[0]

                # The profile name can live in three places depending on the
                # PDF producer (Acrobat, editor, in-house build_pdfx4...):
                #   1. /Info: human-readable string, prioritized when present
                #   2. /OutputConditionIdentifier: fallback (often identical)
                #   3. `desc` tag of the binary ICC file in /DestOutputProfile
                #      — used as a last resort because some custom HP profiles
                #      have an empty desc tag (the name lives in /Info instead)
                icc_name = _read_pdf_string(oi_entry.get("/Info"))
                if not icc_name:
                    icc_name = _read_pdf_string(oi_entry.get("/OutputConditionIdentifier"))

                dest = oi_entry.get("/DestOutputProfile")
                if dest is not None:
                    has_icc = True
                    try:
                        icc_bytes = bytes(dest.read_bytes())
                        icc_md5 = hashlib.md5(icc_bytes).hexdigest()
                        icc_color = icc_color_hash(icc_bytes)
                        if not icc_name:
                            name_from_bytes = _extract_icc_description(icc_bytes)
                            if name_from_bytes and name_from_bytes != "Unknown ICC Profile":
                                icc_name = name_from_bytes
                    except Exception as e:
                        logger.info("PDF ICC bytes read failed: %s", e)
                elif icc_name is not None:
                    # OutputIntent present with a textual identifier but no
                    # embedded binary profile: we consider that we know
                    # the target profile even without the ICC file itself.
                    has_icc = True
    except Exception as e:
        return {
            "kind": "pdf",
            "width_mm": 0.0, "height_mm": 0.0,
            "dpi": None, "bits_per_sample": None,
            "has_icc": False, "icc_name": None, "icc_md5": None,
            "icc_color_hash": None,
            "blocking_issues": [f"PDF illisible : {e}"],
            "warnings": [],
        }

    blocking: list[str] = []
    warnings: list[str] = []
    if width_mm <= 0 or height_mm <= 0:
        blocking.append("MediaBox PDF invalide")
    if not has_icc:
        warnings.append("Aucun profil ICC dans l'OutputIntent")

    return {
        "kind": "pdf",
        "width_mm": width_mm, "height_mm": height_mm,
        "dpi": None, "bits_per_sample": None,
        "has_icc": has_icc, "icc_name": icc_name, "icc_md5": icc_md5,
        "icc_color_hash": icc_color,
        "blocking_issues": blocking,
        "warnings": warnings,
    }


def to_file_info(file_id: str, filename: str, path: Path) -> FileInfo:
    """Build FileInfo. `is_printable` = no blocking_issues."""
    kind = _detect_kind(path)
    if kind == "tiff":
        data = _inspect_tiff(path)
    elif kind == "pdf":
        data = _inspect_pdf(path)
    else:
        return FileInfo(
            file_id=file_id, filename=filename, kind="tiff",
            width_mm=0.0, height_mm=0.0,
            has_icc=False, icc_md5=None,
            is_printable=False,
            blocking_issues=[f"Unsupported format: {path.suffix or '(none)'}"],
            warnings=[],
        )
    is_printable = not data["blocking_issues"]
    return FileInfo(file_id=file_id, filename=filename, is_printable=is_printable, **data)
