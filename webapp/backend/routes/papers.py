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

"""Endpoints /api/papers — inventory + favorites + notes + ICC actions + CLC.

P1.A: list / favorites / notes (read-only inventory).
P2.B: ICC actions (export / replace / restore / rollback) with
automatic local backups (rotation 5).
P4.B: color linear calibration (CLC) in a background thread
+ progress SSE.

The ICC actions and the CLC rely on the existing ``PaperOps`` methods
of ``lib.z9_client.client`` (already proven in the CLI). No lib
change for these features — the webapp wraps them in HTTP, manages
the local backups in ``webapp/data/icc_backups/``, and orchestrates the
CLC via the ``calibration_jobs`` singleton service.

Mapping ge_state UI → SOAP gloss_enhancer:
- ``"off"``    → ``"OFF"``
- ``"on"``     → ``"FULLPAGE"`` (empirical firmware value, cf. FULLPAGE glitch)
- ``"single"`` → ``"OFF"`` (paper without GE, single slot)
"""
import asyncio
import json
import logging
import queue as _queue
import re as _re
import tempfile
from io import BytesIO
from pathlib import Path as PathlibPath
from typing import Optional

from fastapi import (
    APIRouter, Body, Depends, HTTPException, Path, Request,
    Response, UploadFile, File,
)
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.exceptions import Z9PaperError
from lib.z9_client.papers import transform_all, transform_paper
from lib.z9_client.papers import _is_custom_mediaid  # additive discipline: already exposed via papers.py
from lib.z9_client.parsers import parse_soap_medium_list

from lib.z9_client import icc_backups          # unified per-serial backup service (B-backups)
from lib.z9_client import store as _store      # get_serial bridge
from webapp.backend.routes.status import get_z9
from webapp.backend.services import (
    calibration_jobs, ledm_calibration_cache, paper_state,
    profile_jobs,
)
from webapp.backend.models import ProfileRequest, ProfileResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/papers", tags=["papers"])

# MEDIAID pattern: hex 32 chars (customs e.g.
# "9E489F02AE027F9DD93191D872728C1D") OR short numeric id (factory e.g.
# "2001", "4060"). Both are tolerated in the path.
_MEDIAID_REGEX = r"^[0-9A-Fa-f]{2,32}$"
_GE_STATE_PATTERN = r"^(off|on|single)$"


# Mapping ge_state UI ("off"/"on"/"single") → SOAP gloss_enhancer
# (cf. module docstring and FULLPAGE glitch note on the FULLPAGE value).
_GE_TO_SOAP = {
    "off":    "OFF",
    "on":     "FULLPAGE",
    "single": "OFF",
}


def _soap_gloss(ge_state: str) -> str:
    val = _GE_TO_SOAP.get(ge_state)
    if val is None:
        raise HTTPException(422, detail=f"ge_state invalide: {ge_state!r}")
    return val


# Characters tolerated in an ICC filename after sanitization.
# The desc tag may contain commas, spaces, accents (cf.
# "freeglaz_CansonBarytaPhotographique_GEOFF, GE OFF"). We keep a
# broad but filesystem-safe whitelist.
_FILENAME_SAFE_CHARS = _re.compile(r"[^A-Za-z0-9._-]+")


def _read_icc_desc(icc_bytes: bytes) -> Optional[str]:
    """Extract the ``desc`` tag from an ICC binary (human-readable profile name).

    The ``desc`` tag holds the name that color management apps
    display. For
    our Z9 profiles it is typically ``freeglaz_CansonBaryta…_GEOFF``
    or ``HPDesignjetZ9CansonBaryta…GEON``.

    Uses Pillow ImageCms (already a project dependency for the preview).
    Returns ``None`` if the binary is corrupt, non-standard,
    or has no desc tag.
    """
    try:
        from PIL import ImageCms
        profile = ImageCms.ImageCmsProfile(BytesIO(icc_bytes))
        desc = ImageCms.getProfileDescription(profile)
    except Exception as e:
        logger.info("ICC desc read failed: %s", e)
        return None
    if not desc:
        return None
    return desc.strip().strip("\x00").strip()


def _sanitize_icc_filename(name: str) -> str:
    """Turn a desc tag into a filesystem-safe filename.

    Replaces sequences of non ``[A-Za-z0-9._-]`` characters with a
    single underscore. Strips leading/trailing underscores. Truncates to
    120 chars to stay reasonable.
    """
    cleaned = _FILENAME_SAFE_CHARS.sub("_", name).strip("_")
    return cleaned[:120] or "profile"


def _require_z9(z9: Optional[Z9Client] = Depends(get_z9)) -> Z9Client:
    if z9 is None:
        raise HTTPException(503, detail="Z9 not configured (Z9_HOST missing)")
    return z9


# ─── Paper list ───────────────────────────────────────────────────────


@router.get("")
def list_papers(z9: Z9Client = Depends(_require_z9)) -> dict:
    """Return the full paper inventory (factory + custom).

    Pipeline:
    1. SOAP ``getMediumList`` → raw XML (5 min cache recommended in V2,
       but none in V1 — one fetch per HTTP call)
    2. Parse → list of rich dicts (existing paper parser)
    3. Transform each dict → Paper contract of the UX spec, resolving
       the donor_name for customs via internal lookup
    4. Inject ``favorite`` and ``has_notes`` from ``paper_state``

    Response: ``{papers: [...], total: N, custom: M, factory: F}``.
    """
    try:
        xml = z9.paper.get_raw_xml()
    except Z9Error as e:
        logger.warning("list_papers: SOAP getMediumList failed: %s", e)
        raise HTTPException(502, detail=f"Z9 getMediumList failed: {e}")

    raw_papers = parse_soap_medium_list(xml)
    favorites = paper_state.load_favorites()
    notes_keys = paper_state.notes_keys()
    # P3.A2: enrich the CLC status via LEDM (4 statuses vs 2
    # SOAP). 30s cache on the webapp side to avoid spamming Z9 HTTPS.
    ledm_clc = ledm_calibration_cache.get_snapshot(z9)

    papers = transform_all(
        raw_papers,
        favorites=favorites,
        notes_keys=notes_keys,
        ledm_clc=ledm_clc,
    )

    custom_count = sum(1 for p in papers if p["custom"])
    factory_count = sum(1 for p in papers if p["factory"])
    logger.info(
        "list_papers: %d total (%d custom, %d factory)",
        len(papers), custom_count, factory_count,
    )
    return {
        "papers": papers,
        "total": len(papers),
        "custom": custom_count,
        "factory": factory_count,
    }


# ─── Favorites ────────────────────────────────────────────────────────


@router.post("/{mediaid}/favorite", status_code=200)
def toggle_favorite(mediaid: str = Path(..., pattern=_MEDIAID_REGEX)) -> dict:
    """Toggle the favorite state of a paper. Returns the new state.

    No validation of the mediaid against the real paper list
    (no Z9 round-trip needed for a local operation). If
    the user favorites a mediaid that does not exist, it is an
    orphan entry in paper_favorites.json that will be silently
    ignored on the next ``list_papers`` (the transformer only matches
    real papers actually present).
    """
    new_state = paper_state.toggle_favorite(mediaid)
    return {"mediaid": mediaid, "favorite": new_state}


# ─── Notes ────────────────────────────────────────────────────────────


@router.get("/{mediaid}/notes")
def get_notes(mediaid: str = Path(..., pattern=_MEDIAID_REGEX)) -> dict:
    """Return a paper's Markdown notes (empty string if none)."""
    return {"mediaid": mediaid, "notes": paper_state.get_notes(mediaid)}


@router.put("/{mediaid}/notes", status_code=200)
def set_notes(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    body: dict = Body(...),
) -> dict:
    """Update a paper's Markdown notes.

    Body: ``{"notes": "string markdown"}``. If ``notes`` is empty or
    whitespace-only, the entry is removed from the JSON (consistency with
    the ``has_notes`` flag of ``list_papers``).
    """
    notes = body.get("notes", "")
    if not isinstance(notes, str):
        raise HTTPException(
            422, detail="The 'notes' field must be a string",
        )
    paper_state.set_notes(mediaid, notes)
    return {"mediaid": mediaid, "saved": True}


# ─── ICC actions (P2.B) ───────────────────────────────────────────────


def _backup_current_profile(
    z9: Z9Client, mediaid: str, ge_state: str,
) -> Optional[PathlibPath]:
    """Back up a slot's current ICC profile before overwriting.

    Fetches the profile via ``PaperOps.export_icc`` (which writes to disk)
    targeting the backups folder directly. Rotation max 5 after
    writing.

    Returns the ``Path`` of the created backup, or ``None`` if no profile
    existed to back up (empty factory case or missing slot — the
    firmware then returns a SOAP error that we intercept).
    """
    serial = _store.get_serial(z9)             # per-serial backup home
    backup_path = icc_backups.new_backup_path(serial, mediaid, ge_state)
    soap_ge = _soap_gloss(ge_state)
    try:
        z9.paper.export_icc(
            ref=mediaid,
            output_path=str(backup_path),
            gloss_enhancer=soap_ge,
            quality="BEST",
            color_space="RGB",
        )
    except Z9Error as e:
        # No profile to back up → we don't write and we continue
        logger.info(
            "icc backup skipped for %s/%s: %s", mediaid, ge_state, e,
        )
        return None
    icc_backups.rotate(serial, mediaid, ge_state)
    logger.info(
        "icc backup created: %s/%s/%s",
        mediaid, ge_state, backup_path.name,
    )
    return backup_path


@router.get("/{mediaid}/mechanical-properties")
def get_mechanical_properties(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Mechanical properties of a paper (4 SOAP Variable fields)."""
    try:
        paper = z9.paper.details(mediaid)
    except Z9Error as e:
        logger.warning("mechanical-properties: %s", e)
        raise HTTPException(502, detail=f"Z9 error: {e}")
    if not paper:
        raise HTTPException(404, detail=f"Paper {mediaid} not found")
    settings = paper.get("settings") or {}
    return {
        "pen_to_rib": settings.get("PenToRib", "LOW"),
        "dry_time_preset": settings.get("DryTimePreset", "DT_A"),
        "dry_time_factor": int(settings.get("DryTimeFactor", "100") or "100"),
        # Show the register we actually WRITE (StarWheelPos*Roll*, bit-identical to HP),
        # NOT LowerSheet (never written by us or HP → the edit stayed invisible).
        # Aligned with the POST return (which already reads LowerRoll) → GET ↔ POST consistent. Fallbacks
        # LowerSheet/StarWheel = robustness (legacy media not reporting the Roll). Cf. Part 2.
        "star_wheel_pos": settings.get("StarWheelPosLowerRoll",
                          settings.get("StarWheelPosLowerSheet",
                          settings.get("StarWheelPos", "INTERMEDIATE"))),
        "cutter": settings.get("Cutter", "1") == "1",
    }


@router.post("/{mediaid}/mechanical-properties")
def set_mechanical_properties(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    body: dict = None,
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Modify the 4 mechanical properties of a custom paper."""
    body = body or {}
    try:
        z9.paper.set_mechanical_properties(
            medium_id=mediaid,
            pen_to_rib=body.get("pen_to_rib", "LOW"),
            dry_time_factor=int(body.get("dry_time_factor", 100)),
            star_wheels=body.get("star_wheels_position", "INTERMEDIATE"),
            cutter=int(body.get("cutter_enabled", True)),
        )
    except Z9Error as e:
        logger.warning("set_mechanical_properties: %s", e)
        raise HTTPException(502, detail=f"Z9 error: {e}")
    try:
        paper = z9.paper.details(mediaid)
    except Z9Error:
        paper = None
    settings = (paper or {}).get("settings") or {}
    return {
        "pen_to_rib": settings.get("PenToRib", body.get("pen_to_rib")),
        "dry_time_factor": int(settings.get("DryTimeFactor", body.get("dry_time_factor", 100)) or 100),
        "star_wheel_pos": settings.get("StarWheelPosLowerRoll", body.get("star_wheels_position")),
        "cutter": settings.get("Cutter", "1") == "1",
    }


@router.post("/{mediaid}/restore-default-preset")
def restore_default_preset(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Restore the mechanical properties to the original donor values."""
    try:
        z9.paper.restore_default_preset(medium_id=mediaid)
    except Z9Error as e:
        logger.warning("restore_default_preset: %s", e)
        raise HTTPException(502, detail=f"Z9 error: {e}")
    try:
        paper = z9.paper.details(mediaid)
    except Z9Error:
        paper = None
    settings = (paper or {}).get("settings") or {}
    return {
        "pen_to_rib": settings.get("PenToRib", "LOW"),
        "dry_time_factor": int(settings.get("DryTimeFactor", "100") or "100"),
        "star_wheel_pos": settings.get("StarWheelPosLowerRoll",
                          settings.get("StarWheelPos", "INTERMEDIATE")),
        "cutter": settings.get("Cutter", "1") == "1",
    }


@router.get("/{mediaid}/icc/{ge_state}")
def export_icc(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    ge_state: str = Path(..., pattern=_GE_STATE_PATTERN),
    z9: Z9Client = Depends(_require_z9),
) -> Response:
    """Download the ICC binary of a profile (factory or custom).

    Response: ``application/vnd.iccprofile`` + Content-Disposition
    attachment with a readable filename (mediaid_ge_state.icc).
    """
    soap_ge = _soap_gloss(ge_state)
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=False) as tmp:
        tmp_path = PathlibPath(tmp.name)
    try:
        try:
            z9.paper.export_icc(
                ref=mediaid,
                output_path=str(tmp_path),
                gloss_enhancer=soap_ge,
                quality="BEST",
                color_space="RGB",
            )
        except Z9Error as e:
            logger.warning("export_icc failed for %s/%s: %s", mediaid, ge_state, e)
            raise HTTPException(502, detail=f"ICC export failed: {e}")
        icc_bytes = tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    # P2-bis: filename based on the desc tag of the
    # ICC binary (human-readable) rather than the firmware MEDIAID (unreadable).
    # Fallback: ``{mediaid}_{ge_state}.icc`` if the desc tag is absent
    # or the ICC read fails.
    desc = _read_icc_desc(icc_bytes)
    fname = (
        f"{_sanitize_icc_filename(desc)}.icc"
        if desc else f"{mediaid}_{ge_state}.icc"
    )
    return Response(
        content=icc_bytes,
        media_type="application/vnd.iccprofile",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "Content-Length": str(len(icc_bytes)),
        },
    )


@router.put("/{mediaid}/icc/{ge_state}", status_code=200)
async def import_icc(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    ge_state: str = Path(..., pattern=_GE_STATE_PATTERN),
    file: UploadFile = File(...),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Replace a slot's ICC profile with an uploaded file.

    Steps:
    1. ICC header validation (``acsp`` at offset 36)
    2. Automatic backup of the current profile (if present)
    3. SOAP ``setProfile`` via ``PaperOps.import_icc``
    4. Rotation max 5 on the backups
    """
    soap_ge = _soap_gloss(ge_state)
    upload_bytes = await file.read()
    if len(upload_bytes) < 128:
        raise HTTPException(422, detail={
            "code": "icc_too_small", "message": "ICC file too small"})
    # ICC header validation: "acsp" signature at offset 36
    if upload_bytes[36:40] != b"acsp":
        raise HTTPException(422, detail={
            "code": "icc_invalid_signature",
            "message": "Invalid ICC file (signature 'acsp' missing at offset 36)"})

    # Backup before writing — Z9 I/O (SOAP), moved off the event loop
    # (async handler; to_thread pattern of start_print/wake).
    backup = await asyncio.to_thread(_backup_current_profile, z9, mediaid, ge_state)

    # Write the upload to a tempfile to pass to PaperOps
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=False) as tmp:
        tmp.write(upload_bytes)
        tmp_path = PathlibPath(tmp.name)
    try:
        try:
            # SOAP setProfile (slow) — moved off the event loop (to_thread).
            res = await asyncio.to_thread(
                z9.paper.import_icc,
                ref=mediaid,
                icc_path=str(tmp_path),
                icc_name=file.filename or None,
                gloss_enhancer=soap_ge,
                quality="BEST",
                maximum_detail="OFF",
                color_space="RGB",
                auto_backup=False,  # webapp manages its own backups
            )
        except Z9Error as e:
            logger.warning("import_icc failed for %s/%s: %s", mediaid, ge_state, e)
            raise HTTPException(502, detail=f"ICC import failed: {e}")
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return {
        "mediaid": mediaid,
        "ge_state": ge_state,
        "outcome": res.get("outcome"),
        "icc_name": res.get("icc_name"),
        "ticket_date": res.get("ticket_date"),
        "backup_created": backup.name if backup else None,
    }


@router.delete("/{mediaid}/icc/{ge_state}", status_code=200)
def restore_factory_icc(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    ge_state: str = Path(..., pattern=_GE_STATE_PATTERN),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Delete a slot's custom profile to re-expose the factory donor.

    Automatic backup of the profile before deletion (rollback possible).
    """
    soap_ge = _soap_gloss(ge_state)
    backup = _backup_current_profile(z9, mediaid, ge_state)
    try:
        res = z9.paper.delete_profile(
            ref=mediaid,
            gloss_enhancer=soap_ge,
            color_space="RGB",
        )
    except Z9Error as e:
        logger.warning("delete_profile failed for %s/%s: %s", mediaid, ge_state, e)
        raise HTTPException(502, detail=f"Factory restore failed: {e}")

    return {
        "mediaid": mediaid,
        "ge_state": ge_state,
        "outcome": res.get("outcome"),
        "deleted_icc_name": res.get("deleted_icc_name"),
        "backup_created": backup.name if backup else None,
    }


@router.get("/{mediaid}/icc/{ge_state}/backups")
def list_icc_backups(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    ge_state: str = Path(..., pattern=_GE_STATE_PATTERN),
) -> dict:
    """List a slot's local backups (most recent to oldest).

    No Z9 round-trip. Pure read of the folder
    ``webapp/data/icc_backups/{mediaid}/{ge_state}/``.
    """
    return icc_backups.backups_summary(mediaid, ge_state)


@router.post("/{mediaid}/icc/{ge_state}/rollback", status_code=200)
def rollback_icc(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    ge_state: str = Path(..., pattern=_GE_STATE_PATTERN),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Restore the most recent backup and consume it.

    No backup-of-backup: the rollback is destructive on the current
    profile. If no backup → 404.
    """
    serial = _store.get_serial(z9)
    latest = icc_backups.latest_backup(serial, mediaid, ge_state)
    if latest is None:
        raise HTTPException(404, detail="Aucun backup disponible pour ce slot")

    # P2-ter: preserve the original ICC profile name on
    # rollback. The Z9 rewrites the ICC file desc tag on every
    # ``setProfile``, using ``iccName`` from the ProfilingTicket (cf.
    # ``Docs/HP_Ingenium_ICC_Reverse_Engineering.md`` §9.4). If we
    # pass ``icc_name=None``, the firmware sets a default name
    # (timestamp). So we read the backup's desc tag and pass it
    # explicitly as ``iccName``.
    try:
        backup_bytes = latest.read_bytes()
    except OSError as e:
        raise HTTPException(500, detail=f"Backup illisible: {e}")
    original_desc = _read_icc_desc(backup_bytes)

    soap_ge = _soap_gloss(ge_state)
    try:
        res = z9.paper.import_icc(
            ref=mediaid,
            icc_path=str(latest),
            icc_name=original_desc,  # None if desc read fails → firmware default
            gloss_enhancer=soap_ge,
            quality="BEST",
            maximum_detail="OFF",
            color_space="RGB",
            auto_backup=False,
        )
    except Z9Error as e:
        logger.warning("rollback import_icc failed for %s/%s: %s", mediaid, ge_state, e)
        raise HTTPException(502, detail=f"Rollback failed: {e}")

    consumed = icc_backups.consume(latest)
    return {
        "mediaid": mediaid,
        "ge_state": ge_state,
        "outcome": res.get("outcome"),
        "restored_from": latest.name,
        "restored_icc_name": original_desc,
        "consumed": consumed,
    }


# ─── Custom lifecycle (P3.C1) ─────────────────────────────────────────


# Permissive whitelist for names: letters (with accents), digits,
# spaces, common punctuation. Prevents characters that would break
# the SOAP XML or the filesystem.
# ASCII only (re.ASCII → \w = [a-zA-Z0-9_]): "free-text input" origin → we REFUSE
# non-ASCII (case 1). Avoids the `ü` mluc encoding bug downstream (ICC desc / firmware). Papers
# FETCHED from the Z9 (mirror) keep their accents — they do not go through here.
_PAPER_NAME_REGEX = _re.compile(r"^[\w\s\-_.,()']+$", _re.ASCII)


class CreatePaperRequest(BaseModel):
    """Body of POST /api/papers."""
    name: str = Field(..., min_length=1, max_length=64)
    donor_id: str = Field(..., min_length=2, max_length=32)
    force: bool = Field(
        False,
        description=(
            "If True, bypass the local name-duplicate check. "
            "Ported CLI logic — the firmware itself accepts "
            "duplicates silently."
        ),
    )


def _build_paper_contract(z9: Z9Client, mediaid: str) -> Optional[dict]:
    """Re-fetch + re-transform to return a complete Paper to the
    frontend after creation. Used by POST /api/papers.

    Invalidates the LEDM cache first so the fresh status (pending on
    a freshly created paper) is visible immediately.
    """
    ledm_calibration_cache.invalidate()
    try:
        xml = z9.paper.get_raw_xml()
    except Z9Error as e:
        logger.warning("post-create paper refetch failed: %s", e)
        return None
    raw_papers = parse_soap_medium_list(xml)
    raw = next((p for p in raw_papers if p.get("id") == mediaid), None)
    if not raw:
        return None
    factory_papers_by_id = {
        p["id"]: p for p in raw_papers
        if not _is_custom_mediaid(p.get("id"))
    }
    ledm = ledm_calibration_cache.get_snapshot(z9)
    return transform_paper(
        raw,
        factory_papers_by_id=factory_papers_by_id,
        favorites=paper_state.load_favorites(),
        notes_keys=paper_state.notes_keys(),
        ledm_clc=ledm,
    )


@router.post("", status_code=201)
def create_paper(
    body: CreatePaperRequest = Body(...),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Create a custom paper by cloning an existing donor.

    The firmware automatically copies all mechanical properties
    of the donor at creation time. No post-creation modification
    possible in V1 (cf. ``Docs/P3_PRE_FLIGHT.md``).

    - 201: created, returns ``{paper: Paper}``
    - 409: name conflict (the firmware accepts it but we protect the UX —
      bypass via ``force=true``). Returns ``{detail: {error,
      existing_mediaid}}``.
    - 422: invalid name (empty / too long / forbidden characters)
    - 502: SOAP newCustomMedium failed
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(422, detail="The paper name cannot be empty")
    if not name.isascii():
        raise HTTPException(
            422,
            detail=("The name must use ASCII characters, no accents "
                    "(e.g. « Hahnemühle » → « Hahnemuhle »)."),
        )
    if not _PAPER_NAME_REGEX.match(name):
        raise HTTPException(
            422,
            detail=(
                "The name contains forbidden characters (letters, "
                "digits, spaces and common punctuation only)."
            ),
        )

    # Duplicate check (ported CLI logic). We accept mediaid OR name in
    # donor_id; we only run the duplicate check on the name.
    if not body.force:
        try:
            existing = z9.paper.get_by_name(name, exact=True)
        except Z9Error as e:
            logger.warning("name conflict check failed: %s", e)
            existing = []
        if existing:
            raise HTTPException(
                409,
                detail={
                    "error": "name_conflict",
                    "existing_mediaid": existing[0].get("id"),
                    "message": f"A paper already has this name: {existing[0].get('name')}",
                },
            )

    try:
        result = z9.paper.create(
            name=name,
            donor=body.donor_id,
            language="en_US",
        )
    except Z9PaperError as e:
        # donor not found or ambiguous on the lib side
        raise HTTPException(422, detail=f"Invalid donor: {e}")
    except Z9Error as e:
        logger.warning("create_paper SOAP failed: %s", e)
        raise HTTPException(502, detail=f"Creation failed: {e}")

    new_id = result.get("id")
    if not new_id:
        # Firmware accepted but returned no MEDIAID (case
        # observed empirically on some firmwares). We report it.
        raise HTTPException(
            502,
            detail="Firmware accepted the creation but returned no MEDIAID",
        )

    paper = _build_paper_contract(z9, new_id)
    return {
        "paper": paper,
        "mediaid": new_id,
        "donor_id": result.get("donor_id"),
        "donor_name": result.get("donor_name"),
    }


@router.delete("/{mediaid}", status_code=200)
def delete_paper(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Delete a custom paper and clean up its local ICC backups.

    - 200: deleted
    - 403: factory paper (refused by the lib)
    - 409: paper loaded in Z9 (refused by the lib)
    - 502: SOAP deleteCustomMedium failed (other reason)
    """
    # Prefilter: refuse factory format on the webapp side (the lib does it
    # again, but we avoid a useless Z9 round-trip)
    if not _is_custom_mediaid(mediaid):
        raise HTTPException(403, detail="Refus de supprimer un papier factory")

    try:
        result = z9.paper.delete(ref=mediaid)
    except Z9PaperError as e:
        msg = str(e)
        # Map lib errors → HTTP. The lib raises Z9PaperError on the
        # guardrails (factory / loaded paper) with an explicit message.
        if "factory" in msg.lower():
            raise HTTPException(403, detail=msg)
        if "loaded" in msg.lower():
            raise HTTPException(409, detail=msg)
        raise HTTPException(422, detail=msg)
    except Z9Error as e:
        logger.warning("delete_paper SOAP failed: %s", e)
        raise HTTPException(502, detail=f"Deletion failed: {e}")

    # Clean up orphan ICC backups: the paper no longer exists, its backups
    # (all ge_state, under backups/<serial>/<mediaid>/) no longer have value.
    # Best-effort — never mask a successful firmware DELETE with an FS error.
    try:
        icc_backups.purge_media(_store.get_serial(z9), mediaid)
    except (Z9Error, ValueError) as e:
        logger.warning("icc_backups: orphan purge %s ignored: %s", mediaid, e)

    # Invalidate the LEDM cache so a later GET /api/papers no longer sees
    # the deleted paper in its CLC snapshot (edge case if the LEDM
    # takes time to update on the firmware side).
    ledm_calibration_cache.invalidate()

    return {
        "mediaid": mediaid,
        "name": result.get("name"),
        "outcome": result.get("outcome"),
    }


# ─── Color linear calibration (P4.B) ──────────────────────────────────


@router.get("/calibrate/current")
def get_current_calibration() -> dict:
    """State of the active calibration (or the last finished one).

    Lightweight endpoint without a Z9 round-trip — used by the global
    status bar badge to show "CLC running on {paper}" and
    let the user return to the detail panel of the paper
    concerned.

    Returns ``{job: null}`` if no calibration has ever been
    started during this backend session.
    """
    job = calibration_jobs.current()
    return {"job": job.snapshot() if job else None}


@router.post("/{mediaid}/calibrate", status_code=200)
def start_calibration(
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> dict:
    """Start a color linear calibration on a paper.

    The job runs in a daemon thread (the lib method ``paper.calibrate``
    is synchronous blocking ~5-10 min). The client follows progress
    via the SSE ``GET /api/papers/{mediaid}/calibrate/events``.

    Responses:
    - 200: ``{job: {id, mediaid, state, ...}}``
    - 409: a calibration is already running (on this paper or another)
    """
    try:
        job = calibration_jobs.start(mediaid, z9)
    except RuntimeError as e:
        current = calibration_jobs.current()
        raise HTTPException(
            409,
            detail={
                "message": str(e),
                "current": current.snapshot() if current else None,
            },
        )
    return {"job": job.snapshot()}


@router.get("/{mediaid}/calibrate/events")
async def stream_calibration_events(
    request: Request,
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
) -> EventSourceResponse:
    """SSE stream of an in-progress calibration.

    Async generator + 15s keepalive pattern. The backend events
    (``calibration_started``, ``progress``, ``calibration_finished``)
    are relayed in the standard SSE format (event: type, data: JSON).

    If no calibration is active on this paper (already finished / not
    yet started), we immediately return a ``snapshot`` event with
    the current state then close. Lets the client recover
    the state even after reconnection.
    """
    job = calibration_jobs.current()
    if job is None or job.mediaid != mediaid:
        # No calibration for this mediaid — return empty snapshot + end
        async def _empty():
            yield {"event": "snapshot", "data": json.dumps({"job": None})}
        return EventSourceResponse(_empty())

    loop = asyncio.get_event_loop()
    q = job.events_queue

    async def _gen():
        # Initial snapshot (current job state at subscribe time)
        yield {"event": "snapshot", "data": json.dumps({"job": job.snapshot()})}

        def _blocking_get():
            try:
                return q.get(block=True, timeout=15.0)
            except _queue.Empty:
                return None

        while True:
            if await request.is_disconnected():
                break
            item = await loop.run_in_executor(None, _blocking_get)
            if item is None:
                # 15s timeout with no event → keepalive
                yield {"event": "ping", "data": ""}
                if job.state in ("done", "error"):
                    # Job finished, nothing left to wait for, close cleanly
                    break
                continue
            if item is calibration_jobs._END:
                break

            event_type = item["type"]
            yield {"event": event_type, "data": json.dumps(item["data"])}
            if event_type == "calibration_finished":
                break

    return EventSourceResponse(_gen())


# ─── ICC profiling (P5) ────────────────────────────────────────────────


# XML-safe: we forbid `<`, `>`, `&`, unescaped quotes. The ICC
# desc tag may contain accents and spaces (cf.
# "freeglaz_CansonBaryta_GE ON"), so we keep a permissive whitelist
# on printable characters. The firmware lib validation may be
# stricter — we align on the CLI ``freeglaz paper profile`` which
# accepts everything except angle brackets/ampersand/quotes.
_PROFILE_NAME_ALLOWED = _re.compile(r"^[a-zA-Z0-9 _-]+$")


def _validate_profile_name(name: str) -> None:
    """Raise HTTPException 400 if the name contains disallowed chars.

    Allowed: ASCII letters, digits, spaces, hyphens, underscores.
    Max 63 chars (ICC desc tag limit). Pydantic validates min/max length.
    """
    n = name.strip()
    if not _PROFILE_NAME_ALLOWED.match(n):
        raise HTTPException(
            400,
            detail="Profile name must contain only letters, digits, spaces, hyphens and underscores.",
        )
    if len(n) > 63:        # ICC V2 desc limit (textDescriptionTag) — single source of the rule
        raise HTTPException(
            400,
            detail="Profile name too long (max 63 characters — ICC V2 desc limit).",
        )


def _check_paper_supports_ge(z9: Z9Client, mediaid: str) -> bool:
    """Return True if the paper supports the Gloss Enhancer.

    Source of truth: SOAP ``paper.details(mediaid)`` →
    ``capabilities.GlossEnhancerSupported == "1"`` (consistent with CLI
    ``cmd_paper_profile`` lines 879-881 of the ``freeglaz`` file).

    We delegate to the lib rather than re-parsing. The lib method already
    returns the capabilities as a dict.
    """
    try:
        details = z9.paper.details(mediaid)
    except Z9Error as e:
        raise HTTPException(
            502, detail=f"Impossible de lire les capabilities du papier : {e}",
        )
    caps = (details or {}).get("capabilities", {}) or {}
    return caps.get("GlossEnhancerSupported") == "1"


def _check_paper_is_loaded(z9: Z9Client, mediaid: str) -> None:
    """Raise HTTPException 409 if the requested paper is not loaded.

    Backend guardrail (the frontend already checked, but a
    forged curl request could bypass). Consistent with the P5 brief §"critical
    guardrails": 409 Conflict if requested MEDIAID ≠ loaded MEDIAID.
    """
    try:
        dashboard = z9.device.status()
    except Z9Error as e:
        raise HTTPException(502, detail=f"Z9 unreachable: {e}")
    loaded_id = (dashboard or {}).get("loaded_paper_id") or ""
    # Case-insensitive tolerance (hex MEDIAID may arrive in upper/lower)
    if loaded_id.lower() != mediaid.lower():
        raise HTTPException(
            409,
            detail={
                "code": "paper_not_loaded",
                "message": (
                    f"Paper {mediaid} is not loaded in the Z9. "
                    f"Currently loaded paper: {loaded_id or '(none)'}."
                ),
                "loaded_mediaid": loaded_id or None,
            },
        )


@router.get("/profile/current")
def get_current_profile() -> dict:
    """State of the active profiling (or the last finished one).

    Lightweight endpoint without a Z9 round-trip — used by the global
    "Profiling running" status bar badge to let the
    user return to wizard step 3.

    Returns ``{job: null}`` if no profiling has ever been started
    during this backend session.
    """
    job = profile_jobs.current()
    return {"job": job.snapshot() if job else None}


@router.post(
    "/{mediaid}/profile", status_code=200, response_model=ProfileResponse,
)
def start_profile(
    body: ProfileRequest,
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
    z9: Z9Client = Depends(_require_z9),
) -> ProfileResponse:
    """Start an ICC profiling (Print & Scan or Scan only).

    Critical guardrails (cf. P5 brief):
    - 400 if ``gloss_enhancer=True`` but the paper does not support GE
      (firmware capabilities validation).
    - 400 if ``profile_name`` contains XML-reserved characters.
    - 409 if requested MEDIAID ≠ physically loaded paper MEDIAID
      (the firmware would refuse anyway, we fail early with a
      clear message).
    - 409 if another profiling is already running (singleton).

    The job runs in a daemon thread — the lib method
    ``paper.profile`` is synchronous blocking (~7-10 min). The client
    follows progress via the SSE
    ``GET /api/papers/{mediaid}/profile/events``.

    Responses:
    - 200: ``{job_id, estimated_duration_s}``
    - 400: violation of the GE / profile_name rules
    - 409: paper not loaded or profiling already running
    """
    _validate_profile_name(body.profile_name)

    # GE guard — same check as CLI ``cmd_paper_profile``. We only do
    # the lib round-trip if the user wants FULLPAGE, saving
    # a SOAP call on the GE=OFF cases (Hahnemühle mattes, etc.).
    if body.gloss_enhancer:
        if not _check_paper_supports_ge(z9, mediaid):
            raise HTTPException(
                400,
                detail={
                    "code": "ge_not_supported",
                    "message": "This paper does not support Gloss Enhancer.",
                },
            )

    # Loaded MEDIAID guard. For PRINT_AND_SCAN, the firmware refuses if
    # the paper differs; for SCAN_ONLY, the chart must be placed
    # on the scanner which goes through the paper path — same constraint
    # in practice.
    _check_paper_is_loaded(z9, mediaid)

    try:
        job = profile_jobs.start(
            mediaid=mediaid,
            workflow=body.workflow,
            profile_name=body.profile_name,
            gloss_enhancer=body.gloss_enhancer,
            quality=body.quality,
            max_detail=body.max_detail,
            color_space=body.color_space,
            z9_client=z9,
        )
    except RuntimeError as e:
        current = profile_jobs.current()
        raise HTTPException(
            409,
            detail={
                "code": "profile_busy",
                "message": str(e),
                "current": current.snapshot() if current else None,
            },
        )
    return ProfileResponse(
        job_id=job.id,
        estimated_duration_s=profile_jobs.ESTIMATED_DURATION_S.get(
            body.workflow, 600,
        ),
    )


@router.get("/{mediaid}/profile/events")
async def stream_profile_events(
    request: Request,
    mediaid: str = Path(..., pattern=_MEDIAID_REGEX),
) -> EventSourceResponse:
    """SSE stream of an in-progress profiling.

    Same pattern as ``/calibrate/events`` (P4) — async
    generator + 15s keepalive. The backend events
    (``profile_started``, ``progress``, ``profile_finished``) are
    relayed in the standard SSE format.

    If no profiling is active on this mediaid (already finished / not
    yet started), we return a ``snapshot`` event with
    the current state then close.
    """
    job = profile_jobs.current()
    if job is None or job.mediaid != mediaid:
        async def _empty():
            yield {"event": "snapshot", "data": json.dumps({"job": None})}
        return EventSourceResponse(_empty())

    loop = asyncio.get_event_loop()
    q = job.events_queue

    async def _gen():
        # Initial snapshot — covers mid-job reconnection (page
        # refresh, tab navigation, etc.)
        yield {"event": "snapshot", "data": json.dumps({"job": job.snapshot()})}

        def _blocking_get():
            try:
                return q.get(block=True, timeout=15.0)
            except _queue.Empty:
                return None

        while True:
            if await request.is_disconnected():
                break
            item = await loop.run_in_executor(None, _blocking_get)
            if item is None:
                # 15s timeout with no event → keepalive
                yield {"event": "ping", "data": ""}
                if job.state in ("done", "error"):
                    break
                continue
            if item is profile_jobs._END:
                break

            event_type = item["type"]
            yield {"event": event_type, "data": json.dumps(item["data"])}
            if event_type == "profile_finished":
                break

    return EventSourceResponse(_gen())
