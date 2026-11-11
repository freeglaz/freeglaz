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

"""Measurement QC service — runs ``lib.z9_client.measure_qc`` on ONE raw scan
ti3 and caches the report. READ-ONLY with respect to the measurements: the ti3
is never written, no exclusion is applied, no profile is built, no Z9 call.

Cache: ``<measurements>/.qc/<ti3 stem>.<sha256[:16]>.v<algo>.<params digest>.json``
— derived, fully regenerable, written atomically (tmp + ``os.replace``). A
report is reused only if the ti3 bytes (full sha256 stored inside), the
algorithm version, the report format AND the effective parameters all match; anything else is
recomputed (never a silent stale report). Older cache files of the same scan
are pruned after a successful write.

The caller (route) is responsible for choosing a RAW scan ti3 (one per scan,
from ``_raw_ti3_paths``); this module re-checks the name as defence in depth.
"""
from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from lib.z9_client import measure_qc as mq

logger = logging.getLogger(__name__)

QC_DIRNAME = ".qc"
_DERIVED_SUFFIXES = ("_avg", "_qcfilt", "_multisource")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ScanQCError(Exception):
    """QC unavailable for this scan. ``code`` is a stable identifier."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.Lock()
        return lk


def _check_raw_ti3(ti3_path: Path) -> None:
    if ti3_path.suffix != ".ti3" or ti3_path.stem.endswith(_DERIVED_SUFFIXES):
        raise ScanQCError("NOT_A_RAW_SCAN", f"not a raw scan ti3: {ti3_path.name}")
    if not ti3_path.is_file():
        raise ScanQCError("SCAN_NOT_FOUND", f"scan not found: {ti3_path.name}")


def _cache_file(ti3_path: Path, sha: str, params: mq.QCParams) -> Path:
    return (ti3_path.parent / QC_DIRNAME
            / f"{ti3_path.stem}.{sha[:16]}.v{mq.QC_ALGO_VERSION}.{params.digest()}.json")


def _load_valid(cache: Path, sha: str, params: mq.QCParams) -> Optional[dict]:
    try:
        blob = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    rep = blob.get("report") or {}
    if (blob.get("ti3_sha256") == sha and rep.get("algo_version") == mq.QC_ALGO_VERSION
            and rep.get("report_format") == mq.REPORT_FORMAT
            and rep.get("params_digest") == params.digest()):
        return rep
    return None


def _write_atomic(cache: Path, payload: dict) -> None:
    cache.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache.with_name(f"{cache.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, cache)


def _prune_others(ti3_path: Path, keep: Path) -> None:
    qc_dir = ti3_path.parent / QC_DIRNAME
    for f in qc_dir.glob(f"{glob.escape(ti3_path.stem)}.*.json"):
        if f != keep:
            f.unlink(missing_ok=True)


def get_report(ti3_path: Path, params: Optional[mq.QCParams] = None) -> dict:
    """QC report of a raw scan ti3 (cached). Returns
    ``{"ti3_sha256", "cached": bool, "report": {...}}``.
    Raises :class:`ScanQCError` (scan missing / not raw / unreadable / not analysable)."""
    params = params or mq.QCParams()
    ti3_path = Path(ti3_path)
    _check_raw_ti3(ti3_path)
    try:
        data = ti3_path.read_bytes()
    except OSError as e:
        raise ScanQCError("UNREADABLE", f"unreadable ti3: {e}") from None
    sha = hashlib.sha256(data).hexdigest()
    cache = _cache_file(ti3_path, sha, params)
    rep = _load_valid(cache, sha, params)
    if rep is not None:
        return {"ti3_sha256": sha, "cached": True, "report": rep}
    with _lock_for(str(cache)):                      # one computation per (scan, content, params)
        rep = _load_valid(cache, sha, params)        # computed meanwhile by another request
        if rep is not None:
            return {"ti3_sha256": sha, "cached": True, "report": rep}
        try:
            rep = mq.analyze_ti3(data.decode("ascii", errors="replace"), params)
        except mq.QCInputError as e:
            raise ScanQCError(e.code, str(e)) from None
        try:
            _write_atomic(cache, {"ti3": ti3_path.name, "ti3_sha256": sha, "report": rep})
            _prune_others(ti3_path, cache)
        except OSError:
            logger.warning("measure QC cache not written for %s (report still returned)", ti3_path.name)
    return {"ti3_sha256": sha, "cached": False, "report": rep}


def cached_summary(ti3_path: Path, params: Optional[mq.QCParams] = None) -> Optional[dict]:
    """Summary of the QC report ONLY if a valid cache exists (never computes)."""
    params = params or mq.QCParams()
    ti3_path = Path(ti3_path)
    try:
        _check_raw_ti3(ti3_path)
        sha = hashlib.sha256(ti3_path.read_bytes()).hexdigest()
    except (ScanQCError, OSError):
        return None
    rep = _load_valid(_cache_file(ti3_path, sha, params), sha, params)
    return mq.summarize(rep) if rep is not None else None


def cached_report(ti3_path: Path, params: Optional[mq.QCParams] = None) -> Optional[dict]:
    """The full QC report ONLY if a valid cache exists (never computes)."""
    params = params or mq.QCParams()
    ti3_path = Path(ti3_path)
    try:
        _check_raw_ti3(ti3_path)
        sha = hashlib.sha256(ti3_path.read_bytes()).hexdigest()
    except (ScanQCError, OSError):
        return None
    return _load_valid(_cache_file(ti3_path, sha, params), sha, params)


def purge(ti3_path: Path) -> int:
    """Delete every cached report of this scan (e.g. when the scan is deleted)."""
    ti3_path = Path(ti3_path)
    n = 0
    for f in (ti3_path.parent / QC_DIRNAME).glob(f"{glob.escape(ti3_path.stem)}.*.json"):
        f.unlink(missing_ok=True)
        n += 1
    return n
