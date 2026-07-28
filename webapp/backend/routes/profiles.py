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

"""Endpoints for ICC profile inspection and management.

GET /api/profiles/inspect → inspect an ICC profile, returns JSON
enriched with the TRC layers + taxonomy.

Two access modes:
- ``path``        : absolute path to a local ICC file
- ``paper_mediaid`` + ``slot`` : fetch from the Z9 firmware

The two modes are mutually exclusive.

Profiles screen endpoints (consumes the store):
- GET /api/profiles/store               → mirror + repo tree
- POST /api/profiles/repo               → upload ICC into a repo category
- DELETE /api/profiles/repo/{...}       → delete a repo profile
- PATCH /api/profiles/repo/{...}        → rename/move within the repo
- POST /api/profiles/mirror/copy-to-repo → copy a mirror profile to the repo
- POST /api/profiles/sync               → trigger store.sync_mirror

Architectural guardrail: no endpoint writes to or deletes in
``mirror/``. Only the sync does. ``copy-to-repo`` reads the
mirror and writes the repo — never the reverse.
"""
import hashlib
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile,
)
from pydantic import BaseModel, Field

from lib.z9_client import Z9Client, Z9Error
from lib.z9_client.inspect import InspectOps
from lib.z9_client.profile_compare import compare_profiles
from lib.z9_client import cache as _cache
from lib.z9_client import store as _store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/profiles", tags=["profiles"])


_MEDIAID_REGEX = r"^[A-F0-9]{32}$"
_SLOT_VALUES = {"on", "off", "single", "ge_on", "ge_off"}


def _require_z9(request: Request) -> Z9Client:
    z9 = getattr(request.app.state, "z9", None)
    if z9 is None:
        raise HTTPException(503, detail="Z9Client not configured")
    return z9


def _normalize_slot(slot: str) -> str:
    """Align 'ge_on'/'on' → 'on' and 'ge_off'/'off' → 'off'."""
    return {"ge_on": "on", "ge_off": "off"}.get(slot, slot)


def _soap_gloss(slot: str) -> str:
    """slot → SOAP GlossEnhancer value."""
    return {"on": "FULLPAGE", "off": "OFF", "single": "OFF"}.get(slot, "OFF")


@router.get("/inspect")
def inspect_profile(
    request: Request,
    path: Optional[str] = Query(None, description="Absolute path to a local ICC file"),
    paper_mediaid: Optional[str] = Query(None, pattern=_MEDIAID_REGEX,
                                          description="Z9 paper MediaId (hex 32)"),
    slot: Optional[str] = Query(None, description="ICC slot: on|off|single"),
) -> dict:
    """Inspect an ICC profile.

    Mode 1: ``path`` (local file).
    Mode 2: ``paper_mediaid`` + ``slot`` (Z9 firmware fetch).

    Returns the ``ProfileInspection.to_dict()`` dict enriched with the
    TRC layers + taxonomy.
    """
    # Mutually exclusive parameter validation
    has_path = bool(path)
    has_paper = bool(paper_mediaid)
    if not has_path and not has_paper:
        raise HTTPException(422, detail="Provide 'path' or ('paper_mediaid' + 'slot')")
    if has_path and has_paper:
        raise HTTPException(422, detail="'path' and 'paper_mediaid' are mutually exclusive")
    if has_paper and not slot:
        raise HTTPException(422, detail="'slot' is required with 'paper_mediaid'")
    if slot and slot not in _SLOT_VALUES:
        raise HTTPException(422, detail=f"'slot' must be one of {sorted(_SLOT_VALUES)}")

    ops = InspectOps()

    # Mode 1 — local file
    if has_path:
        p = Path(path)
        if not p.is_absolute():
            raise HTTPException(422, detail="'path' must be an absolute path")
        if not p.exists() or not p.is_file():
            raise HTTPException(404, detail=f"File not found: {p}")
        try:
            insp = ops.inspect_icc_file(p)
        except Exception as e:  # noqa: BLE001
            logger.exception("inspect_icc_file failed for %s", p)
            raise HTTPException(500, detail=f"Inspection failed: {e}")
        return ops.to_dict(insp)

    # Mode 2 — Z9 firmware fetch
    z9 = _require_z9(request)
    normalized_slot = _normalize_slot(slot)
    soap_ge = _soap_gloss(normalized_slot)
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        try:
            z9.paper.export_icc(
                ref=paper_mediaid,
                output_path=str(tmp_path),
                gloss_enhancer=soap_ge,
                quality="BEST",
                color_space="RGB",
            )
        except Z9Error as e:
            logger.warning("export_icc failed for inspect %s/%s: %s",
                           paper_mediaid, normalized_slot, e)
            raise HTTPException(404, detail=f"Firmware profile not found: {e}")
        try:
            insp = ops.inspect_icc_file(tmp_path)
        except Exception as e:  # noqa: BLE001
            logger.exception("inspect_icc_file failed for firmware fetch %s/%s",
                             paper_mediaid, normalized_slot)
            raise HTTPException(500, detail=f"Inspection failed: {e}")
        return ops.to_dict(insp)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════
# profile-compare — profile comparison endpoint
# ═══════════════════════════════════════════════════════════════════════


# Hard cap to prevent a client from flooding the backend (each profile
# runs lcms2 + convex hull 33³; with --curvature adds a
# 3D Laplacian + percentiles). 8 is enough for all realistic uses.
_COMPARE_MAX_PROFILES = 8


class CompareRequest(BaseModel):
    """JSON body for POST /api/profiles/compare.

    - ``paths``         : list of absolute paths to .icc files (≥2).
    - ``ti3_paths``     : optional, aligned with ``paths`` (None|"" possible
                           in the list to omit a profcheck per profile).
    - ``with_curvature``: if True, adds curvature stats [EXPERIMENTAL]
                           to each profile + a root notice.
    """

    paths: list[str] = Field(..., min_length=2, max_length=_COMPARE_MAX_PROFILES)
    # ti3_paths: None/"" items allowed to mean "no profcheck
    # for that profile". Pydantic strict list[str] would reject None.
    ti3_paths: Optional[list[Optional[str]]] = None
    with_curvature: bool = False


@router.post("/compare")
def compare_endpoint(req: CompareRequest) -> dict:
    """Compare 2+ ICC profiles (read-only).

    Security:
      - each ``path`` must be absolute, exist, and point to a file.
      - no file is created/modified; profcheck is optional and only
        runs if a valid ti3 is provided.

    ⚠ The curvature metric is LABELED EXPERIMENTAL in the
    response (field ``curvature.experimental: True`` + ``curvature_notice``
    root). Do not consume it as a validated metric on the UI side.
    """
    paths: list[Path] = []
    for raw in req.paths:
        p = Path(raw)
        if not p.is_absolute():
            raise HTTPException(
                422, detail=f"path must be absolute: {raw!r}",
            )
        if not p.exists() or not p.is_file():
            raise HTTPException(
                404, detail=f"Profile not found: {raw}",
            )
        paths.append(p)

    ti3_list: Optional[list[Optional[str]]] = None
    if req.ti3_paths is not None:
        if len(req.ti3_paths) != len(req.paths):
            raise HTTPException(
                422,
                detail=(
                    f"ti3_paths provided {len(req.ti3_paths)} times for "
                    f"{len(req.paths)} profiles — one ti3 per profile (or "
                    f"omit ti3_paths)"
                ),
            )
        # None or "" items → no profcheck for this profile. Non-empty items
        # must be absolute and point to an existing file.
        ti3_list = []
        for ti3 in req.ti3_paths:
            if ti3 in (None, ""):
                ti3_list.append(None)
                continue
            tp = Path(ti3)
            if not tp.is_absolute():
                raise HTTPException(
                    422, detail=f"ti3 must be absolute: {ti3!r}",
                )
            if not tp.exists() or not tp.is_file():
                raise HTTPException(
                    404, detail=f"ti3 not found: {ti3}",
                )
            ti3_list.append(str(tp))

    try:
        return compare_profiles(
            paths,
            ti3_paths=ti3_list,
            with_curvature=req.with_curvature,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("compare_profiles failed: %s", e)
        raise HTTPException(500, detail=f"Comparison failed: {e}")


# ═══════════════════════════════════════════════════════════════════════
# Profiles screen (store read + repo CRUD)
# ═══════════════════════════════════════════════════════════════════════


_REPO_CATEGORIES = {"printers", "displays", "workingspaces"}

# B3: two distinct validation levels.
#
# (a) Folder segments (device in printers/<device>/) → strict.
#     It is an internal organization identifier, we keep the
#     strict regex.
#
# (b) ICC filenames → relaxed: we allow spaces, accents,
#     parentheses ("My Eizo Screen (2026).icc" is a legitimate name),
#     but we forbid separators and characters dangerous for the
#     FS / anti-traversal.
#
# The anti-path-traversal relies on two COMBINED protections:
#   - filtering of each token (reject '/', '\', '..', NULL, controls)
#   - resolution via Path(...).resolve() + relative_to(root) (_ensure_under).
# B3 RELAXES the NAME filtering, not the PATH protection.

import re as _re

_SAFE_SEGMENT_RE = _re.compile(r"^[A-Za-z0-9._-]+$")
_FS_BAD_FILENAME_RE = _re.compile(r'[/\\\x00-\x1f\x7f:*?"<>|\n\r]')


def _safe_segment(s: str, field: str) -> str:
    """Validate that a path segment (device) contains no
    separator or escape dot. Strict refusal — no
    silent normalization."""
    if not s or not _SAFE_SEGMENT_RE.match(s):
        raise HTTPException(
            422,
            detail=f"{field!r} must match [A-Za-z0-9._-]+ (got: {s!r})",
        )
    if s in (".", "..") or s.startswith("."):
        raise HTTPException(422, detail=f"{field!r} cannot start with '.'")
    return s


def _safe_filename(s: str, field: str) -> str:
    """Validate an ICC filename allowing spaces and accents,
    while refusing dangerous characters (B3).

    Guardrails:
      - Reject any separator or FS-forbidden character
        (``/ \\ \\0 : * ? " < > | \\n \\r`` + controls).
      - Reject ``.`` and ``..`` (dot/escape).
      - Reject the ``.`` prefix (hidden file).
      - Reject if empty after strip.

    Kept: spaces, Unicode accents (`ü`, `é`, ...), parentheses,
    commas, hyphens, underscores, internal dots.
    """
    if not s:
        raise HTTPException(422, detail=f"{field!r} cannot be empty")
    stripped = s.strip()
    if not stripped or stripped in (".", ".."):
        raise HTTPException(
            422, detail=f"{field!r} forbidden: {stripped!r}",
        )
    if stripped.startswith("."):
        raise HTTPException(422, detail=f"{field!r} cannot start with '.'")
    if _FS_BAD_FILENAME_RE.search(stripped):
        raise HTTPException(
            422,
            detail=f"{field!r} contains a forbidden character "
                   f"(path separator or control): {stripped!r}",
        )
    return stripped


def _ensure_under(path: Path, root: Path) -> Path:
    """Resolve `path` and check it is under `root` (anti path-traversal)."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        raise HTTPException(403, detail="Path outside the repo refused")
    return resolved


def _validate_icc_bytes(data: bytes) -> None:
    """Check ICC minimum: 128 bytes + ``acsp`` signature at offset 36."""
    if len(data) < 132:
        raise HTTPException(422, detail={
            "code": "icc_too_small", "message": "File too small to be an ICC"})
    if data[36:40] != b"acsp":
        raise HTTPException(422, detail={
            "code": "icc_invalid_signature",
            "message": "ICC signature 'acsp' missing at offset 36 — invalid file"})


def _md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _now_iso() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


# ─── Tree serialization ───────────────────────────────────────────────


def _mirror_to_dict() -> list[dict]:
    """List the mirrors present in memory (one per known Z9 serial)."""
    mdir = _cache.mirror_dir()
    out: list[dict] = []
    if not mdir.exists():
        return out
    for serial_dir in sorted(mdir.iterdir()):
        if not serial_dir.is_dir():
            continue
        serial = serial_dir.name
        manifest = _cache.read_mirror_manifest(serial)
        papers_dir = _cache.mirror_papers_dir(serial)
        papers: list[dict] = []
        if papers_dir.exists():
            for pd in sorted(papers_dir.iterdir()):
                if not pd.is_dir():
                    continue
                pm = _cache.read_paper_manifest(serial, pd.name)
                # CRITICAL (cf. brief §2) — we read ONLY profiles[].
                # The legacy `donor` block (format residue, to be removed
                # by a store micro-fix) is ignored entirely.
                profiles = []
                for prof in (pm.get("profiles") or []):
                    fname = prof.get("filename")
                    if not fname:
                        continue
                    profiles.append({
                        "filename": fname,
                        "absolute_path": str(pd / fname),
                        "gloss_enhancer": prof.get("gloss_enhancer"),
                        "color_space": prof.get("color_space"),
                        "custom": bool(prof.get("custom")),
                        "z9_uuid": prof.get("z9_uuid"),
                        "z9_icc_name": prof.get("z9_icc_name"),
                        "z9_date": prof.get("z9_date"),
                        "md5": prof.get("md5"),
                        "size_bytes": prof.get("size_bytes"),
                        "fetched_at": prof.get("fetched_at"),
                    })
                papers.append({
                    "slug": pd.name,
                    "paper_id": pm.get("paper_id"),
                    "paper_name": pm.get("paper_name") or pd.name,
                    # `donor_paper_id` = firmware reference of the donor
                    # paper (cf. brief §2, info OK to display).
                    "donor_paper_id": pm.get("donor_paper_id"),
                    "category_name": pm.get("category_name"),
                    "is_factory": bool(pm.get("is_factory")),
                    "is_user_custom": bool(pm.get("is_user_custom")),
                    "profiles": profiles,
                })
        out.append({
            "serial": serial,
            "medium_list_version": manifest.get("medium_list_version"),
            "last_sync_at": manifest.get("last_sync_at"),
            "last_check_at": manifest.get("last_check_at"),
            "n_papers": len(papers),
            "papers": papers,
        })
    return out


def _repo_section_to_list(root: Path) -> list[dict]:
    """List the ICC files of a category (recursive up to 1 level
    for ``printers/<device>/``)."""
    if not root.exists():
        return []
    out: list[dict] = []
    for p in sorted(root.iterdir()):
        if p.is_dir():
            # e.g. printers/<device>/  → list the .icc inside
            device = p.name
            for f in sorted(p.iterdir()):
                if f.is_file() and f.suffix.lower() == ".icc":
                    out.append(_repo_profile_entry(f, device=device))
        elif p.is_file() and p.suffix.lower() == ".icc":
            out.append(_repo_profile_entry(p, device=None))
    return out


def _repo_profile_entry(path: Path, device: Optional[str]) -> dict:
    meta = _read_repo_meta(path)
    return {
        "filename": path.name,
        "absolute_path": str(path),
        "device": device,
        "md5": meta.get("md5") or _md5_file(path),
        "size_bytes": meta.get("size_bytes") or path.stat().st_size,
        "imported_at": meta.get("imported_at"),
        "origin": meta.get("origin"),  # e.g. "user_import" | "mirror_copy"
        "origin_detail": meta.get("origin_detail"),
        "display_name": meta.get("display_name") or path.stem,
    }


def _md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(64 * 1024):
            h.update(chunk)
    return h.hexdigest()


def _meta_path_for(profile_path: Path) -> Path:
    return profile_path.parent / f".{profile_path.name}.meta.json"


def _read_repo_meta(profile_path: Path) -> dict:
    meta_p = _meta_path_for(profile_path)
    if not meta_p.exists():
        return {}
    try:
        with open(meta_p, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_repo_meta(profile_path: Path, meta: dict) -> None:
    meta_p = _meta_path_for(profile_path)
    tmp = meta_p.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False, sort_keys=True)
    tmp.replace(meta_p)


@router.get("/store")
def get_profiles_store() -> dict:
    """Store tree: Z9 mirror(s) (read-only) + personal repo (read/write).

    No Z9 call — pure filesystem read. Available offline.
    The legacy `donor` block possibly present at the head of the
    ``_paper.json`` is ignored (cf. brief §2 and § store residue).
    """
    _cache.ensure_store()
    repo_printers = _cache.repo_printers_dir()
    repo_displays = _cache.repo_displays_dir()
    repo_workspaces = _cache.repo_workingspaces_dir()
    return {
        "store_root": str(_cache.root_dir()),
        "mirrors": _mirror_to_dict(),
        "repo": {
            "printers": _repo_section_to_list(repo_printers),
            "displays": _repo_section_to_list(repo_displays),
            "workingspaces": _repo_section_to_list(repo_workspaces),
        },
        "repo_printers_dir": str(repo_printers),
        "repo_displays_dir": str(repo_displays),
        "repo_workingspaces_dir": str(repo_workspaces),
    }


# ─── Repo resolution helpers ──────────────────────────────────────────


def _resolve_repo_root(category: str) -> Path:
    if category == "printers":
        return _cache.repo_printers_dir()
    if category == "displays":
        return _cache.repo_displays_dir()
    if category == "workingspaces":
        return _cache.repo_workingspaces_dir()
    raise HTTPException(
        422, detail=f"category must be one of {sorted(_REPO_CATEGORIES)}",
    )


def _resolve_repo_path(
    category: str, device: Optional[str], filename: str,
) -> Path:
    """Build the path of a profile in the repo + validate it exists
    and stays under the repo root (anti-traversal)."""
    if category not in _REPO_CATEGORIES:
        raise HTTPException(422, detail="invalid category")
    _safe_filename(filename, "filename")
    if device is not None:
        _safe_segment(device, "device")
    root = _resolve_repo_root(category)
    sub = root / device / filename if device else root / filename
    resolved = _ensure_under(sub, _cache.repo_dir())
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, detail=f"Profile not found: {filename}")
    return resolved


# ─── POST /api/profiles/repo: upload ──────────────────────────────────


@router.post("/repo")
async def upload_repo_profile(
    file: UploadFile = File(..., description="ICC file to import"),
    category: str = Form(..., description="printers | displays | workingspaces"),
    device: Optional[str] = Form(
        None, description="Device name (printers only)",
    ),
    display_name: Optional[str] = Form(
        None, description="Display name (optional, default = file stem)",
    ),
) -> dict:
    """Upload an ICC file into the personal repo.

    - Validate the ICC signature (acsp at offset 36).
    - Compute the MD5.
    - Write the file in the requested category + a sidecar
      ``.<filename>.meta.json`` with the metadata.
    - Silently refuse to replace an existing file (409).
    """
    if category not in _REPO_CATEGORIES:
        raise HTTPException(
            422, detail=f"category must be one of {sorted(_REPO_CATEGORIES)}",
        )
    if category == "printers":
        if not device:
            raise HTTPException(
                422, detail="device required for category=printers",
            )
        _safe_segment(device, "device")
    elif device:
        raise HTTPException(
            422, detail="device is only valid for category=printers",
        )

    if not file.filename or not file.filename.lower().endswith(".icc"):
        raise HTTPException(422, detail="File must have the .icc extension")
    safe_name = _safe_filename(Path(file.filename).name, "filename")

    data = await file.read()
    _validate_icc_bytes(data)

    _cache.ensure_store()
    root = _resolve_repo_root(category)
    target_dir = root / device if device else root
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    _ensure_under(target, _cache.repo_dir())

    if target.exists():
        raise HTTPException(
            409, detail=f"A profile {safe_name!r} already exists in this category",
        )

    target.write_bytes(data)
    md5 = _md5_bytes(data)
    _write_repo_meta(target, {
        "display_name": display_name or target.stem,
        "md5": md5,
        "size_bytes": len(data),
        "imported_at": _now_iso(),
        "origin": "user_import",
        "origin_detail": f"upload {file.filename}",
    })

    return {
        "ok": True,
        "absolute_path": str(target),
        "category": category,
        "device": device,
        "filename": safe_name,
        "md5": md5,
        "size_bytes": len(data),
    }


# ─── DELETE /api/profiles/repo/...: deletion ──────────────────────────


@router.delete("/repo")
def delete_repo_profile(
    category: str = Query(...),
    filename: str = Query(...),
    device: Optional[str] = Query(None),
) -> dict:
    """Delete a profile from the repo (never from the mirror)."""
    p = _resolve_repo_path(category, device, filename)
    meta_p = _meta_path_for(p)
    try:
        p.unlink()
    except OSError as e:
        raise HTTPException(500, detail=f"Deletion failed: {e}")
    if meta_p.exists():
        try:
            meta_p.unlink()
        except OSError:
            pass
    return {"ok": True, "deleted": str(p)}


# ─── PATCH /api/profiles/repo: rename / move ──────────────────────────


class PatchRepoBody(BaseModel):
    category: str = Field(..., description="current category")
    filename: str = Field(..., description="current filename")
    device: Optional[str] = Field(None)
    new_category: Optional[str] = Field(
        None, description="new category (move)",
    )
    new_device: Optional[str] = Field(
        None, description="new device (printers only)",
    )
    new_filename: Optional[str] = Field(
        None, description="new filename (.icc)",
    )
    new_display_name: Optional[str] = Field(
        None, description="new display_name label (meta only)",
    )


@router.patch("/repo")
def patch_repo_profile(body: PatchRepoBody) -> dict:
    """Rename and/or move a repo profile.

    At least one of: new_category, new_device, new_filename, new_display_name.
    Atomic updates (rename + replace of the meta).
    """
    src = _resolve_repo_path(body.category, body.device, body.filename)
    new_cat = body.new_category or body.category
    if new_cat not in _REPO_CATEGORIES:
        raise HTTPException(422, detail="invalid new_category")
    new_dev = body.new_device if body.new_device is not None else body.device
    if new_cat == "printers":
        if not new_dev:
            raise HTTPException(
                422, detail="device required for category=printers",
            )
        _safe_segment(new_dev, "new_device")
    else:
        new_dev = None
    new_name = body.new_filename or body.filename
    _safe_filename(new_name, "new_filename")
    if not new_name.lower().endswith(".icc"):
        raise HTTPException(422, detail="new_filename must end with .icc")

    root = _resolve_repo_root(new_cat)
    dest_dir = root / new_dev if new_dev else root
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _ensure_under(dest_dir / new_name, _cache.repo_dir())

    if dest != src and dest.exists():
        raise HTTPException(
            409, detail=f"Target already occupied: {dest.name}",
        )

    meta = _read_repo_meta(src)
    if body.new_display_name is not None:
        meta["display_name"] = body.new_display_name
    meta.setdefault("origin", "user_import")
    if dest != src:
        # rename + move
        src.rename(dest)
        old_meta_p = _meta_path_for(src)
        if old_meta_p.exists():
            try:
                old_meta_p.unlink()
            except OSError:
                pass
    _write_repo_meta(dest, meta)

    return {
        "ok": True,
        "absolute_path": str(dest),
        "category": new_cat,
        "device": new_dev,
        "filename": new_name,
    }


# ─── POST /api/profiles/mirror/copy-to-repo ───────────────────────────


class CopyToRepoBody(BaseModel):
    source_path: str = Field(
        ..., description="Absolute path of a mirror profile",
    )
    target_category: str = Field(
        "printers", description="printers | displays | workingspaces",
    )
    target_device: Optional[str] = Field(
        None, description="device name (printers only, default = 'Z9')",
    )
    target_filename: Optional[str] = Field(
        None, description="target filename (.icc). Default: source name.",
    )
    display_name: Optional[str] = Field(None)


@router.post("/mirror/copy-to-repo")
def copy_mirror_to_repo(body: CopyToRepoBody) -> dict:
    """Copy a profile from the mirror (read-only) to the repo (read/write).

    Preserves the ICC bit-identical. The meta records the origin
    ``mirror_copy`` + the source path. Refused if destination occupied.
    """
    src = Path(body.source_path).resolve()
    mirror_root = _cache.mirror_dir().resolve()
    try:
        src.relative_to(mirror_root)
    except ValueError:
        raise HTTPException(
            403, detail="source_path is not in the mirror",
        )
    if not src.exists() or not src.is_file():
        raise HTTPException(404, detail=f"Source not found: {src}")

    if body.target_category not in _REPO_CATEGORIES:
        raise HTTPException(422, detail="invalid target_category")
    target_dev = body.target_device
    if body.target_category == "printers":
        target_dev = target_dev or "Z9"
        _safe_segment(target_dev, "target_device")
    elif target_dev:
        raise HTTPException(
            422, detail="target_device valid only for printers",
        )
    target_filename = body.target_filename or src.name
    _safe_filename(target_filename, "target_filename")
    if not target_filename.lower().endswith(".icc"):
        raise HTTPException(422, detail="target_filename must end with .icc")

    data = src.read_bytes()
    _validate_icc_bytes(data)

    root = _resolve_repo_root(body.target_category)
    dest_dir = root / target_dev if target_dev else root
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _ensure_under(dest_dir / target_filename, _cache.repo_dir())
    if dest.exists():
        raise HTTPException(409, detail="Destination already occupied")
    dest.write_bytes(data)

    md5 = _md5_bytes(data)
    _write_repo_meta(dest, {
        "display_name": body.display_name or dest.stem,
        "md5": md5,
        "size_bytes": len(data),
        "imported_at": _now_iso(),
        "origin": "mirror_copy",
        "origin_detail": str(src),
    })

    return {
        "ok": True,
        "absolute_path": str(dest),
        "category": body.target_category,
        "device": target_dev,
        "filename": target_filename,
        "md5": md5,
        "size_bytes": len(data),
    }


# ─── POST /api/profiles/mirror/copy-to-z9 ─────────────────────────────


class CopyToZ9Body(BaseModel):
    source_path: str = Field(
        ..., description="Absolute path of a mirror profile",
    )
    label: Optional[str] = Field(
        None, description="Readable label (default: derived from paper + GE)",
    )
    on_conflict: Optional[str] = Field(
        None,
        description="Collision intent (OS): None/'cancel' = SAFE (409); "
                    "'replace' = overwrites the repo/z9 COPY (the source mirror survives); "
                    "'keep_both' = -N suffix.",
    )


@router.post("/mirror/copy-to-z9")
def copy_mirror_to_z9(body: CopyToZ9Body) -> dict:
    """Copy a profile from the mirror to the personal Z9 space, filed BY PAPER.

    This is what the "copy" button of the Profiles screen does: it simply
    files the profile in ``repo/z9/<serial>/papers/<media_id>/`` (the dedicated
    Z9 repo). It INSTALLS NOTHING on the printer (that is done in the
    Papers screen via setProfile). serial / media_id / GE / paper_name are derived
    from the mirror path + ``_paper.json``; the filename is reconstructed
    cleanly by ``save_repo_z9_profile`` (no double extension).
    """
    src = Path(body.source_path).resolve()
    mirror_root = _cache.mirror_dir().resolve()
    try:
        rel = src.relative_to(mirror_root)
    except ValueError:
        raise HTTPException(403, detail="source_path is not in the mirror")
    if not src.exists() or not src.is_file():
        raise HTTPException(404, detail=f"Source not found: {src}")

    # Mirror = mirror/<serial>/papers/<slug>/<file>.icc
    parts = rel.parts
    if len(parts) < 4 or parts[1] != "papers":
        raise HTTPException(
            422, detail="Unexpected mirror path (serial/papers/slug/file)",
        )
    serial, filename = parts[0], parts[-1]

    manifest_path = src.parent / _cache.PAPER_MANIFEST_FILENAME
    if not manifest_path.exists():
        raise HTTPException(404, detail="_paper.json missing for this paper")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    media_id = manifest.get("paper_id")
    if not media_id:
        raise HTTPException(422, detail="paper_id (media_id) missing from manifest")
    paper_name = manifest.get("paper_name") or src.parent.name
    entry = next(
        (p for p in (manifest.get("profiles") or [])
         if p.get("filename") == filename),
        {},
    )
    gloss = entry.get("gloss_enhancer")
    color_space = entry.get("color_space") or "PRINTER_RGB"
    label = (body.label or "").strip() or f"{paper_name} · GE {gloss or '?'}"

    data = src.read_bytes()
    _validate_icc_bytes(data)
    _cache.ensure_store()
    # Collision WITHOUT intent → 409 (OS paradigm). "replace" overwrites the repo/z9 COPY (the
    # read-only source mirror survives → golden rule respected). "keep_both" → -N suffix.
    from webapp.backend.routes.charts import _norm_on_conflict
    oc = _norm_on_conflict(body.on_conflict)
    if oc in (None, "cancel"):
        coll = _cache.repo_z9_name_conflict(serial, media_id, gloss, label=label)
        if coll:
            raise HTTPException(409, detail={
                "error": "name_conflict", "name": coll["name"], "suggestion": coll["suggestion"],
                "message": f"A profile \"{coll['name']}\" already exists for this paper.",
            })
    path, meta = _cache.save_repo_z9_profile(
        icc_bytes=data,
        serial=serial,
        media_id=media_id,
        paper_name=paper_name,
        label=label,
        gloss_slot=gloss,
        color_space=color_space,
        origin="mirror_copy",
        source_profile=str(src),
        on_conflict=oc,            # None/cancel (no collision here) | replace | keep_both
    )
    return {
        "ok": True,
        "absolute_path": str(path),
        "filename": path.name,
        "serial": serial,
        "media_id": media_id,
        "meta": meta,
    }


# ─── POST /api/profiles/sync ──────────────────────────────────────────


@router.post("/sync")
def trigger_sync(request: Request, force: bool = False) -> dict:
    """Trigger ``store.sync_mirror`` (pure mirror).

    Synchronous: returns the summary once the sync is finished. For long
    syncs an async/SSE switch could be added later (out of scope).
    """
    z9 = getattr(request.app.state, "z9", None)
    if z9 is None:
        raise HTTPException(503, detail="Z9 not configured")
    try:
        result = _store.sync_mirror(z9, force=force)
    except Z9Error as e:
        raise HTTPException(502, detail=f"Sync failed: {e}")
    return result


# ═══════════════════════════════════════════════════════════════════════
# Part B — Refinement "Redo with Argyll"
# ═══════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════
# Part A — CRUD of the "personal Z9" space per paper (repo/z9/)
# ═══════════════════════════════════════════════════════════════════════


def _z9_to_grouped() -> list[dict]:
    """Group the repo/z9 profiles by serial → paper → profiles.

    Relies on ``cache.list_repo_z9_profiles`` (merged sidecar). The
    human-readable paper name is read from the sidecar (self-contained
    space, no MediaId→name resolution via an external resource).
    """
    flat = _cache.list_repo_z9_profiles()
    serials: dict = {}
    for e in flat:
        serial = e.get("serial")
        media = e.get("media_id")
        papers = serials.setdefault(serial, {})
        paper = papers.setdefault(media, {
            "media_id": media,
            "paper_name": e.get("paper_name") or media,
            "profiles": [],
        })
        paper["profiles"].append(e)
    out: list[dict] = []
    for serial in sorted(serials):
        papers = serials[serial]
        out.append({
            "serial": serial,
            "n_papers": len(papers),
            "papers": [papers[m] for m in sorted(papers)],
        })
    return out


@router.get("/z9")
def get_z9_profiles() -> dict:
    """Tree of the personal Z9 space: serial → paper → refined profiles.

    Pure filesystem read (no Z9 call). This is what the future Profiles
    screen will consume for the per-paper space.
    """
    _cache.ensure_store()
    return {
        "store_root": str(_cache.root_dir()),
        "repo_z9_dir": str(_cache.repo_z9_dir()),
        "serials": _z9_to_grouped(),
    }


class RangeZ9Body(BaseModel):
    source_path: str = Field(
        ..., description="Absolute path of the variant (scratch) to file",
    )
    serial: str = Field(..., description="Z9 serial number")
    media_id: str = Field(..., description="Paper MediaId")
    paper_name: str = Field(..., description="Readable paper name (layer 1)")
    label: str = Field(..., description="Readable profile name/label (layer 1)")
    gloss_slot: Optional[str] = Field(
        None, description="OFF | ON | FULLPAGE (layer 1)",
    )
    color_space: str = Field("PRINTER_RGB")
    method: Optional[str] = Field(None, description="layer 2 (e.g. 'argyll')")
    method_flags: Optional[str] = Field(None, description="layer 2 (colprof flags)")
    n_patches: Optional[int] = Field(None, description="layer 2")
    source_profile: Optional[str] = Field(None, description="layer 2 (base profile)")
    purpose_tags: Optional[list[str]] = Field(None, description="layer 3 (purpose)")
    notes: Optional[str] = Field(None, description="layer 3 (free notes)")
    name: Optional[str] = Field(
        None, max_length=63,
        description="CHOSEN name at refine (optional) — same policy as the "
                    "path-A build: if provided, filename = exact slugify(name) + desc = name, "
                    "ASCII/length validated + collision refusal (409). Absent = auto label.",
    )
    on_conflict: Optional[str] = Field(
        None,
        description="Collision intent (OS): None/'cancel' = SAFE (409); "
                    "'replace' = overwrites + preserves tags/notes; 'keep_both' = -N suffix.",
    )


@router.post("/z9")
def range_z9_profile(body: RangeZ9Body) -> dict:
    """File a variant into repo/z9/<serial>/papers/<media_id>/.

    Reads the scratch variant (produced by POST /refine), validates the ICC
    signature, writes the profile + its 3-layer sidecar (name anti-collision).
    Does not delete the scratch source (the caller cleans up its working folder).
    """
    src = Path(body.source_path)
    if not src.is_absolute():
        raise HTTPException(422, detail="source_path must be absolute")
    if not src.exists() or not src.is_file():
        raise HTTPException(404, detail=f"Variant not found: {body.source_path}")
    _safe_segment(body.serial, "serial")
    _safe_segment(body.media_id, "media_id")
    if not body.label.strip():
        raise HTTPException(422, detail="label cannot be empty")

    # CHOSEN name (optional) — path-A build policy: ASCII/length validation. Then, WITHOUT
    # intent (None/cancel = safe default), collision refusal BEFORE writing for ANY name (custom
    # OR auto label) → 409 + suggestion (OS paradigm Cancel/Replace/Keep-both; the front
    # replays with on_conflict). With intent (replace/keep_both), save_repo applies it.
    from webapp.backend.routes.charts import _norm_on_conflict
    custom_name = (body.name or "").strip() or None
    if custom_name:
        from webapp.backend.routes.papers import _validate_profile_name
        _validate_profile_name(custom_name)           # 400 if non-ASCII / forbidden characters / >63
    oc = _norm_on_conflict(body.on_conflict)
    if oc in (None, "cancel"):
        coll = _cache.repo_z9_name_conflict(
            body.serial, body.media_id, body.gloss_slot,
            label=(custom_name or body.label), basename=(custom_name or None))
        if coll:
            raise HTTPException(409, detail={
                "error": "name_conflict", "name": coll["name"], "suggestion": coll["suggestion"],
                "message": f"A profile \"{coll['name']}\" already exists for this paper.",
            })

    data = src.read_bytes()
    _validate_icc_bytes(data)

    _cache.ensure_store()
    path, meta = _cache.save_repo_z9_profile(
        icc_bytes=data,
        serial=body.serial,
        media_id=body.media_id,
        paper_name=body.paper_name,
        label=(custom_name or body.label),
        gloss_slot=body.gloss_slot,
        color_space=body.color_space,
        method=body.method,
        method_flags=body.method_flags,
        n_patches=body.n_patches,
        source_profile=body.source_profile,
        purpose_tags=body.purpose_tags,
        notes=body.notes,
        basename=(custom_name or None),               # exact (slugified) name if chosen; else auto
        on_conflict=oc,            # None/cancel (no collision here) | replace | keep_both
    )
    return {
        "ok": True,
        "absolute_path": str(path),
        "filename": path.name,
        "serial": body.serial,
        "media_id": body.media_id,
        "meta": meta,
    }


@router.delete("/z9")
def delete_z9_profile(
    serial: str = Query(...),
    media_id: str = Query(...),
    filename: str = Query(...),
) -> dict:
    """Delete a profile from repo/z9 (the .icc + its sidecar)."""
    _safe_segment(serial, "serial")
    _safe_segment(media_id, "media_id")
    _safe_filename(filename, "filename")
    ok = _cache.delete_repo_z9_profile(serial, media_id, filename)
    if not ok:
        raise HTTPException(404, detail=f"Profile not found: {filename}")
    return {
        "ok": True,
        "deleted": filename,
        "serial": serial,
        "media_id": media_id,
    }


@router.get("/z9/export")
def export_z9_profile(
    serial: str = Query(...),
    media_id: str = Query(...),
    filename: str = Query(...),
) -> Response:
    """Download the .icc binary of a repo/z9 profile (direct download).

    Saves the user from digging through the disk after generation. Response
    ``application/vnd.iccprofile`` + Content-Disposition attachment, filename
    derived from the readable sidecar label (fallback: filename).
    """
    _safe_segment(serial, "serial")
    _safe_segment(media_id, "media_id")
    _safe_filename(filename, "filename")
    icc = _cache.repo_z9_paper_dir(serial, media_id) / filename
    resolved = _ensure_under(icc, _cache.repo_z9_dir())
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, detail=f"Profile not found: {filename}")

    meta = _cache.read_repo_z9_profile_meta(resolved)
    label = meta.get("label") or resolved.stem
    dl_name = f"{_cache.safe_fs_name(label)}.icc"
    data = resolved.read_bytes()
    return Response(
        content=data,
        media_type="application/vnd.iccprofile",
        headers={
            "Content-Disposition": f'attachment; filename="{dl_name}"',
            "Content-Length": str(len(data)),
        },
    )


class CheckProfileBody(BaseModel):
    path: str = Field(..., description="Absolute path of a store .icc (mirror / repo z9 / printer repo)")


@router.post("/check")
def check_profile(body: CheckProfileBody) -> dict:
    """SELF-CONSISTENCY check (increment a) of ANY store profile:
    profcheck -k (CIEDE2000) on ITS creation ti3 (auto-sourced from the
    embedded measurements: CIED for HP, targ for Argyll) + white Lab test.
    LOCAL, NO Z9 action.

    Covers, BY PATH (validated under the store root, anti-traversal), all zones:
    - `mirror/` = synchronized copy of the profiles INSTALLED at the slots →
      checks the installed one WITHOUT waking the machine (last-sync state);
    - `repo/z9/` = printer profiles filed in the personal repo;
    - other store .icc files.

    A "dry" manufacturer profile (without embedded measurements, e.g. a matte
    paper) returns `no_measurements` (→ independent validation, increment b).
    Warning: best case / optimistic — mostly detects misreads, not predictive
    quality. Reuses profile_compare.verify_profile_self (profcheck wrapper +
    in-house grid)."""
    from lib.z9_client.profile_compare import verify_profile_self
    resolved = _ensure_under(Path(body.path), _cache.root_dir())
    if resolved.suffix.lower() != ".icc":
        raise HTTPException(422, detail="Path does not point to a .icc")
    if not resolved.exists() or not resolved.is_file():
        raise HTTPException(404, detail=f"Profile not found: {resolved.name}")
    report = verify_profile_self(resolved)
    # Best-effort enrichment: repo/z9 sidecar if present (mirror has none).
    try:
        meta = _cache.read_repo_z9_profile_meta(resolved) or {}
    except Exception:
        meta = {}
    report["label"] = meta.get("label") or resolved.stem
    report["paper_name"] = meta.get("paper_name")
    return report


class RenameZ9Body(BaseModel):
    serial: str = Field(..., description="Z9 serial number")
    media_id: str = Field(..., description="Paper MediaId")
    filename: str = Field(..., description="Profile filename (unchanged)")
    new_label: str = Field(..., description="New displayed label")


@router.post("/z9/rename")
def rename_z9_profile(body: RenameZ9Body) -> dict:
    """Rename a repo/z9 profile: modifies ONLY the sidecar label.

    The .icc file keeps its auto name on disk (no physical rename, no file
    collision to manage). The profile stays functional.

    POST on a sub-path (rather than PATCH /z9): PATCH is sometimes not
    forwarded by some server/proxy layers (405 observed); POST is universally
    routed and consistent with the other z9 routes (GET/POST/DELETE).
    """
    _safe_segment(body.serial, "serial")
    _safe_segment(body.media_id, "media_id")
    _safe_filename(body.filename, "filename")
    if not body.new_label.strip():
        raise HTTPException(422, detail="new_label cannot be empty")
    if not body.new_label.isascii():   # free-text input (case 1) → REFUSE non-ASCII (`ü` mluc bug)
        raise HTTPException(
            422,
            detail="Name must be ASCII characters, without accents "
                   "(e.g. \"Hahnemühle\" → \"Hahnemuhle\").",
        )
    meta = _cache.rename_repo_z9_profile(
        body.serial, body.media_id, body.filename, body.new_label.strip(),
    )
    if meta is None:
        raise HTTPException(404, detail=f"Profile not found: {body.filename}")
    return {
        "ok": True,
        "serial": body.serial,
        "media_id": body.media_id,
        "filename": body.filename,
        "label": meta.get("label"),
        "meta": meta,
    }


class SetTagsZ9Body(BaseModel):
    serial: str = Field(..., description="Z9 serial number")
    media_id: str = Field(..., description="Paper MediaId")
    filename: str = Field(..., description="Profile filename")
    tags: list[str] = Field(default_factory=list, description="Classification tags (purpose_tags)")


@router.post("/z9/tags")
def set_z9_profile_tags(body: SetTagsZ9Body) -> dict:
    """Write the classification tags (``purpose_tags``) of a repo/z9 profile into
    its ``.meta`` sidecar — NEVER into the ``.icc`` (byte fidelity). Normalizes:
    trim + drop of empties + dedup (order preserved) + **strict ASCII** (non-ASCII
    refused, 422).
    """
    _safe_segment(body.serial, "serial")
    _safe_segment(body.media_id, "media_id")
    _safe_filename(body.filename, "filename")
    clean: list[str] = []
    seen: set[str] = set()
    for raw in body.tags:
        tag = (raw or "").strip()
        if not tag or tag in seen:
            continue
        if not tag.isascii():
            raise HTTPException(
                422,
                detail=f"Tag \"{tag}\": ASCII characters only, without accents.",
            )
        seen.add(tag)
        clean.append(tag)
    meta = _cache.set_repo_z9_profile_tags(body.serial, body.media_id, body.filename, clean)
    if meta is None:
        raise HTTPException(404, detail=f"Profile not found: {body.filename}")
    return {"ok": True, "filename": body.filename, "purpose_tags": meta.get("purpose_tags", [])}
