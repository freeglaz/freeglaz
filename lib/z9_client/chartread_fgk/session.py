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

"""An external measurement session: persistent, recoverable, checked.

**Folder** ``charts/<serial>/<chart_id>/external/<session_id>/``::

    session.json          the durable state (atomic writes)
    work/<chart_id>.ti2   copy of the chart's .ti2 (sha256 checked against chart.json)
    work/<chart_id>.ti3   chartread-fgK's file, rewritten by it after each strip pass
    validated.ti3         the last state freeglaz validated (hard link, or atomic copy)
    corrections/          the validated file as it was before each offline correction
    logs/run<k>.log       bounded transcript of each engine run

Persisted statuses: ``open``, ``published``, ``abandoned``. What the engine is doing
(calibrating, waiting at a strip, saving…) only lives in memory, in :class:`Run`.
``suspended`` (inside an open session) records why a run was stopped and that it must
not just go on: a refused checkpoint, a checkpoint whose durability was not confirmed, an
invalid checkpoint, a changed instrument.

**A checkpoint**, in order: (1) chartread finishes a strip pass, (2) writes and renames
its TI3, (3) freeglaz links that file under a temporary name and validates the linked
bytes (a later rewrite by chartread cannot change them: it never writes in place),
(4) renames the link to ``validated.ti3``, (5) writes ``session.json``. Any of these can be
interrupted. :meth:`Session.reconcile`, run before every engine run, makes the three files
agree again, deterministically:

- temporary files of an interrupted step 3/4 are removed;
- ``validated.ti3`` newer than ``session.json`` (stopped between 4 and 5) is adopted: it
  was validated before being put in place;
- a work file that extends the validated state (more whole passes, the validated ones
  unchanged, same instrument, calibration standard and spectral grid: stopped between 2
  and 4) is validated in full and adopted; its durability is then ``recovered``;
- any other work file (fewer passes, other values, invalid, foreign) is kept aside, never
  deleted, and the work file is restored from ``validated.ti3``;
- chartread's ``.tmp`` is never recovered (v1).

**Resume** always runs ``-r -N -K`` when a work file exists, after checking the chart (its
.ti2 sha256), the instrument model and condition, the engine (same binary, same origin) and,
once chartread prints it, the instrument's serial number when one was recorded.

**Engine origin**, established here from what the backend checked, never read from a
record: ``approved`` (the copy shipped with freeglaz), ``development`` (a path pinned in
the environment or the config), ``test`` (a fake). Only ``approved`` will be publishable.
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import secrets
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from .. import measurement_identity as mi
from . import checkpoint as ck
from .engine import ArgvError, EngineStatus, build_argv
from .prompts import KeyGate, KeyRefused, PromptParser
from .pty_process import ProcessGone, PtyProcess
from .transcript import ActivityMonitor, Transcript

SCHEMA = "freeglaz.external_session"
SCHEMA_VERSION = 1
OPEN, PUBLISHED, ABANDONED = "open", "published", "abandoned"
STATUSES = (OPEN, PUBLISHED, ABANDONED)
EXTERNAL_DIR = "external"
ORIGIN_APPROVED, ORIGIN_DEVELOPMENT, ORIGIN_TEST = "approved", "development", "test"
LOCK_NAME = "external_instrument.lock"
RECONCILIATIONS_KEPT = 20


class SessionError(RuntimeError):
    def __init__(self, code: str, message: str, **info):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.info = info


class LockBusy(SessionError):
    pass


def _iso(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fsync_dir(d: Path) -> None:
    try:
        fd = os.open(d, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """``path`` holds ``data`` entirely, or keeps its previous content."""
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


# ─── engine provenance ──────────────────────────────────────────────────────
@dataclass(frozen=True)
class EngineSpec:
    path: str
    sha256: str
    origin: str                     # approved | development | test
    source: str                     # bundle | env | config | test
    argyll_version: Optional[str] = None
    patch_version: Optional[str] = None
    patch_sha256: Optional[str] = None

    @classmethod
    def from_status(cls, st: EngineStatus) -> "EngineSpec":
        if not st.ok:
            raise SessionError(st.code or "engine_unavailable", st.message)
        origin = ORIGIN_APPROVED if st.source == "bundle" else ORIGIN_DEVELOPMENT
        return cls(st.path, st.binary_sha256, origin, st.source, st.argyll_version,
                   st.patch_version, st.patch_sha256)

    @classmethod
    def for_test(cls, path: str) -> "EngineSpec":
        return cls(str(path), _sha256_file(Path(path)), ORIGIN_TEST, ORIGIN_TEST)

    def to_dict(self) -> dict:
        return asdict(self)


# ─── interprocess lock ──────────────────────────────────────────────────────
class InstrumentLock:
    """One engine run at a time on this store, across processes (flock: released by the
    kernel when the holder dies). The file is never deleted."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            from .. import cache
            path = cache.root_dir() / LOCK_NAME
        self.path = Path(path)
        self._fd: Optional[int] = None

    def acquire(self, owner: dict) -> None:
        if self._fd is not None:
            raise LockBusy("instrument_busy", "this lock is already held here")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            holder = self.holder()
            os.close(fd)
            if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                raise LockBusy("instrument_busy", "another measurement is running", holder=holder) from None
            raise
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({**owner, "pid": os.getpid(), "since": _iso()}).encode())
        self._fd = fd

    def holder(self) -> Optional[dict]:
        try:
            return json.loads(self.path.read_text() or "null")
        except (OSError, ValueError):
            return None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


# ─── the persistent session ─────────────────────────────────────────────────
class Session:
    def __init__(self, directory: Path, data: dict):
        self.dir = Path(directory)
        self.data = data
        self._plan: Optional[ck.ChartPlan] = None
        self._vstate: Optional[ck.CheckpointState] = None
        self._vstate_loaded = False

    # paths
    @property
    def chart_id(self) -> str:
        return self.data["chart_id"]

    @property
    def json_path(self) -> Path:
        return self.dir / "session.json"

    @property
    def work_dir(self) -> Path:
        return self.dir / "work"

    @property
    def work_ti2(self) -> Path:
        return self.work_dir / f"{self.chart_id}.ti2"

    @property
    def work_ti3(self) -> Path:
        return self.work_dir / f"{self.chart_id}.ti3"

    @property
    def validated(self) -> Path:
        return self.dir / "validated.ti3"

    @property
    def chart_dir(self) -> Path:
        return self.dir.parent.parent

    @property
    def model(self) -> str:
        return self.data["instrument"]["model"]

    @property
    def condition(self) -> str:
        return self.data["instrument"]["condition"]

    # ─── create / open ──────────────────────────────────────────────────────
    @classmethod
    def create(cls, chart_dir: Path, *, model: str, filter_option: Optional[str], engine: EngineSpec,
               now: Optional[datetime] = None) -> "Session":
        chart_dir = Path(chart_dir)
        try:
            desc = json.loads((chart_dir / "chart.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SessionError("chart_unreadable", f"chart.json: {e}") from None
        if mi.chart_layout(desc) != mi.LAYOUT_PRINTTARG:
            raise SessionError("chart_not_external", "this chart is measured by the built-in scan")
        key = (desc.get("instrument") or {}).get("key")
        if key != model:
            raise SessionError("instrument_not_for_chart", f"the chart is laid out for {key!r}, not {model!r}")
        try:                                         # dry run: model, condition, base name
            build_argv("/chartread-fgK", instrument=model, chart_base=desc["chart_id"],
                       filter_option=filter_option)
        except ArgvError as e:
            raise SessionError(e.code, e.message) from None
        existing = cls.find_open(chart_dir)
        if existing is not None:
            raise SessionError("session_exists", "an open session already exists for this chart",
                               session_id=existing.data["session_id"])
        ti2_rel = (desc.get("files") or {}).get("ti2")
        ti2_sha = (desc.get("provenance") or {}).get("ti2_sha256")
        if not ti2_rel or not ti2_sha:
            raise SessionError("chart_ti2_unverified", "chart.json does not name and fingerprint its .ti2")
        try:
            ti2 = (chart_dir / ti2_rel).read_bytes()
        except OSError as e:
            raise SessionError("chart_unreadable", f".ti2: {e}") from None
        if hashlib.sha256(ti2).hexdigest() != ti2_sha:
            raise SessionError("chart_ti2_altered", "the chart's .ti2 does not match its fingerprint")
        try:
            plan = ck.ChartPlan.from_ti2(ti2)
        except ck.CheckpointInvalid as e:
            raise SessionError(e.code, e.message) from None
        m = mi.INSTRUMENT_MODELS[model]
        condition = mi.CHARTREAD_FILTERS[filter_option] if m.filter_selectable else mi.CONDITION_FIXED
        now = now or datetime.now(timezone.utc)
        sid = f"{now.astimezone(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"
        d = chart_dir / EXTERNAL_DIR / sid
        for sub in ("work", "corrections", "logs"):
            (d / sub).mkdir(parents=True)
        data = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION, "session_id": sid,
                "chart_id": desc["chart_id"], "status": OPEN, "created_at": _iso(now), "updated_at": _iso(now),
                "instrument": {"model": model, "condition": condition, "filter_option": filter_option,
                               "serial": None},
                "engine": engine.to_dict(),
                "chart": {"ti2_sha256": ti2_sha, "passes": len(plan.passes), "useful_patches": plan.n_useful,
                          "padding": plan.padding},
                "runs": [], "last_checkpoint": None, "durability_warning": None, "suspended": None,
                "accepted_warnings": [], "corrections": [], "reconciliations": [], "published": None}
        s = cls(d, data)
        atomic_write(s.work_ti2, ti2)
        s._save()
        return s

    @classmethod
    def open(cls, directory: Path) -> "Session":
        p = Path(directory) / "session.json"
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SessionError("session_unreadable", f"{p}: {e}") from None
        if data.get("schema") != SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
            raise SessionError("session_unsupported", f"{p}: not a {SCHEMA} v{SCHEMA_VERSION}")
        if data.get("status") not in STATUSES:
            raise SessionError("session_unreadable", f"{p}: unknown status {data.get('status')!r}")
        return cls(Path(directory), data)

    @classmethod
    def sessions(cls, chart_dir: Path) -> list["Session"]:
        base = Path(chart_dir) / EXTERNAL_DIR
        out = []
        for d in sorted(base.iterdir()) if base.is_dir() else []:
            if (d / "session.json").is_file():
                try:
                    out.append(cls.open(d))
                except SessionError:
                    continue
        return out

    @classmethod
    def find_open(cls, chart_dir: Path) -> Optional["Session"]:
        opened = [s for s in cls.sessions(chart_dir) if s.data["status"] == OPEN]
        return opened[-1] if opened else None

    # ─── state ──────────────────────────────────────────────────────────────
    @property
    def plan(self) -> ck.ChartPlan:
        if self._plan is None:
            data = self.work_ti2.read_bytes()
            if hashlib.sha256(data).hexdigest() != self.data["chart"]["ti2_sha256"]:
                raise SessionError("session_ti2_altered", "the session's copy of the .ti2 was changed")
            self._plan = ck.ChartPlan.from_ti2(data)
        return self._plan

    def _validate(self, data: bytes, **kw) -> ck.CheckpointState:
        return ck.validate(data, self.plan, model=self.model, condition=self.condition, **kw)

    @property
    def validated_state(self) -> Optional[ck.CheckpointState]:
        if not self._vstate_loaded:
            self._vstate = self._validate(self.validated.read_bytes()) if self.validated.exists() else None
            self._vstate_loaded = True
        return self._vstate

    def summary(self) -> dict:
        """What the interface shows: exact counts from the validated file."""
        lc = self.data.get("last_checkpoint") or {}
        return {"session_id": self.data["session_id"], "chart_id": self.chart_id, "status": self.data["status"],
                "model": self.model, "condition": self.condition,
                "passes_total": self.data["chart"]["passes"], "useful_patches": self.data["chart"]["useful_patches"],
                "passes_saved": lc.get("passes_saved", 0), "patches_saved": lc.get("patches_saved", 0),
                "complete": bool(lc.get("complete")), "durability": lc.get("durability"),
                "durability_warning": self.data.get("durability_warning"),
                "suspended": self.data.get("suspended"), "engine_origin": self.data["engine"]["origin"],
                "runs": len(self.data["runs"])}

    def ensure_open(self) -> None:
        if self.data["status"] != OPEN:
            raise SessionError("session_closed", f"the session is {self.data['status']}")

    def _hook(self, point: str) -> None:
        """Interruption points, for tests."""

    def _save(self, now: Optional[datetime] = None) -> None:
        self.data["updated_at"] = _iso(now)
        atomic_write(self.json_path, (json.dumps(self.data, indent=1, ensure_ascii=False) + "\n").encode())
        self._hook("after_save_json")

    def _note_reconciliation(self, actions: list[str]) -> None:
        if actions:
            self.data["reconciliations"] = (self.data.get("reconciliations") or [])[-(RECONCILIATIONS_KEPT - 1):] \
                + [{"at": _iso(), "actions": actions}]

    # ─── checkpoint commit: link, validate the linked bytes, rename, save ───
    def commit_work(self, *, durability: str, announced: Optional[int] = None, may_add=None,
                    may_change=(), may_remove=(), run: Optional[int] = None, final: bool = False,
                    message: Optional[str] = None) -> ck.CheckpointState:
        prev = self.validated_state
        tmp = self.dir / f".validated.{secrets.token_hex(4)}.tmp"
        try:
            try:
                os.link(self.work_ti3, tmp)
                how = "hardlink"
            except OSError as e:
                if e.errno == errno.ENOENT:
                    raise
                with open(self.work_ti3, "rb") as f:
                    data = f.read()
                with open(tmp, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                how = "copy"
            # Read-only: with a hard link, the work file and validated.ti3 are ONE inode until
            # chartread replaces the work file (it only ever renames a new file over it, as
            # freeglaz does). An in-place write by anything else would change both; now it fails.
            try:
                os.chmod(tmp, 0o444)
            except OSError:
                pass
            self._hook("after_link_tmp")
            data = tmp.read_bytes()
            state = self._validate(data, previous=prev, announced=announced, may_add=may_add,
                                   may_change=may_change, may_remove=may_remove)
            os.replace(tmp, self.validated)
            _fsync_dir(self.dir)
            self._hook("after_replace_validated")
        except Exception:                # a dying process leaves the temporary: reconcile() removes it
            tmp.unlink(missing_ok=True)
            raise
        self._vstate, self._vstate_loaded = state, True
        self._record_checkpoint(state, durability, run=run, final=final, how=how, message=message)
        self._save()
        self._hook("after_commit")
        return state

    def _record_checkpoint(self, state: ck.CheckpointState, durability: str, *, run=None, final=False,
                           how=None, message=None) -> None:
        self.data["last_checkpoint"] = {
            **state.summary(), "useful_total": self.plan.n_useful, "durability": durability,
            "validated_at": _iso(), "run": run, "final": final, "stored_as": how}
        if durability == "reduced":
            w = self.data.get("durability_warning") or {"kind": "reduced", "since": _iso(), "count": 0}
            w["count"] += 1
            w["message"] = message
            self.data["durability_warning"] = w

    # ─── reconciliation ─────────────────────────────────────────────────────
    def reconcile(self) -> list[str]:
        """Make work file, validated file and session.json agree (see module doc). Returns
        what was done; raises SessionError when it cannot be decided safely."""
        actions: list[str] = []
        for tmp in self.dir.glob(".validated.*.tmp"):
            tmp.unlink(missing_ok=True)
            actions.append(f"removed interrupted temporary {tmp.name}")
        for tmp in self.dir.glob(".session.json.*.tmp"):
            tmp.unlink(missing_ok=True)
            actions.append(f"removed interrupted temporary {tmp.name}")
        for r in self.data["runs"]:                              # its process died with freeglaz
            if r.get("ended_at") is None:
                r.update({"ended_at": _iso(), "end": "interrupted"})
                actions.append(f"run {r['k']} had not been closed: marked interrupted")
        _ = self.plan                                            # the .ti2 copy is intact
        self._vstate_loaded = False
        try:
            v = self.validated_state
        except ck.CheckpointInvalid as e:
            raise SessionError("validated_invalid", f"validated.ti3 does not validate: {e.message}") from None
        actions += self._finish_pending_correction(v)
        v = self.validated_state
        lc = self.data.get("last_checkpoint")
        s_sha = lc["sha256"] if lc else None
        if v is not None and v.sha256 != s_sha:
            if lc and not set(lc["passes"]) <= set(v.passes):
                raise SessionError("validated_ahead_incompatible",
                                   "validated.ti3 lacks passes session.json says were saved")
            self._record_checkpoint(v, "recovered", run=lc.get("run") if lc else None,
                                    final=bool(lc and lc.get("final")))
            actions.append(f"adopted validated.ti3 ({len(v.passes)} passes): session.json was behind")
        elif v is None and s_sha is not None:
            if self.work_ti3.exists() and _sha256_file(self.work_ti3) == s_sha:
                self.commit_work(durability=lc.get("durability") or "recovered", run=lc.get("run"))
                actions.append("validated.ti3 was missing: rebuilt from the identical work file")
                self._note_reconciliation(actions)
                self._save()
                return actions
            raise SessionError("validated_missing", "validated.ti3 is missing and no identical work file exists")
        v = self.validated_state
        if not self.work_ti3.exists():
            if v is not None:
                atomic_write(self.work_ti3, self.validated.read_bytes())
                actions.append("work file was missing: restored from validated.ti3")
        elif v is None or _sha256_file(self.work_ti3) != v.sha256:
            try:
                st = self._validate(self.work_ti3.read_bytes(), previous=v)
            except ck.CheckpointInvalid as e:
                actions.append(self._set_work_aside(e.code, e.message, restore=v is not None))
            else:
                if v is not None and set(st.passes) == set(v.passes) and st.digests == v.digests:
                    actions.append("work file has the validated content (rewritten): kept")
                    self.commit_work(durability=self.data["last_checkpoint"]["durability"],
                                     run=self.data["last_checkpoint"].get("run"))
                else:
                    n_new = len(st.passes) - (len(v.passes) if v else 0)
                    self.commit_work(durability="recovered")
                    actions.append(f"recovered {n_new} pass(es) chartread had saved but freeglaz had not "
                                   f"validated ({len(st.passes)} now)")
        if (self.work_dir / f"{self.chart_id}.ti3.tmp").exists():
            actions.append("chartread's .tmp left as is (never recovered)")
        self._note_reconciliation(actions)
        self._save()
        return actions

    def _set_work_aside(self, code: str, message: str, *, restore: bool) -> str:
        aside = self.work_dir / f"{self.chart_id}.ti3.unconfirmed-{time.strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
        os.replace(self.work_ti3, aside)
        _fsync_dir(self.work_dir)
        if restore:
            atomic_write(self.work_ti3, self.validated.read_bytes())
        return (f"work file not adopted ({code}: {message}); kept as {aside.name}"
                + ("; restored from validated.ti3" if restore else ""))

    # ─── offline correction ─────────────────────────────────────────────────
    def remove_pass(self, label: str, lock: InstrumentLock) -> ck.CheckpointState:
        """Withdraw a validated strip pass so that the next run (``-r``) reads it again. The
        validated file is first copied to corrections/; an intent in session.json makes an
        interruption at any point recoverable by :meth:`reconcile`."""
        self.ensure_open()
        lock.acquire({"session_id": self.data["session_id"], "chart_id": self.chart_id, "op": "correction"})
        try:
            self.reconcile()
            v = self.validated_state
            if v is None or label not in v.passes:
                raise SessionError("pass_not_saved", f"pass {label} is not in the validated state")
            new = ck.without_pass(self.validated.read_bytes(), self.plan, label)
            n = len(self.data["corrections"]) + 1
            backup = Path("corrections") / f"{n:03d}_{label}_{time.strftime('%Y%m%dT%H%M%S')}.ti3"
            atomic_write(self.dir / backup, self.validated.read_bytes())
            self._validate(new, previous=v, may_add=set(), may_remove={label})     # before any change
            self.data["pending_correction"] = {"pass": label, "backup": str(backup),
                                               "target_sha256": hashlib.sha256(new).hexdigest(), "at": _iso()}
            self._save()
            atomic_write(self.work_ti3, new)
            self._hook("correction_after_work")
            state = self.commit_work(durability="correction", may_add=set(), may_remove={label})
            self._finish_pending_correction(state)
            self._save()
            return state
        finally:
            lock.release()

    def _finish_pending_correction(self, v: Optional[ck.CheckpointState]) -> list[str]:
        pc = self.data.get("pending_correction")
        if not pc:
            return []
        if v is not None and v.sha256 == pc["target_sha256"]:
            done = True
        elif self.work_ti3.exists() and _sha256_file(self.work_ti3) == pc["target_sha256"]:
            self.commit_work(durability="correction", may_add=set(), may_remove={pc["pass"]})
            done = True
        else:
            done = False
        self.data.pop("pending_correction")
        if done:
            self.data["corrections"].append({"pass": pc["pass"], "backup": pc["backup"], "at": pc["at"]})
            return [f"completed the interrupted removal of pass {pc['pass']}"]
        return [f"the interrupted removal of pass {pc['pass']} had not started: dropped"]

    def abandon(self, lock: InstrumentLock) -> None:
        self.ensure_open()
        lock.acquire({"session_id": self.data["session_id"], "chart_id": self.chart_id, "op": "abandon"})
        try:
            self.data["status"] = ABANDONED
            self.data["abandoned_at"] = _iso()
            self._save()
        finally:
            lock.release()

    # ─── resume checks ──────────────────────────────────────────────────────
    def check_run(self, engine: EngineSpec) -> None:
        self.ensure_open()
        try:
            desc = json.loads((self.chart_dir / "chart.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise SessionError("chart_unreadable", f"chart.json: {e}") from None
        if desc.get("chart_id") != self.chart_id or (desc.get("instrument") or {}).get("key") != self.model:
            raise SessionError("chart_mismatch", "the chart no longer matches the session")
        if (desc.get("provenance") or {}).get("ti2_sha256") != self.data["chart"]["ti2_sha256"]:
            raise SessionError("chart_ti2_altered", "the chart's .ti2 fingerprint changed")
        _ = self.plan
        rec = self.data["engine"]
        if (engine.sha256, engine.origin) != (rec["sha256"], rec["origin"]):
            raise SessionError("engine_changed", "this session was measured with another engine binary",
                               recorded=rec["origin"], now=engine.origin)
        if _sha256_file(Path(engine.path)) != engine.sha256:
            raise SessionError("engine_changed", "the engine file changed since it was checked")


# ─── one engine run ─────────────────────────────────────────────────────────
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
        rc = self.proc.poll()
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
