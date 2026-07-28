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

"""NON-BLOCKING background job for the ICC profile BUILD (colprof) of a chart.

Diagnosis 12/06: colprof on ~1400 measurements (-qh) takes **~2.5 min**. Held in a
synchronous request → the "Build" button seems frozen; if the request drops (reap/timeout/nav)
→ promise never resolved + residual partial ICC. SAME anti-pattern as the "gray screen" freeze.
→ Build = background thread + pollable status, like `chart_scan_job` (the SOL scan).

NOT a Z9 act (colprof + disk copy = software) → no SOL cooldown here. GLOBAL
singleton: only one build at a time (colprof = CPU-bound; two in parallel thrash).

TEST mode (`FREEGLAZ_PROFILE_BUILD_SYNC=1`): the build runs INLINE (no thread) →
deterministic, no race in the tests. The route then flattens the `result` (backward compat).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_current: Optional[dict] = None      # current/last job (GLOBAL singleton)


class ProfileBuildBusyError(RuntimeError):
    """A profile build is already in progress (anti-concurrent)."""


def reset() -> None:
    """Reset the singleton (TESTS: the globals persist between tests)."""
    global _current
    with _lock:
        _current = None


def status() -> Optional[dict]:
    with _lock:
        return dict(_current) if _current else None


def is_busy() -> bool:
    with _lock:
        return _current is not None and _current.get("state") == "running"


def _sync_mode() -> bool:
    return os.environ.get("FREEGLAZ_PROFILE_BUILD_SYNC") == "1"


def start(chart_id: str, runner: Callable[[Callable[[str], None]], dict]) -> dict:
    """Start the build in the background; returns a snapshot IMMEDIATELY. ``runner`` receives
    a ``set_phase(str)`` callback and returns the result dict. In sync mode, runs inline and
    returns the final state (done|error).

    :raises ProfileBuildBusyError: a build is already running.
    """
    global _current
    with _lock:
        if _current is not None and _current.get("state") == "running":
            raise ProfileBuildBusyError("a profile build is already in progress")
        _current = {"chart_id": chart_id, "state": "running", "phase": "preparing",
                    "result": None, "error": None}
        snapshot = dict(_current)
    if _sync_mode():
        _run(chart_id, runner)
        return status()
    threading.Thread(target=_run, args=(chart_id, runner), daemon=True).start()
    return snapshot


def _set_phase(phase: str) -> None:
    with _lock:
        if _current is not None and _current.get("state") == "running":
            _current["phase"] = phase


def _run(chart_id: str, runner: Callable[[Callable[[str], None]], dict]) -> None:
    try:
        result = runner(_set_phase)
        with _lock:
            if _current is not None:
                _current.update(state="done", phase="done", result=result, error=None)
    except Exception as e:  # noqa: BLE001 — build failure (concat/colprof) → error state + message
        logger.exception("profile build failed (%s)", chart_id)
        with _lock:
            if _current is not None:
                _current.update(state="error", phase="error", error=str(e))
