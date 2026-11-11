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

"""Unified preparation of the measurements that feed colprof.

One place decides WHICH readings enter a profile, for every build mode
(base ``last``, base ``average``, multi-source), in two steps:

* :func:`plan_inputs` — PURE: from a frozen snapshot of the charts and the build
  request, decides the scans used, the rejections applied or not (with their
  reason), unmatched rejection ids, the source profiles, the colprof flags and the
  sha256 fingerprints. It reads files (to hash them, and to match SAMPLE_IDs when
  rejections apply) but never writes anything.
* :func:`materialize` — writes the working ti3 from the plan, ONLY inside a
  per-build directory. Every source is copied from bytes whose sha256 matches the
  plan (a file changed in between aborts the build); filtering and concatenation
  reuse :func:`multipass.filter_ti3_text` and :func:`multipass.concat_ti3`.

Rules that never bend: a raw ti3 is never modified; a rejection always targets
``(chart, scan, SAMPLE_ID)`` of a RAW scan and is applied BEFORE any concatenation
(concatenation renumbers SAMPLE_IDs); the provenance of every effective reading is
recoverable from the manifest (ranges per source + raw sha256 + applied rejections).

Policies: ``legacy`` reproduces the historical behaviour exactly (base ``last``
ignores the current chart's rejections — now REPORTED as not applied); ``apply_all``
applies them in every mode (defined, not enabled yet).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from .multipass import concat_ti3, filter_ti3_text, parse_ti3

BUILD_INPUTS_VERSION = 1
MANIFEST_VERSION = 1
POLICIES = ("legacy", "apply_all")
REASON_LEGACY_LAST = "legacy_last_ignores_rejections"
REASON_NOT_USED = "not_used_by_base_last"
REASON_EXCLUDED = "scan_excluded"
REASON_NOT_FOUND = "scan_not_found"
BUILD_DIR_NAME = ".build"
_BUILD_ID_RE = re.compile(r"^\d{8}T\d{6}(?:\d{3})?Z-[a-z0-9]{6}$")


class BuildInputsError(ValueError):
    """Inputs cannot be prepared (unreadable scan, no included scan, …)."""


class SourceChangedError(BuildInputsError):
    """A source file changed between planning and materialization: the build is aborted
    rather than built from data that is not the planned one."""


# ── snapshot & request ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class ChartSnapshot:
    """State of ONE chart, read once at the start of a build (roles and rejections from
    scan_state.json, raw scans of measurements/ in name order)."""
    chart_id: str
    chart_dir: Path
    raw_scans: tuple[str, ...]
    roles: Mapping[str, str] = field(default_factory=dict)          # missing = included
    rejected: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def included(self) -> tuple[str, ...]:
        return tuple(s for s in self.raw_scans if self.roles.get(s, "included") == "included")

    def scan_path(self, scan: str) -> Path:
        return Path(self.chart_dir) / "measurements" / scan


@dataclass(frozen=True)
class BuildRequest:
    base: Optional[str] = None                    # "average" | "last" | None (= last)
    source_profiles: tuple[str, ...] = ()         # absolute paths, identity checked upstream
    colprof_flags: tuple[str, ...] = ()           # RESOLVED flags (aliases → paths)
    policy: str = "legacy"


# ── plan ────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class PlannedScan:
    chart_id: str
    role: str                      # "current" | "extra"
    scan: str
    path: Path
    sha256: str
    rejections_applied: tuple[str, ...]

    @property
    def filtered(self) -> bool:
        return bool(self.rejections_applied)


@dataclass(frozen=True)
class PlannedProfile:
    index: int
    path: Path
    name: str
    sha256: str


@dataclass(frozen=True)
class RejectionNote:
    chart_id: str
    scan: str
    sids: tuple[str, ...]
    reason: str                    # REASON_* (not applied) or "unmatched"


@dataclass(frozen=True)
class InputPlan:
    chart_id: str
    policy: str
    base_requested: Optional[str]
    base_effective: str
    current: tuple[PlannedScan, ...]
    extras: tuple[PlannedScan, ...]
    profiles: tuple[PlannedProfile, ...]
    rejections_not_applied: tuple[RejectionNote, ...]
    rejections_unmatched: tuple[RejectionNote, ...]
    colprof_flags: tuple[str, ...]
    flag_files: tuple[tuple[str, str], ...]       # (path, sha256) of files named by the flags
    build_inputs_version: int = BUILD_INPUTS_VERSION

    @property
    def scans(self) -> tuple[PlannedScan, ...]:
        return self.current + self.extras

    @property
    def fingerprint(self) -> str:
        """sha256 of exactly what determines the effective ti3 and the profile (no time)."""
        core = {
            "v": self.build_inputs_version, "chart_id": self.chart_id, "base_effective": self.base_effective,
            "scans": [[s.chart_id, s.role, s.scan, s.sha256, list(s.rejections_applied)] for s in self.scans],
            "profiles": [[str(p.path), p.sha256] for p in self.profiles],
            "flags": list(self.colprof_flags), "flag_files": [list(x) for x in self.flag_files],
        }
        return "sha256:" + hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict:
        scan = lambda s: {"chart_id": s.chart_id, "role": s.role, "scan": s.scan, "sha256": s.sha256,  # noqa: E731
                          "rejections_applied": list(s.rejections_applied)}
        note = lambda n: {"chart_id": n.chart_id, "scan": n.scan, "sids": list(n.sids), "reason": n.reason}  # noqa: E731
        return {
            "build_inputs_version": self.build_inputs_version, "policy": self.policy, "chart_id": self.chart_id,
            "base_requested": self.base_requested, "base_effective": self.base_effective,
            "current_scans": [scan(s) for s in self.current], "extra_scans": [scan(s) for s in self.extras],
            "source_profiles": [{"index": p.index, "path": str(p.path), "name": p.name, "sha256": p.sha256}
                                for p in self.profiles],
            "rejections_not_applied": [note(n) for n in self.rejections_not_applied],
            "rejections_unmatched": [note(n) for n in self.rejections_unmatched],
            "colprof_flags": list(self.colprof_flags),
            "colprof_flag_files": [{"path": p, "sha256": h} for p, h in self.flag_files],
            "inputs_fingerprint": self.fingerprint,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _natural_key(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", str(s))]


def _scan_sample_ids(path: Path, chart_id: str, scan: str) -> set[str]:
    try:
        _, fmt, rows = parse_ti3(Path(path).read_text(encoding="ascii", errors="replace"))
        i = fmt.index("SAMPLE_ID")
    except (OSError, ValueError, IndexError) as e:
        raise BuildInputsError(f"unreadable ti3 {chart_id}/{scan}: {e}") from None
    return {r[i] for r in rows if i < len(r)}


def _plan_chart(snap: ChartSnapshot, role: str, used: Sequence[str], apply: bool,
                not_applied_reason_for_used: Optional[str],
                notes: list, unmatched: list) -> list[PlannedScan]:
    """PlannedScan for the USED scans of one chart + notes for the rejections that do not apply."""
    raw = set(snap.raw_scans)
    included = set(snap.included())
    out: list[PlannedScan] = []
    for scan in used:
        path = snap.scan_path(scan)
        sha = _sha256_file(path)
        reg = tuple(sorted({str(x) for x in snap.rejected.get(scan, ())}, key=_natural_key))
        applied: tuple[str, ...] = ()
        if reg and apply:
            sids = _scan_sample_ids(path, snap.chart_id, scan)
            applied = tuple(x for x in reg if x in sids)
            missing = tuple(x for x in reg if x not in sids)
            if missing:
                unmatched.append(RejectionNote(snap.chart_id, scan, missing, "unmatched"))
        elif reg:
            notes.append(RejectionNote(snap.chart_id, scan, reg, not_applied_reason_for_used))
        out.append(PlannedScan(snap.chart_id, role, scan, path, sha, applied))
    used_set = set(used)
    for scan in sorted(snap.rejected, key=_natural_key):
        reg = tuple(sorted({str(x) for x in snap.rejected[scan]}, key=_natural_key))
        if not reg or scan in used_set:
            continue
        reason = (REASON_NOT_FOUND if scan not in raw else
                  REASON_EXCLUDED if scan not in included else REASON_NOT_USED)
        notes.append(RejectionNote(snap.chart_id, scan, reg, reason))
    return out


def plan_inputs(current: ChartSnapshot, extras: Sequence[ChartSnapshot],
                request: BuildRequest) -> InputPlan:
    """Deterministic plan of the build inputs. Never writes. See the module docstring."""
    if request.policy not in POLICIES:
        raise BuildInputsError(f"unknown rejection policy: {request.policy!r}")
    included = current.included()
    if not included:
        raise BuildInputsError("no included scan")
    base_eff = "average" if (request.base == "average" and len(included) >= 2) else "last"
    used = list(included) if base_eff == "average" else [included[-1]]
    apply_current = base_eff == "average" or request.policy == "apply_all"
    notes: list[RejectionNote] = []
    unmatched: list[RejectionNote] = []
    cur = _plan_chart(current, "current", used, apply_current, REASON_LEGACY_LAST, notes, unmatched)
    ext: list[PlannedScan] = []
    for snap in extras:
        xinc = snap.included()
        if not xinc:
            raise BuildInputsError(f"source chart {snap.chart_id}: no included scan")
        # additional charts contribute ALL their included scans with THEIR rejections (both policies)
        ext.extend(_plan_chart(snap, "extra", list(xinc), True, None, notes, unmatched))
    profiles = tuple(PlannedProfile(i, Path(p), Path(p).name, _sha256_file(Path(p)))
                     for i, p in enumerate(request.source_profiles))
    flag_files = tuple((tok, _sha256_file(Path(tok))) for tok in request.colprof_flags
                       if os.path.isabs(tok) and Path(tok).is_file())
    return InputPlan(chart_id=current.chart_id, policy=request.policy, base_requested=request.base,
                     base_effective=base_eff, current=tuple(cur), extras=tuple(ext), profiles=profiles,
                     rejections_not_applied=tuple(notes), rejections_unmatched=tuple(unmatched),
                     colprof_flags=tuple(request.colprof_flags), flag_files=flag_files)


# ── materialization ─────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EffectiveInputs:
    profile_ti3: Path
    workdir: Path
    n_rows: int
    sha256: str
    renumbered: bool                     # SAMPLE_IDs renumbered by a concatenation
    layout: tuple[dict, ...]             # per source, in order: rows and out_sid_range
    bytes_passthrough: bool              # single raw scan, bytes reused unchanged (copy)

    def to_dict(self) -> dict:
        return {"effective_ti3": {"name": self.profile_ti3.name, "sha256": self.sha256, "n_rows": self.n_rows,
                                  "renumbered": self.renumbered, "bytes_passthrough": self.bytes_passthrough},
                "layout": [dict(x) for x in self.layout]}


def _frozen_bytes(path: Path, expected_sha: str, what: str) -> bytes:
    data = Path(path).read_bytes()
    if _sha256_bytes(data) != expected_sha:
        raise SourceChangedError(f"{what} changed since the build started ({Path(path).name})")
    return data


def _count_rows(path: Path, what: str) -> int:
    try:
        _, _, rows = parse_ti3(Path(path).read_text(encoding="ascii", errors="replace"))
    except (OSError, ValueError, IndexError) as e:      # truncated / malformed CGATS
        raise BuildInputsError(f"unreadable ti3 {what}: {e or type(e).__name__}") from None
    return len(rows)


def _default_extract(icc_path: Path, out_ti3: Path, descriptor: str) -> None:
    from .profiling import ProfilingOps
    ProfilingOps().extract_cgats_from_icc(icc_path, out_ti3, descriptor=descriptor)


def materialize(plan: InputPlan, workdir: Path,
                extract_profile: Callable[[Path, Path, str], None] = _default_extract) -> EffectiveInputs:
    """Writes the working ti3 of ``plan`` inside ``workdir`` (and nowhere else).

    Same files and the same concat_ti3 calls as the historical pipeline (file stems are
    the concat labels, so they are kept): ``<stem>.ti3`` (byte copy) or
    ``<stem>_qcfilt.ti3`` (filtered), ``<chart>_avg.ti3`` (labels scan1…N),
    ``src_profile_<i>.ti3``, ``<chart>_multisource.ti3``."""
    workdir = Path(workdir)
    scan_files: dict[tuple[str, str], Path] = {}
    for s in plan.scans:
        data = _frozen_bytes(s.path, s.sha256, f"scan {s.chart_id}/{s.scan}")
        d = workdir / "charts" / s.chart_id
        d.mkdir(parents=True, exist_ok=True)
        stem = Path(s.scan).stem
        if s.filtered:
            dst = d / f"{stem}_qcfilt.ti3"
            dst.write_text(filter_ti3_text(data.decode("ascii", errors="replace"), set(s.rejections_applied)),
                           encoding="ascii")
        else:
            dst = d / f"{stem}.ti3"
            dst.write_bytes(data)
        scan_files[(s.chart_id, s.scan)] = dst

    layout: list[dict] = []
    cur_files = [scan_files[(s.chart_id, s.scan)] for s in plan.current]
    if plan.base_effective == "average":
        contrib = workdir / f"{plan.chart_id}_avg.ti3"
        try:
            concat_ti3(cur_files, contrib, labels=[f"scan{i + 1}" for i in range(len(cur_files))],
                       dedup_strategy="keep", descriptor=f"freeglaz average {plan.chart_id}")
        except (ValueError, FileNotFoundError) as e:
            raise BuildInputsError(f"Scan averaging refused (inconsistent measurements): {e}") from None
    else:
        contrib = cur_files[0]
    sources: list[Path] = [contrib]
    entries: list[dict] = [{"kind": "scan", "chart_id": s.chart_id, "role": s.role, "scan": s.scan,
                            "filtered": s.filtered, "file": scan_files[(s.chart_id, s.scan)]} for s in plan.current]
    for s in plan.extras:
        sources.append(scan_files[(s.chart_id, s.scan)])
        entries.append({"kind": "scan", "chart_id": s.chart_id, "role": s.role, "scan": s.scan,
                        "filtered": s.filtered, "file": scan_files[(s.chart_id, s.scan)]})
    for p in plan.profiles:
        data = _frozen_bytes(p.path, p.sha256, f"source profile {p.name}")
        d = workdir / "profiles" / str(p.index)
        d.mkdir(parents=True, exist_ok=True)
        icc_copy = d / p.name
        icc_copy.write_bytes(data)
        out = d / f"src_profile_{p.index}.ti3"
        try:
            extract_profile(icc_copy, out, f"freeglaz source {p.name}")
        except (FileNotFoundError, ValueError) as e:
            raise BuildInputsError(f"source profile {p.name}: extraction failed ({e})") from None
        sources.append(out)
        entries.append({"kind": "profile", "index": p.index, "name": p.name, "file": out})

    if len(sources) > 1:
        profile_ti3 = workdir / f"{plan.chart_id}_multisource.ti3"
        try:
            concat_ti3(sources, profile_ti3, dedup_strategy="keep",
                       descriptor=f"freeglaz multi-source {plan.chart_id}")
        except (ValueError, FileNotFoundError) as e:
            raise BuildInputsError(f"multi-source concat refused (inconsistent measurements): {e}") from None
    else:
        profile_ti3 = contrib
    renumbered = profile_ti3 != cur_files[0] or plan.base_effective == "average"

    pos = 1
    for e in entries:
        what = f"{e['chart_id']}/{e['scan']}" if e["kind"] == "scan" else f"source profile {e['name']}"
        n = _count_rows(e.pop("file"), what)
        e["rows"] = n
        e["out_sid_range"] = [pos, pos + n - 1] if renumbered else None
        pos += n
        layout.append(e)
    n_rows = _count_rows(profile_ti3, profile_ti3.name)
    if n_rows != pos - 1:                                  # provenance must cover every reading
        raise BuildInputsError(f"provenance mismatch: {n_rows} effective rows vs {pos - 1} planned")
    single_raw = (len(plan.scans) == 1 and not plan.profiles and plan.base_effective == "last"
                  and not plan.current[0].filtered)
    return EffectiveInputs(profile_ti3=profile_ti3, workdir=workdir, n_rows=n_rows,
                           sha256=_sha256_file(profile_ti3), renumbered=renumbered,
                           layout=tuple(layout), bytes_passthrough=single_raw)


# ── provenance ──────────────────────────────────────────────────────────────
def reconstruct_provenance(manifest: dict, scan_path: Callable[[str, str], Path]) -> list[dict]:
    """Every effective reading of a manifest → {out_sid, chart_id, scan, sample_id} (scans),
    {out_sid, profile, row} (source profiles). Recomputed from the RAW scans (sha256 checked)
    minus the applied rejections, in the recorded order: works after the build directory is
    gone, and whatever SAMPLE_IDs the charts share."""
    plan = manifest["plan"]
    applied = {(s["chart_id"], s["scan"]): (s["sha256"], set(s["rejections_applied"]))
               for s in plan["current_scans"] + plan["extra_scans"]}
    out: list[dict] = []
    renumbered = manifest["materialized"]["effective_ti3"]["renumbered"]
    for e in manifest["materialized"]["layout"]:
        if e["kind"] == "scan":
            sha, rej = applied[(e["chart_id"], e["scan"])]
            p = scan_path(e["chart_id"], e["scan"])
            data = Path(p).read_bytes()
            if _sha256_bytes(data) != sha:
                raise SourceChangedError(f"scan {e['chart_id']}/{e['scan']} differs from the built one")
            _, fmt, rows = parse_ti3(data.decode("ascii", errors="replace"))
            i = fmt.index("SAMPLE_ID")
            sids = [r[i] for r in rows if r[i] not in rej]
            if len(sids) != e["rows"]:
                raise BuildInputsError(f"provenance mismatch for {e['chart_id']}/{e['scan']}")
            start = e["out_sid_range"][0] if renumbered else None
            for k, sid in enumerate(sids):
                out.append({"out_sid": str(start + k) if renumbered else sid,
                            "chart_id": e["chart_id"], "scan": e["scan"], "sample_id": sid})
        else:
            start = e["out_sid_range"][0]
            out.extend({"out_sid": str(start + k), "profile": e["name"], "row": k + 1} for k in range(e["rows"]))
    return out


# ── manifest ────────────────────────────────────────────────────────────────
def build_manifest(plan: InputPlan, eff: Optional[EffectiveInputs] = None, *, build_id: Optional[str] = None,
                   result: Optional[dict] = None, created_at: Optional[str] = None,
                   request: Optional[dict] = None, snapshot: Optional[dict] = None,
                   software: Optional[dict] = None, coverage: Optional[dict] = None) -> dict:
    """Manifest v1: ``request`` (what was asked), ``snapshot`` (current chart state used),
    ``plan`` (known before writing anything), ``coverage``, ``materialized`` (after the
    working ti3 exists), ``result`` (after colprof), ``software`` (versions)."""
    return {"manifest_version": MANIFEST_VERSION, "kind": "build", "build_id": build_id,
            "created_at": created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "request": request, "snapshot": snapshot, "plan": plan.to_dict(), "coverage": coverage,
            "materialized": eff.to_dict() if eff else None, "result": result, "software": software}


_COLPROF_VERSION_CACHE: dict = {}


def software_versions() -> dict:
    """freeglaz and colprof versions (colprof read from its own banner, cached; None if absent)."""
    out = {"build_inputs": BUILD_INPUTS_VERSION, "freeglaz": None, "colprof": None}
    try:
        from webapp.backend.version import __version__ as _v       # optional (lib used without webapp)
        out["freeglaz"] = _v
    except Exception:                                              # noqa: BLE001
        pass
    try:
        from .argyll import resolve_argyll_binary
        exe = resolve_argyll_binary("colprof")
        key = (exe, os.path.getmtime(exe))
        if key not in _COLPROF_VERSION_CACHE:
            import subprocess
            r = subprocess.run([exe], capture_output=True, text=True, timeout=20)
            m = re.search(r"Version\s+([0-9][0-9.]*)", r.stdout + r.stderr)
            _COLPROF_VERSION_CACHE[key] = m.group(1) if m else None
        out["colprof"] = _COLPROF_VERSION_CACHE[key]
        out["colprof_path"] = exe
    except Exception:                                              # noqa: BLE001
        pass
    return out


def write_json_atomic(path: Path, data: dict) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ── per-build working directories ───────────────────────────────────────────
def new_build_id() -> str:
    """UTC time to the millisecond + 6 random characters: lexical order = chronological order
    (versions are listed by build id), unique even for builds started in the same second."""
    global _LAST_MS
    ms = max(int(time.time() * 1000), _LAST_MS + 1)     # strictly increasing within the process
    _LAST_MS = ms
    return (time.strftime("%Y%m%dT%H%M%S", time.gmtime(ms / 1000)) + f"{ms % 1000:03d}Z-"
            + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(6)))


_LAST_MS = 0


def create_build_dir(chart_dir: Path) -> tuple[str, Path]:
    """``<chart>/.build/<build_id>/`` — fresh and private to one build (never reused)."""
    root = Path(chart_dir) / BUILD_DIR_NAME
    root.mkdir(exist_ok=True)
    while True:
        build_id = new_build_id()
        d = root / build_id
        try:
            d.mkdir()
            return build_id, d
        except FileExistsError:
            continue


def _is_build_dir(path: Path) -> bool:
    path = Path(path)
    return path.parent.name == BUILD_DIR_NAME and bool(_BUILD_ID_RE.match(path.name))


def remove_build_dir(path: Path) -> None:
    """Removes a build directory — and refuses anything that is not one."""
    path = Path(path)
    if not _is_build_dir(path):
        raise BuildInputsError(f"refusing to remove a non-build directory: {path}")
    shutil.rmtree(path, ignore_errors=True)


def sweep_stale_build_dirs(chart_dir: Path, max_age_s: float = 2 * 3600) -> list[str]:
    """Removes build directories left by an interrupted build (older than ``max_age_s``).
    Only entries named like a build id inside ``.build/`` are considered: nothing else is
    ever deleted."""
    root = Path(chart_dir) / BUILD_DIR_NAME
    removed: list[str] = []
    if not root.is_dir():
        return removed
    now = time.time()
    for d in root.iterdir():
        if d.is_dir() and not d.is_symlink() and _BUILD_ID_RE.match(d.name):
            try:
                if now - d.stat().st_mtime > max_age_s:
                    shutil.rmtree(d, ignore_errors=True)
                    removed.append(d.name)
            except OSError:
                continue
    return removed
