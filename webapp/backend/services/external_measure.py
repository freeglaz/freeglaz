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

"""External measurement (chartread-fgK) in the backend: sessions on disk, one live run.

**One run per backend.** The live :class:`~lib.z9_client.chartread_fgk.run.Run` belongs to
a dedicated pump thread, the only one that touches it: HTTP handlers hand it commands
through a queue and wait for the result. Across processes, the store lock and, when it
exists, the per-user lock keep a second freeglaz from starting another run.

**Commands are checked twice** against the chart's current state and its ``state_seq``
(the sequence number at which that state began): by the handler, then again on the pump
thread right before acting. A command built on an older state is refused with
``stale_command`` and does nothing. Operations on the session files (create, start,
correct, finalize, abandon) are serialised by one lock.

**Events.** Every change publishes a fresh snapshot of the chart, with a global,
increasing ``seq``. Publishing only stores it and wakes waiters (a condition variable):
the pump never waits for a reader. Each SSE connection waits on its own for a newer
snapshot, so two windows both receive every update; a slow one just skips to the
latest snapshot; the snapshot route is the polling fallback; ``Last-Event-ID`` (or
``?since=``) resumes after a reconnection.

**What leaves the backend** (snapshots, events, logs): never the instrument serial number
(only a fingerprint stays in the session files), never the engine path, never a store path.

**Publication** goes through ``measurement_publish.publish_measurement`` only, and only for
a complete session whose engine origin is ``approved`` (a trusted build found where freeglaz
ships it; the trusted list is checked again at publication): an engine pinned for
development, an untrusted binary in the shipped place, or a test engine, can run and save
sessions, never publish them as measurements.

**Start-up** reconciles the open sessions and closes interrupted runs; it never starts an
engine, so the instrument is not opened. **Shutdown** pauses the live run within the
budget (prompt-aware, then the graduated stop).
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import logging
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from lib.z9_client import cache
from lib.z9_client import measurement_identity as mi
from lib.z9_client import measurement_publish as mp
from lib.z9_client.argyll import instrument_env
from lib.z9_client.chartread_fgk import engine as fgk
from lib.z9_client.chartread_fgk import usb_presence
from lib.z9_client.chartread_fgk.prompts import KeyRefused
from lib.z9_client.chartread_fgk.run import Run
from lib.z9_client.chartread_fgk.session import (EngineSpec, InstrumentLock, LockBusy, LockSet,
                                                 ORIGIN_APPROVED, Session, SessionError, LOCK_NAME,
                                                 user_lock_path)

logger = logging.getLogger(__name__)

NOTICES_KEPT = 200
COMMAND_TIMEOUT_S = 5.0
_KEY_PROMPT_ACTIONS = {
    "calibration_prompt": ["calibrate", "abort_calibration", "pause"],
    "calibration_retry": ["calibrate", "abort_calibration", "pause"],
    "ready": ["prev", "next", "next_unread", "pause"],
    "unexpected": ["retry", "accept", "give_up", "pause"],
}
_RETRY_ACTIONS = ["retry", "give_up", "pause"]
_PUBLIC_PHASES = {"starting", "calibration", "calibrating", "calibrated", "ready", "saving", "saved",
                  "warning", "finishing"}
# Commands a user may send to a live run → Run.command names ("calibrate" maps to the
# calibration prompt's "continue"; there is no command that starts a reading).
_RUN_COMMANDS = {"calibrate", "abort_calibration", "prev", "next", "next_unread", "retry", "give_up",
                 "accept", "pause"}


class ExternalMeasureError(Exception):
    def __init__(self, status: int, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.status, self.code, self.message, self.info = status, code, message, info

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.info}


def _scrub(value):
    """Public copy: no store path, no home path (event and error texts may quote a file)."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in ("serial", "path")}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str):
        for root, label in ((str(cache.root_dir()), "<store>"), (str(Path.home()), "~")):
            if root and root != "/":
                value = value.replace(root, label)
    return value


# ─── providers (replaced by tests) ──────────────────────────────────────────
def default_engine() -> tuple[EngineSpec, dict]:
    """chartread-fgK as engine.engine_status finds and checks it; never the system chartread."""
    st = fgk.engine_status()
    return EngineSpec.from_status(st), instrument_env()


def default_locks() -> LockSet:
    locks = [("store", InstrumentLock(cache.root_dir() / LOCK_NAME))]
    path, scope = user_lock_path()
    if path is not None:
        locks.append((scope, InstrumentLock(path)))
    return LockSet(locks)


def default_lock_scope() -> str:
    return user_lock_path()[1]


# ─── broadcast ──────────────────────────────────────────────────────────────
class Broadcast:
    """Latest snapshot per chart + recent notices, a global increasing seq, and a condition
    readers wait on. Writers never block on readers.

    The seq starts at the wall clock in microseconds: it keeps growing ACROSS backend restarts,
    so a window that kept the last snapshot of a backend that died accepts the new backend's
    snapshots (clients keep the snapshot with the highest seq), and a command decided on the
    old state is refused as stale. (Starting at 0, the windows stayed frozen on the dead
    run, note 142.) Below 2**53: exact in JavaScript."""

    def __init__(self):
        self._cond = threading.Condition()
        self.seq = time.time_ns() // 1000
        self._rev: dict[str, int] = {}
        self.notices: deque = deque(maxlen=NOTICES_KEPT)

    def bump(self, chart_id: str, notices=()) -> int:
        with self._cond:
            self.seq += 1
            for n in notices:
                self.notices.append({**n, "seq": self.seq, "chart_id": chart_id})
            self._rev[chart_id] = self.seq
            self._cond.notify_all()
            return self.seq

    def rev(self, chart_id: str) -> int:
        with self._cond:
            return self._rev.get(chart_id, 0)

    def wait(self, chart_id: str, after: int, timeout: float) -> int:
        with self._cond:
            self._cond.wait_for(lambda: self._rev.get(chart_id, 0) > after, timeout)
            return self._rev.get(chart_id, 0)

    def recent(self, chart_id: str, after: int = 0) -> list[dict]:
        with self._cond:
            return [n for n in self.notices if n["chart_id"] == chart_id and n["seq"] > after]


@dataclass
class _Active:
    chart_id: str
    session: Session
    run: Run
    commands: "queue.Queue" = field(default_factory=queue.Queue)
    thread: Optional[threading.Thread] = None
    view: dict = field(default_factory=dict)            # replaced whole by the pump thread only
    prompt_key: Optional[tuple] = None


class ExternalMeasureService:
    def __init__(self, *, engine_provider: Callable = default_engine,
                 presence_provider: Callable = usb_presence.check_presence,
                 locks_provider: Callable = default_locks, lock_scope: Callable = default_lock_scope):
        self.engine_provider, self.presence_provider = engine_provider, presence_provider
        self.locks_provider, self.lock_scope = locks_provider, lock_scope
        self.broadcast = Broadcast()
        self.probe = usb_presence.PresenceProbe(presence_provider)
        self._ops = threading.Lock()                     # session-file operations, one at a time
        self._lock = threading.RLock()                   # self._active and the idle states
        self._active: Optional[_Active] = None
        self._idle_state: dict[str, tuple[str, int]] = {}
        self._presence_note: dict[str, str] = {}
        self._ctx_cache: dict[str, tuple[float, dict]] = {}

    # ─── lookups ────────────────────────────────────────────────────────────
    def _chart_dir(self, chart_id: str) -> Path:
        d = cache.locate_chart_dir(chart_id)
        if d is None:
            raise ExternalMeasureError(404, "chart_not_found", f"unknown chart {chart_id}")
        return d

    def active_chart(self) -> Optional[str]:
        with self._lock:
            return self._active.chart_id if self._active else None

    def is_running(self, chart_id: str) -> bool:
        return self.active_chart() == chart_id

    # ─── context shown before and during a measurement ─────────────────────
    CONTEXT_TTL_S = 2.0
    # USB presence, through self.probe (one listing at a time, on its own thread):
    PRESENCE_FRESH_S = 1.0           # a verdict this recent is shared (windows, SSE, the run's watch)
    PRESENCE_STALE_S = 5.0           # while a listing is late, the last verdict is shown this long
    SNAPSHOT_PRESENCE_WAIT_S = 1.0   # the longest a snapshot without a live run waits for a listing
    START_PRESENCE_WAIT_S = 3.0      # start: a listing begun after the request, awaited this long

    def context(self, chart_id: str, presence_wait_s: float = SNAPSHOT_PRESENCE_WAIT_S) -> dict:
        """What the interface needs around the session: the chart's instrument and size, the
        conditions it may be read under, whether the engine is there (and publishable), whether
        the instrument is plugged in. Passive only (no instrument opened). Everything but the
        presence is cached 2 s.

        A change of presence alone publishes no event: while it is open on a chart without a
        live run, the wizard reads the snapshot again every 2 s (createPresenceWatch in the
        frontend). The presence comes from the probe: a verdict less than PRESENCE_FRESH_S old,
        or a new listing awaited up to ``presence_wait_s``; if it is late, the last verdict
        (up to PRESENCE_STALE_S old) or ``checking``. Never under a lock of this service."""
        base = self._static_context(chart_id)
        presence = None
        if base["model"] in mi.INSTRUMENT_MODELS:
            p = self.probe.get(base["model"], fresh_s=self.PRESENCE_FRESH_S, wait_s=presence_wait_s,
                               stale_s=self.PRESENCE_STALE_S)
            presence = ({"status": p.status, "family_only": p.family_only,
                         "instruments": [x.argyll_name for x in p.instruments]} if p is not None else
                        {"status": usb_presence.CHECKING, "family_only": False, "instruments": []})
        return {**base, "presence": presence}

    def _static_context(self, chart_id: str) -> dict:
        now = time.monotonic()
        hit = self._ctx_cache.get(chart_id)
        if hit and now - hit[0] < self.CONTEXT_TTL_S:
            return hit[1]
        d = self._chart_dir(chart_id)
        try:
            desc = json.loads((d / "chart.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            desc = {}
        model = (desc.get("instrument") or {}).get("key")
        m = mi.INSTRUMENT_MODELS.get(model) if model else None
        options = ([{"option": opt, "condition": cond} for opt, cond in mi.CHARTREAD_FILTERS.items()
                    if cond in m.conditions] if m and m.filter_selectable else [])
        try:
            spec, _ = self.engine_provider()
            engine = {"ok": True, "origin": spec.origin, "publishable": spec.origin == ORIGIN_APPROVED,
                      "argyll_version": spec.argyll_version, "patch_version": spec.patch_version}
        except SessionError as e:
            engine = {"ok": False, "code": e.code}
        geom = desc.get("geometry") or {}
        ctx = {"model": model, "filter_options": options, "filter_required": bool(options),
               "chart": {"patch_count": geom.get("patch_count"), "n_strips": geom.get("total_strips")},
               "engine": engine, "lock_scope": self.lock_scope()}
        self._ctx_cache[chart_id] = (now, ctx)
        return ctx

    # ─── state ──────────────────────────────────────────────────────────────
    def _idle(self, chart_id: str, session: Optional[Session]) -> tuple[str, list[str]]:
        if session is None:
            last = Session.sessions(self._chart_dir(chart_id))
            state = last[-1].data["status"] if last else "none"
            return state, ["create"]
        sm = session.summary()
        if sm["complete"]:
            return "complete", ["finalize", "correct", "abandon"]
        return "idle", ["start", "abandon"] + (["correct"] if sm["passes_saved"] else [])

    def _idle_seq(self, chart_id: str, state: str) -> int:
        """state_seq of a chart without a live run: the seq at which that state was entered."""
        with self._lock:
            prev = self._idle_state.get(chart_id)
            if prev is None or prev[0] != state:
                seq = self.broadcast.bump(chart_id)
                self._idle_state[chart_id] = (state, seq)
                return seq
            return prev[1]

    def snapshot(self, chart_id: str, presence_wait_s: Optional[float] = None) -> dict:
        """The state and its context. During a live run the context never waits for a USB
        listing (the run watches the instrument itself); otherwise up to
        ``presence_wait_s``, SNAPSHOT_PRESENCE_WAIT_S by default."""
        st = self._state(chart_id)
        if presence_wait_s is None:
            presence_wait_s = 0.0 if st["running"] else self.SNAPSHOT_PRESENCE_WAIT_S
        return {**st, "context": self.context(chart_id, presence_wait_s)}

    def _state(self, chart_id: str) -> dict:
        """The snapshot without its context: what commands are checked against. It never waits
        for a USB listing, so neither does a command (pause) nor the pump thread."""
        d = self._chart_dir(chart_id)
        with self._lock:
            act = self._active if self._active and self._active.chart_id == chart_id else None
            if act is not None:
                return {**act.view, "chart_id": chart_id, "seq": self.broadcast.rev(chart_id), "running": True}
        session = Session.find_open(d)
        state, actions = self._idle(chart_id, session)
        state_seq = self._idle_seq(chart_id, state)
        return {"chart_id": chart_id, "seq": max(self.broadcast.rev(chart_id), state_seq), "running": False,
                "state": state, "state_seq": state_seq, "actions": actions, "prompt": None,
                "session": _scrub(session.summary()) if session else None,
                "publishable": bool(session and session_publishable(session)),
                "lock_scope": self.lock_scope(), "presence": self._presence_note.get(chart_id),
                "notices": self.broadcast.recent(chart_id)[-20:],
                "last_session": _scrub(last.summary()) if session is None and (last := self._last_session(chart_id))
                else None}

    def _last_session(self, chart_id: str) -> Optional[Session]:
        sessions = Session.sessions(self._chart_dir(chart_id))
        return sessions[-1] if sessions else None

    def _check(self, chart_id: str, action: str, expect_state: Optional[str], seq: Optional[int],
               snap: Optional[dict] = None) -> dict:
        snap = snap or self._state(chart_id)
        if expect_state != snap["state"] or seq is None or seq < snap["state_seq"] or seq > self.broadcast.seq:
            raise ExternalMeasureError(409, "stale_command", "the measurement state changed; reload it",
                                       state=snap["state"], state_seq=snap["state_seq"], seq=snap["seq"])
        if action not in snap["actions"]:
            raise ExternalMeasureError(409, "command_not_allowed", f"{action!r} is not allowed now",
                                       state=snap["state"], actions=snap["actions"])
        return snap

    # ─── session operations ─────────────────────────────────────────────────
    def create(self, chart_id: str, filter_option: Optional[str] = None) -> dict:
        """Create the chart's session, or return the open one (never a second)."""
        d = self._chart_dir(chart_id)
        with self._ops:
            if Session.find_open(d) is None:
                try:
                    spec, _ = self.engine_provider()
                except SessionError as e:
                    raise ExternalMeasureError(409, e.code, e.message) from None
                try:
                    key = (json.loads((d / "chart.json").read_text()).get("instrument") or {}).get("key")
                    Session.create(d, model=key or "", filter_option=filter_option, engine=spec)
                except SessionError as e:
                    raise ExternalMeasureError(409, e.code, e.message) from None
                except (OSError, ValueError) as e:
                    raise ExternalMeasureError(409, "chart_unreadable", str(e)) from None
                logger.info("external measure: session created for %s", chart_id)
            self._changed(chart_id)
        return self.snapshot(chart_id)

    def _changed(self, chart_id: str, notices=()) -> None:
        """A session-file operation happened: a new state_seq for the next snapshot."""
        with self._lock:
            self._idle_state.pop(chart_id, None)
        self.broadcast.bump(chart_id, notices)

    def _open_session(self, chart_id: str) -> Session:
        s = Session.find_open(self._chart_dir(chart_id))
        if s is None:
            raise ExternalMeasureError(404, "session_not_found", "no open measurement session on this chart")
        return s

    def _refuse_if_active(self) -> None:
        with self._lock:
            if self._active is not None:
                raise ExternalMeasureError(409, "run_active", "a measurement is already running",
                                           chart_id=self._active.chart_id)

    def _confirm_presence(self, model: str) -> usb_presence.Presence:
        """The authoritative check before an engine is started: a listing begun after this
        call, awaited up to START_PRESENCE_WAIT_S outside every lock."""
        p = self.probe.get(model, since=time.monotonic(), wait_s=self.START_PRESENCE_WAIT_S)
        if p is None:
            return usb_presence.Presence(usb_presence.ERROR, model, detail="the USB listing did not answer "
                                         f"within {self.START_PRESENCE_WAIT_S:.0f} s")
        return p

    def _run_presence(self, model: str) -> usb_presence.Presence:
        """The live run's watch, on the pump thread: never waits. The last verdict (a new
        listing starts in the background once it is PRESENCE_FRESH_S old), or ``checking``."""
        p = self.probe.get(model, fresh_s=self.PRESENCE_FRESH_S, stale_s=self.PRESENCE_STALE_S)
        return p if p is not None else usb_presence.Presence(usb_presence.CHECKING, model)

    def start(self, chart_id: str, expect_state: Optional[str], seq: Optional[int]) -> dict:
        """Start (or resume, -r -N -K) the engine for the chart's open session.

        Only with the chart's instrument confirmed: chartread-fgK opens the first instrument
        ArgyllCMS finds (-c 1), so absent, another model, several, and a listing that fails
        or does not answer in time are all refused (409 ``instrument_<status>``). The listing
        is awaited before the session lock is taken; the state is checked again under it."""
        self._refuse_if_active()
        self._check(chart_id, "start", expect_state, seq)
        model = self._open_session(chart_id).model
        try:
            spec, env = self.engine_provider()
        except SessionError as e:
            raise ExternalMeasureError(409, e.code, e.message) from None
        pres = self._confirm_presence(model)
        self._presence_note[chart_id] = pres.status
        if not pres.present:
            raise ExternalMeasureError(409, f"instrument_{pres.status}", pres.detail)
        with self._ops:
            self._refuse_if_active()
            self._check(chart_id, "start", expect_state, seq)
            s = self._open_session(chart_id)
            locks = self.locks_provider()
            try:
                run = Run.start(s, spec, lock=locks, env=env, presence=lambda: self._run_presence(s.model))
            except LockBusy as e:
                holder = e.info.get("holder") or {}
                raise ExternalMeasureError(409, "instrument_busy", "another measurement holds the instrument",
                                           scope=e.info.get("scope"), chart_id=holder.get("chart_id")) from None
            except SessionError as e:
                raise ExternalMeasureError(409, e.code, e.message) from None
            act = _Active(chart_id, s, run)
            with self._lock:
                self._active = act
                self._idle_state.pop(chart_id, None)
            self._publish(act, [])
            act.thread = threading.Thread(target=self._pump, args=(act,), name="external-measure", daemon=True)
            act.thread.start()
            logger.info("external measure: run %d started on %s (resume=%s)", run.k, chart_id,
                        s.data["runs"][-1]["resume"])
        return self.snapshot(chart_id)

    def command(self, chart_id: str, command: str, expect_state: Optional[str], seq: Optional[int]) -> dict:
        if command not in _RUN_COMMANDS:
            raise ExternalMeasureError(422, "command_unknown", f"unknown command {command!r}")
        with self._lock:
            act = self._active if self._active and self._active.chart_id == chart_id else None
        if act is None:
            raise ExternalMeasureError(409, "run_not_active", "no measurement is running on this chart")
        self._check(chart_id, command, expect_state, seq)

        def apply(run: Run):
            snap = self._state(chart_id)                      # again, on the pump thread
            self._check(chart_id, command, expect_state, seq, snap)
            run.command(command)
            return True
        self._submit(act, apply)
        return self.snapshot(chart_id)

    def correct(self, chart_id: str, pass_label: str, expect_state: Optional[str], seq: Optional[int]) -> dict:
        with self._ops:
            self._check(chart_id, "correct", expect_state, seq)
            s = self._open_session(chart_id)
            try:
                s.remove_pass(pass_label, self.locks_provider())
            except LockBusy:
                raise ExternalMeasureError(409, "instrument_busy", "another measurement holds the lock") from None
            except SessionError as e:
                raise ExternalMeasureError(409, e.code, e.message) from None
            self._changed(chart_id, [{"kind": "corrected", "pass": pass_label, "t": time.monotonic()}])
        return self.snapshot(chart_id)

    def abandon(self, chart_id: str, expect_state: Optional[str], seq: Optional[int]) -> dict:
        with self._ops:
            self._check(chart_id, "abandon", expect_state, seq)
            s = self._open_session(chart_id)
            try:
                s.abandon(self.locks_provider())
            except LockBusy:
                raise ExternalMeasureError(409, "instrument_busy", "another measurement holds the lock") from None
            self._changed(chart_id, [{"kind": "abandoned", "t": time.monotonic()}])
            logger.info("external measure: session abandoned on %s", chart_id)
        return self.snapshot(chart_id)

    # ─── publication ────────────────────────────────────────────────────────
    def finalize(self, chart_id: str, expect_state: Optional[str], seq: Optional[int]) -> dict:
        """Publish the complete session through measurement_publish (the only contract)."""
        d = self._chart_dir(chart_id)
        with self._ops:
            self._check(chart_id, "finalize", expect_state, seq)
            s = self._open_session(chart_id)
            if not session_publishable(s):
                raise ExternalMeasureError(409, "engine_not_approved",
                                           "measured with a development or test engine: not publishable",
                                           origin=s.data["engine"]["origin"])
            locks = self.locks_provider()
            try:
                locks.acquire({"session_id": s.data["session_id"], "chart_id": chart_id, "op": "finalize"})
            except LockBusy:
                raise ExternalMeasureError(409, "instrument_busy", "another measurement holds the lock") from None
            try:
                try:
                    s.reconcile()
                except SessionError as e:
                    raise ExternalMeasureError(409, e.code, e.message) from None
                v = s.validated_state
                if v is None or not v.complete:
                    raise ExternalMeasureError(409, "measurement_incomplete", "not every strip pass is saved",
                                               passes_saved=len(v.passes) if v else 0)
                record = build_record(s, v)
                try:
                    pub = mp.publish_measurement(d, s.validated, record, list(s.plan.patches))
                except mp.PublishError as e:
                    raise ExternalMeasureError(409, e.code, e.message, **_scrub(e.info)) from None
                s.data["status"] = "published"
                s.data["published"] = {"stem": pub.stem, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                       "ti3": pub.ti3_path.name, "n_readings": pub.n_readings}
                s._save()
            finally:
                locks.release()
            self._changed(chart_id, [{"kind": "published", "stem": pub.stem, "t": time.monotonic()}])
            logger.info("external measure: %s published as %s", chart_id, pub.stem)
        return {**self.snapshot(chart_id), "published": {"stem": pub.stem, "n_readings": pub.n_readings,
                                                         "identity": pub.identity.to_dict()}}

    # ─── the pump thread ────────────────────────────────────────────────────
    def _submit(self, act: _Active, fn: Callable[[Run], object]):
        fut: cf.Future = cf.Future()
        act.commands.put((fn, fut))
        try:
            return fut.result(COMMAND_TIMEOUT_S)
        except (ExternalMeasureError, cf.TimeoutError):
            raise
        except KeyRefused as e:
            raise ExternalMeasureError(409, "command_refused", str(e), reason=e.code) from None
        except SessionError as e:
            raise ExternalMeasureError(409, e.code, e.message) from None

    def _pump(self, act: _Active) -> None:
        run = act.run
        try:
            while run.ended is None:
                while True:
                    try:
                        fn, fut = act.commands.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        fut.set_result(fn(run))
                    except BaseException as e:           # handed to the waiting handler
                        fut.set_exception(e)
                notices = run.poll(0.05)
                self._publish(act, notices)
        except Exception:                                # never leave a run behind
            logger.exception("external measure: pump failed on %s", act.chart_id)
            try:
                run.stop_now("error:driver_failure")
            except Exception:
                logger.exception("external measure: emergency stop failed")
        finally:
            with self._lock:
                self._active = None
                self._idle_state.pop(act.chart_id, None)
            while True:                                  # nobody waits for ever
                try:
                    _, fut = act.commands.get_nowait()
                except queue.Empty:
                    break
                fut.set_exception(ExternalMeasureError(409, "run_ended", "the run has ended"))
            self.broadcast.bump(act.chart_id, [{"kind": "run_ended", "end": (run.ended or {}).get("end"),
                                                "t": time.monotonic()}])

    def _publish(self, act: _Active, notices: list) -> None:
        snap = act.run.snapshot()
        p = act.run.gate.prompt
        if snap["stopping"]:
            state, actions = "stopping", []
        else:
            state = snap["phase"] if snap["phase"] in _PUBLIC_PHASES else "starting"
            actions = list(_KEY_PROMPT_ACTIONS.get(p.kind, _RETRY_ACTIONS)) if p is not None else ["pause"]
        key = (state, (p.kind, p.t) if p is not None else None)       # one key per prompt shown
        public = [_scrub(n) for n in notices]
        if key == act.prompt_key and not public and act.view:
            return
        seq = self.broadcast.bump(act.chart_id, public)
        state_seq = seq if key != act.prompt_key else act.view.get("state_seq", seq)
        act.prompt_key = key
        act.view = {"state": state, "state_seq": state_seq, "actions": actions,
                    "prompt": {"kind": p.kind, **_scrub(dict(p.data))} if p else None,
                    "pass": snap["pass"], "acquired_not_saved": snap["acquired_not_saved"],
                    "error": _scrub(snap["error"]), "session": _scrub(snap["session"]),
                    "publishable": session_publishable(act.session),
                    "lock_scope": self.lock_scope(), "presence": self._presence_note.get(act.chart_id),
                    "notices": self.broadcast.recent(act.chart_id)[-20:]}

    # ─── backend life ───────────────────────────────────────────────────────
    def boot_reconcile(self) -> list[dict]:
        """Open sessions reconciled, interrupted runs closed. No engine is started: the
        instrument is not opened. (Start-up: the probe lists again if an earlier shutdown of
        this process closed it.)"""
        self.probe.reopen()
        report = []
        base = cache.charts_root()
        for serial_dir in sorted(base.iterdir()) if base.is_dir() else []:
            for chart_dir in sorted(serial_dir.iterdir()) if serial_dir.is_dir() else []:
                for s in Session.sessions(chart_dir):
                    if s.data["status"] != "open":
                        continue
                    locks = self.locks_provider()
                    try:
                        locks.acquire({"session_id": s.data["session_id"], "chart_id": s.chart_id, "op": "boot"})
                    except LockBusy:
                        report.append({"chart_id": s.chart_id, "skipped": "lock busy"})
                        continue
                    try:
                        actions = s.reconcile()
                        report.append({"chart_id": s.chart_id, "session_id": s.data["session_id"],
                                       "actions": actions})
                    except SessionError as e:
                        report.append({"chart_id": s.chart_id, "error": e.code})
                    finally:
                        locks.release()
        for r in report:
            logger.info("external measure at start-up: %s", _scrub(r))
        return report

    def shutdown(self, budget: float = 4.0) -> Optional[dict]:
        """Pause the live run within ``budget`` seconds (prompt-aware pause, then the
        graduated stop); returns how it ended. Then no USB listing is started any more, and
        the one running, if any, is killed."""
        try:
            return self._shutdown_run(budget)
        finally:
            self.probe.close()

    def _shutdown_run(self, budget: float) -> Optional[dict]:
        with self._lock:
            act = self._active
        if act is None:
            return None
        t0 = time.monotonic()
        fut: cf.Future = cf.Future()
        act.commands.put((lambda run: run.shutdown(max(0.5, budget - 0.8)), fut))
        if act.thread is not None:
            act.thread.join(max(0.1, budget - (time.monotonic() - t0)))
        if act.thread is not None and act.thread.is_alive():   # last resort: the whole group
            try:
                os.killpg(act.run.proc.pgid, 9)
            except (ProcessLookupError, PermissionError):
                pass
        logger.info("external measure: shut down in %.2f s", time.monotonic() - t0)
        return act.run.ended


def session_publishable(s: Session) -> bool:
    """Measured with a trusted build of the engine, found where freeglaz ships it. The origin
    in session.json is not enough on its own (a file in the store): the sha256 it records is
    checked against the trusted builds in freeglaz's code, now."""
    eng = s.data["engine"]
    return (eng.get("origin") == ORIGIN_APPROVED and eng.get("source") == "bundle"
            and fgk.trusted_build(eng.get("sha256")))


def build_record(s: Session, v) -> dict:
    """The measurement record of a session, from what the backend recorded itself. The
    serial number never leaves the session: only a fingerprint does."""
    runs = s.data["runs"]
    argv = list(runs[0]["argv"])
    argv[0] = os.path.basename(argv[0])
    eng = s.data["engine"]
    serial = s.data["instrument"].get("serial")
    return {
        "method": "external",
        "instrument": {"model": s.model, "serial": None,
                       "unit_fingerprint": hashlib.sha256(serial.encode()).hexdigest()[:16] if serial else None},
        "acquisition": {
            "tool": "chartread", "argv": argv, "measured_at": runs[0]["started_at"],
            "software": {"argyllcms": eng.get("argyll_version"),
                         "chartread_patch": (f"v{eng.get('patch_version')} {(eng.get('patch_sha256') or '')[:12]}"
                                             if eng.get("patch_version") else None)},
            "engine": {"origin": eng["origin"], "source": eng["source"], "sha256": eng["sha256"]},
            "runs": [{k: r.get(k) for k in ("k", "started_at", "ended_at", "end", "resume")} for r in runs],
            "session_id": s.data["session_id"]},
        "chart": {"chart_id": s.chart_id, "ti2_sha256": s.data["chart"]["ti2_sha256"]},
        "raw_files": [{"path": f"external/{s.data['session_id']}/validated.ti3", "sha256": v.sha256}],
        "accepted_warnings": s.data.get("accepted_warnings") or [],
        "corrections": [{"pass": c["pass"], "at": c["at"]} for c in s.data.get("corrections") or []],
    }


_service: Optional[ExternalMeasureService] = None
_service_lock = threading.Lock()


def get_service() -> ExternalMeasureService:
    global _service
    with _service_lock:
        if _service is None:
            _service = ExternalMeasureService()
        return _service


def set_service(svc: Optional[ExternalMeasureService]) -> None:
    """Tests: install a service with injected providers (engine, presence, locks)."""
    global _service
    with _service_lock:
        _service = svc
