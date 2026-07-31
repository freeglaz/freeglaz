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

"""Pydantic schemas exposed by the freeglaz webapp API."""
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["info", "warning", "error"]
    code: Optional[str] = None
    message: str


class Ink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str
    label: str
    level_pct: int = Field(ge=0, le=100)
    state: Literal["ok", "warning", "error"] = "ok"


class LoadedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    short_id: Optional[int] = None
    name: str
    category: str
    is_factory: bool

    media_source: str
    sheet_width_mm: Optional[float] = None
    sheet_height_mm: Optional[float] = None
    roll_width_mm: Optional[float] = None

    gloss_enhancer_supported: bool = False  # default-False: unknown ⇒ GE not offered
    max_detail_supported: bool = True
    profilable: bool = True


class Z9Activity(BaseModel):
    """Current physical activity of the Z9, as exposed by the
    firmware in ``/DeviceStatus.json > ActivitiesOverview >
    MostRelevantActivity``.

    Conceptually separate from ``current_job`` (the backend-side webapp
    job) because the Z9 stays active (Processing → Drying → ...) well
    after the webapp worker has finished (PRN sent to the buffer).
    It is precisely this desynchronization that this object allows us
    to honestly report to the frontend.
    """
    model_config = ConfigDict(extra="forbid")

    name: str        # e.g. "Processing", "Drying", "NoActivity"
    progress_pct: Optional[float] = None  # None if firmware doesn't expose it


class ArgyllStatus(BaseModel):
    """Availability of the Argyll CMS dependency (binaries + reference data).

    Static per session (resolved once at backend startup). Surfaced so the
    frontend can show a non-blocking warning banner when the profiling
    pipeline cannot run. See lib.z9_client.argyll.check_argyll().
    """
    model_config = ConfigDict(extra="forbid")

    ok: bool          # bin_ok and ref_ok
    bin_ok: bool      # all required binaries resolved
    ref_ok: bool      # ref/ data directory resolved and populated
    missing: list[str]  # human tokens: missing binary names + "ref"
    bin_dir: Optional[str] = None  # resolved binaries directory (where found)
    ref_dir: Optional[str] = None  # resolved ref/ directory (where found)


class Status(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    serial: str
    firmware: str

    ready: bool
    state_text: str

    # DEDICATED signal, distinct from reachability: False = no printer
    # resolved (neither Z9_HOST/.env nor active store.json) → onboarding/auto-open.
    # True = configured (whether it responds or not — offline is carried by
    # ready/alerts). Auto-open only if z9_configured == False.
    z9_configured: bool = True

    loaded_paper: Optional[LoadedPaper] = None
    inks: list[Ink]
    alerts: list[Alert]
    z9_activity: Optional[Z9Activity] = None

    # Argyll CMS availability (bin + ref). Static per session; None only if
    # not yet resolved. Drives a non-blocking frontend warning banner.
    argyll: Optional[ArgyllStatus] = None

    # Offline demo mode (mock Z9). True → the frontend shows a demo badge and an
    # "exit demo" affordance. Flipped by POST /api/printers/demo.
    demo: bool = False


class PrintParams(BaseModel):
    """User print parameters — **without** file identifier.

    The ``file_id`` lives in ``PreviewRequest`` / ``StartPrintRequest`` to
    keep clean semantics: *print parameters* on one side, *source file*
    on the other.

    Important note: ``offset_x_mm`` / ``offset_y_mm`` are **deltas**
    relative to the auto-computed position (centering by default, except in
    economy ROLL). 0 = auto position. The absolute result is found in
    ``GeometryResult.image_x_mm`` / ``image_y_mm``.
    """

    model_config = ConfigDict(extra="forbid")

    gloss_enhancer: Literal["FULLPAGE", "OFF"] = "FULLPAGE"
    quality: Literal["HIGH", "NORMAL", "FAST"] = "HIGH"
    copies: int = Field(default=1, ge=1, le=99)

    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    # Rotation of the image content (landscape/portrait), in degrees CCW. Applied
    # to pixels (np.rot90) by the lib; the geometry layer transposes
    # image_w/h_mm before compute_geometry for 90/270. 0 = none.
    orientation: Literal[0, 90, 180, 270] = 0
    max_detail: Literal["ON", "OFF"] = "OFF"
    drytime: Literal["NORMAL", "EXTENDED"] = "NORMAL"
    rendermode: Literal["COLOR", "GRAYSCALE"] = "COLOR"

    media_source_override: Optional[Literal["AUTO", "MANUALFEED", "ROLL"]] = None
    sheet_width_mm_override: Optional[float] = None
    sheet_height_mm_override: Optional[float] = None


class GeometryResult(BaseModel):
    """Computed print geometry.

    Convention:
    - ``sheet_*_mm``: effective sheet dimensions (measured for SHEET /
      MANUAL_FEED, override or image_h+10mm in economy ROLL).
    - ``image_*_mm``: source image dimensions in mm.
    - ``image_x_mm`` / ``image_y_mm``: **absolute** position of the image
      from the top-left corner of the sheet. This is the output used
      to draw the geometry preview on the UI side.
    - ``margin_*_mm``: spaces between the image and the sheet edges
      (margin_left_mm == image_x_mm). Informative for the UI.
    - ``centered_x`` / ``centered_y``: true only if the user delta
      is 0 AND the mode auto-positions at the center.
    """

    model_config = ConfigDict(extra="forbid")

    sheet_width_mm: float
    sheet_height_mm: float
    image_width_mm: float
    image_height_mm: float

    image_x_mm: float
    image_y_mm: float

    # AUTO placement anchor in X (mm), BEFORE the user delta
    # (offset_x_mm): centered (SHEET) or stuck to the left + margin (economy
    # ROLL). This is EXACTLY the value used for the PDF/PRN —
    # ``image_x_mm == auto_x_mm + offset_x_mm`` user. Exposed so the
    # front consumes a SINGLE source of truth instead of recomputing it (and
    # diverging in ROLL: it centered on the total width → misleading field).
    auto_x_mm: float
    # Same in Y (``image_y_mm == auto_y_mm + offset_y_mm``): centered (SHEET) or
    # stuck to the top + margin (economy ROLL = 5 mm). Consumed by the front to
    # no longer recompute a divergent autoY (in ROLL height_mm is null →
    # autoCenter returned 0 ≠ 5 → misleading "Position Y" field).
    auto_y_mm: float

    margin_left_mm: float
    margin_top_mm: float
    margin_right_mm: float
    margin_bottom_mm: float

    media_source: Literal["SHEET", "ROLL", "MANUAL_FEED"]
    centered_x: bool
    centered_y: bool

    # Printable area (sheet minus mechanical margins). Origin = top-left
    # corner of the sheet. The frontend draws it dashed to signal to the
    # user the physical limit of the Z9.
    printable_x_mm: float = 0.0
    printable_y_mm: float = 0.0
    printable_w_mm: float = 0.0
    printable_h_mm: float = 0.0

    # Directional overflows in mm. 0 if no overflow on that side.
    # Any non-zero overflow = oversize (blocking).
    overflow_left_mm: float = 0.0
    overflow_top_mm: float = 0.0
    overflow_right_mm: float = 0.0
    overflow_bottom_mm: float = 0.0


class PreviewRequest(BaseModel):
    """Body of POST /api/print/preview: file reference + parameters."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    params: PrintParams = Field(default_factory=PrintParams)


class PrintPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    geometry: GeometryResult
    can_print: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    file_icc_name: Optional[str] = None
    file_icc_md5: Optional[str] = None
    paper_icc_name: Optional[str] = None
    paper_icc_md5: Optional[str] = None
    # Semantics of the 4 values (see print_geometry.icc_match_status):
    #   match    — profiles confirmed identical (MD5 priority, name fallback)
    #   mismatch — profiles confirmed different → distorted colorimetric render
    #   unknown  — comparison impossible on the paper side (SOAP fails) or
    #              partial data → alert without blocking
    #   none     — file without embedded ICC profile (marginal case in 2026,
    #              photo editors embed the ICC by default)
    icc_match: Literal["match", "mismatch", "unknown", "none"] = "none"
    # Human-readable reason for the icc_match decision. Serves the UI tooltip and debug.
    # E.g. "Byte-identical profiles (MD5)", "File without embedded ICC profile".
    icc_match_reason: Optional[str] = None


class JobStage(str, Enum):
    """Stages of a print job (mock worker 5a, real 5b)."""

    PREPARING = "preparing"
    PREFLIGHT = "preflight"
    RENDERING = "rendering"
    SENDING   = "sending"
    DONE      = "done"
    ERROR     = "error"
    CANCELLED = "cancelled"


class JobEvent(BaseModel):
    """A progress event in a print job."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    stage: JobStage
    progress: int = Field(ge=0, le=100)
    message: str
    data: Optional[dict] = None


class PrintJobState(BaseModel):
    """Current state of a job + full history of events."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    file_id: str
    params: PrintParams
    status: JobStage
    progress: int = Field(ge=0, le=100)
    elapsed_seconds: float
    started_at: datetime
    finished_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    events: list[JobEvent] = Field(default_factory=list)


class StartPrintRequest(BaseModel):
    """Body of POST /api/print."""

    model_config = ConfigDict(extra="forbid")

    file_id: str
    params: PrintParams = Field(default_factory=PrintParams)


class StartPrintResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStage


class FileInfo(BaseModel):
    """Metadata of an uploaded file (TIFF/PDF) for the viewer.

    Invariant: ``is_printable`` must be ``True`` if and only if
    ``blocking_issues`` is empty. ``warnings`` don't prevent
    printing but are signaled to the user (state F on the UI side).
    """

    model_config = ConfigDict(extra="forbid")

    file_id: str
    filename: str
    kind: Literal["tiff", "pdf"]

    width_mm: float
    height_mm: float

    dpi: Optional[int] = None
    bits_per_sample: Optional[int] = None

    has_icc: bool
    icc_name: Optional[str] = None
    # MD5 hash of the embedded ICC profile bytes (TIFF tag 34675 or
    # PDF /DestOutputProfile). Exact-identity value, kept for debug. None if no
    # ICC bytes accessible (PDF with OutputIntent /Info only, file without ICC).
    icc_md5: Optional[str] = None
    # Colorimetric identity hash (colour tags only, metadata excluded — see
    # icc_identity). THE value used for paper matching: robust to name/copyright/
    # date-only byte differences between the editor's export and the Z9 firmware.
    icc_color_hash: Optional[str] = None

    is_printable: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ─── ICC profiling ────────────────────────────────────────


class ProfileRequest(BaseModel):
    """Payload of POST /api/papers/{mediaid}/profile.

    3 workflows exposed: ``PRINT_AND_SCAN`` (full ~10 min),
    ``PRINT_ONLY`` (prints the chart only), ``SCAN_ONLY``
    (scans a pre-printed chart). Cancellation descoped to V2
    following empirical proof that the Z9 firmware accepts
    the 3 workflowKinds natively.

    ``gloss_enhancer`` is exposed as a bool on the webapp side for UX
    simplicity, the backend maps it internally to FULLPAGE/OFF (consistent
    with Bug 15-1). The POST route also validates that the paper supports GE
    when ``gloss_enhancer=True``.

    ``quality`` / ``max_detail`` / ``color_space`` are exposed in the
    contract from V1 (consistency with CLI ``freeglaz paper profile``) but V1
    frontend will hardcode them to BEST/False/RGB. The UI extension will be
    purely frontend, without contract change.
    """

    model_config = ConfigDict(extra="forbid")

    workflow: Literal["PRINT_AND_SCAN", "PRINT_ONLY", "SCAN_ONLY"]
    # Profile name (visible in the ICC desc tag + on the Z9 panel).
    # Character validation: XML-safe (cf. brief, SOAP constraint). The
    # backend re-applies a regex on reception (covers injection).
    profile_name: str = Field(min_length=1, max_length=63)
    gloss_enhancer: bool
    quality: Literal["BEST", "NORMAL", "FAST"] = "BEST"
    max_detail: bool = False
    color_space: Literal["RGB", "GRAYSCALE"] = "RGB"


class ProfileResponse(BaseModel):
    """Response of POST /api/papers/{mediaid}/profile."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    estimated_duration_s: int  # ~600 for PRINT_AND_SCAN, ~420 for SCAN_ONLY
