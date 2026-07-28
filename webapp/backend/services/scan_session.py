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

"""PERSISTENT state of a ROLL multi-scan session (backend PHASE 1).

PER-SESSION disk marker (`scan_session_<chart_id>_<ts>.json`), NEVER a
rewritten global file (§3.2) → survives app restart. "Only one active
session at a time" lock: the Z9 spectro is a single resource and we do not mix
two physical charts. Mirrors the `refine_state` pattern (_atomic_write_json,
JSON marker, stages).

Design ref: Docs/Design_Webapp_Scan_Rouleau_Multi_Scan.md §3.
⚠️ The "chart moved" warning (§3.1) is a FRONTEND decision (human
assertion) — this backend EXPOSES the state (stage=awaiting_next = resumable
session), it does not decide the physical reality.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lib.z9_client import cache

SCAN_SESSION_SCHEMA = 1

# ── Stages ───────────────────────────────────────────────────────────────────
# The session = lightweight transient lock around the scan (the DURABLE lives on disk:
# measurements/*.ti3 + scan_state.json). The print/dry/backfeed coupled stages of the
# online model (printing/drying/awaiting_backfeed) have been REMOVED: online SOL =
# confirmed dead end → they were neither written nor read, they will not come back.
STAGE_SCANNING = "scanning"
STAGE_AWAITING_NEXT = "awaiting_next"
# A SINGLE neutral terminal state: the session = lock; closing it = releasing the lock,
# whether we succeeded (profile built) or gave up. No more done/abandoned (the "done"
# was never reached, "abandoned" misrepresented the "I'm finished" case).
STAGE_CLOSED = "closed"

_TERMINAL = {STAGE_CLOSED}
_ALL_STAGES = {STAGE_SCANNING, STAGE_AWAITING_NEXT, STAGE_CLOSED}

# Idle TTL: an ``awaiting_next`` session (between multi-scans) untouched for longer
# than this is an ORPHAN → auto-closed on the next lock check and at boot. Chosen
# well above a normal inter-scan gap so a DELIBERATE multi-scan is never cut short.
# NB: only ``awaiting_next`` is expired here — a ``scanning`` session may legitimately
# run for a long time (2000+ patches) and is resolved by the scan job (success →
# awaiting_next, error → close) or by boot cleanup (crash orphan).
SCAN_SESSION_TTL_S = 900.0   # 15 min


class SessionLockError(RuntimeError):
    """A session is already active ("only one at a time" lock)."""


# ── Location / IO (per-serial) ───────────────────────────────────────────
# Sessions live under scan_sessions/<serial>/ (the spectro is on ONE Z9 —
# 1 spectro = 1 Z9). WRITE: serial known (store.get_serial(z9)). READ by
# session_id: FS enumeration over the <serial>/ folders (unique session_id), like
# locate_chart_dir for charts → no need for the serial or a connected Z9.
def _sessions_root() -> Path:
    return cache.root_dir() / "scan_sessions"


def _sessions_dir(serial: str) -> Path:
    if not serial:
        raise ValueError("_sessions_dir: serial requis (ex. 'CNXXXXXXXX')")
    return _sessions_root() / serial


def _session_path(session_id: str, serial: str) -> Path:
    return _sessions_dir(serial) / f"{session_id}.json"


def _locate_session_path(session_id: str) -> Optional[Path]:
    """Locate scan_sessions/<serial>/<session_id>.json by FS enumeration
    (globally unique session_id) — client-free. None if not found."""
    base = _sessions_root()
    if not base.is_dir():
        return None
    for sdir in base.iterdir():
        if not sdir.is_dir():
            continue
        cand = sdir / f"{session_id}.json"
        if cand.is_file():
            return cand
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # atomic on the same FS


def _read(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


# ── Read ─────────────────────────────────────────────────────────────────
def list_sessions(serial: Optional[str] = None) -> list[dict]:
    """Sessions of a given serial, or of ALL known Z9s (serial=None →
    multi-serial enumeration). V1 mono = a single serial, zero mixing."""
    if serial is not None:
        dirs = [_sessions_dir(serial)]
    else:
        base = _sessions_root()
        dirs = [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []
    out = []
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("scan_session_*.json")):
            s = _read(p)
            if s:
                out.append(s)
    return out


def read_session(session_id: str) -> Optional[dict]:
    p = _locate_session_path(session_id)   # FS enumeration (client-free)
    return _read(p) if p else None


def _idle_seconds(s: dict, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since ``updated_at`` (None if unparseable)."""
    ts = s.get("updated_at")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    ref = now or datetime.now(timezone.utc)
    return (ref - dt).total_seconds()


def _is_orphan(s: dict, ttl: float, now: Optional[datetime] = None) -> bool:
    """An ``awaiting_next`` session idle longer than ``ttl`` = forgotten multi-scan
    lock. Never expires a ``scanning`` session (see SCAN_SESSION_TTL_S note)."""
    if s.get("stage") != STAGE_AWAITING_NEXT:
        return False
    idle = _idle_seconds(s, now)
    return idle is not None and idle > ttl


def active_session(serial: Optional[str] = None, *,
                   ttl: Optional[float] = SCAN_SESSION_TTL_S,
                   now: Optional[datetime] = None) -> Optional[dict]:
    """The non-terminal session, if there is one — EXCLUDING idle orphans, which are
    auto-closed here (``awaiting_next`` untouched for more than ``ttl``). Auto-release
    preserves recent/deliberate multi-scan but frees forgotten locks (the systematic
    orphan bug). Pass ``ttl=None`` to disable expiry (raw view).

    "Only one active" lock: PER SERIAL on the creation side (create_session passes the
    connected serial). On the read side without a client (status/abandon), serial=None →
    global view, but these routes re-filter by chart_id so never any inter-chart leak."""
    for s in list_sessions(serial):
        if s.get("stage") in _TERMINAL:
            continue
        if ttl is not None and _is_orphan(s, ttl, now):
            try:
                close_session(s["session_id"])   # auto-release the forgotten lock
            except (OSError, KeyError):
                pass
            continue
        return s
    return None


def cleanup_orphans(serial: Optional[str] = None) -> int:
    """BOOT cleanup: close EVERY non-terminal session. After a backend restart no scan
    thread is alive, so any active session (``scanning`` crash orphan OR forgotten
    ``awaiting_next``) is stale by definition. Returns the number closed."""
    n = 0
    for s in list_sessions(serial):
        if s.get("stage") not in _TERMINAL:
            try:
                close_session(s["session_id"])
                n += 1
            except (OSError, KeyError):
                pass
    return n


# ── Write ─────────────────────────────────────────────────────────────────
def create_session(*, chart_id: str, serial: str,
                   door: int = 2, stage: str = STAGE_SCANNING,
                   now: Optional[datetime] = None) -> dict:
    """Create a session under scan_sessions/<serial>/ (serial REQUIRED — the
    session belongs to the connected Z9, via store.get_serial(z9)). REFUSES
    (SessionLockError) if an active session already exists FOR THIS SERIAL."""
    act = active_session(serial)
    if act is not None:
        raise SessionLockError(
            f"A scan session is already active (chart {act.get('chart_id')}, "
            f"stage={act.get('stage')}). Close it (done) or discard it first.")
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    session_id = f"scan_session_{chart_id}_{ts}"
    data = {
        "schema": SCAN_SESSION_SCHEMA,
        "session_id": session_id,
        "chart_id": chart_id,
        "serial": serial,
        "door": door,
        "stage": stage,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "scans": [],                 # [{ti3, n_patches, scanned_at, kept}]
    }
    _atomic_write_json(_session_path(session_id, serial), data)
    return data


def _save(s: dict) -> dict:
    # serial carried by the session → per-serial write without enumeration.
    s["updated_at"] = _now_iso()
    _atomic_write_json(_session_path(s["session_id"], s["serial"]), s)
    return s


def update_stage(session_id: str, stage: str) -> dict:
    if stage not in _ALL_STAGES:
        raise ValueError(f"stage inconnu : {stage}")
    s = read_session(session_id)
    if s is None:
        raise KeyError(session_id)
    s["stage"] = stage
    return _save(s)


def add_scan(session_id: str, *, ti3: str, n_patches: Optional[int] = None,
             scanned_at: Optional[str] = None) -> dict:
    """Add a successful scan (ti3 already written in measurements/) → stage=awaiting_next."""
    s = read_session(session_id)
    if s is None:
        raise KeyError(session_id)
    s["scans"].append({
        "ti3": ti3, "n_patches": n_patches,
        "scanned_at": scanned_at or _now_iso(), "kept": True,
    })
    s["stage"] = STAGE_AWAITING_NEXT
    return _save(s)


def close_session(session_id: str) -> dict:
    """Close the session (→ STAGE_CLOSED) → releases the "only one active" lock. A single
    terminal: whether we built the profile or gave up, closing = releasing the lock."""
    return update_stage(session_id, STAGE_CLOSED)
