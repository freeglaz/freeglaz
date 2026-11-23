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

"""One live ``chartread-fgK`` process for a :class:`~.session.Session`.

:class:`Run` drives the process through the increment-2 pieces (PTY, transcript, prompt
parser, key gate, activity monitor) and hands every checkpoint to the session, which owns
everything durable. Nothing here writes a file except through the session and the run's
own log. Its owner calls :meth:`Run.poll` in a loop, from one thread.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from . import checkpoint as ck
from .engine import build_argv
from .prompts import KeyGate, KeyRefused, PromptParser
from .pty_process import ProcessGone, PtyProcess
from .session import EngineSpec, InstrumentLock, Session, SessionError, _iso
from .transcript import ActivityMonitor, Transcript

WAIT_PROMPTS = {"misread", "comms_failed", "strip_error", "instrument_error", "wrong_strip",
                "unexpected", "retry_prompt", "stopped"}
PHASE_LIMITS = {"starting": 30.0, "calibrating": 90.0, "saving": 30.0, "finishing": 30.0}
USER_COMMANDS = {"calibrate": "continue", "retry": "retry", "give_up": "give_up", "accept": "accept",
                 "prev": "prev", "next": "next", "next_unread": "next_unread", "abort_calibration": "abort"}


class Run:
    """One chartread-fgK process for a session, driven through the increment-2 pieces.
    ``poll()`` is called in a loop by its owner (the backend thread later, tests now)."""

    def __init__(self, session: Session, engine: EngineSpec, proc: PtyProcess, lock: InstrumentLock, k: int,
                 *, presence: Optional[Callable] = None, monitor: Optional[ActivityMonitor] = None,
                 auto_finish: bool = True, min_delay: float = 0.1, resend_after: float = 1.5,
                 phase_limits: Optional[dict] = None):
        self.session, self.engine, self.proc, self.lock, self.k = session, engine, proc, lock, k
        self.transcript = Transcript(session.dir / "logs" / f"run{k}.log")
        self.parser = PromptParser()
        self.gate = KeyGate(proc.write, min_delay=min_delay, resend_after=resend_after)
        self.monitor = monitor or ActivityMonitor()
        self.presence, self.auto_finish = presence, auto_finish
        self.phase_limits = {**PHASE_LIMITS, **(phase_limits or {})}
        self.phase, self.phase_since = "starting", time.monotonic()
        self.current_pass: Optional[str] = None
        self.read_before = False
        self.acquired: Optional[str] = None          # pass read, not yet saved
        self.stopping: Optional[str] = None
        self.stop_deadline: Optional[float] = None
        self.error: Optional[dict] = None
        self.finishing = False
        self.final_durability = "full"
        self._ck_warning: Optional[tuple[str, str]] = None
        self.notices: list[dict] = []
        self.ended: Optional[dict] = None
        self._next_cpu = self._next_presence = 0.0

    @classmethod
    def start(cls, session: Session, engine: EngineSpec, *, lock: InstrumentLock, env: dict, **kw) -> "Run":
        session.ensure_open()
        lock.acquire({"session_id": session.data["session_id"], "chart_id": session.chart_id, "op": "run"})
        try:
            actions = session.reconcile()
            session.check_run(engine)
            if (session.data.get("last_checkpoint") or {}).get("complete"):
                raise SessionError("session_complete", "every pass is already saved")
            resume = session.work_ti3.exists()
            argv = build_argv(engine.path, instrument=session.model, chart_base=session.chart_id,
                              filter_option=session.data["instrument"]["filter_option"],
                              resume=resume, no_initial_calibration=resume)
            if resume and "-r" not in argv:                  # never without -r over a work file
                raise SessionError("internal", "a work file exists: -r is mandatory")
            k = len(session.data["runs"]) + 1
            if session.data.get("suspended"):
                session.data["last_suspension"] = session.data.pop("suspended")
            session.data["suspended"] = None
            session.data["runs"].append({"k": k, "argv": argv, "resume": resume, "started_at": _iso(),
                                         "ended_at": None, "end": None, "exit_code": None,
                                         "log": f"logs/run{k}.log", "reconciliation": actions})
            session._save()
            proc = PtyProcess.spawn(argv, cwd=str(session.work_dir), env=env)
        except BaseException:
            lock.release()
            raise
        run = cls(session, engine, proc, lock, k, **kw)
        run.transcript.note(f"run {k}: {' '.join(argv[1:])}; reconciliation: {actions or 'nothing to do'}",
                            time.monotonic())
        return run

    # ─── loop ───────────────────────────────────────────────────────────────
    def poll(self, timeout: float = 0.05) -> list[dict]:
        if self.ended is not None:
            return []
        data = self.proc.read(timeout)
        now = time.monotonic()
        if data is None:
            self._on_exit(now)
            return self._drain()
        if data:
            self.gate.on_output(now)
            self.monitor.on_output(now)
            for ev in self.parser.feed(self.transcript.feed(data, now), now):
                if self.ended is not None:
                    break
                self._event(ev, now)
        if self.ended is None:
            try:
                self.gate.tick(time.monotonic())
            except ProcessGone:
                pass                                         # seen as an exit by the next read
            self._watch(now)
        return self._drain()

    def _drain(self) -> list[dict]:
        out, self.notices = self.notices, []
        return out

    def _notice(self, kind: str, **data) -> None:
        self.notices.append({"kind": kind, "t": time.monotonic(), **data})

    def _phase(self, name: str, now: float) -> None:
        if name != self.phase:
            self.phase, self.phase_since = name, now

    # ─── events ─────────────────────────────────────────────────────────────
    def _event(self, ev, now: float) -> None:
        self.gate.on_event(ev)
        if ev.kind != "usb_error":
            self.monitor.on_progress(now)
            self.transcript.note(f"{ev.kind} {ev.public().data}", now)
        k = ev.kind
        if k == "instrument_field" and ev.data["name"] == "Serial Number":
            self._serial(ev.data["value"])
        elif k in ("calibration_prompt", "calibration_retry"):
            self._phase("calibration", now)
            self._notice(k, **ev.public().data)
        elif k == "calibration_done":
            self._phase("calibrated", now)
        elif k == "ready":
            self._ready(ev, now)
        elif k == "strip_ok":
            self.acquired = self.current_pass
            self._phase("saving", now)
            self._notice("strip_acquired", **{"pass": self.current_pass, "reversed": ev.data["reversed"]})
        elif k in ("ck_reduced", "ck_not_durable"):
            self._ck_warning = (k, ev.data["message"])
            if self.finishing:
                self.final_durability = "reduced" if k == "ck_reduced" else "not_durable"
        elif k == "ck_refused":
            self._suspend("checkpoint_refused", ev.data["message"])
        elif k == "checkpoint":
            self._checkpoint(ev, now)
        elif k in WAIT_PROMPTS:
            self._phase("warning", now)
            self._notice(k, **ev.data)
        elif k == "done_confirm":
            self.gate.request("no")
            self._fail("counts_diverge", "chartread says a patch is unread while freeglaz finished")
        elif k in ("start_failed", "fatal"):
            self.error = self.error or {"kind": k, "detail": ev.data.get("message")}
            self._notice(k, **ev.data)
        elif k == "usb_error" and ev.data["loss"]:
            self._fail("instrument_lost", f"USB transfer error {ev.data['code']}", now=True)
        elif k == "instrument_not_spectral":
            self._fail("instrument_not_spectral", "the instrument gives no spectra: not a profiling measurement")
        elif k in ("needcal", "sensor_position"):
            self._notice(k, **ev.data)
        if self.stopping:
            self._act_stop()

    def _serial(self, value: str) -> None:
        value = value.strip()
        if not value or value.startswith("<"):
            return                                           # nothing reliable: invent nothing
        inst = self.session.data["instrument"]
        if inst.get("serial") is None:
            inst["serial"] = value
            self.session._save()
        elif inst["serial"] != value:
            self._fail("instrument_unit_changed", "another unit of the model is connected",
                       suspend=True)

    def _ready(self, ev, now: float) -> None:
        self._phase("ready", now)
        if not ev.data["checkpointing"]:
            self._fail("engine_not_checkpointing", "the strip prompt does not confirm -K")
            return
        self.current_pass, self.read_before = ev.data["pass"], ev.data["read_before"]
        self._notice("ready", **{"pass": self.current_pass, "read_before": self.read_before,
                                  "all_read": ev.data["all_read"]})
        if ev.data["all_read"] and self.auto_finish and not self.stopping:
            v = self.session.validated_state
            if v is not None and v.complete:
                self.gate.request("finish")
                self.finishing = True
                self._phase("finishing", now)
            else:
                self._fail("counts_diverge", "chartread reports every row read, the validated file does not")

    def _checkpoint(self, ev, now: float) -> None:
        dur, warn = ev.data["durability"], self._ck_warning
        self._ck_warning = None
        cur = {self.current_pass} if self.current_pass else set()
        try:
            st = self.session.commit_work(durability=dur, announced=ev.data["n"], may_add=cur,
                                          may_change=cur if self.read_before else (), run=self.k,
                                          message=warn[1] if warn else None)
        except ck.CheckpointInvalid as e:
            self._suspend("checkpoint_invalid", f"{e.code}: {e.message}")
            return
        except OSError as e:
            self._suspend("session_write_failed", str(e))
            return
        self.acquired = None
        self._phase("saved", now)
        self._notice("checkpoint", durability=dur, passes_saved=len(st.passes), patches_saved=st.n_patches,
                     complete=st.complete, **{"pass": self.current_pass})
        if dur == "not_durable":
            self._suspend("checkpoint_not_durable", warn[1] if warn else "durability not confirmed")

    # ─── stopping ───────────────────────────────────────────────────────────
    def _suspend(self, reason: str, detail: str) -> None:
        self.session.data["suspended"] = {"reason": reason, "detail": detail, "at": _iso(), "run": self.k,
                                          "unsaved_pass": self.acquired}
        try:
            self.session._save()
        except OSError:
            pass                                             # reconcile() will sort the files out
        self._notice("suspended", reason=reason, detail=detail)
        self.pause(f"suspended:{reason}")

    def _fail(self, kind: str, detail: str, *, now: bool = False, suspend: bool = False) -> None:
        self.error = {"kind": kind, "detail": detail}
        self._notice("error", error=kind, detail=detail)
        if suspend:
            self._suspend(kind, detail)
        elif now:
            self.stop_now(f"error:{kind}")
        else:
            self.pause(f"error:{kind}")

    def pause(self, reason: str = "user_pause", deadline: float = 30.0) -> None:
        """Prompt-aware stop: Esc at the strip prompt, then give up; give up at a failure
        prompt; abort a calibration. Escalates to :meth:`stop_now` after ``deadline``."""
        if self.stopping is None:
            self.stopping = reason
        self.stop_deadline = min(self.stop_deadline or float("inf"), time.monotonic() + deadline)
        self._act_stop()

    def _act_stop(self) -> None:
        p = self.gate.prompt
        if p is None:
            return
        cmd = {"ready": "pause", "calibration_prompt": "abort", "calibration_retry": "abort",
               "done_confirm": "no"}.get(p.kind, "give_up")
        try:
            self.gate.request(cmd)
        except KeyRefused:
            pass

    def stop_now(self, reason: str) -> None:
        if self.stopping is None or not self.stopping.startswith("error"):
            self.stopping = reason
        if self.proc.alive:
            rep = self.proc.terminate(on_output=lambda b: self.transcript.feed(b, time.monotonic()))
            self._notice("stopped", outcome=rep.outcome, elapsed=rep.elapsed, group_empty=rep.group_empty)
        self._on_exit(time.monotonic())

    def shutdown(self, budget: float = 4.0) -> None:
        """Application shutdown: pause within ~1.5 s, then the graduated stop."""
        self.pause("backend_stop", deadline=min(1.5, budget))
        end = time.monotonic() + budget
        while self.ended is None and time.monotonic() < end:
            self.poll(0.05)
        if self.ended is None:
            self.stop_now("backend_stop")

    # ─── user commands ──────────────────────────────────────────────────────
    def command(self, name: str) -> None:
        if name == "pause":
            self.pause("user_pause")
            return
        if self.stopping:
            raise SessionError("run_stopping", "the run is stopping")
        if name not in USER_COMMANDS:
            raise SessionError("command_unknown", f"unknown command {name!r}")
        prompt = self.gate.prompt
        self.gate.request(USER_COMMANDS[name])
        if name == "calibrate":
            self._phase("calibrating", time.monotonic())
        if name == "accept" and prompt is not None:
            self.session.data["accepted_warnings"].append(
                {"pass": self.current_pass, "kind": prompt.kind, "delta_e": prompt.data.get("delta_e"),
                 "run": self.k, "at": _iso()})
            self.session._save()

    # ─── watching ───────────────────────────────────────────────────────────
    def _watch(self, now: float) -> None:
        if self.ended is not None or not self.proc.alive:
            return
        if now >= self._next_cpu:
            self.monitor.sample_cpu(self.proc.cpu_seconds(), now)
            self._next_cpu = now + 0.5
        v = self.monitor.verdict(now, self.transcript.flood(now))
        if v.status == "flooding":
            self._fail("output_flood", v.detail, now=True)
            return
        if v.status == "spinning":
            self._fail("engine_spinning", v.detail, now=True)
            return
        limit = self.phase_limits.get(self.phase)
        if limit is not None and now - self.phase_since > limit and not self.stopping:
            self._fail("engine_timeout", f"no progress for {limit:.0f} s while {self.phase}", now=True)
            return
        if self.presence is not None and now >= self._next_presence:
            self._next_presence = now + 2.0
            p = self.presence()
            if p.status in ("absent", "other", "several"):
                self._fail("instrument_lost", p.detail, now=True)
                return
        if self.stopping and self.stop_deadline is not None and now > self.stop_deadline:
            self.stop_now(self.stopping)

    # ─── end ────────────────────────────────────────────────────────────────
    def _on_exit(self, now: float) -> None:
        if self.ended is not None:
            return
        rc = self.proc.wait(2.0)                             # end of file can precede the exit
        if rc is None:                                       # terminal closed, process still there
            self.proc.terminate()
            rc = self.proc.poll()
        final = None
        if self.finishing and not self.stopping:
            # chartread's final save rewrites the same passes (new CREATED). If it failed, the
            # last checkpoint already held every pass: the validated file stays complete.
            try:
                if rc == 0:
                    st = self.session.commit_work(durability=self.final_durability, may_add=set(),
                                                  run=self.k, final=True)
                else:
                    st = self.session.validated_state
                final = {"complete": bool(st and st.complete), "final_save": "ok" if rc == 0 else "failed",
                         "durability": self.final_durability if rc == 0 else None}
            except (ck.CheckpointInvalid, OSError) as e:
                final = {"complete": bool(self.session.validated_state and self.session.validated_state.complete),
                         "final_save": f"not adopted: {e}"}
        if self.stopping:
            end = self.stopping
        elif final is not None:
            end = "finished" if final["complete"] else "finish_incomplete"
        else:
            end = "engine_error" if self.error else "exited"
        self.ended = {"end": end, "exit_code": rc, "final": final, "error": self.error}
        rec = self.session.data["runs"][self.k - 1]
        rec.update({"ended_at": _iso(), "end": end, "exit_code": rc})
        self.session.data["last_stop"] = {"reason": end, "at": _iso(), "run": self.k, "error": self.error}
        try:
            self.session._save()
        except OSError:
            pass
        self.transcript.note(f"run {self.k} ended: {end} (exit {rc})", now)
        self.transcript.close(now)
        self.proc.close()
        self.lock.release()
        self._notice("ended", **self.ended)

    def snapshot(self) -> dict:
        p = self.gate.prompt
        return {"phase": self.phase, "prompt": p.kind if p else None, "pass": self.current_pass,
                "acquired_not_saved": self.acquired, "stopping": self.stopping, "error": self.error,
                "ended": self.ended, "session": self.session.summary()}
