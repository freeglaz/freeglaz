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
freeglaz printing pipeline - pure logic, no console I/O or prompts.

Native port of 02_freeglaz.py + tiff16_to_pdfx4_icc_v3.py + inject_pdf_vFinal.py.

Architecture:
    PrintJob       : dataclass describing a job (immutable, validable)
    PrintResult    : return of send() with metrics
    PrintOps       : attached to Z9Client.print, exposes the pipeline

Typical usage (CLI or GUI):
    job = PrintJob(
        tiff_path=Path("photo.tif"),
        paper_id="83A59964BD0B...",
        sheet_w_mm=297, sheet_h_mm=420,
        image_w_mm=200, image_h_mm=140,
        offset_x_mm=48.5, offset_y_mm=20.0,
        gloss="FULLPAGE", quality="HIGH",
    )
    job.validate()                  # raises Z9GeometryError if invalid
    result = client.print.send(job, on_progress=callback)

Individual steps available for fine-grained control:
    pdf_path = client.print.build_pdfx4(job, Path("output.pdf"))
    report = client.print.preflight(pdf_path)
    prn_path = client.print.build_prn(job, pdf_path, Path("job.prn"))
    client.print.send_raw(prn_path)

The pipeline runs locally (build PDF/PRN) then sends a single file over a
plain TCP socket on port 9100. The PDF/X-4 embeds the same ICC as image
colorspace AND OutputIntent, so the Z9's APPE performs no conversion between
two embedded profiles (APPE stays transparent). Device passthrough — the RGB
reaching the inks untouched — additionally requires that this embedded profile
IS the loaded slot's *resident* profile: the firmware decodes device→ink via
the OutputIntent profile, so source == resident is what makes the values raw.
"""

import hashlib
import logging
import os
import re
import socket
import subprocess
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Union

import numpy as np
import pikepdf
from pikepdf import Array, Dictionary, Name, Stream, String
from tifffile import TiffFile

from .exceptions import (
    Z9Error, Z9GeometryError, Z9PaperError, Z9PreflightError,
    Z9PrintError, Z9SendError,
)
from .preflight import PreflightReport, preflight_pdfx4

logger = logging.getLogger(__name__)


# UUID of the application instance - generated only once at module load,
# stable for the whole process lifetime (1 webapp launch = 1 UUID; 1 CLI
# invocation = 1 UUID). Emitted in JobAcct16 to allow grouping all jobs of
# the same freeglaz session on the Z9 firmware side / future queue.
# PJL UUID format: "{<lowercase-uuid4>}".
_APP_INSTANCE_UUID = "{" + str(uuid.uuid4()) + "}"


def _freeglaz_version() -> str:
    """Package version, read from the installed metadata.

    Fallback "dev" if running in source mode (not pip installed).
    Emitted in JobAcct7 (application version field).
    """
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version("freeglazcli")
        except PackageNotFoundError:
            return "dev"
    except ImportError:
        return "dev"


_FREEGLAZ_VERSION = _freeglaz_version()


def sanitize_for_pjl(text: str, max_len: int = 60) -> str:
    """Clean a string to make it safe as a PJL value (JOB NAME, etc.).

    Strict whitelist ``[a-zA-Z0-9_.- ]`` (alphanumeric, underscore,
    dot, dash, space). Everything else becomes ``_``. Conservative
    approach motivated by known Z9 firmware bugs with
    non-ASCII characters (cf. ``ü`` bug observed in ICC
    profile names). Truncates to ``max_len`` characters. Returns
    ``"untitled"`` if the cleaned string is empty.
    """
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9_.\- ]", "_", text)
    text = re.sub(r"\s+", " ", text).strip()[:max_len]
    return text or "untitled"


# =======================================================================
# Constants
# =======================================================================

# Unit conversions
PT_PER_MM = 72.0 / 25.4              # PDF points per mm = 2.8346
PJL_PER_MM = PT_PER_MM * 10          # PJL units per mm = 28.346

# Minimum mechanical margins per media_source (mm).
# - MANUALFEED bottom = 17.4mm because the feed rollers release the sheet
#   about 17mm before the edge and the printer then loses precision.
# - ROLL bottom = 5mm because the paper stays held by the entry rollers
#   throughout the print. No mechanical "forbidden zone" at the bottom.
# - The other margins (top, left, right) = 5mm in both modes.
MECHANICAL_MARGINS_MM = {
    "MANUALFEED": {"top": 5.0, "left": 5.0, "right": 5.0, "bottom": 17.4},
    "ROLL":       {"top": 5.0, "left": 5.0, "right": 5.0, "bottom": 5.0},
}

# PJL bottom margins = encoding in PJL units (1/720 inch) of the values above.
BOTTOMMARGIN_PJL_BY_SOURCE = {
    "MANUALFEED": 493,   # 17.4mm * 28.346 ~ 493
    "ROLL":       142,   # 5.0mm  * 28.346 ~ 142
}

# Right margin PJL = 142 (5mm) in both modes (constant)
RIGHTMARGIN_PJL = 142

# Default rendering intent (ignored when COLORSPACE=DEVICECALIBRATED)
RENDERINTENT_DEFAULT = "PERCEPTUAL"

# Allowed values (validated on the job side, not by the firmware)
ALLOWED_GLOSS = ("FULLPAGE", "OFF")
ALLOWED_QUALITY = ("HIGH", "NORMAL", "FAST")
ALLOWED_RENDERMODE = ("COLOR", "GRAYSCALE")
ALLOWED_ORIENTATION = (0, 90, 180, 270)
ALLOWED_MEDIASOURCE = ("MANUALFEED", "ROLL")
ALLOWED_DRYTIME = ("NORMAL", "EXTENDED")
ALLOWED_MAXDETAIL = ("ON", "OFF")
ALLOWED_CUTTER = ("ON", "OFF")

# UEL - PJL Universal Exit Language escape sequence
UEL = b"\x1b%-12345X"

# TCP connect() timeout for send_raw (the Z9 answers on :9100 or it doesn't).
# Distinct from the per-I/O timeout used for the (possibly multi-minute) transfer.
_SEND_CONNECT_TIMEOUT_S = 10.0

# Suppress SIGPIPE on a broken send (Linux). macOS/BSD use SO_NOSIGPIPE instead.
# The freeglaz CLI sets SIGPIPE to SIG_DFL (shell-pipe behaviour), so a Z9 that
# closes mid-send would otherwise kill the process instead of raising an error.
_MSG_NOSIGNAL = getattr(socket, "MSG_NOSIGNAL", 0)


# =======================================================================
# Utilities
# =======================================================================

def mm_to_pt(mm: float) -> float:
    return mm * PT_PER_MM


def mm_to_pjl(mm: float) -> int:
    return round(mm * PJL_PER_MM)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_icc_profile_description(icc_bytes: bytes) -> str:
    """
    Read the profile name from the 'desc' tag of the ICC bytes.

    Handles both formats:
      - mluc (UTF-16BE, ICC v4 format) - used by HP ICCs
      - desc (ASCII, ICC v2 format) - used by older tools

    Returns "Unknown ICC Profile" if the tag is not found.
    """
    try:
        tag_count = int.from_bytes(icc_bytes[128:132], "big")
        for i in range(tag_count):
            offset = 132 + i * 12
            tag_sig = icc_bytes[offset:offset + 4]
            if tag_sig != b"desc":
                continue
            tag_offset = int.from_bytes(icc_bytes[offset + 4:offset + 8], "big")
            tag_size = int.from_bytes(icc_bytes[offset + 8:offset + 12], "big")
            type_sig = icc_bytes[tag_offset:tag_offset + 4]
            if type_sig == b"mluc":
                # mluc structure (ICC v4): at tag_offset -> 'mluc'(4) reserved(4)
                # count(4) recsize(4=12), then records of 12 bytes:
                # lang(2) country(2) LENGTH(4) OFFSET(4). We read the 1st record
                # (length at +20, offset at +24; offset relative to the tag start).
                rec_len = int.from_bytes(icc_bytes[tag_offset + 20:tag_offset + 24], "big")
                rec_offset = int.from_bytes(icc_bytes[tag_offset + 24:tag_offset + 28], "big")
                name_bytes = icc_bytes[tag_offset + rec_offset:tag_offset + rec_offset + rec_len]
                return name_bytes.decode("utf-16-be").strip("\x00")
            elif type_sig == b"desc":
                str_len = int.from_bytes(icc_bytes[tag_offset + 8:tag_offset + 12], "big")
                return icc_bytes[tag_offset + 12:tag_offset + 12 + str_len].decode("ascii").strip("\x00")
    except Exception:
        pass
    return "Unknown ICC Profile"


# =======================================================================
# Dataclasses: PrintJob, PrintResult, TiffInfo
# =======================================================================

@dataclass
class TiffInfo:
    """Metadata of a source TIFF (read once, reused)."""
    path: Path
    width_px: int
    height_px: int
    xdpi: float
    ydpi: float
    width_mm: float
    height_mm: float
    has_icc: bool

    @classmethod
    def from_path(cls, path: Union[str, Path]) -> "TiffInfo":
        """Read the metadata without loading the pixels (fast)."""
        path = Path(path)
        if not path.exists():
            raise Z9PrintError(f"TIFF not found: {path}")

        with TiffFile(path) as tif:
            page = tif.pages[0]

            # Dimensions
            w = page.tags[256].value if 256 in page.tags else None
            h = page.tags[257].value if 257 in page.tags else None
            if w is None or h is None:
                raise Z9PrintError(
                    f"Dimensions absentes du TIFF : {path}"
                )

            # DPI (rational XResolution/YResolution)
            xdpi = ydpi = None
            if 282 in page.tags and 283 in page.tags:
                xres = page.tags[282].value
                yres = page.tags[283].value
                xdpi = float(xres[0]) / float(xres[1]) if isinstance(xres, tuple) else float(xres)
                ydpi = float(yres[0]) / float(yres[1]) if isinstance(yres, tuple) else float(yres)

            if not xdpi or not ydpi:
                raise Z9PrintError(
                    f"DPI absents du TIFF : {path} — "
                    "fournis explicitement image_w_mm/image_h_mm"
                )

            # Embedded ICC?
            has_icc = 34675 in page.tags

        return cls(
            path=path,
            width_px=w,
            height_px=h,
            xdpi=xdpi,
            ydpi=ydpi,
            width_mm=w / xdpi * 25.4,
            height_mm=h / ydpi * 25.4,
            has_icc=has_icc,
        )


@dataclass
class PrintJob:
    """
    Specification of a freeglaz print job.

    All dimensions in mm. Offsets are margins from the top-left edge of the
    sheet (user reading orientation).

    The default values were chosen to reproduce the reference placement
    behavior that has been validated.
    """

    # Source input - either TIFF, or an already-built PDF
    tiff_path: Optional[Path] = None
    pdf_path: Optional[Path] = None      # already-built PDF/X-4 (Affinity)

    # ICC override (bytes of a full profile). If set, these bytes are embedded
    # as BOTH the image /ICCBased colorspace AND the OutputIntent
    # /DestOutputProfile, IN PLACE OF the ICC embedded in the source TIFF. This
    # is the single source used by build_pdfx4 to embed the live *resident*
    # profile (device passthrough): source == resident is what the Z9 firmware
    # decodes with. Pixels are NEVER touched — only which ICC bytes get embedded.
    # None = fall back to the source TIFF's own embedded ICC.
    icc_override: Optional[bytes] = None

    # Paper target
    paper_id: str = ""                   # MediaID hex (32 chars)
    paper_name: str = ""                 # display only
    media_source: str = "MANUALFEED"     # MANUALFEED | ROLL

    # Sheet geometry (mm)
    sheet_w_mm: float = 297.0
    sheet_h_mm: float = 420.0

    # Image geometry (mm)
    image_w_mm: float = 0.0
    image_h_mm: float = 0.0

    # Placement (mm from top-left edge)
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0

    # Rendering options (HP defaults)
    gloss: str = "FULLPAGE"              # FULLPAGE | OFF
    quality: str = "HIGH"                # HIGH | NORMAL | FAST
    rendermode: str = "COLOR"            # COLOR | GRAYSCALE
    max_detail: str = "OFF"              # OFF | ON
    drytime: str = "NORMAL"              # NORMAL | EXTENDED
    cutter: str = "ON"                   # ON | OFF - automatic cut at end of job (roll)
    # Rotation of the image CONTENT, applied TO THE BUFFER (np.rot90, CCW) in
    # _load_tiff_for_pdf. 0|90|180|270. The geometry (image_w/h_mm, sheet_h)
    # must already reflect the TRANSPOSED dims for 90/270 (done upstream by
    # the caller) -> standard upright PDF, firmware placement unchanged (no
    # /Rotate, GE FULLPAGE undisturbed). 0 = no rotation (default, charts).
    orientation: int = 0

    # Job
    copies: int = 1
    username: str = ""                   # PJL JobAttribute JobAcct1
    # Original filename (uploaded by the user, before internal renaming to
    # /tmp/.../source.<ext>). Optional: if provided, it is used as-is as the
    # base of the PJL JOB NAME; otherwise build_prn falls back to
    # ``tiff_path.name`` or ``pdf_path.name`` (which is "source.tif" for
    # webapp uploads - not usable to distinguish jobs on the Z9 queue side,
    # cf. UUID-per-print follow-up).
    source_filename: Optional[str] = None

    # -- Construction from partial values -------------------------

    @classmethod
    def for_tiff(cls, tiff_path: Union[str, Path], **kwargs) -> "PrintJob":
        """
        Build a pre-filled job from a source TIFF.

        Reads the image dimensions from the embedded DPI (raises Z9PrintError
        if absent). The other fields take defaults or the kwargs.
        """
        tiff_path = Path(tiff_path)
        info = TiffInfo.from_path(tiff_path)

        defaults = {
            "tiff_path": tiff_path,
            "image_w_mm": info.width_mm,
            "image_h_mm": info.height_mm,
        }
        defaults.update(kwargs)
        return cls(**defaults)

    # -- Geometry helpers -----------------------------------------

    def centered(self) -> "PrintJob":
        """Return a copy of the job with the image centered on the sheet."""
        from dataclasses import replace
        return replace(
            self,
            offset_x_mm=(self.sheet_w_mm - self.image_w_mm) / 2,
            offset_y_mm=(self.sheet_h_mm - self.image_h_mm) / 2,
        )

    # -- Validation -----------------------------------------------

    def validate(self) -> None:
        """
        Validate all the job parameters.

        Raises Z9GeometryError or Z9PrintError depending on the problem type.
        The report (`details` attribute of the exception) lists all the issues,
        not just the first one.
        """
        errors = []

        # Source mandatory and exclusive
        if not self.tiff_path and not self.pdf_path:
            errors.append("No source: tiff_path or pdf_path required")
        if self.tiff_path and self.pdf_path:
            errors.append("Mutually exclusive sources: tiff_path OR pdf_path")
        if self.tiff_path and not Path(self.tiff_path).exists():
            errors.append(f"TIFF not found: {self.tiff_path}")
        if self.pdf_path and not Path(self.pdf_path).exists():
            errors.append(f"PDF not found: {self.pdf_path}")

        # Paper
        if not self.paper_id:
            errors.append("paper_id required (target paper MediaID)")

        # Enums
        if self.gloss not in ALLOWED_GLOSS:
            errors.append(f"invalid gloss '{self.gloss}' (expected: {ALLOWED_GLOSS})")
        if self.quality not in ALLOWED_QUALITY:
            errors.append(f"invalid quality '{self.quality}' (expected: {ALLOWED_QUALITY})")
        if self.rendermode not in ALLOWED_RENDERMODE:
            errors.append(f"invalid rendermode '{self.rendermode}' (expected: {ALLOWED_RENDERMODE})")
        if self.media_source not in ALLOWED_MEDIASOURCE:
            errors.append(f"invalid media_source '{self.media_source}' (expected: {ALLOWED_MEDIASOURCE})")
        if self.max_detail not in ALLOWED_MAXDETAIL:
            errors.append(f"invalid max_detail '{self.max_detail}' (expected: {ALLOWED_MAXDETAIL})")
        if self.drytime not in ALLOWED_DRYTIME:
            errors.append(f"invalid drytime '{self.drytime}' (expected: {ALLOWED_DRYTIME})")
        if self.cutter not in ALLOWED_CUTTER:
            errors.append(f"invalid cutter '{self.cutter}' (expected: {ALLOWED_CUTTER})")
        if self.orientation not in ALLOWED_ORIENTATION:
            errors.append(f"invalid orientation '{self.orientation}' (expected: {ALLOWED_ORIENTATION})")

        # Dimensions
        if self.sheet_w_mm <= 0 or self.sheet_h_mm <= 0:
            errors.append(f"Dimensions feuille invalides : {self.sheet_w_mm}×{self.sheet_h_mm} mm")
        if self.image_w_mm < 10 or self.image_h_mm < 10:
            errors.append(f"Image too small: {self.image_w_mm:.1f}×{self.image_h_mm:.1f} mm (min 10mm)")
        if self.copies < 1:
            errors.append(f"copies must be >= 1 (current: {self.copies})")

        # Geometry: offsets and overflows per the mechanical margins
        # specific to the media_source (MANUALFEED/ROLL)
        margins = MECHANICAL_MARGINS_MM.get(self.media_source)
        if margins is None:
            # invalid media_source already reported above, we stop here
            if errors:
                raise Z9GeometryError(
                    f"Invalid job ({len(errors)} issue(s))",
                    details=errors,
                )
            return

        if self.offset_x_mm < margins["left"]:
            errors.append(
                f"offset_x={self.offset_x_mm:.1f}mm < min margin "
                f"({margins['left']} mm for {self.media_source})"
            )
        if self.offset_y_mm < margins["top"]:
            errors.append(
                f"offset_y={self.offset_y_mm:.1f}mm < min margin "
                f"({margins['top']} mm for {self.media_source})"
            )
        if self.offset_x_mm + self.image_w_mm > self.sheet_w_mm - margins["right"]:
            overflow = (self.offset_x_mm + self.image_w_mm) - (self.sheet_w_mm - margins["right"])
            errors.append(f"Image overflows on the right by {overflow:.1f} mm")
        if self.offset_y_mm + self.image_h_mm > self.sheet_h_mm - margins["bottom"]:
            overflow = (self.offset_y_mm + self.image_h_mm) - (self.sheet_h_mm - margins["bottom"])
            errors.append(
                f"Image overflows at the bottom by {overflow:.1f} mm "
                f"(bottom margin {self.media_source}={margins['bottom']} mm)"
            )

        if errors:
            raise Z9GeometryError(
                f"Invalid job ({len(errors)} issue(s))",
                details=errors,
            )


@dataclass
class PrintResult:
    """Result of a send to the Z9."""
    pdf_path: Optional[Path] = None
    prn_path: Optional[Path] = None
    prn_size_bytes: int = 0
    sent: bool = False
    nc_returncode: Optional[int] = None
    duration_seconds: float = 0.0
    preflight: Optional[PreflightReport] = None
    warnings: list = field(default_factory=list)  # non-blocking notices (e.g. GE dropped)


def resolve_gloss_capability(requested_gloss: str, capable: Optional[bool]):
    """Decide the effective Gloss Enhancer state given the paper's capability.

    Returns ``(effective_gloss, warning_or_None)``. GE is applied ONLY when the
    paper is explicitly capable (``capable is True``). If GE is requested
    (``FULLPAGE``) and the paper is not known-capable — i.e. ``False`` OR unknown
    (``None``) — it is dropped to ``OFF`` with a warning. Default-False: not
    knowing means not applying the GE (coherent with the UI). Pure/side-effect-
    free (unit-tested directly).
    """
    if requested_gloss == "FULLPAGE" and capable is not True:
        return "OFF", "Gloss Enhancer ignored: this paper does not support it."
    return requested_gloss, None


def fetch_resident_icc(z9, paper_id: str, gloss_enhancer: str,
                       rendermode: str = "COLOR") -> bytes:
    """Live SOAP read of the RESIDENT profile bytes for (paper_id, GE, colorspace).

    The single fetch primitive used at print time by every path (photo/chart,
    webapp/CLI): the resident is read FRESH at the print go and embedded via
    ``job.icc_override`` → source == what the firmware decodes with (device
    passthrough). No cache, no temp file: bytes straight from ``getProfile``.

    Raises ``Z9PrintError`` (a ``Z9Error`` subclass, so a plain ``except
    Z9Error`` also catches it) on ANY failure — network unreachable OR empty
    bytes — always with a clear "resident" message. Callers turn it into a hard
    block (never embed a stale or absent resident, never fall back to the file's
    own ICC).
    """
    color_space = "PRINTER_GRAYSCALE" if rendermode == "GRAYSCALE" else "PRINTER_RGB"
    try:
        result = z9.soap.get_profile(
            medium_id=paper_id, gloss_enhancer=gloss_enhancer,
            color_space=color_space,
        )
    except Z9Error as e:
        raise Z9PrintError(
            f"cannot read the live resident profile for paper={paper_id} "
            f"GE={gloss_enhancer}: {e}"
        ) from e
    icc = (result or {}).get("icc_bytes")
    if not icc:
        raise Z9PrintError(
            f"the Z9 returned no resident ICC bytes for paper={paper_id} "
            f"GE={gloss_enhancer} — cannot embed the resident profile."
        )
    return bytes(icc)


# =======================================================================
# PrintOps - attached to Z9Client.print
# =======================================================================

class PrintOps:
    """
    Printing operations on the Z9.

    Pipeline: TIFF/PDF -> PDF/X-4 -> preflight -> PRN (PJL header) -> port 9100.

    All methods are pure (no print/input) and raise structured
    exceptions on error. The caller (CLI/GUI) handles the
    display and confirmations.
    """

    def __init__(self, client):
        self._client = client

    # -- Gloss Enhancer capability guard (all paths, incl. CLI) ----

    def _gloss_enhancer_capable(self, paper_id: str) -> Optional[bool]:
        """True/False/None — whether the firmware reports the paper as
        gloss-enhancer-capable. None on any lookup failure (permissive)."""
        try:
            caps = self._client.paper.capabilities(paper_id) or {}
            return caps.get("supports_gloss_enhancer")
        except Exception as e:  # noqa: BLE001 — never let the guard break a print
            logger.info("GE capability lookup failed for %s: %s", paper_id, e)
            return None

    def _apply_gloss_guard(self, job: "PrintJob", result: "PrintResult") -> None:
        """Neutralize the Gloss Enhancer if requested on a non-capable paper.

        Safety net independent of the UI (protects the CLI too): never send
        ``GLOSSENHANCER = FULLPAGE`` to a paper the firmware reports as not
        capable. Mutates ``job.gloss`` to OFF and appends a warning to
        ``result.warnings`` (surfaced by the caller: CLI stdout / logs).
        """
        capable = self._gloss_enhancer_capable(job.paper_id)
        effective, warning = resolve_gloss_capability(job.gloss, capable)
        if warning:
            job.gloss = effective
            result.warnings.append(warning)
            logger.warning("%s (paper=%s)", warning, job.paper_name or job.paper_id)

    # -- Step 1: PDF/X-4 construction ----------------------------

    def build_pdfx4(self, job: PrintJob, output_pdf: Union[str, Path]) -> Path:
        """
        Generate a freeglaz-compliant 16-bit PDF/X-4 from the source TIFF.

        The produced PDF has:
          - 16-bit RGB image placed according to the freeglaz formula
          - Embedded ICC profile (identical to the image's and the OutputIntent's)
          - MediaBox = TrimBox = sheet dimensions
          - XMP with GTS_PDFXVersion = PDF/X-4

        :param job: PrintJob (must be validated beforehand via job.validate())
        :param output_pdf: output path
        :return: Path of the created PDF
        """
        if not job.tiff_path:
            raise Z9PrintError(
                "build_pdfx4 requires a source TIFF (job.tiff_path). "
                "For an already-made PDF, pass it directly to build_prn()."
            )

        output_pdf = Path(output_pdf)

        # Full TIFF read (pixels + ICC)
        raw, px_w, px_h, icc_data = self._load_tiff_for_pdf(job.tiff_path, job.orientation)

        # Single ICC source for BOTH PDF/X-4 roles (image /ICCBased +
        # OutputIntent /DestOutputProfile): job.icc_override wins if set (the
        # resident profile bytes, fetched live by the caller), otherwise the ICC
        # embedded in the source TIFF. Pixels (raw) are untouched — only the ICC
        # bytes change. Charts and photos converge on this exact line.
        if job.icc_override is not None:
            icc_data = job.icc_override

        if icc_data is None:
            raise Z9PrintError(
                f"No embedded ICC profile in {job.tiff_path}. "
                "freeglaz requires a TIFF with a profile to build the PDF/X-4."
            )

        icc_name = _get_icc_profile_description(icc_data)

        # PDF geometry (freeglaz formula - see _build_pdf docstring)
        self._build_pdf(
            raw=raw,
            px_w=px_w, px_h=px_h,
            icc_data=icc_data, icc_name=icc_name,
            output_pdf=output_pdf,
            job=job,
        )

        return output_pdf

    # -- Step 2: preflight ---------------------------------------

    def preflight(self, pdf_path: Union[str, Path]) -> PreflightReport:
        """
        Check that a PDF respects the freeglaz PDF/X-4 convention.

        Does NOT raise - returns a PreflightReport. The caller
        decides what to do with it (see Z9PreflightError for the conversion).

        :param pdf_path: PDF to check
        :return: PreflightReport
        """
        return preflight_pdfx4(pdf_path)

    def preflight_or_raise(self, pdf_path: Union[str, Path]) -> PreflightReport:
        """
        Strict variant: raises Z9PreflightError if the PDF is not compliant.

        :raises Z9PreflightError: if blocking checks failed
        """
        report = self.preflight(pdf_path)
        if not report.ok:
            failed = len(report.failures())
            raise Z9PreflightError(
                f"PDF not compliant with freeglaz ({failed} check(s) failed)",
                report=report,
            )
        return report

    # -- Step 3: PRN construction with PJL header ----------------

    def build_prn(self, job: PrintJob, pdf_path: Union[str, Path],
                  output_prn: Union[str, Path]) -> Path:
        """
        Build the final PRN file (PJL header + PDF + PJL footer).

        The produced file is ready to be sent as-is to port 9100.

        :param job: PrintJob (validated)
        :param pdf_path: PDF/X-4 generated by build_pdfx4() or provided by the user
        :param output_prn: output path of the PRN
        :return: Path of the created PRN
        """
        pdf_path = Path(pdf_path)
        output_prn = Path(output_prn)

        if not pdf_path.exists():
            raise Z9PrintError(f"PDF not found: {pdf_path}")

        # PJL computations - mechanical margins per media_source
        bottommargin_pjl = BOTTOMMARGIN_PJL_BY_SOURCE[job.media_source]

        topmargin = mm_to_pjl(job.offset_y_mm)
        leftmargin = mm_to_pjl(job.offset_x_mm)
        paperwidth = leftmargin + mm_to_pjl(job.image_w_mm) + RIGHTMARGIN_PJL
        paperlength = topmargin + mm_to_pjl(job.image_h_mm) + bottommargin_pjl

        # Effective* = internal values for the firmware (slightly shifted)
        etm = topmargin - 1
        elm = leftmargin - 1
        erm = RIGHTMARGIN_PJL - 1
        ebm = bottommargin_pjl
        ew = mm_to_pjl(job.image_w_mm)
        el = mm_to_pjl(job.image_h_mm)

        username = job.username or os.environ.get("USER") or "freeglaz"
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

        # JobAcct5 - fresh UUID4 per print. Without it, the Z9 treats 2
        # consecutive PRNs with the same JOB NAME as the same job and
        # silently overwrites the previous one in the queue (verified
        # empirically). UUID format: uppercase, with dashes, without braces.
        job_uuid = str(uuid.uuid4()).upper()
        # Source filename for the PJL JOB NAME. We prefer
        # ``job.source_filename`` (original uploaded name) if provided,
        # otherwise we fall back to ``path.name`` (which is "source.tif" for
        # webapp uploads - not very useful to distinguish 2 jobs in the Z9
        # queue). Sanitize ASCII to stay firmware-safe.
        if job.source_filename:
            source_name = job.source_filename
        else:
            source_path = job.tiff_path or job.pdf_path
            source_name = Path(source_path).name if source_path else "untitled"
        job_filename = sanitize_for_pjl(source_name)

        header_lines = self._build_pjl_header(
            job=job, username=username, timestamp=timestamp,
            job_uuid=job_uuid, job_filename=job_filename,
            paperwidth=paperwidth, paperlength=paperlength,
            topmargin=topmargin, leftmargin=leftmargin,
            bottommargin_pjl=bottommargin_pjl,
            etm=etm, elm=elm, erm=erm, ebm=ebm, ew=ew, el=el,
        )

        # Writing the PRN
        header_bytes = UEL
        for line in header_lines:
            header_bytes += (line + "\n").encode("utf-8")

        footer = b"\r\n" + UEL + b"@PJL EOJ\r\n" + UEL

        with open(pdf_path, "rb") as f:
            pdf_data = f.read()

        with open(output_prn, "wb") as f:
            f.write(header_bytes)
            f.write(pdf_data)
            f.write(footer)

        return output_prn

    # -- Step 4: send to port 9100 -------------------------------

    def send_raw(self, prn_path: Union[str, Path], timeout: int = 600,
                 port: int = 9100) -> int:
        """
        Send a raw PRN to port 9100 (JetDirect) over a plain Python socket,
        guaranteeing that EVERY byte is delivered even under Z9 backpressure.

        History / why not `nc`: the previous implementation shelled out to
        ``nc -w 5`` under a 120s subprocess timeout. On a large job (e.g. a
        60x90cm @ 600dpi PRN ~1.55GB, ~225s to transfer while the Z9 ingests
        slowly) the transfer was TRUNCATED — the 120s timeout killed nc before
        the end (and ``nc -w 5`` could also drop on a >5s write stall). The Z9
        then received an INCOMPLETE PDF and rejected it with
        ``0090-0007-0096 EH-PDL parse error`` ("format error" = malformed/
        incomplete, NOT "too big"). Sending over a socket with ``sendall``
        blocks through backpressure and delivers all bytes; it also removes the
        netcat dependency (works on Windows).

        Close semantics (behaviour observed to be consistent across senders):
        the CLIENT closes first
        (``shutdown(SHUT_WR)``); the Z9 acknowledges, sends a few opaque bytes,
        then closes (FIN). We drain until EOF as an ingestion confirmation — the
        content of those bytes is treated as optional and opaque.

        :param prn_path: PRN to send
        :param timeout: socket I/O timeout in seconds — deliberately generous
                        (a 1.5GB job can take minutes under backpressure). This
                        is a per-operation stall guard, NOT a global cap that
                        would cut a legitimate in-progress transfer.
        :return: 0 on success (historical contract: callers test ``rc == 0``)
        :raises Z9SendError: connection refused, incomplete transfer, or I/O error
        """
        prn_path = Path(prn_path)
        if not prn_path.exists():
            raise Z9SendError(f"PRN not found: {prn_path}")

        total = prn_path.stat().st_size
        host = self._client.host
        try:
            with socket.create_connection((host, port),
                                          timeout=_SEND_CONNECT_TIMEOUT_S) as s:
                # A Z9 that RSTs mid-send must NOT deliver SIGPIPE and kill the
                # process (the CLI sets SIGPIPE to SIG_DFL) — we want a catchable
                # error. macOS/BSD: SO_NOSIGPIPE; Linux: MSG_NOSIGNAL on send.
                try:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_NOSIGPIPE, 1)
                except (AttributeError, OSError):
                    pass
                s.settimeout(float(timeout))
                # Stream the file (1MB chunks, memory-safe on multi-GB PRNs).
                # We write the WHOLE chunk (handling partial writes + backpressure),
                # which delivers every byte — the actual fix vs the truncating nc.
                sent = 0
                with open(prn_path, "rb") as f:
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            n = s.send(view, _MSG_NOSIGNAL)
                            view = view[n:]
                            sent += n
                if sent != total:
                    raise Z9SendError(
                        f"Incomplete transfer: {sent}/{total} bytes sent to the Z9"
                    )
                # Explicit close (client closes first) + drain the Z9 reply until
                # EOF = ingestion confirmed. Short timeout: the reply comes fast.
                s.shutdown(socket.SHUT_WR)
                s.settimeout(15.0)
                while True:
                    try:
                        if not s.recv(4096):
                            break
                    except socket.timeout:
                        break
        except Z9SendError:
            raise
        except OSError as e:
            raise Z9SendError(f"Send to Z9 ({host}:{port}) failed: {e}") from e

        return 0

    # -- Full pipeline -------------------------------------------

    def send(self, job: PrintJob,
             work_dir: Optional[Union[str, Path]] = None,
             on_progress: Optional[Callable[[str, dict], None]] = None,
             skip_preflight: bool = False) -> PrintResult:
        """
        Full pipeline: validate -> PDF/X-4 -> preflight -> PRN -> 9100.

        :param job: PrintJob (validated automatically at the start)
        :param work_dir: directory for intermediate PDF/PRN. If None, uses
                         the directory of the source TIFF.
        :param on_progress: callback(stage, data) called before each step.
                            stages: "validate", "pdf", "preflight", "prn", "send", "done"
        :param skip_preflight: if True, skips the preflight (not recommended)
        :return: PrintResult with paths and metrics
        :raises Z9PrintError: any error in the chain
        """
        import time
        start = time.time()
        result = PrintResult()

        def progress(stage, **data):
            if on_progress is not None:
                on_progress(stage, data)

        # 0. Validation
        progress("validate")
        job.validate()

        # 0.5 Gloss Enhancer capability guard (all paths, incl. CLI): drop GE +
        # warn if requested on a paper the firmware reports as not capable.
        self._apply_gloss_guard(job, result)

        # Determine the paths
        if work_dir is not None:
            work_dir = Path(work_dir)
            work_dir.mkdir(parents=True, exist_ok=True)
        else:
            source = job.tiff_path or job.pdf_path
            work_dir = Path(source).parent

        base = Path(job.tiff_path or job.pdf_path).stem
        pdf_path = work_dir / f"{base}.pdf"
        prn_path = work_dir / f"{base}.prn"

        # 1. PDF/X-4 (unless the user provided an already-built PDF)
        if job.tiff_path:
            progress("pdf", output=pdf_path)
            self.build_pdfx4(job, pdf_path)
        else:
            pdf_path = Path(job.pdf_path)

        result.pdf_path = pdf_path

        # 2. Preflight (unless explicitly skipped)
        if not skip_preflight:
            progress("preflight", pdf=pdf_path)
            report = self.preflight_or_raise(pdf_path)
            result.preflight = report

        # 3. PRN
        progress("prn", pdf=pdf_path, output=prn_path)
        self.build_prn(job, pdf_path, prn_path)
        result.prn_path = prn_path
        result.prn_size_bytes = prn_path.stat().st_size

        # 4. Send
        progress("send", prn=prn_path, host=self._client.host)
        rc = self.send_raw(prn_path)
        result.nc_returncode = rc
        result.sent = (rc == 0)

        result.duration_seconds = time.time() - start
        progress("done", result=result)
        return result

    # -----------------------------------------------------------
    # Internals - PDF/X-4 construction
    # -----------------------------------------------------------

    def _load_tiff_for_pdf(self, tiff_path, orientation: int = 0):
        """
        Load an RGB TIFF and return (raw_be, w, h, icc_bytes).

        16-bit pixels pass through as-is; 8-bit pixels are promoted to
        full-range 16-bit. Returns the pixels in big-endian (native PDF
        16-bit format). Raises Z9PrintError if the format is incompatible.
        """
        tiff_path = Path(tiff_path)
        with TiffFile(tiff_path) as tif:
            arr = tif.asarray()
            page = tif.pages[0]

            icc_data = None
            if 34675 in page.tags:
                icc_data = bytes(page.tags[34675].value)

        # Shape normalization
        if arr.ndim == 2:
            arr = np.stack([arr, arr, arr], axis=-1)
        if arr.ndim != 3:
            raise Z9PrintError(f"Unsupported TIFF shape: {arr.shape}")
        if arr.shape[2] < 3:
            raise Z9PrintError(f"TIFF must have >=3 RGB channels: {arr.shape}")

        arr = arr[..., :3]

        # Bit depth: the print path is natively 16-bit. A genuine 16-bit TIFF
        # passes through unchanged; an 8-bit TIFF is promoted to full-range
        # 16-bit. ×257 maps 0..255 onto 0..65535 exactly (255*257 == 65535),
        # unlike a bare <<8 which caps at 65280 and would skew the white point.
        # Same idiom as the calibration-chart path (chart.py). Any other dtype
        # (float, 32-bit…) is still rejected.
        if arr.dtype == np.uint8:
            arr = arr.astype(np.uint16) * 257
        elif arr.dtype != np.uint16:
            raise Z9PrintError(f"TIFF not 8/16-bit: dtype={arr.dtype}")

        # Rotation of the CONTENT (landscape/portrait orientation) TO THE BUFFER:
        # np.rot90 CCW, k = orientation//90. The buffer is really rotated -> the
        # PDF contains a standard upright image (no firmware /Rotate). For
        # 90/270, the px shape is transposed (H,W)->(W,H), consistent with the
        # transposed mm dims that the caller already passed to
        # compute_geometry / job.
        if orientation:
            arr = np.rot90(arr, k=orientation // 90)

        # Big-endian conversion (native PDF)
        arr_be = arr.astype(">u2", copy=True)
        h, w, _ = arr_be.shape
        raw = arr_be.tobytes()

        return raw, w, h, icc_data

    def _build_pdf(self, raw, px_w, px_h, icc_data, icc_name,
                   output_pdf: Path, job: PrintJob):
        """
        Build the PDF/X-4 according to the freeglaz formula.

        Geometric formula (empirically validated):
          - The image is ALWAYS placed at y = margins["bottom"] from the bottom
            of the PDF, regardless of the desired offset_y on the sheet. This
            value is 17.4mm in MANUALFEED and 5mm in ROLL.
          - The offset_y is encoded in TOPMARGIN PJL only (the firmware
            applies this translation to position the image in the page).
          - The offset_x IS encoded directly in the X position of the PDF
            (no horizontal flip).

        Consequence: the firmware does a vertical translation of the PDF, so
        the bottom of the PDF (y=margin_bottom) corresponds to the TOP edge of
        the image on the sheet. The same mechanics apply in MANUALFEED and ROLL,
        only the mechanical margin constants change.
        """
        margin_bottom_mm = MECHANICAL_MARGINS_MM[job.media_source]["bottom"]

        page_w_pt = mm_to_pt(job.sheet_w_mm)
        page_h_pt = mm_to_pt(job.sheet_h_mm)
        img_w_pt = mm_to_pt(job.image_w_mm)
        img_h_pt = mm_to_pt(job.image_h_mm)

        x_pt = mm_to_pt(job.offset_x_mm)
        y_pt = mm_to_pt(margin_bottom_mm)         # per media_source

        pdf = pikepdf.Pdf.new()

        # ICC profile shared between image and OutputIntent
        icc_stream = pdf.make_indirect(Stream(pdf, icc_data))
        icc_stream["/N"] = 3   # RGB

        # 16-bit FlateDecode image stream
        image_stream = pdf.make_indirect(
            Stream(pdf, zlib.compress(raw, level=6))
        )
        image_stream["/Type"] = Name("/XObject")
        image_stream["/Subtype"] = Name("/Image")
        image_stream["/Width"] = px_w
        image_stream["/Height"] = px_h
        image_stream["/BitsPerComponent"] = 16
        image_stream["/ColorSpace"] = Array([Name("/ICCBased"), icc_stream])
        image_stream["/Filter"] = Name("/FlateDecode")
        image_stream["/Intent"] = Name("/RelativeColorimetric")

        resources = pdf.make_indirect(Dictionary({
            "/ProcSet": Array([Name("/PDF"), Name("/ImageC")]),
            "/XObject": Dictionary({"/Im0": image_stream}),
        }))

        # Content stream - placement
        content_bytes = (
            "q\n"
            f"{img_w_pt:.6f} 0 0 {img_h_pt:.6f} {x_pt:.6f} {y_pt:.6f} cm\n"
            "/Im0 Do\n"
            "Q\n"
        ).encode("ascii")
        content_stream = pdf.make_indirect(Stream(pdf, content_bytes))

        # Page (CropBox/BleedBox deliberately omitted - Affinity behavior)
        page = pdf.make_indirect(Dictionary({
            "/Type": Name("/Page"),
            "/MediaBox": Array([0, 0, page_w_pt, page_h_pt]),
            "/TrimBox": Array([0, 0, page_w_pt, page_h_pt]),
            "/Resources": resources,
            "/Contents": content_stream,
        }))
        pages = pdf.make_indirect(Dictionary({
            "/Type": Name("/Pages"),
            "/Kids": Array([page]),
            "/Count": 1,
        }))
        page["/Parent"] = pages

        # OutputIntent - same ICC as the image -> APPE transparent (no APPE-side
        # conversion). NB device passthrough itself needs this embedded profile
        # to BE the resident (the firmware decodes device->ink via it).
        output_intent = pdf.make_indirect(Dictionary({
            "/Type": Name("/OutputIntent"),
            "/S": Name("/GTS_PDFX"),
            "/OutputConditionIdentifier": String(icc_name),
            "/OutputCondition": String(icc_name),
            "/Info": String(icc_name),
            "/DestOutputProfile": icc_stream,
        }))

        # XMP Metadata (GTS_PDFXVersion like Affinity)
        xmp = (
            "<?xpacket begin='﻿' id='W5M0MpCehiHzreSzNTczkc9d'?>\n"
            "<x:xmpmeta xmlns:x='adobe:ns:meta/'>\n"
            " <rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
            "  <rdf:Description rdf:about=''\n"
            "    xmlns:pdfxid='http://www.npes.org/pdfx/ns/id/'>\n"
            "   <pdfxid:GTS_PDFXVersion>PDF/X-4</pdfxid:GTS_PDFXVersion>\n"
            "  </rdf:Description>\n"
            " </rdf:RDF>\n"
            "</x:xmpmeta>\n"
            "<?xpacket end='w'?>"
        ).encode("utf-8")
        metadata = pdf.make_indirect(Stream(pdf, xmp))
        metadata["/Type"] = Name("/Metadata")
        metadata["/Subtype"] = Name("/XML")

        pdf.Root["/Type"] = Name("/Catalog")
        pdf.Root["/Pages"] = pages
        pdf.Root["/OutputIntents"] = Array([output_intent])
        pdf.Root["/Metadata"] = metadata

        pdf.save(output_pdf, min_version="1.6", compress_streams=True)

    # -----------------------------------------------------------
    # Internals - PJL construction
    # -----------------------------------------------------------

    def _build_pjl_header(self, job, username, timestamp,
                          job_uuid, job_filename,
                          paperwidth, paperlength,
                          topmargin, leftmargin,
                          bottommargin_pjl,
                          etm, elm, erm, ebm, ew, el):
        """
        Build the list of PJL header lines.

        The line order reproduces the PJL format expected for an
        APPE PDF/X-4 job.

        Job identifiers:
        - ``JOB NAME`` : ``"<sanitized_filename> (1 page) - freeglaz"`` -
          unique per source file, readable on the EWS / front panel side.
        - ``JobAcct5`` : fresh UUID4 per print. Without it, 2 consecutive PRNs
          with the same JOB NAME were considered identical by the Z9 queue and
          silently overwrote the previous one.
        - ``JobAcct6`` / ``JobAcct7`` : application name + version
          (freeglaz + ``_FREEGLAZ_VERSION``), in the expected PJL JobAcct format.
        - ``JobAcct16`` : UUID4 of the application instance, stable for
          the whole process lifetime (cf. ``_APP_INSTANCE_UUID``). Allows
          grouping all jobs of the same freeglaz session.
        """
        # JOB NAME in PJL format: "<filename> (1 page) - App".
        # We keep "(1 page)" literal because all our PDF/X-4 are
        # single-page (1 image per sheet - no step & repeat in freeglaz).
        job_name = f"{job_filename} (1 page) - freeglaz"
        return [
            "@PJL RESET",
            f'@PJL JOB NAME="{job_name}"',
            "@PJL SET GLVERSION = 1.0",
            f"@PJL SET COPIES = {job.copies}",
            f"@PJL SET CUTTER = {job.cutter}",
            f'@PJL SET JOBATTR = "JobAcct1={username}"',
            f'@PJL SET JOBATTR = "JobAcct4={timestamp}"',
            f'@PJL SET JOBATTR = "JobAcct5={job_uuid}"',
            '@PJL SET JOBATTR = "JobAcct6=freeglaz"',
            f'@PJL SET JOBATTR = "JobAcct7={_FREEGLAZ_VERSION}"',
            '@PJL SET JOBATTR = "JobAcct11=osx"',
            '@PJL SET JOBATTR = "JobAcct15=freeglaz"',
            f'@PJL SET JOBATTR = "JobAcct16={_APP_INSTANCE_UUID}"',
            "@PJL SET JOBSOURCE = APPLICATION",
            "@PJL SET MATCHEXACTMEDIA = OFF",
            "@PJL SET MISMATCHACTION = HOLD",
            "@PJL SET PRINTINGORDER = DIRECT",
            f'@PJL SET TIMESTAMP = "{timestamp}"',
            f'@PJL SET USERNAME = "{username}"',
            f"@PJL SET PAPERWIDTH = {paperwidth}",
            f"@PJL SET PAPERLENGTH = {paperlength}",
            f"@PJL SET TOPMARGIN = {topmargin}",
            f"@PJL SET LEFTMARGIN = {leftmargin}",
            f"@PJL SET BOTTOMMARGIN = {bottommargin_pjl}",
            f"@PJL SET RIGHTMARGIN = {RIGHTMARGIN_PJL}",
            "@PJL SET COLORSPACE = DEVICECALIBRATED",
            f"@PJL SET RENDERINTENT = {RENDERINTENT_DEFAULT}",
            f"@PJL SET RENDERMODE = {job.rendermode}",
            "@PJL SET CROPMARKS = OFF",
            f"@PJL SET DRYTIME = {job.drytime}",
            "@PJL SET ECONOMODE = OFF",
            f"@PJL SET EFFECTIVEBOTTOMMARGIN = {ebm}",
            f"@PJL SET EFFECTIVELEFTMARGIN = {elm}",
            f"@PJL SET EFFECTIVELENGTH = {el}",
            f"@PJL SET EFFECTIVERIGHTMARGIN = {erm}",
            f"@PJL SET EFFECTIVETOPMARGIN = {etm}",
            f"@PJL SET EFFECTIVEWIDTH = {ew}",
            "@PJL SET EMULATEDPRINTER = NONE",
            f"@PJL SET GLOSSENHANCER = {job.gloss}",
            "@PJL SET MARGINLAYOUT = STANDARD",
            "@PJL SET MARGINS = NORMAL",
            f"@PJL SET MAXDETAIL = {job.max_detail}",
            f'@PJL SET MEDIAID = "{job.paper_id}"',
            f"@PJL SET MEDIASOURCE = {job.media_source}",
            "@PJL SET NESTMODE = OFF",
            f"@PJL SET PAGECOPIES = {job.copies}",
            "@PJL SET PRINTAREA = FULLSIZE",
            f"@PJL SET PRINTQUALITY = {job.quality}",
            "@PJL SET RET = ON",
            "@PJL SET STARTPRINTING = IMMEDIATELY",
            "@PJL SET UNIDIRECTIONAL = OFF",
            "@PJL SET YCUTTER = OFF",
            "@PJL ENTER LANGUAGE=PDF",
        ]

    # -----------------------------------------------------------
    # PRN inspection (utility for debugging)
    # -----------------------------------------------------------

    @staticmethod
    def inspect_prn(prn_path: Union[str, Path]) -> dict:
        """
        Extract the PJL header of a .prn and return the parsed parameters.

        :param prn_path: path of the .prn
        :return: dict of PJL parameters and PDF payload size
        """
        prn_path = Path(prn_path)
        with open(prn_path, "rb") as f:
            data = f.read()

        # Find the end of the PJL header (= just before @PJL ENTER LANGUAGE=PDF + newline)
        marker = b"@PJL ENTER LANGUAGE="
        idx = data.find(marker)
        if idx < 0:
            raise Z9PrintError(f"PJL header not found in {prn_path}")

        # Include the full ENTER LANGUAGE line
        end_of_header = data.find(b"\n", idx) + 1
        header_text = data[:end_of_header].decode("utf-8", errors="replace")

        params = {}
        for line in header_text.splitlines():
            line = line.strip()
            if line.startswith("@PJL SET "):
                rest = line[len("@PJL SET "):]
                if " = " in rest:
                    k, v = rest.split(" = ", 1)
                    params[k.strip()] = v.strip().strip('"')

        # Find the end of the PDF (before the final UEL)
        uel_pos = data.rfind(UEL)
        payload_start = end_of_header
        payload_end = uel_pos if uel_pos > payload_start else len(data)

        return {
            "prn_path": str(prn_path),
            "total_size": len(data),
            "header_size": end_of_header,
            "payload_size": payload_end - payload_start,
            "language": header_text.split("LANGUAGE=")[-1].strip(),
            "params": params,
        }
