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

"""Publication of a validated measurement into ``measurements/`` (contract for imports).

Every ``measurements/*.ti3`` is an acquired scan for freeglaz: it is listed, counted,
proposed to the build (included by default) and the LAST one is what ``validate``
reads. So a ti3 may appear there only complete, checked, and with its measurement
record already in place:

1. the caller prepares and checks the reading OUTSIDE ``measurements/`` (its session
   folder) and hands over the staged file, the expected SAMPLE_IDs and the record;
2. :func:`publish_measurement` checks completeness (the exact set of expected
   SAMPLE_IDs: none missing, none unknown, no duplicate, no padding id 0) and the
   record against the ti3 (:func:`measurement_identity.identity_from_bytes`) — nothing
   is written before both pass;
3. the bytes are copied to ``<chart>/.publish/`` (same filesystem) and flushed;
4. the record is written to ``measurements/<stem>.json`` (temporary file + replace);
5. the ti3 is linked to ``measurements/<stem>.ti3`` LAST, without ever overwriting.

A failure in 3-5 removes what this call wrote: nothing becomes visible as a scan. A
crash between 4 and 5 leaves an orphan ``<stem>.json`` without ``<stem>.ti3``: no reader
sees it (they all list ``*.ti3``), its stem is never reused, and
:func:`find_orphan_records` lists it. Temporary names never end in ``.ti3``.

The ti3 bytes are published unchanged (no renumbering, no rewrite). Single backend
process per store assumed (an in-process lock serialises publications).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Collection, Mapping, Optional

from . import measurement_identity as mi

PUBLISH_DIR_NAME = ".publish"
PADDING_SAMPLE_ID = "0"
_LOCK = threading.Lock()


class PublishError(ValueError):
    """Publication refused. ``code`` is stable: measurement_incomplete |
    measurement_unreadable | measurement_staged_in_place | measurement_publish_conflict |
    measurement_record_* (from measurement_identity)."""

    def __init__(self, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.info = info


def _natural(s: str):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def check_complete(ti3_text: str, expected_sample_ids: Collection[str]) -> int:
    """Raises PublishError(measurement_incomplete) unless the ti3 holds EXACTLY the
    expected SAMPLE_IDs once each, without padding (id 0), with XYZ or Lab. Returns the
    number of readings. Completeness is a set equality, not a row count."""
    from .multipass import parse_ti3
    expected = {str(x) for x in expected_sample_ids}
    if not expected:
        raise ValueError("no expected SAMPLE_ID given")
    if PADDING_SAMPLE_ID in expected:
        raise ValueError("the padding id 0 cannot be an expected reading")
    try:
        _, fmt, rows = parse_ti3(ti3_text)
        i = fmt.index("SAMPLE_ID")
        ids = [r[i] for r in rows]
    except (ValueError, IndexError) as e:
        raise PublishError("measurement_unreadable", f"unreadable ti3 ({e or type(e).__name__})") from None
    counts = Counter(ids)
    duplicates = sorted((k for k, v in counts.items() if v > 1), key=_natural)
    missing = sorted(expected - counts.keys(), key=_natural)
    unexpected = sorted(counts.keys() - expected - {PADDING_SAMPLE_ID}, key=_natural)
    padding = counts.get(PADDING_SAMPLE_ID, 0)
    if duplicates or missing or unexpected or padding:
        raise PublishError("measurement_incomplete",
                           f"{len(missing)} missing, {len(unexpected)} unexpected, "
                           f"{len(duplicates)} duplicated, {padding} padding reading(s)",
                           missing=missing, unexpected=unexpected, duplicates=duplicates,
                           padding=padding, expected=len(expected), found=len(ids))
    cols = set(fmt)
    if not ({"XYZ_X", "XYZ_Y", "XYZ_Z"} <= cols or {"LAB_L", "LAB_A", "LAB_B"} <= cols):
        raise PublishError("measurement_incomplete", "the ti3 has neither XYZ nor Lab columns")
    return len(ids)


@dataclass(frozen=True)
class PublishedMeasurement:
    stem: str
    ti3_path: Path
    record_path: Path
    identity: mi.MeasurementIdentity
    n_readings: int


def _stem_taken(meas: Path, stem: str) -> bool:
    return any((meas / f"{stem}{ext}").exists() for ext in (".ti3", mi.RECORD_SUFFIX, ".cgats"))


def choose_stem(meas: Path, now: datetime) -> str:
    """``YYYYMMDD_HHMMSS`` (local time, like SOL scans), ``_N`` suffix when taken by a ti3,
    a record (even orphan) or a cgats."""
    base = now.strftime("%Y%m%d_%H%M%S")
    stem, n = base, 1
    while _stem_taken(meas, stem):
        stem, n = f"{base}_{n}", n + 1
    return stem


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_flushed(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def _link_no_overwrite(src: Path, dst: Path) -> None:
    """dst appears atomically with src's content, or FileExistsError. Never overwrites."""
    try:
        os.link(src, dst)
    except FileExistsError:
        raise
    except OSError:                       # filesystem without hard links
        if dst.exists():
            raise FileExistsError(str(dst)) from None
        os.replace(src, dst)
        return
    os.unlink(src)


def publish_measurement(chart_dir: Path, staged_ti3: Path, record: Mapping,
                        expected_sample_ids: Collection[str], *,
                        now: Optional[datetime] = None) -> PublishedMeasurement:
    """Publishes ``staged_ti3`` as ``<chart_dir>/measurements/<stem>.ti3`` with its record
    ``<stem>.json``. ``record`` holds the facts (method, instrument, acquisition, chart,
    raw_files); ``schema``, ``ti3`` and ``published_at`` are filled here. See the module
    docstring for the order and the failure handling."""
    chart_dir = Path(chart_dir)
    meas = chart_dir / "measurements"
    staged = Path(staged_ti3)
    if staged.resolve().parent == meas.resolve():
        raise PublishError("measurement_staged_in_place",
                           "the staged ti3 must be prepared outside measurements/")
    data = staged.read_bytes()
    n = check_complete(data.decode("ascii", errors="replace"), expected_sample_ids)
    sha = hashlib.sha256(data).hexdigest()
    now = now or datetime.now()
    with _LOCK:
        meas.mkdir(parents=True, exist_ok=True)
        stem = choose_stem(meas, now)
        rec = {k: v for k, v in dict(record).items() if k not in ("schema", "schema_version", "ti3")}
        rec = {"schema": mi.RECORD_SCHEMA, "schema_version": mi.RECORD_SCHEMA_VERSION,
               "ti3": {"name": f"{stem}.ti3", "sha256": sha}, **rec,
               "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        rec_bytes = json.dumps(rec, indent=1, ensure_ascii=False).encode("utf-8")
        try:
            identity = mi.identity_from_bytes(data, rec_bytes, chart_id=chart_dir.name)
        except mi.MeasurementRecordError as e:
            raise PublishError(e.code, e.message, **e.info) from None
        pub = chart_dir / PUBLISH_DIR_NAME
        pub.mkdir(exist_ok=True)
        tmp_ti3 = pub / f"{stem}.{secrets.token_hex(4)}.tmp"
        ti3_final = meas / f"{stem}.ti3"
        rec_final = mi.record_path(ti3_final)
        rec_tmp = meas / f".{rec_final.name}.{secrets.token_hex(4)}.tmp"
        record_written = False
        try:
            _write_flushed(tmp_ti3, data)                         # 3. staged copy
            _write_flushed(rec_tmp, rec_bytes)                    # 4. record
            os.replace(rec_tmp, rec_final)
            record_written = True
            _fsync_dir(meas)
            try:
                _link_no_overwrite(tmp_ti3, ti3_final)            # 5. the scan appears
            except FileExistsError:
                raise PublishError("measurement_publish_conflict",
                                   f"{ti3_final.name} appeared during the publication") from None
            _fsync_dir(meas)
        except BaseException:
            if record_written and rec_final.is_file() and rec_final.read_bytes() == rec_bytes:
                rec_final.unlink(missing_ok=True)
            raise
        finally:
            tmp_ti3.unlink(missing_ok=True)
            rec_tmp.unlink(missing_ok=True)
            try:
                pub.rmdir()                                       # only if empty
            except OSError:
                pass
    return PublishedMeasurement(stem, ti3_final, rec_final, identity, n)


def find_orphan_records(chart_dir: Path) -> list[Path]:
    """Records without their ti3 (publication interrupted between steps 4 and 5)."""
    meas = Path(chart_dir) / "measurements"
    if not meas.is_dir():
        return []
    return sorted(p for p in meas.glob(f"*{mi.RECORD_SUFFIX}")
                  if not p.name.startswith(".") and not p.with_suffix(".ti3").exists())


def sweep_publish_leftovers(chart_dir: Path, max_age_s: float = 2 * 3600) -> list[str]:
    """Removes stale temporary files of an interrupted publication (``.publish/*.tmp`` and
    ``measurements/.*.json.*.tmp``). Never touches a ``.ti3`` or a record."""
    removed: list[str] = []
    now = time.time()
    chart_dir = Path(chart_dir)
    candidates = list((chart_dir / PUBLISH_DIR_NAME).glob("*.tmp")) if (chart_dir / PUBLISH_DIR_NAME).is_dir() else []
    meas = chart_dir / "measurements"
    if meas.is_dir():
        candidates += [p for p in meas.glob(f".*{mi.RECORD_SUFFIX}.*.tmp")]
    for p in candidates:
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age_s:
                p.unlink()
                removed.append(p.name)
        except OSError:
            continue
    try:
        (chart_dir / PUBLISH_DIR_NAME).rmdir()                    # only if empty
    except OSError:
        pass
    return removed
