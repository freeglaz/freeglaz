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
Local freeglaz store — foundation.

Structure:

    <root>/                       Default ~/Documents/freeglaz/
                                  Configurable via paths.store_root (config.toml).
    ├── store.json                Root manifest (store_version, date, effective root).
    │
    ├── mirror/                   READ-ONLY — projected from the Z9 by the sync.
    │   └── <serial>/             e.g. CNXXXXXXXX — one folder per Z9.
    │       ├── _mirror.json      stored medium_list_version, last sync date,
    │       │                     paper index.
    │       └── papers/
    │           └── <paper-slug>/
    │               ├── _paper.json    MediaId, name, type, GE/quality, z9_uuid per
    │               │                  profile, custom flag, md5.
    │               └── *.icc         factory + custom profiles (donor = factory
    │                                 marked `freeglaz-factory`).
    │
    ├── repo/                     READ/WRITE — user's own profiles.
    │   ├── printers/             Organized by type/device.
    │   │   └── <device>/         e.g. Epson-XXX/  (+ _meta.json)
    │   │       └── *.icc
    │   ├── displays/             Display profiles (+ _meta.json)
    │   │   └── *.icc
    │   └── workingspaces/        Rec2020, sRGB, AdobeRGB… (+ _meta.json)
    │       └── *.icc
    │
    ├── backups/                  Snapshots before a destructive operation (unchanged).
    │   └── _metadata.json
    │
    └── sessions/                 Workspace per profiling session (unchanged).

Architectural guardrails:
    - `mirror/` never written except by the sync (cf. `store.sync_mirror`).
    - No mirror → repo writes and vice versa.
    - Schema indexed by Z9 serial number (stable, prepares for multi-Z9).

Cross-platform: macOS / Linux / Windows via Path.home() / "Documents".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# ─── Store versioning ─────────────────────────────────────────────────


STORE_VERSION = 1
STORE_MANIFEST_FILENAME = "store.json"
MIRROR_MANIFEST_FILENAME = "_mirror.json"
PAPER_MANIFEST_FILENAME = "_paper.json"

# Marker to distinguish a factory donor ICC file extracted by freeglaz
# from an arbitrary user profile. Included in the donor filename so that
# `paper import-icc` can refuse to re-inject them into a custom slot
# (which would create a fake custom indistinguishable from a real one).
FACTORY_MARKER = "freeglaz-factory"


# ─── Store root (configurable) ────────────────────────────────────────


def _default_root() -> Path:
    """Default root: ~/Documents/freeglaz/ (visible in Finder/Explorer/Files).
    Choosing ~/Documents (vs ~/.freeglaz) keeps Windows in play and preserves
    mainstream visibility."""
    return Path.home() / "Documents" / "freeglaz"


def root_dir() -> Path:
    """Store root. Reads ``paths.store_root`` from the config, default
    ``~/Documents/freeglaz/``. Tilde and environment variables expanded.

    The resolver is central: all other helpers (mirror_dir, repo_dir,
    backups_dir, sessions_dir) derive from it. Override via config OR via the
    ``FREEGLAZ_STORE_ROOT`` environment variable (useful for tests).
    """
    # Env-var override (absolute priority, handy in tests)
    env_override = os.environ.get("FREEGLAZ_STORE_ROOT")
    if env_override:
        return Path(os.path.expandvars(env_override)).expanduser()

    # Config read (lazy import to avoid the config → cache → config cycle)
    try:
        from . import config as _config
        cfg = _config.get_config()
        store_root = cfg.get("paths", {}).get("store_root")
        if store_root:
            return Path(os.path.expandvars(str(store_root))).expanduser()
    except Exception:  # noqa: BLE001 — silent fallback to the default
        pass

    return _default_root()


# ─── Store zones ──────────────────────────────────────────────────────


def mirror_dir() -> Path:
    """``mirror/`` folder (read-only — populated by the sync only)."""
    return root_dir() / "mirror"


def mirror_serial_dir(serial: str) -> Path:
    """Mirror folder of a specific Z9 (indexed by serial number)."""
    if not serial:
        raise ValueError("mirror_serial_dir: serial required (e.g. 'CNXXXXXXXX')")
    return mirror_dir() / serial


def mirror_papers_dir(serial: str) -> Path:
    """``mirror/<serial>/papers/`` folder holding one subfolder per paper."""
    return mirror_serial_dir(serial) / "papers"


def mirror_paper_dir(serial: str, paper_name: str) -> Path:
    """Folder of a paper in the mirror: ``mirror/<serial>/papers/<slug>/``."""
    return mirror_papers_dir(serial) / slugify(paper_name)


def repo_dir() -> Path:
    """``repo/`` folder (read/write — user's own profiles)."""
    return root_dir() / "repo"


def repo_printers_dir() -> Path:
    """``repo/printers/`` folder — printer profiles (Z9, Epson, etc.)."""
    return repo_dir() / "printers"


def repo_displays_dir() -> Path:
    """``repo/displays/`` folder — display profiles."""
    return repo_dir() / "displays"


def repo_workingspaces_dir() -> Path:
    """``repo/workingspaces/`` folder — working spaces (Rec2020, sRGB, …)."""
    return repo_dir() / "workingspaces"


def repo_z9_dir() -> Path:
    """``repo/z9/`` folder — personal space for refined profiles, organized PER
    PAPER (refinement "redo with Argyll" + enrich-per-pass).

    Distinct from ``repo/printers/`` (untouched, flat per device). Hierarchy
    modeled on the mirror: ``repo/z9/<serial>/papers/<media_id>/``. The paper
    subfolder is named by the **MediaId** (stable technical key); the
    human-readable paper name lives in each profile's sidecar, so the space
    stays self-contained even if the paper disappears from the Z9 / mirror.
    """
    return repo_dir() / "z9"


def repo_z9_serial_dir(serial: str) -> Path:
    """``repo/z9/<serial>/`` folder (one per Z9 printer)."""
    if not serial:
        raise ValueError("repo_z9_serial_dir: serial required (e.g. 'CNXXXXXXXX')")
    return repo_z9_dir() / safe_fs_name(serial)


def repo_z9_papers_dir(serial: str) -> Path:
    """``repo/z9/<serial>/papers/`` folder."""
    return repo_z9_serial_dir(serial) / "papers"


def repo_z9_paper_dir(serial: str, media_id: str) -> Path:
    """Folder of a paper in the personal Z9 space: indexed by MediaId.

    The MediaId is the technical key (hex 32 for custom, numeric for
    factory). Sanitized for the FS for robustness, but never slugified — it's
    an identifier, not a displayable name.
    """
    if not media_id:
        raise ValueError("repo_z9_paper_dir: media_id required")
    return repo_z9_papers_dir(serial) / safe_fs_name(media_id)


def backups_dir() -> Path:
    """``backups/`` folder (unchanged — snapshots before a destructive operation)."""
    return root_dir() / "backups"


def sessions_dir() -> Path:
    """``sessions/`` folder (unchanged — workspace per profiling session)."""
    return root_dir() / "sessions"


def charts_root() -> Path:
    """``charts/`` folder (parent — read-only in multi-serial enumeration).

    Contains ONLY ``<serial>/`` subfolders. Used for reads by FS enumeration
    (``charts_root().glob("*/<chart_id>")``), like ``mirror_dir()`` for the
    mirror — since ``chart_id`` are globally unique, a chart can be located
    without knowing its serial."""
    return root_dir() / "charts"


def charts_dir(serial: str) -> Path:
    """DURABLE library of a Z9's free charts (indexed by serial):
    ``charts/<serial>/<chart_id>/`` (chart.json + TIFF + measurements/).

    Same model as ``mirror_serial_dir``: requires the serial, raises if absent (the
    serial comes from the ``store.get_serial(client)`` bridge on the write side)."""
    if not serial:
        raise ValueError("charts_dir: serial required (e.g. 'CNXXXXXXXX')")
    return charts_root() / serial


def locate_chart_dir(chart_id: str) -> Optional[Path]:
    """Locate ``charts/<serial>/<chart_id>/`` by its ``chart_id`` WITHOUT knowing
    the serial nor querying the Z9 — chart_id are globally unique.

    FS enumeration over the ``<serial>/`` folders (same spirit as ``store_status``
    for the mirror). Single source of the "existing chart → folder" resolution
    for read routes/CLI. Returns ``None`` if not found."""
    base = charts_root()
    if not base.is_dir():
        return None
    for serial_dir in base.iterdir():
        if not serial_dir.is_dir():
            continue
        cand = serial_dir / chart_id
        if (cand / "chart.json").is_file():
            return cand
    return None


# ─── Store initialization ─────────────────────────────────────────────


def ensure_store() -> Path:
    """Create the GLOBAL store SKELETON if absent + write ``store.json`` v1.

    Idempotent. Does not touch an old ``donors/`` folder that may linger from
    an earlier phase: it is simply ignored (no migration — disposable data).
    No longer creates a ``profiles/`` folder (removed vestige).

    :return: effective store root.
    """
    root = root_dir()
    root.mkdir(parents=True, exist_ok=True)
    for d in (mirror_dir(), repo_dir(),
              repo_printers_dir(), repo_displays_dir(), repo_workingspaces_dir()):
        d.mkdir(parents=True, exist_ok=True)
    # charts/, sessions/ AND backups/ are NO LONGER created here: they are born
    # on-demand at first use — charts/<serial>/ via charts_dir(serial),
    # sessions/<name>/ via session_dir(), backups/<serial>/<mediaid>/<ge_state>/
    # via icc_backups.slot_dir() (mkdir-on-demand). All per-serial except sessions.

    manifest_path = root / STORE_MANIFEST_FILENAME
    if not manifest_path.exists():
        _write_json(manifest_path, {
            "store_version": STORE_VERSION,
            "created_at": _now_iso(),
            "root": str(root),
        })
    return root


def purge_mirror_paper(serial: str, paper_name: str) -> bool:
    """Purge a paper's folder in the mirror.

    Removes ALL ICC files + ``_paper.json`` from that paper's folder.
    Eliminates orphans when the sync brings down the current slots after
    a UUID change. Idempotent.

    INVARIANT: the mirror is a disposable projection of the current Z9
    state. No rollback point depends on the mirror — they live in
    ``backups/``. The purge is therefore always safe. Touches ONLY
    ``mirror/``.

    :return: True if the folder existed and was purged, False otherwise.
    """
    paper_dir = mirror_paper_dir(serial, paper_name)
    if not paper_dir.exists():
        return False
    import shutil
    shutil.rmtree(paper_dir, ignore_errors=False)
    return True


def purge_mirror_serial_papers(serial: str) -> int:
    """Purge ALL paper folders of a serial in the mirror (on --force or on a
    bump of SYNC_SCHEMA_VERSION).

    Keeps ``_mirror.json`` (will be rewritten by the sync). Does not touch
    ``backups/``, ``repo/``, ``sessions/``.

    :return: number of paper folders removed.
    """
    papers_dir = mirror_papers_dir(serial)
    if not papers_dir.exists():
        return 0
    import shutil
    n = 0
    for d in papers_dir.iterdir():
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=False)
            n += 1
    return n


def ensure_mirror(serial: str) -> Path:
    """Create the mirror structure for a Z9 (idempotent). Write a minimal
    ``_mirror.json`` if absent. Returns the serial folder."""
    ensure_store()
    sdir = mirror_serial_dir(serial)
    sdir.mkdir(parents=True, exist_ok=True)
    mirror_papers_dir(serial).mkdir(parents=True, exist_ok=True)

    manifest = sdir / MIRROR_MANIFEST_FILENAME
    if not manifest.exists():
        _write_json(manifest, {
            "serial": serial,
            "medium_list_version": None,
            "last_sync_at": None,
            "papers": {},   # slug → {paper_id, paper_name, profile_uuids: {ge: uuid}}
        })
    return sdir


# ─── Slugify (cross-platform filenames) ────────────────────────────────


_FS_BAD_CHARS_RE = re.compile(r'[/\\\x00-\x1f\x7f:*?"<>|\n\r]')


def safe_fs_name(name: str, fallback: str = "unnamed") -> str:
    """Sanitize a filename for the cross-platform filesystem without
    distorting it.

    Preserves: spaces, accents, parentheses, commas, dashes, dots
               (except leading), case. Use case: names reflecting user
               data (`z9_icc_name`, import filename).

    Replaces with ``_``: FS-forbidden characters or control chars
    (``/ \\ \\0 : * ? " < > | \\n \\r`` + ctrl < 32, 127).

    Different from ``slugify()`` which is destructive (loss of info used
    for internal key consistency, never for displayed names).

    :param fallback: name to return if the result is empty after
                     sanitization (default ``unnamed``).
    """
    if not name:
        return fallback
    out = _FS_BAD_CHARS_RE.sub("_", str(name))
    # Collapse consecutive underscores introduced by the substitution
    out = re.sub(r"_+", "_", out).strip("_ ")
    # Strip leading dots (cache file / hidden file)
    out = out.lstrip(".")
    # Reasonable cap (most FS support 255 bytes)
    if len(out.encode("utf-8")) > 200:
        # Truncate cleanly to 200 bytes (without breaking a multibyte character)
        b = out.encode("utf-8")[:200]
        out = b.decode("utf-8", errors="ignore")
    return out or fallback


def slugify(name: str) -> str:
    """Convert a paper name into a slug usable as a cross-platform
    filename (macOS / Linux / Windows).

    Rules:
      - Accents removed (NFKD decomposition)
      - Spaces → dashes
      - Anything but [a-z0-9_-] → dash
      - Multiple dashes collapsed
      - No leading/trailing dash
      - All lowercase

    Examples:
        "Canson Baryta Photographique"  → "canson-baryta-photographique"
        "Hahnemühle Fine Art Baryta"    → "hahnemuhle-fine-art-baryta"
        "HP Photo Gloss / Semi-Gloss"   → "hp-photo-gloss-semi-gloss"
    """
    import unicodedata
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "unnamed"


# ─── MD5 ──────────────────────────────────────────────────────────────


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        while chunk := f.read(64 * 1024):
            h.update(chunk)
    return h.hexdigest()


def lut_md5(icc_bytes: bytes) -> str:
    """MD5 of the content of the main colorimetric LUTs of an ICC profile.

    Includes only the 6 essential LUT tags (A2B0/1/2, B2A0/1/2). Lets two
    profiles be compared colorimetrically, ignoring cosmetic differences
    (desc tag with "GE ON"/"GE OFF", copyright, metadata, etc.).

    Typical use: prevent re-injecting an identical factory donor into a
    protected factory slot (an operation that would degrade traceability
    with no colorimetric change).
    """
    import struct
    n_tags = struct.unpack(">I", icc_bytes[128:132])[0]
    h = hashlib.md5()
    for i in range(n_tags):
        off = 132 + i * 12
        sig = icc_bytes[off:off+4]
        if sig in (b"A2B0", b"A2B1", b"A2B2", b"B2A0", b"B2A1", b"B2A2"):
            tag_off = struct.unpack(">I", icc_bytes[off+4:off+8])[0]
            tag_sz = struct.unpack(">I", icc_bytes[off+8:off+12])[0]
            h.update(icc_bytes[tag_off:tag_off+tag_sz])
    return h.hexdigest()


# ─── JSON helpers ─────────────────────────────────────────────────────


def _read_json(path: Path, default: Optional[dict] = None) -> dict:
    if not path.exists():
        return dict(default) if default else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(default) if default else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
    tmp.replace(path)


def _now_iso() -> str:
    """ISO 8601 with local timezone (without microseconds)."""
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _now_filename() -> str:
    """Timestamp for filenames: 2026-05-29_14h27m05."""
    return datetime.now().strftime("%Y-%m-%d_%Hh%Mm%S")


# ─── Profiles resident in the mirror ───────────────────────────────────
#
# The mirror contains ALL of the Z9's resident profiles (factory + custom),
# one file per combination (paper × GE × ColorSpace). The `custom` flag is a
# METADATA in `_paper.json`, never an inclusion criterion.
#
# File naming convention:
#   <slug>__GE-{OFF|FULLPAGE}__{PRINTER_RGB|PRINTER_GRAYSCALE}{.freeglaz-factory}?.icc
#
# The `.freeglaz-factory.` suffix appears ONLY on factory profiles.
# It acts as a safety net (name read-only) to prevent
# `paper import-icc` from re-injecting a factory profile as a fake custom.
# For custom profiles, no suffix — it's a user profile that has every
# right to be imported/exported.


def _norm_ge(ge: Optional[str]) -> str:
    """Normalize the GlossEnhancer value into a filename token."""
    if ge is None:
        return "UNKNOWN"
    s = str(ge).upper().strip()
    return s or "UNKNOWN"


def _norm_cs(cs: Optional[str]) -> str:
    """Normalize the ColorSpace value into a filename token."""
    if cs is None:
        return "UNKNOWN"
    s = str(cs).upper().strip()
    return s or "UNKNOWN"


def mirror_profile_path(
    serial: str,
    paper_name: str,
    gloss_enhancer: Optional[str],
    color_space: Optional[str],
    custom: bool,
    icc_name: Optional[str] = None,
) -> Path:
    """Path of a resident profile in the mirror.

    If ``icc_name`` is provided, the filename is derived from it (FS-sanitized
    only via :func:`safe_fs_name`), so the mirror faithfully reflects the
    ``z9_icc_name`` exposed by the Z9. If absent, historical slot-keyed
    fallback.

    Factory profiles carry the ``freeglaz-factory`` marker in their name
    (safety net for ``paper import-icc``). Custom profiles do not have this
    marker.

    NB: collision detection (two slots with the same ``z9_icc_name``) is done
    on the caller side (sync_mirror), not here — this allows adding a slot
    suffix only on a real collision, without systematically distorting.

    :return: e.g. mirror/<serial>/papers/<slug>/<icc_name>.freeglaz-factory.icc
    """
    if icc_name:
        base = safe_fs_name(icc_name)
        # The z9_icc_name exposed by the Z9 may ALREADY end with .icc
        # (e.g. "freeglaz roundtrip test 2026-05-13.icc"). Without this strip,
        # we produce a double extension "….icc.icc". (sync bug #6)
        if base.lower().endswith(".icc"):
            base = base[:-4]
    else:
        slug = slugify(paper_name)
        base = f"{slug}__GE-{_norm_ge(gloss_enhancer)}__{_norm_cs(color_space)}"
    marker = f".{FACTORY_MARKER}" if not custom else ""
    filename = f"{base}{marker}.icc"
    return mirror_paper_dir(serial, paper_name) / filename


def is_freeglaz_factory_filename(filename: str) -> bool:
    """Report whether a filename carries the freeglaz factory marker.

    Recognizes factory ICC files written by the mirror (sync or
    `donor-export`) that must not be re-injected as-is into a custom slot
    via `paper import-icc`.
    """
    basename = os.path.basename(filename)
    return f".{FACTORY_MARKER}." in basename


def _paper_manifest_path(serial: str, paper_name: str) -> Path:
    return mirror_paper_dir(serial, paper_name) / PAPER_MANIFEST_FILENAME


def read_paper_manifest(serial: str, paper_name: str) -> dict:
    """Read a mirror paper's ``_paper.json`` (or minimal dict if absent)."""
    return _read_json(
        _paper_manifest_path(serial, paper_name),
        default={"paper_name": paper_name, "profiles": []},
    )


def save_mirror_profile(
    *,
    icc_bytes: bytes,
    serial: str,
    paper: dict,
    profile: dict,
) -> tuple[Path, dict]:
    """Write ONE resident profile into the mirror + update ``_paper.json``.

    Sync-friendly: used by ``store.sync_mirror`` to materialize each
    resident profile of a paper (factory AND custom), as-is.

    :param paper: paper dict (from paper.list_full()). Expected fields:
        id, name, is_factory, is_user_custom, donor_id,
        category_id, category_name.
    :param profile: resident profile dict (from
        ``paper["profiles"][i]``). Expected fields: gloss_enhancer,
        color_space, custom (bool), uuid, icc_name, date.
    :return: (path of the written file, dict of the profile entry in the manifest)
    """
    ensure_mirror(serial)
    paper_dir = mirror_paper_dir(serial, paper["name"])
    paper_dir.mkdir(parents=True, exist_ok=True)

    path = mirror_profile_path(
        serial, paper["name"],
        profile.get("gloss_enhancer"),
        profile.get("color_space"),
        bool(profile.get("custom")),
    )
    path.write_bytes(icc_bytes)

    profile_entry = {
        "filename": path.name,
        "gloss_enhancer": profile.get("gloss_enhancer"),
        "color_space": profile.get("color_space"),
        "custom": bool(profile.get("custom")),
        "z9_uuid": profile.get("uuid"),
        "z9_icc_name": profile.get("icc_name"),
        "z9_date": profile.get("date"),
        "size_bytes": len(icc_bytes),
        "md5": md5_bytes(icc_bytes),
        "fetched_at": _now_iso(),
    }

    manifest = read_paper_manifest(serial, paper["name"])
    # Purge the legacy `donor` block if present (leftover from the 17.1
    # format, redundant with profiles[]).
    manifest.pop("donor", None)
    manifest.update({
        "paper_id": paper.get("id"),
        "paper_name": paper.get("name"),
        # "donor paper" = the HP factory type this custom paper inherits from
        # (permanent firmware reference; nothing to do with an ICC file).
        "donor_paper_id": paper.get("donor_id"),
        "category_id": paper.get("category_id"),
        "category_name": paper.get("category_name"),
        "is_factory": bool(paper.get("is_factory")),
        "is_user_custom": bool(paper.get("is_user_custom")),
        "updated_at": _now_iso(),
    })

    # Profiles: indexed by (gloss_enhancer, color_space) for idempotence
    profiles = manifest.get("profiles") or []
    slot_key = (profile.get("gloss_enhancer"), profile.get("color_space"))
    replaced = False
    for i, existing in enumerate(profiles):
        if (existing.get("gloss_enhancer"), existing.get("color_space")) == slot_key:
            profiles[i] = profile_entry
            replaced = True
            break
    if not replaced:
        profiles.append(profile_entry)
    manifest["profiles"] = profiles
    _write_json(_paper_manifest_path(serial, paper["name"]), manifest)

    return path, profile_entry


def update_paper_manifest_for_profile(
    *,
    serial: str,
    paper: dict,
    profile: dict,
    filename: str,
    md5: str,
    size_bytes: int,
) -> dict:
    """Update ``_paper.json`` for a profile whose ICC file is ALREADY written
    to disk (via ``client.paper.export_icc``, for example).

    The mirror sync uses the ``export_icc`` primitive (from the client
    module, shared with /api/papers) which writes the file itself. This
    function just updates the metadata without rewriting the file — avoids
    the double profile-extraction path.

    :return: dict of the profile_entry recorded in the manifest.
    """
    profile_entry = {
        "filename": filename,
        "gloss_enhancer": profile.get("gloss_enhancer"),
        "color_space": profile.get("color_space"),
        "custom": bool(profile.get("custom")),
        "z9_uuid": profile.get("uuid"),
        "z9_icc_name": profile.get("icc_name"),
        "z9_date": profile.get("date"),
        "size_bytes": int(size_bytes),
        "md5": md5,
        "fetched_at": _now_iso(),
    }
    manifest = read_paper_manifest(serial, paper["name"])
    # Do NOT carry over the legacy `donor` block (leftover from the 17.1
    # format, redundant with the factory entry in profiles[]). If present in
    # the manifest read (sync on a pre-17.1.2 mirror), purge it here.
    # donor_paper_id stays, it's the legitimate firmware reference.
    manifest.pop("donor", None)
    manifest.update({
        "paper_id": paper.get("id"),
        "paper_name": paper.get("name"),
        "donor_paper_id": paper.get("donor_id"),
        "category_id": paper.get("category_id"),
        "category_name": paper.get("category_name"),
        "is_factory": bool(paper.get("is_factory")),
        "is_user_custom": bool(paper.get("is_user_custom")),
        "updated_at": _now_iso(),
    })
    profiles = manifest.get("profiles") or []
    slot_key = (profile.get("gloss_enhancer"), profile.get("color_space"))
    replaced = False
    for i, existing in enumerate(profiles):
        if (existing.get("gloss_enhancer"), existing.get("color_space")) == slot_key:
            profiles[i] = profile_entry
            replaced = True
            break
    if not replaced:
        profiles.append(profile_entry)
    manifest["profiles"] = profiles
    _write_json(_paper_manifest_path(serial, paper["name"]), manifest)
    return profile_entry


def find_resident_profile(
    serial: str, paper_name: str,
    gloss_enhancer: str,
    color_space: str = "PRINTER_RGB",
) -> Optional[tuple]:
    """Look up the exact RESIDENT profile of a slot.

    Used by ``chart generate --tag-mode resident`` (default) to tag the
    chart with the **very profile that will decode it at print time**,
    guaranteeing source=resident content identity (raw RGB at the RIP,
    regardless of the slot's factory/custom flag).

    Differs from :func:`find_factory_profile` (which filters ``custom=False``
    and allows GE preferences) — here we look for THE exact slot, whatever its
    custom flag.

    :return: tuple ``(absolute_path, profile_entry_dict)`` or ``None`` if no
        profile is mirrored for this slot.
    """
    manifest = read_paper_manifest(serial, paper_name)
    profiles = manifest.get("profiles") or []
    ge_norm = (gloss_enhancer or "").upper()
    for p in profiles:
        if ((p.get("gloss_enhancer") or "").upper() == ge_norm
                and (p.get("color_space") or "") == color_space):
            filename = p.get("filename")
            if not filename:
                return None
            abs_path = mirror_paper_dir(serial, paper_name) / filename
            return (abs_path, p)
    return None


def find_factory_profile(
    serial: str, paper_name: str,
    prefer_ge: tuple = ("OFF", "FULLPAGE"),
    color_space: str = "PRINTER_RGB",
) -> Optional[dict]:
    """Look up a paper's factory profile in the mirror.

    Used by ``chart generate`` (chart tag logic = factory donor) and by
    ``load_donor`` (backward compat of the R&D donor API).

    Prefers GE=OFF by default, falls back to FULLPAGE. Default ColorSpace
    PRINTER_RGB.

    :return: profile dict (filename, ge, cs, uuid, ...) or None if no factory
        profile is found for the paper.
    """
    manifest = read_paper_manifest(serial, paper_name)
    profiles = manifest.get("profiles") or []
    # Sort: factory first, then GE preference order
    for ge_pref in prefer_ge:
        for p in profiles:
            if (not p.get("custom")
                    and (p.get("gloss_enhancer") or "").upper() == ge_pref
                    and (p.get("color_space") or "") == color_space):
                return p
    # Fallback: any factory profile in any slot
    for p in profiles:
        if not p.get("custom"):
            return p
    return None


# ─── "Personal Z9" space: refined profiles per paper (repo/z9/) ────────
#
# Read/write space distinct from repo/printers/ (untouched). Organized by
# printer (serial) → paper (MediaId) → profiles. Each profile = one .icc
# + a hidden sidecar `.<filename>.meta.json` (same convention as the rest
# of the repo), carrying the 3 metadata layers:
#   - Layer 1 (required): paper (readable name + MediaId), GE slot, label.
#   - Layer 2 (auto at generation): method, flags, n_patches, source, date.
#   - Layer 3 (optional): purpose labels, free-form notes.
#
# The filename carries the GE slot for Finder spotting: GE-<slot>__<label>.icc
# The meaning lives in the sidecar; the name is only a unique readable identifier.

REPO_Z9_SCHEMA_VERSION = 1
REPO_Z9_META_SUFFIX = ".meta.json"


def _repo_z9_meta_path(icc_path: Path) -> Path:
    """Path of the hidden sidecar of a personal Z9 profile: ``.<filename>.meta.json``."""
    return icc_path.parent / f".{icc_path.name}{REPO_Z9_META_SUFFIX}"


# Printer name at the HEAD of the profile name. Module constant for now —
# intended to migrate to Settings (the "universal scanner" vision: editable
# per printer). PRESENTATION only (filename), no firmware value.
PROFILE_PRINTER_NAME = "HPZ9"

# Max length of the profile name/desc = limit of the ICC V2 desc tag
# (textDescriptionTag, ~67 bytes; Argyll ONLY produces ICC V2). 63 = conservative
# margin, SINGLE limit (filename == desc). Referenced by validation (route) and
# the truncation of the auto name.
REPO_Z9_NAME_MAXLEN = 63


def repo_z9_profile_basename(
    gloss_slot: Optional[str], label: str, *, date_str: Optional[str] = None,
) -> str:
    """Base of the profile name (WITHOUT extension nor collision suffix):
    ``<HPZ9>_<slugified-label>_GE-<ON|OFF>[_<date>]``. SINGLE source of the name
    convention — reused by ``repo_z9_profile_path`` (filename) AND by the ICC
    ``desc`` tag at build (desc = base, so = filename without `.icc` nor suffix).
    ASCII guaranteed (``slugify`` strips accents). PRESENTATION: ``FULLPAGE`` →
    ``GE-ON``; the internal ``gloss_slot`` value stays ``'FULLPAGE'`` everywhere
    else."""
    slot = _norm_ge(gloss_slot)                          # OFF / FULLPAGE (internal, unchanged)
    slot_label = "ON" if slot == "FULLPAGE" else slot    # presentation: FULLPAGE → ON
    # Cap at 63 (= ICC V2 desc textDescriptionTag limit, the only format produced by Argyll).
    # SMART truncation: keep printer + GE + date (short/fixed) and trim ONLY the
    # variable part (paper slug) so the TOTAL fits in 63 — never a blind cut of the
    # end (which would lose GE/date).
    head = f"{PROFILE_PRINTER_NAME}_"
    tail = f"_GE-{slot_label}" + (f"_{date_str}" if date_str else "")
    paper = slugify(label)
    budget = REPO_Z9_NAME_MAXLEN - len(head) - len(tail)
    if budget > 0 and len(paper) > budget:
        paper = paper[:budget].rstrip("-") or paper[:budget]
    base = f"{head}{paper}{tail}"
    return base


def repo_z9_profile_path(
    serial: str, media_id: str,
    gloss_slot: Optional[str], label: str,
    *, taken: Optional[set] = None, date_str: Optional[str] = None,
    basename: Optional[str] = None,
) -> Path:
    """Path of a personal Z9 profile: ``repo/z9/<serial>/papers/<media_id>/<HPZ9>_<label>_GE-<ON|OFF>[_<date>].icc``.

    Printer name (``PROFILE_PRINTER_NAME``) at the head, then the paper identity,
    then the GE (a PROPERTY of the paper), then the date as a suffix. ``date_str``
    (optional, e.g. '2026-06-11'). The label is slugified. If ``taken`` (names
    already present) is provided and a collision occurs, a suffix ``-2``, ``-3``…
    is appended (uniqueness logic UNCHANGED).

    PRESENTATION: the internal slot ``FULLPAGE`` is written ``GE-ON`` in the NAME
    (the GE hack is not "full page" → clear label). The ``gloss_slot`` value stays
    ``'FULLPAGE'`` EVERYWHERE ELSE (manifest, slot, guard) — only the composed
    string for the filename is translated.
    """
    # ``basename`` = name CHOSEN by the user (at build): used as-is (slugified),
    # WITHOUT the auto composition HPZ9_<paper>_GE_<date>. Otherwise, auto naming.
    base = slugify(basename) if basename else repo_z9_profile_basename(gloss_slot, label, date_str=date_str)
    paper_dir = repo_z9_paper_dir(serial, media_id)
    if taken is None:
        candidate = f"{base}.icc"
    else:
        candidate = f"{base}.icc"
        n = 2
        while candidate in taken:
            candidate = f"{base}-{n}.icc"
            n += 1
    return paper_dir / candidate


def read_repo_z9_profile_meta(icc_path: Path) -> dict:
    """Read the sidecar of a personal Z9 profile (empty dict if absent)."""
    return _read_json(_repo_z9_meta_path(icc_path), default={})


class ProfileNameConflictError(Exception):
    """The target filename already exists AND no intent (``on_conflict``) was given →
    the caller (route) must let the user choose: Cancel / Replace / Keep both. Carries the
    conflicting name + a free ``-N`` suggestion (to pre-fill "Keep both")."""

    def __init__(self, name: str, suggestion: str):
        self.name = name
        self.suggestion = suggestion
        super().__init__(f"profile '{name}' already present (suggestion: {suggestion})")


def repo_z9_name_conflict(
    serial: str, media_id: str, gloss_slot: Optional[str], label: str,
    *, date_str: Optional[str] = None, basename: Optional[str] = None,
) -> Optional[dict]:
    """Detect (WITHOUT writing) whether the EXACT target filename already exists in the
    repo/z9 paper folder. Returns ``{name, suggestion}`` (first free ``-N`` variant) on
    collision, otherwise ``None``. SINGLE source of detection — called by routes to return a
    409 when no ``on_conflict`` intent is given (OS Cancel/Replace/Keep paradigm)."""
    if not (serial and media_id):
        return None
    exact = repo_z9_profile_path(serial, media_id, gloss_slot, label,
                                 taken=None, date_str=date_str, basename=basename)
    if not exact.exists():
        return None
    paper_dir = exact.parent
    base = exact.stem
    n = 2
    while (paper_dir / f"{base}-{n}.icc").exists():
        n += 1
    return {"name": base, "suggestion": f"{base}-{n}"}


def save_repo_z9_profile(
    *,
    icc_bytes: bytes,
    serial: str,
    media_id: str,
    paper_name: str,
    label: str,
    gloss_slot: Optional[str] = None,
    color_space: str = "PRINTER_RGB",
    method: Optional[str] = None,
    method_flags: Optional[str] = None,
    n_patches: Optional[int] = None,
    source_profile: Optional[str] = None,
    purpose_tags: Optional[list] = None,
    notes: Optional[str] = None,
    origin: str = "refine_argyll",
    date_str: Optional[str] = None,
    basename: Optional[str] = None,
    on_conflict: Optional[str] = None,
) -> tuple[Path, dict]:
    """Write a refined profile into the personal Z9 space + its 3-layer sidecar.

    The ``.icc`` is the carrier of the measurements (self-sufficient); the
    sidecar does not duplicate the measurements. ``paper_name`` is stored for
    the space's autonomy (readable even if the paper disappears from the Z9 /
    mirror).

    ``on_conflict`` (OS Cancel/Replace/Keep-both paradigm) — what to do if the
    EXACT target filename already exists:
      * ``None`` / ``"cancel"`` → **SAFE**: raises :class:`ProfileNameConflictError` (no
        write) → the route returns 409 and the user chooses;
      * ``"keep_both"`` → ``-N`` suffix (historical uniqueness behavior);
      * ``"replace"`` → writes at the EXACT path, FULLY overwriting the old one (icc + fresh
        sidecar: the new profile carries ONLY its own metadata). NO preservation of
        tags/notes: same name ≠ same profile → grafting the old curation onto the new one
        would be a lie. To KEEP tags/notes, the user picks "Keep both"
        (old intact + new ``-N``). Overwrites ONLY in repo/z9 (never mirror/firmware/backups).

    :param label: readable name given by the user (layer 1).
    :param gloss_slot: OFF / ON / FULLPAGE (layer 1).
    :param method: 'argyll' / 'hp_native' / 'multipass' (layer 2).
    :param method_flags: exact colprof flags, e.g. '-qm -r1.0' (layer 2).
    :param source_profile: reference of the refined base profile (layer 2).
    :param purpose_tags: free-form purpose labels (layer 3).
    :param notes: free-form notes (layer 3).
    :return: (path of the written .icc, sidecar dict).
    :raises ProfileNameConflictError: collision + on_conflict cancel/None.
    """
    paper_dir = repo_z9_paper_dir(serial, media_id)
    paper_dir.mkdir(parents=True, exist_ok=True)

    # EXACT path (desired name, no suffix) — basis of collision detection + replace.
    exact = repo_z9_profile_path(serial, media_id, gloss_slot, label,
                                 taken=None, date_str=date_str, basename=basename)
    if on_conflict == "keep_both":
        taken = {p.name for p in paper_dir.glob("*.icc")}
        path = repo_z9_profile_path(serial, media_id, gloss_slot, label,
                                    taken=taken, date_str=date_str, basename=basename)
    elif on_conflict == "replace":
        path = exact                                   # FULLY overwrites (icc + fresh meta) — no
        # preservation: same name ≠ same profile → keeping = "Keep both", not "Replace".
    else:                                              # None / "cancel" = SAFE: never overwrites
        if exact.exists():
            conflict = repo_z9_name_conflict(serial, media_id, gloss_slot, label,
                                             date_str=date_str, basename=basename)
            raise ProfileNameConflictError(name=conflict["name"], suggestion=conflict["suggestion"])
        path = exact

    path.write_bytes(icc_bytes)

    meta = {
        "schema_version": REPO_Z9_SCHEMA_VERSION,
        "label": label,
        "paper_name": paper_name,
        "media_id": media_id,
        "serial": serial,
        "gloss_slot": _norm_ge(gloss_slot),
        "color_space": color_space,
        "method": method,
        "method_flags": method_flags,
        "n_patches": n_patches,
        "source_profile": source_profile,
        "purpose_tags": list(purpose_tags) if purpose_tags else [],
        "notes": notes or "",
        "origin": origin,
        "created_at": _now_iso(),
        "size_bytes": len(icc_bytes),
        "md5": md5_bytes(icc_bytes),
    }
    _write_json(_repo_z9_meta_path(path), meta)
    return path, meta


def list_repo_z9_profiles(serial: Optional[str] = None) -> list[dict]:
    """List the profiles of the personal Z9 space, sidecar merged.

    Walks ``repo/z9/<serial>/papers/<media_id>/*.icc``. For each profile,
    returns the sidecar enriched with ``filename``, ``serial``, ``media_id``
    (inferred from the path if absent from the sidecar) and ``path``.

    :param serial: restrict to one printer; otherwise all.
    :return: list of dicts sorted by (serial, media_id, label).
    """
    out: list[dict] = []
    base = repo_z9_dir()
    if not base.exists():
        return out
    serial_dirs = ([repo_z9_serial_dir(serial)] if serial
                   else [d for d in base.iterdir() if d.is_dir()])
    for sdir in serial_dirs:
        if not sdir.exists():
            continue
        papers = sdir / "papers"
        if not papers.exists():
            continue
        for media_dir in papers.iterdir():
            if not media_dir.is_dir():
                continue
            for icc in sorted(media_dir.glob("*.icc")):
                meta = read_repo_z9_profile_meta(icc)
                entry = dict(meta)
                entry.setdefault("serial", sdir.name)
                entry.setdefault("media_id", media_dir.name)
                entry["filename"] = icc.name
                entry["path"] = str(icc)
                entry["mtime"] = icc.stat().st_mtime   # FS mtime (list sorting; robust, no app-level built_at)
                out.append(entry)
    out.sort(key=lambda e: (e.get("serial", ""), e.get("media_id", ""),
                            e.get("label", "")))
    return out


def delete_repo_z9_profile(serial: str, media_id: str, filename: str) -> bool:
    """Delete a personal Z9 profile (the .icc + its sidecar). Idempotent.

    :return: True if the .icc existed and was deleted, False otherwise.
    """
    icc = repo_z9_paper_dir(serial, media_id) / os.path.basename(filename)
    if not icc.exists():
        return False
    meta = _repo_z9_meta_path(icc)
    icc.unlink()
    if meta.exists():
        meta.unlink()
    return True


def rename_repo_z9_profile(
    serial: str, media_id: str, filename: str, new_label: str,
) -> Optional[dict]:
    """Rename a personal Z9 profile: modifies ONLY the ``label`` in the sidecar.

    The ``.icc`` file keeps its name on disk (auto label at filing) — only the
    display changes. Avoids any physical file rename and the associated
    collision handling (decided). The profile stays functional (re-refinable,
    comparable) because its path is unchanged.

    :return: the updated sidecar, or ``None`` if the profile does not exist.
    """
    icc = repo_z9_paper_dir(serial, media_id) / os.path.basename(filename)
    if not icc.exists():
        return None
    meta = read_repo_z9_profile_meta(icc)
    meta["label"] = new_label
    meta["renamed_at"] = _now_iso()
    _write_json(_repo_z9_meta_path(icc), meta)
    return meta


def set_repo_z9_profile_tags(
    serial: str, media_id: str, filename: str, tags: list,
) -> Optional[dict]:
    """Write the classification tags (``purpose_tags``) into the ``.meta`` sidecar —
    NEVER into the ``.icc`` (byte fidelity, zero colorimetric risk). Reuses the
    `.meta` write pattern of :func:`rename_repo_z9_profile`. Normalization
    (trim/dedup/ASCII) is done on the caller side (route).

    :return: the updated sidecar, or ``None`` if the profile does not exist."""
    icc = repo_z9_paper_dir(serial, media_id) / os.path.basename(filename)
    if not icc.exists():
        return None
    meta = read_repo_z9_profile_meta(icc)
    meta["purpose_tags"] = list(tags)
    meta["tags_updated_at"] = _now_iso()
    _write_json(_repo_z9_meta_path(icc), meta)
    return meta


# ─── R&D "donor" API (backward compat of the donor-export command) ─────
#
# The functions below exist for the `paper donor-export` command
# (which remains a separate R&D command) and its consumer
# `chart generate`. They now rely on the unified mirror.


@dataclass
class DonorEntry:
    """Metadata of a donor returned by save_donor / load_donor.

    Keeps the historical shape (used by the tests + the CLI). The physical
    file now lives at mirror_profile_path() with slot-keyed naming.
    """
    paper_id: str
    paper_name: str
    donor_id: Optional[str]
    donor_name: Optional[str]
    gloss_enhancer_extracted_from: str   # "FULLPAGE" or "OFF"
    color_space: str                      # "PRINTER_RGB" or "PRINTER_GRAYSCALE"
    z9_uuid: Optional[str]
    z9_icc_name: Optional[str]
    z9_date: Optional[str]
    extracted_at: str
    extracted_from_host: str
    size_bytes: int
    md5: str
    note: str = (
        "Colorimetrically valid profile for GE ON and GE OFF "
        "(only the desc tag differs between the two slots)"
    )


def donor_path(paper_name: str, serial: str) -> Path:
    """Canonical path of a donor (backward compat).

    Returns the slot-keyed path for the canonical factory slot
    (GE=OFF, ColorSpace=PRINTER_RGB). If the effective donor is on FULLPAGE,
    the function still returns the OFF path (tests that don't use the real Z9
    can rely on this deterministic path). For reads, prefer
    ``find_factory_profile`` which searches all slots.
    """
    return mirror_profile_path(
        serial, paper_name,
        gloss_enhancer="OFF", color_space="PRINTER_RGB", custom=False,
    )


def donor_exists(paper_name: str, serial: str) -> bool:
    """True if a factory profile is present in the mirror for this paper."""
    return find_factory_profile(serial, paper_name) is not None


def get_donor_entry(paper_name: str, serial: str) -> Optional[dict]:
    """Return the donor metadata (factory profile) or None."""
    return find_factory_profile(serial, paper_name)


def save_donor(
    *,
    icc_bytes: bytes,
    paper_id: str,
    paper_name: str,
    serial: str,
    donor_id: Optional[str],
    donor_name: Optional[str],
    gloss_enhancer_extracted_from: str,
    color_space: str,
    z9_uuid: Optional[str],
    z9_icc_name: Optional[str],
    z9_date: Optional[str],
    extracted_from_host: str,
) -> tuple[Path, DonorEntry]:
    """Write a factory donor into the mirror (``donor-export`` command).

    Uses the unified slot-keyed naming. Factory profiles written this way
    coexist without duplication with those written by the mirror sync (same
    filename, same expected content).
    """
    ensure_mirror(serial)
    paper_dir = mirror_paper_dir(serial, paper_name)
    paper_dir.mkdir(parents=True, exist_ok=True)

    # Build the profile dict to materialize
    profile = {
        "gloss_enhancer": gloss_enhancer_extracted_from,
        "color_space": color_space,
        "custom": False,
        "uuid": z9_uuid,
        "icc_name": z9_icc_name,
        "date": z9_date,
    }
    paper = {
        "id": paper_id,
        "name": paper_name,
        "donor_id": donor_id,
        "is_factory": False,
        "is_user_custom": True,
    }
    path, profile_entry = save_mirror_profile(
        icc_bytes=icc_bytes, serial=serial, paper=paper, profile=profile,
    )

    entry = DonorEntry(
        paper_id=paper_id,
        paper_name=paper_name,
        donor_id=donor_id,
        donor_name=donor_name,
        gloss_enhancer_extracted_from=gloss_enhancer_extracted_from,
        color_space=color_space,
        z9_uuid=z9_uuid,
        z9_icc_name=z9_icc_name,
        z9_date=z9_date,
        extracted_at=_now_iso(),
        extracted_from_host=extracted_from_host,
        size_bytes=len(icc_bytes),
        md5=md5_bytes(icc_bytes),
    )
    return path, entry


def load_donor(paper_name: str, serial: str,
               verify_md5: bool = True) -> tuple[Path, bytes, dict]:
    """Load a paper's factory profile (donor) from the mirror.

    Looks up the factory profile via ``find_factory_profile`` (may be GE=OFF
    or GE=FULLPAGE depending on what is present), regardless of whether the
    file was written by the sync or by the ``donor-export`` command.

    :raises FileNotFoundError: if no factory profile is in the mirror
    :raises ValueError: if verify_md5 and MD5 does not match
    """
    entry = find_factory_profile(serial, paper_name)
    if entry is None:
        raise FileNotFoundError(
            f"No factory profile in the mirror for {paper_name!r} "
            f"(serial {serial}).\n"
            f"  → First run: freeglaz store sync\n"
            f"    (or for the donor only: freeglaz paper donor-export "
            f"{paper_name!r})"
        )
    path = mirror_paper_dir(serial, paper_name) / entry["filename"]
    if not path.exists():
        raise FileNotFoundError(
            f"Manifest points to {path.name} but file is missing.\n"
            f"  → Re-run: freeglaz store sync"
        )

    data = path.read_bytes()
    if verify_md5 and entry.get("md5"):
        current = md5_bytes(data)
        if current != entry["md5"]:
            raise ValueError(
                f"Cache integrity compromised for {paper_name!r}:\n"
                f"  MD5 expected  : {entry['md5']}\n"
                f"  MD5 computed  : {current}\n"
                f"  → File {path.name} was modified or is corrupted.\n"
                f"  → Re-run: freeglaz store sync"
            )
    return path, data, entry


def donor_needs_refresh(paper_name: str, serial: str,
                        current_z9_uuid: Optional[str]) -> bool:
    """Report whether the donor cache is stale vs the Z9.

    Compares the stored z9_uuid to the current UUID of the factory profile on
    the firmware side. A difference = likely firmware update, the mirror must
    be refreshed.
    """
    entry = find_factory_profile(serial, paper_name)
    if entry is None:
        return True
    cached_uuid = entry.get("z9_uuid")
    if cached_uuid is None or current_z9_uuid is None:
        return False
    return cached_uuid != current_z9_uuid


# ─── Session: temporary workspace (unchanged) ──────────────────────────


def session_dir(name: str) -> Path:
    """Session folder (created if absent)."""
    d = sessions_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_session_name(paper_name: str, suffix: Optional[str] = None) -> str:
    """Generate a unique session name: '2026-05-29_canson-baryta_15h47m23'."""
    ts = _now_filename()
    slug = slugify(paper_name)
    base = f"{ts}_{slug}"
    if suffix:
        base += f"_{slugify(suffix)}"
    return base


# ─── Mirror manifest (read/write) ─────────────────────────────────────


def read_mirror_manifest(serial: str) -> dict:
    """Read ``mirror/<serial>/_mirror.json`` (or empty dict if absent)."""
    return _read_json(
        mirror_serial_dir(serial) / MIRROR_MANIFEST_FILENAME,
        default={"serial": serial, "medium_list_version": None,
                 "last_sync_at": None, "papers": {}},
    )


def write_mirror_manifest(serial: str, manifest: dict) -> None:
    """Write ``mirror/<serial>/_mirror.json`` (atomic creation)."""
    ensure_mirror(serial)
    _write_json(mirror_serial_dir(serial) / MIRROR_MANIFEST_FILENAME, manifest)


def read_store_manifest() -> dict:
    """Read ``store.json`` (root)."""
    return _read_json(
        root_dir() / STORE_MANIFEST_FILENAME,
        default={"store_version": STORE_VERSION, "created_at": None,
                 "root": str(root_dir())},
    )


def write_store_manifest(manifest: dict) -> None:
    """Write ``store.json`` (root). Atomic (tmp + replace via _write_json)."""
    _write_json(root_dir() / STORE_MANIFEST_FILENAME, manifest)


# ─── Known printers (store.json — IP config, multi-ready) ──────────────
# Declarative registry {ip, serial, name, active} in store.json (state, not a
# preference → not in the TOML). V1 mono = 1 active entry; the format carries N
# entries for V2 (at most ONE active). The recorded serial = registry
# identity/IP; the SOURCE of per-serial paths stays get_serial(client) live (V1).


def read_printers() -> list[dict]:
    """List of known printers: ``[{ip, serial, name, active}]``; ``[]`` if none
    (store.json without ``printers`` = not configured)."""
    return read_store_manifest().get("printers") or []


def active_printer() -> Optional[dict]:
    """The active printer (``active=True``), or ``None``. At most one (set_active)."""
    for p in read_printers():
        if p.get("active"):
            return p
    return None


def _save_printers(printers: list[dict]) -> None:
    """Rewrite the ``printers`` list in store.json, preserving the other keys."""
    manifest = read_store_manifest()
    manifest["printers"] = printers
    write_store_manifest(manifest)


def add_printer(*, ip: str, serial: str, name: str = "",
                active: bool = False, model_support: Optional[str] = None,
                admin_pwd: Optional[str] = None) -> dict:
    """Add (or update BY serial — upsert, no duplicate) a printer.
    If ``active=True``, deactivates all others. ``model_support``
    ("validated"|"untested") from the connection test → the UI can show a
    "not tested" badge. ``admin_pwd`` (optional) is the Z9 admin password,
    stored so the web/desktop app can reach protected endpoints (job queue)
    without a .env; the env var ``Z9_ADMIN_PWD`` still takes precedence
    (cf. Z9Client.from_env). Returns the written entry.

    SECURITY: ``admin_pwd`` is stored in cleartext in store.json (same posture
    as the .env file). Never log it; API layers redact it (has_admin_pwd)."""
    if not serial:
        raise ValueError("add_printer: serial required")
    printers = [p for p in read_printers() if p.get("serial") != serial]
    if active:
        for p in printers:
            p["active"] = False
    entry = {"ip": ip, "serial": serial, "name": name, "active": bool(active),
             "model_support": model_support}
    if admin_pwd:
        entry["admin_pwd"] = admin_pwd
    printers.append(entry)
    _save_printers(printers)
    return entry


def remove_printer(serial: str) -> bool:
    """Remove the ``serial`` printer. ``False`` if unknown."""
    printers = read_printers()
    kept = [p for p in printers if p.get("serial") != serial]
    if len(kept) == len(printers):
        return False
    _save_printers(kept)
    return True


def update_printer(serial: str, *, ip: Optional[str] = None,
                   name: Optional[str] = None,
                   admin_pwd: Optional[str] = None) -> Optional[dict]:
    """Update ``ip``/``name``/``admin_pwd`` of a printer (NOT ``active`` →
    set_active_printer). Returns the entry, or ``None`` if unknown.

    ``admin_pwd`` semantics: ``None`` = leave unchanged, ``""`` = clear the
    stored password, any other value = set it. (See add_printer SECURITY note.)"""
    printers = read_printers()
    found = None
    for p in printers:
        if p.get("serial") == serial:
            if ip is not None:
                p["ip"] = ip
            if name is not None:
                p["name"] = name
            if admin_pwd is not None:
                if admin_pwd == "":
                    p.pop("admin_pwd", None)      # explicit clear
                else:
                    p["admin_pwd"] = admin_pwd
            found = p
    if found is None:
        return None
    _save_printers(printers)
    return found


def set_active_printer(serial: str) -> bool:
    """Mark ``serial`` active and deactivate the others. ``False`` if unknown."""
    printers = read_printers()
    if not any(p.get("serial") == serial for p in printers):
        return False
    for p in printers:
        p["active"] = (p.get("serial") == serial)
    _save_printers(printers)
    return True
