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
(calibrating, waiting at a strip, saving…) only lives in memory, in :class:`~.run.Run`.
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
record: ``approved`` (a trusted build, :data:`~.engine.TRUSTED_BUILDS`, found where freeglaz
ships its copy), ``development`` (a path pinned in the environment or the config, or a
shipped-place binary that is not a trusted build), ``test`` (a fake). Only ``approved`` is
publishable, and the publication checks the trusted list again (``session.json`` is a file
in the store, not a proof).
"""
from __future__ import annotations

import configparser
import errno
import fcntl
import hashlib
import json
import os
import secrets
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import measurement_identity as mi
from . import checkpoint as ck
from .engine import ArgvError, EngineStatus, build_argv

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
        # Both: a trusted build pinned by hand is still a development set-up, and the shipped
        # place proves nothing about what was put there.
        origin = ORIGIN_APPROVED if st.source == "bundle" and st.trusted else ORIGIN_DEVELOPMENT
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


USER_LOCK_NAME = "external_instrument.lock"
USER_LOCK_GRANT = "xdg-run/freeglaz"              # the Flatpak permission that shares the dir


def _flatpak_grants_lock_dir(flatpak_info: Path) -> bool:
    """Was ``xdg-run/freeglaz`` granted read-write to THIS sandbox? Read from
    ``/.flatpak-info``, where Flatpak writes the instance's effective permissions (manifest,
    overrides, command line). A ``freeglaz`` directory inside the sandbox proves nothing:
    the sandbox's ``$XDG_RUNTIME_DIR`` is a per-app directory that outlives the instance, and
    keeps the empty mountpoint of an earlier run that had the permission (note 143)."""
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(flatpak_info.read_text(encoding="utf-8"))
        items = cp.get("Context", "filesystems", fallback="")
    except (OSError, UnicodeDecodeError, configparser.Error):
        return False
    granted = False
    for item in items.split(";"):
        name, _, mode = item.strip().partition(":")
        if name == USER_LOCK_GRANT:
            granted = mode in ("", "rw", "create")    # :ro cannot hold the lock file
        elif name == "!" + USER_LOCK_GRANT:
            granted = False
    return granted


def user_lock_path(platform: Optional[str] = None, env: Optional[dict] = None,
                   flatpak_info: Path = Path("/.flatpak-info")) -> tuple[Optional[Path], str]:
    """(path, scope) of a lock shared by EVERY freeglaz install of this user, whatever its
    store. Scope ``user`` when such a place exists; otherwise (None, why).

    - macOS: ``~/Library/Caches/freeglaz/`` (no sandbox for the app or a source install);
    - Linux: ``$XDG_RUNTIME_DIR/freeglaz/``. Inside a Flatpak that directory is private to
      the sandbox unless ``--filesystem=xdg-run/freeglaz:create`` is granted, as
      ``/.flatpak-info`` says (not whether the directory exists): otherwise the scope is
      ``sandbox`` and nothing is claimed beyond the store.

    It does not reach other users, nor programs other than freeglaz. The operating system
    adds its own exclusivity when chartread opens the instrument (Linux: one usbfs claim per
    interface, and Argyll does not detach a ColorMunki or an i1Pro 3; macOS: exclusive
    IOKit open): not observed with a real instrument yet."""
    p = platform or sys.platform
    env = os.environ if env is None else env
    if p == "darwin":
        return Path.home() / "Library" / "Caches" / "freeglaz" / USER_LOCK_NAME, "user"
    run = env.get("XDG_RUNTIME_DIR")
    if not run or not Path(run).is_dir():
        return None, "no_runtime_dir"
    d = Path(run) / "freeglaz"
    if flatpak_info.is_file() and not (_flatpak_grants_lock_dir(flatpak_info) and d.is_dir()):
        return None, "sandbox"
    return d / USER_LOCK_NAME, "user"


class LockSet:
    """Several InstrumentLocks taken together, in order (store, then user): all or none.
    Same interface as InstrumentLock."""

    def __init__(self, locks: list[tuple[str, InstrumentLock]]):
        self.locks = locks

    @property
    def scopes(self) -> list[str]:
        return [scope for scope, _ in self.locks]

    @property
    def held(self) -> bool:
        return all(lk.held for _, lk in self.locks)

    def holder(self) -> Optional[dict]:
        for _, lk in self.locks:
            h = lk.holder()
            if h:
                return h
        return None

    def acquire(self, owner: dict) -> None:
        taken = []
        try:
            for scope, lk in self.locks:
                try:
                    lk.acquire(owner)
                except LockBusy as e:
                    raise LockBusy(e.code, e.message, scope=scope, **e.info) from None
                taken.append(lk)
        except BaseException:
            for lk in reversed(taken):
                lk.release()
            raise

    def release(self) -> None:
        for _, lk in reversed(self.locks):
            lk.release()


CREATION_LOCK_NAME = ".create.lock"
CREATION_LOCK_WAIT_S = 5.0


@contextmanager
def _creation_lock(chart_dir: Path):
    """Exclusive flock on ``<chart>/external/.create.lock`` while a session is created: the
    "never a second open session" check and the creation are one step for every process on
    this machine (the in-process lock of the backend does not reach another process)."""
    d = Path(chart_dir) / EXTERNAL_DIR
    d.mkdir(exist_ok=True)
    fd = os.open(d / CREATION_LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        end = time.monotonic() + CREATION_LOCK_WAIT_S
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() > end:
                    raise LockBusy("session_busy", "another process is creating a session on this chart") from None
                time.sleep(0.02)
        yield
    finally:
        os.close(fd)                                     # releases the flock


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
        with _creation_lock(chart_dir):                  # two freeglaz processes, one chart
            return cls._create_locked(chart_dir, desc, model=model, filter_option=filter_option,
                                      engine=engine, now=now)

    @classmethod
    def _create_locked(cls, chart_dir: Path, desc: dict, *, model: str, filter_option: Optional[str],
                       engine: EngineSpec, now: Optional[datetime]) -> "Session":
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
                "runs": len(self.data["runs"]),
                # for the strip map and the final check (labels in chartread's order)
                "passes": list(lc.get("passes") or []), "pass_labels": list(self.plan.passes),
                "identity": lc.get("identity"), "final": bool(lc.get("final")),
                "accepted_warnings": len(self.data.get("accepted_warnings") or []),
                "corrections": [c["pass"] for c in self.data.get("corrections") or []],
                "last_stop": self.data.get("last_stop"),
                "last_run_end": self.data["runs"][-1].get("end") if self.data["runs"] else None,
                "published": self.data.get("published")}

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
        if actions:                                      # nothing to fix: nothing written
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
        self.check_engine_file(engine)

    @staticmethod
    def check_engine_file(engine: EngineSpec) -> None:
        """The engine file still has the bytes that were checked (called again right before
        it is started, to keep the window for a substitution as short as possible)."""
        if _sha256_file(Path(engine.path)) != engine.sha256:
            raise SessionError("engine_changed", "the engine file changed since it was checked")
