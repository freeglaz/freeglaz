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

"""Immutable history of the profiles built for a chart, and a publication protocol that
never loses a profile.

Layout of a chart folder::

    <chart_id>.icc              current profile (unchanged location)
    profile_manifest.json       POINTER: the current build's manifest + {"pointer": {build_id, sha256…}}
    builds/<build_id>/          one IMMUTABLE version per successful build
        profile.icc
        manifest.json           durable description (plan, materialized inputs, result)
        references.json         MUTABLE: where copies were published (repo/z9…)

Publication protocol (each step is atomic on its own; the sequence is made safe by the
order and by :func:`inspect` / :func:`repair`):

1. the current profile, if it is not in the history yet (profiles built before versions
   existed), is first preserved as a version of partially unknown provenance;
2. the new version is staged in ``builds/.staging-<build_id>/`` then renamed to
   ``builds/<build_id>/`` (directory rename = atomic) → the new profile is durable;
3. ``<chart_id>.icc`` is replaced from the version (temporary file + ``os.replace``);
4. the pointer ``profile_manifest.json`` is rewritten (temporary file + ``os.replace``).

An interruption leaves one of these states, all detected by :func:`inspect`:
before 2 → staging leftover (never published, removable); between 2 and 3 → an
"orphan" version, the previous profile still current (nothing to repair); between 3
and 4 → stale pointer (repaired from the version whose sha256 is the current file's).
Versions are never modified or deleted by this module (no purge in this version).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BUILDS_DIR = "builds"
POINTER_NAME = "profile_manifest.json"
VERSION_ICC = "profile.icc"
VERSION_MANIFEST = "manifest.json"
REFERENCES_NAME = "references.json"
POINTER_VERSION = 1
_STAGING_PREFIX = ".staging-"
_LEFTOVER_MAX_AGE_S = 3600.0
_VERSION_ID_RE = re.compile(r"^\d{8}T\d{6}(?:\d{3})?Z-(?:[a-z0-9]{6}|preserved-[0-9a-f]{8})$")


class VersionError(RuntimeError):
    """A publication step failed; the previous profile is intact (see inspect/repair)."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha(Path(path).read_bytes())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, data: bytes) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _write_json_atomic(path: Path, obj: dict) -> None:
    _write_atomic(path, json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def builds_dir(chart_dir: Path) -> Path:
    return Path(chart_dir) / BUILDS_DIR


def current_icc(chart_dir: Path, chart_id: str) -> Path:
    return Path(chart_dir) / f"{chart_id}.icc"


def read_pointer(chart_dir: Path) -> Optional[dict]:
    try:
        return json.loads((Path(chart_dir) / POINTER_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_versions(chart_dir: Path) -> list[dict]:
    """Every version (published directories only), oldest first, with an integrity check."""
    out = []
    root = builds_dir(chart_dir)
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir() or not _VERSION_ID_RE.match(d.name):
            continue
        e = {"build_id": d.name, "path": str(d), "kind": None, "icc_sha256": None,
             "manifest_sha256": None, "intact": False, "problem": None}
        try:
            mbytes = (d / VERSION_MANIFEST).read_bytes()
            m = json.loads(mbytes)
            e["manifest_sha256"] = _sha(mbytes)
            e["kind"] = m.get("kind", "build")
            e["created_at"] = m.get("created_at") or m.get("preserved_at")
            want = (m.get("result") or {}).get("icc_sha256")
            got = _sha_file(d / VERSION_ICC)
            e["icc_sha256"] = got
            e["intact"] = bool(want) and want == got and m.get("build_id") == d.name
            if not e["intact"]:
                e["problem"] = "icc_sha256_mismatch" if want != got else "build_id_mismatch"
        except (OSError, ValueError) as ex:
            e["problem"] = f"unreadable: {ex}"
        out.append(e)
    return out


def read_version_manifest(chart_dir: Path, build_id: str) -> dict:
    return json.loads((builds_dir(chart_dir) / build_id / VERSION_MANIFEST).read_text(encoding="utf-8"))


def _stage_version(chart_dir: Path, build_id: str, icc_bytes: bytes, manifest: dict) -> Path:
    """Write a complete version under a staging name, then rename it into place."""
    root = builds_dir(chart_dir)
    root.mkdir(exist_ok=True)
    final = root / build_id
    if final.exists():
        raise VersionError(f"version {build_id} already exists (versions are immutable)")
    staging = root / f"{_STAGING_PREFIX}{build_id}"
    staging.mkdir()
    for name, data in ((VERSION_ICC, icc_bytes),
                       (VERSION_MANIFEST, json.dumps(manifest, indent=1, ensure_ascii=False,
                                                     sort_keys=True).encode("utf-8"))):
        with open(staging / name, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    os.rename(staging, final)                  # atomic: the version appears complete or not at all
    return final


def preserve_current(chart_dir: Path, chart_id: str, legacy_trace: Optional[dict] = None) -> Optional[str]:
    """If the current profile is not in the history (built before versions existed), keep it
    as a version of PARTIALLY UNKNOWN provenance before anything replaces it. No complete
    manifest is invented: only what is known (sha256, size, mtime, the old trace as recorded,
    and the 4B manifest when its sha256 matches the file). Returns the new build_id or None."""
    icc = current_icc(chart_dir, chart_id)
    if not icc.is_file():
        return None
    data = icc.read_bytes()
    sha = _sha(data)
    if any(v["icc_sha256"] == sha for v in list_versions(chart_dir)):
        return None
    mtime = datetime.fromtimestamp(icc.stat().st_mtime, timezone.utc)
    build_id = f"{mtime.strftime('%Y%m%dT%H%M%SZ')}-preserved-{sha[:8]}"
    pointer = read_pointer(chart_dir)
    known = pointer if pointer and (pointer.get("result") or {}).get("icc_sha256") == sha else None
    manifest = {
        "manifest_version": 1, "kind": "preserved", "build_id": build_id,
        "provenance": "from_previous_manifest" if known else "unknown",
        "preserved_at": _now(), "source_file": icc.name, "source_mtime": mtime.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "result": {"icc_sha256": sha, "icc_size_bytes": len(data)},
        "legacy_trace": ({**legacy_trace, "note": "recorded by an earlier version, not verified"}
                         if legacy_trace else None),
        "plan": (known or {}).get("plan"), "materialized": (known or {}).get("materialized"),
    }
    _stage_version(chart_dir, build_id, data, manifest)
    return build_id


def publish(chart_dir: Path, chart_id: str, build_id: str, icc_path: Path, manifest: dict) -> dict:
    """Steps 2-4 of the protocol (step 1, preserve_current, is the caller's). ``manifest``
    must carry ``result.icc_sha256`` of ``icc_path``.

    A failure at step 2 or 3 raises (the current profile is unchanged → the build failed).
    A failure at step 4 does NOT raise: the new profile IS current, only the pointer is stale
    → reported in ``warnings`` and repaired by :func:`repair` at the next build."""
    data = Path(icc_path).read_bytes()
    if (manifest.get("result") or {}).get("icc_sha256") != _sha(data):
        raise VersionError("manifest result.icc_sha256 does not match the profile to publish")
    if manifest.get("build_id") != build_id:
        raise VersionError("manifest build_id does not match")
    vdir = _stage_version(chart_dir, build_id, data, manifest)                       # step 2
    _write_atomic(current_icc(chart_dir, chart_id), (vdir / VERSION_ICC).read_bytes())  # step 3
    out = {"build_id": build_id, "icc_sha256": manifest["result"]["icc_sha256"],
           "manifest_sha256": _sha_file(vdir / VERSION_MANIFEST), "pointer_written": False, "warnings": []}
    try:                                                                              # step 4
        _write_json_atomic(Path(chart_dir) / POINTER_NAME, {**manifest, "pointer": {
            "pointer_version": POINTER_VERSION, "build_id": build_id, "icc_sha256": out["icc_sha256"],
            "manifest_sha256": out["manifest_sha256"], "published_at": _now()}})
        out["pointer_written"] = True
    except OSError as e:
        out["warnings"].append(f"pointer not written ({e}); repaired at the next build")
    return out


def add_reference(chart_dir: Path, build_id: str, ref: dict) -> None:
    """Record where a copy of a version was published (mutable file next to the immutable ones)."""
    p = builds_dir(chart_dir) / build_id / REFERENCES_NAME
    try:
        refs = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        refs = {"references": []}
    refs["references"].append({**ref, "at": _now()})
    _write_json_atomic(p, refs)


def inspect(chart_dir: Path, chart_id: str) -> dict:
    """Read-only diagnosis of the publication state (see the module docstring)."""
    chart_dir = Path(chart_dir)
    icc = current_icc(chart_dir, chart_id)
    cur_sha = _sha_file(icc) if icc.is_file() else None
    versions = list_versions(chart_dir)
    pointer = read_pointer(chart_dir)
    pinfo = (pointer or {}).get("pointer") or {}
    pid = pinfo.get("build_id")
    by_sha = {v["icc_sha256"]: v["build_id"] for v in versions if v["intact"]}
    root = builds_dir(chart_dir)
    staging = [d.name for d in root.iterdir() if d.is_dir() and d.name.startswith(_STAGING_PREFIX)] if root.is_dir() else []
    tmps = [p.name for p in chart_dir.glob(f".{chart_id}.icc.*.tmp")] + [p.name for p in chart_dir.glob(f".{POINTER_NAME}.*.tmp")]
    rep = {"current_sha256": cur_sha, "current_build_id": by_sha.get(cur_sha) if cur_sha else None,
           "pointer_build_id": pid, "versions": versions, "staging_leftovers": sorted(staging),
           "tmp_leftovers": sorted(tmps), "problems": [], "state": "ok"}
    if cur_sha is None:
        if pid:
            rep["problems"].append("current_profile_missing")
    elif rep["current_build_id"] is None:
        rep["problems"].append("current_not_in_history")         # legacy profile, preserved at next build
    elif pid != rep["current_build_id"]:
        rep["problems"].append("pointer_stale")                   # interrupted between steps 3 and 4
    if pid and pid not in {v["build_id"] for v in versions}:
        rep["problems"].append("pointer_version_missing")
    corrupt = [v["build_id"] for v in versions if not v["intact"]]
    if corrupt:
        rep["problems"].append("corrupt_versions")
        rep["corrupt_versions"] = corrupt
    newer = [v["build_id"] for v in versions if v["intact"] and rep["current_build_id"]
             and v["build_id"] > rep["current_build_id"] and v["kind"] != "preserved"]
    rep["orphan_versions"] = newer                                # interrupted between steps 2 and 3
    if cur_sha is None and not pid and not versions:
        rep["state"] = "no_profile"
    elif not rep["problems"]:
        rep["state"] = "ok"
    elif set(rep["problems"]) <= {"pointer_stale", "current_not_in_history"}:
        rep["state"] = "repairable"
    else:
        rep["state"] = "degraded"
    return rep


def repair(chart_dir: Path, chart_id: str, legacy_trace: Optional[dict] = None, preserve: bool = False,
           max_leftover_age_s: float = _LEFTOVER_MAX_AGE_S) -> list[str]:
    """Safe repairs only: rewrite a stale pointer from the version whose sha256 is the current
    file's, remove OLD staging and temporary leftovers (never-published copies), and — only if
    ``preserve`` — keep a current profile missing from the history. Never modifies or deletes a
    version. (A build calls it with preserve=False at start, so a FAILED build changes nothing,
    and preserves the current profile only right before publishing.)"""
    chart_dir = Path(chart_dir)
    actions: list[str] = []
    rep = inspect(chart_dir, chart_id)
    now = time.time()
    for name in rep["staging_leftovers"]:
        d = builds_dir(chart_dir) / name
        if now - d.stat().st_mtime > max_leftover_age_s:
            shutil.rmtree(d, ignore_errors=True)
            actions.append(f"removed staging leftover {name}")
    for name in rep["tmp_leftovers"]:
        p = chart_dir / name
        if now - p.stat().st_mtime > max_leftover_age_s:
            p.unlink(missing_ok=True)
            actions.append(f"removed temporary leftover {name}")
    if preserve and "current_not_in_history" in rep["problems"]:
        bid = preserve_current(chart_dir, chart_id, legacy_trace)
        if bid:
            actions.append(f"preserved current profile as {bid}")
            rep = inspect(chart_dir, chart_id)
    if "pointer_stale" in rep["problems"] and rep["current_build_id"]:
        bid = rep["current_build_id"]
        m = read_version_manifest(chart_dir, bid)
        _write_json_atomic(chart_dir / POINTER_NAME, {**m, "pointer": {
            "pointer_version": POINTER_VERSION, "build_id": bid, "icc_sha256": rep["current_sha256"],
            "manifest_sha256": _sha_file(builds_dir(chart_dir) / bid / VERSION_MANIFEST),
            "published_at": _now(), "repaired": True}})
        actions.append(f"pointer rewritten to {bid}")
    return actions
