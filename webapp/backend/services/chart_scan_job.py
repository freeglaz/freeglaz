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

"""NON-BLOCKING background job for the SOL scan of a chart (backend PHASE 1).

Rework of the old BLOCKING `scan_chart`: `sol_native.measure` (~10-25 min) NEVER
holds an HTTP handler (cause of the "gray screen" freeze). Background thread + pollable status,
like `refine_scan_job`. GLOBAL singleton: only one SOL scan at a time (the Z9 spectro =
single resource).

HARD guards on the server side (not just cosmetic UI — design ref §1/§3):
- **anti-concurrent**: refuse a 2nd scan if one is running (ScanBusyError);
- **cooldown ≥30 s** between two SOL ops (success OR failure/OP_CANCELLED) → anti-ASSERT
  firmware `8143-DFD2` (a close re-scan already triggered it) — ScanCooldownError;
- **ti3 written ONLY at OP_FINISHED_OK** (no half-written ti3 → no corruption
  if the app closes during the scan);
- **NO duration timeout, zero kill**: we poll until the Z9 concludes
  (OP_FINISHED_OK / error status / disconnection). A scan of any size runs to its
  end — the Z9 concludes, never a client clock (cf. sol_native.measure). On
  failure/error we CLOSE the session (no orphan lock left behind).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SOL_COOLDOWN_S = 30.0
# NO scan duration timeout: the Z9 concludes the scan (cf. sol_native.measure).
# A fixed cap would arbitrarily kill long scans (2000+ patches). The only time
# bound is the per-poll socket timeout inside measure() = disconnection detection.

_lock = threading.Lock()
_current: Optional[dict] = None              # current/last job (GLOBAL singleton)
_last_end_monotonic: Optional[float] = None  # end of the last scan → cooldown


class ScanBusyError(RuntimeError):
    """A SOL scan is already in progress (anti-concurrent)."""


class ScanCooldownError(RuntimeError):
    """SOL cooldown not elapsed (< 30 s since the last SOL op)."""

    def __init__(self, remaining: float):
        self.remaining = remaining
        super().__init__(f"SOL cooldown: retry in {remaining:.0f} s")


def reset() -> None:
    """Reset the singleton state (TESTS only: the module globals
    persist between tests → cooldown/anti-concurrent would pollute otherwise)."""
    global _current, _last_end_monotonic
    with _lock:
        _current = None
        _last_end_monotonic = None


def status() -> Optional[dict]:
    with _lock:
        return dict(_current) if _current else None


def is_busy() -> bool:
    with _lock:
        return _current is not None and _current.get("state") == "running"


def cooldown_remaining() -> float:
    with _lock:
        if _last_end_monotonic is None:
            return 0.0
        return max(0.0, SOL_COOLDOWN_S - (time.monotonic() - _last_end_monotonic))


def start(*, host: str, chart_id: str, fields: list, desc: dict,
          meas_dir: Path, session_id: Optional[str] = None) -> dict:
    """Start the scan in the background; returns a snapshot IMMEDIATELY.

    :raises ScanBusyError: a scan is already running.
    :raises ScanCooldownError: < 30 s since the last SOL op.
    """
    global _current
    with _lock:
        if _current is not None and _current.get("state") == "running":
            raise ScanBusyError("A SOL scan is already in progress")
        if _last_end_monotonic is not None:
            rem = SOL_COOLDOWN_S - (time.monotonic() - _last_end_monotonic)
            if rem > 0:
                raise ScanCooldownError(rem)
        _current = {
            "chart_id": chart_id, "session_id": session_id,
            "state": "running", "phase": "starting", "percent": 0,
            "ti3": None, "n_patches": None, "bands": None, "error": None,
        }
        snapshot = dict(_current)
    t = threading.Thread(
        target=_run, args=(host, chart_id, fields, desc, meas_dir, session_id),
        daemon=True)
    t.start()
    return snapshot


def _set(**kw) -> None:
    with _lock:
        if _current is not None:
            _current.update(**kw)


def _run(host: str, chart_id: str, fields: list, desc: dict,
         meas_dir: Path, session_id: Optional[str]) -> None:
    global _last_end_monotonic
    from lib.z9_client import sol_native
    from lib.z9_client.sol_ti3 import build_ti3_from_native_cgats

    def on_status(op: str, _f: dict) -> None:
        # honest progress: we surface the raw firmware phase (IDLE/SCANNING/…)
        pct = 10 if op == "SCANNING" else None
        with _lock:
            if _current is not None:
                _current["phase"] = op
                if pct is not None and pct > _current.get("percent", 0):
                    _current["percent"] = pct

    try:
        # ⚠️ HARDWARE ACT: NO duration timeout, zero kill — we wait for the Z9 to
        # conclude (OP_FINISHED_OK / error / disconnection). cf. sol_native.measure.
        cgats = sol_native.measure(host, fields=fields, on_status=on_status)
        # SUCCESS (OP_FINISHED_OK) ONLY → we write cgats + ti3.
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        meas_dir.mkdir(parents=True, exist_ok=True)
        # Uniqueness: two scans NEVER overwrite their ti3 (defensive; in prod the cooldown
        # ≥30 s already guarantees distinct timestamps — here we cover the same-second edge).
        base, n = ts, 1
        while (meas_dir / f"{base}.ti3").exists():
            base, n = f"{ts}_{n}", n + 1
        (meas_dir / f"{base}.cgats").write_bytes(cgats)
        ti3_path = meas_dir / f"{base}.ti3"
        res = build_ti3_from_native_cgats(cgats.decode("ascii", "replace"), desc, ti3_path)
        if session_id:
            try:
                from webapp.backend.services import scan_session
                scan_session.add_scan(session_id, ti3=ti3_path.name,
                                           n_patches=res.n_patches)
            except Exception:  # noqa: BLE001
                logger.exception("scan_session.add_scan failed (%s)", session_id)
        _set(state="done", phase="done", percent=100, ti3=ti3_path.name,
             n_patches=res.n_patches, bands=res.n_spectral_bands)
    except Exception as e:  # noqa: BLE001 — failure/OP_CANCELLED/disconnection: NO ti3 written
        logger.exception("chart scan failed (%s)", chart_id)
        _set(state="error", error=str(e))
        # Anti-orphan: a failed scan must NOT leave its session active (else the
        # "only one active" lock blocks every future scan). The ti3 of any prior
        # successful scan stays on disk (measurements/ = durable truth).
        if session_id:
            try:
                from webapp.backend.services import scan_session
                scan_session.close_session(session_id)
            except Exception:  # noqa: BLE001
                logger.exception("scan_session.close_session on error failed (%s)", session_id)
    finally:
        with _lock:
            _last_end_monotonic = time.monotonic()   # arm the cooldown (success OR failure)
