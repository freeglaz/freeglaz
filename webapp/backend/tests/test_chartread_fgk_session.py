"""External measurement sessions (lib.z9_client.chartread_fgk.session): persistent,
recoverable after an interruption at any point, checked at every checkpoint. Offline: the
engine is the fake chartread in "measure" mode (it reads the .ti2, rewrites the TI3
atomically after each strip pass like -K, resumes with -r); the chart is synthetic."""
import errno
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lib.z9_client.chartread_fgk import run as R
from lib.z9_client.chartread_fgk import session as S
from lib.z9_client.chartread_fgk.engine import EngineStatus
from lib.z9_client.chartread_fgk.usb_presence import Presence
from webapp.backend.tests.session_harness import (data_rows, drive, kinds, make_chart, python_engine,
                                                  ti3_bytes)

pytestmark = pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="POSIX PTY")


class Crash(BaseException):
    """Stands for freeglaz dying at an exact point (no cleanup code runs, like a kill)."""


def setup(tmp, *, n=260, steps=26, model="colormunki", filt=None, **fake):
    chart = make_chart(tmp, n_useful=n, steps=steps, model=model)
    exe, env = python_engine(tmp, chart, **fake)
    eng = S.EngineSpec.for_test(str(exe))
    s = S.Session.create(chart.dir, model=model, filter_option=filt, engine=eng)
    return chart, s, eng, env


def start(s, eng, env, tmp, **kw):
    return R.Run.start(s, eng, lock=S.InstrumentLock(tmp / "lock"), env=env, **kw)


def full(s, eng, env, tmp, **drive_kw):
    run = start(s, eng, env, tmp)
    notices = drive(run, **drive_kw)
    return run, notices


def reconfigure(tmp, chart, **fake):
    """Same engine binary (same sha256), new behaviour for the next run."""
    python_engine(tmp, chart, **fake)


def pause_after(n_checkpoints):
    def on(n, run):
        if n["kind"] == "checkpoint" and n["passes_saved"] >= n_checkpoints and not run.stopping:
            run.command("pause")
    return on


def died(run):
    """Clean up after a simulated crash of freeglaz (the process, the lock)."""
    if run.proc.alive:
        run.proc.terminate()
    run.proc.close()
    run.lock.release()


def crash_at(monkeypatch, point, nth):
    seen = {"n": 0}

    def hook(self, p):
        if p == point:
            seen["n"] += 1
            if seen["n"] == nth:
                raise Crash(p)
    monkeypatch.setattr(S.Session, "_hook", hook)


def reopen(s):
    return S.Session.open(s.dir)


def expected_rows(chart, passes=None, **kw):
    return data_rows(ti3_bytes(chart, passes or chart.labels, **kw))


# ─── creation ───────────────────────────────────────────────────────────────
def test_the_session_folder(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    assert s.dir.parent == chart.dir / "external"
    assert sorted(p.name for p in s.dir.iterdir()) == ["corrections", "logs", "session.json", "work"]
    assert s.work_ti2.read_bytes() == chart.ti2.read_bytes()
    d = json.loads(s.json_path.read_text())
    assert (d["schema"], d["status"], d["suspended"], d["last_checkpoint"]) == (S.SCHEMA, "open", None, None)
    assert d["engine"]["origin"] == "test" and d["instrument"] == {"model": "colormunki", "condition": "fixed",
                                                                     "filter_option": None, "serial": None}
    assert s.summary()["passes_total"] == 10 and s.summary()["useful_patches"] == 260


def test_creation_refusals(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    with pytest.raises(S.SessionError) as e:
        S.Session.create(chart.dir, model="colormunki", filter_option=None, engine=eng)
    assert e.value.code == "session_exists"
    cases = [("i1pro3plus", None, "instrument_not_for_chart")]
    for model, filt, code in cases:
        with pytest.raises(S.SessionError) as e:
            S.Session.create(chart.dir, model=model, filter_option=filt, engine=eng)
        assert e.value.code == code
    other = make_chart(tmp_path / "i1", n_useful=40, steps=20, model="i1pro3plus")
    for filt, code in ((None, "argv_filter_required"), ("p", "argv_filter_unsupported")):
        with pytest.raises(S.SessionError) as e:
            S.Session.create(other.dir, model="i1pro3plus", filter_option=filt, engine=eng)
        assert e.value.code == code
    other.ti2.write_bytes(other.ti2.read_bytes().replace(b"100.0000", b"99.0000", 1))
    with pytest.raises(S.SessionError) as e:
        S.Session.create(other.dir, model="i1pro3plus", filter_option="n", engine=eng)
    assert e.value.code == "chart_ti2_altered"
    desc = json.loads((other.dir / "chart.json").read_text())
    desc.pop("layout")
    (other.dir / "chart.json").write_text(json.dumps(desc))
    with pytest.raises(S.SessionError) as e:
        S.Session.create(other.dir, model="i1pro3plus", filter_option="n", engine=eng)
    assert e.value.code == "chart_not_external"


def test_engine_origin_is_established_not_declared(tmp_path):
    def st(source, trusted=True):
        return EngineStatus(True, None, "", source=source, path="/x/chartread-fgK", binary_sha256="a" * 64,
                            argyll_version="3.5.0", patch_version="3", trusted=trusted)
    assert S.EngineSpec.from_status(st("bundle")).origin == "approved"
    assert S.EngineSpec.from_status(st("bundle", trusted=False)).origin == "development"   # just a folder
    assert S.EngineSpec.from_status(st("env")).origin == "development"     # even a trusted build, pinned
    assert S.EngineSpec.from_status(st("config")).origin == "development"
    with pytest.raises(S.SessionError):
        S.EngineSpec.from_status(EngineStatus(False, "engine_missing", "no"))


# ─── whole sessions ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,steps,model,filt", [(2000, 26, "colormunki", None), (5000, 20, "i1pro3plus", "u")])
def test_a_whole_session(tmp_path, n, steps, model, filt):
    chart, s, eng, env = setup(tmp_path, n=n, steps=steps, model=model, filt=filt,
                               type="i1pro3" if model == "i1pro3plus" else "colormunki")
    t0 = time.monotonic()
    run, ns = full(s, eng, env, tmp_path, timeout=240)
    assert run.ended["end"] == "finished" and run.ended["final"]["complete"]
    sm = reopen(s).summary()
    assert (sm["passes_saved"], sm["patches_saved"], sm["complete"]) == (len(chart.labels), n, True)
    flt = "UVCUT" if filt == "u" else None
    assert data_rows(s.validated.read_bytes()) == expected_rows(chart, instrument_filter=flt)
    assert kinds(ns).count("checkpoint") == len(chart.labels)
    assert "-r" not in reopen(s).data["runs"][0]["argv"]
    assert (s.dir / "logs" / "run1.log").stat().st_size < 1_200_000
    print(f"{n} patches: {time.monotonic() - t0:.1f} s")


def test_engine_crash_then_resume_gives_the_same_ti3(tmp_path):
    chart, s, eng, env = setup(tmp_path, n=2000, actions={"40": "kill"})
    run, _ = full(s, eng, env, tmp_path)
    assert run.ended["end"] == "exited"
    assert reopen(s).summary()["passes_saved"] == 39
    reconfigure(tmp_path, chart)
    s2 = reopen(s)
    run2, ns2 = full(s2, eng, env, tmp_path)
    argv = reopen(s).data["runs"][1]["argv"]
    assert "-r" in argv and "-N" in argv and "-K" in argv
    assert run2.ended["end"] == "finished"
    assert next(n for n in ns2 if n["kind"] == "ready")["pass"] == chart.labels[39]   # chartread's word
    assert data_rows(s2.validated.read_bytes()) == expected_rows(chart)


@pytest.mark.parametrize("how,point,expect,note", [
    ("fake", "crash_during_write", 4, ".tmp left as is"),
    ("fake", "crash_after_rename", 5, "recovered 1 pass"),
    ("fake", "crash_after_line", 5, None),
    ("hook", "after_link_tmp", 5, "removed interrupted temporary"),
    ("hook", "after_replace_validated", 5, "session.json was behind"),
    ("hook", "after_commit", 5, None),
])
def test_interruption_at_every_step_of_a_checkpoint(tmp_path, monkeypatch, how, point, expect, note):
    """(1) strip read (2) chartread writes/renames (3) freeglaz validates the linked file
    (4) renames it to validated.ti3 (5) writes session.json: stopped between any two."""
    chart, s, eng, env = setup(tmp_path, checkpoints={"5": point} if how == "fake" else {})
    if how == "hook":
        crash_at(monkeypatch, point, nth=5)
    run = start(s, eng, env, tmp_path)
    try:
        drive(run, timeout=30)
    except Crash:
        died(run)
    monkeypatch.setattr(S.Session, "_hook", lambda self, p: None)
    s2 = reopen(s)
    actions = s2.reconcile()
    assert s2.summary()["passes_saved"] == expect, actions
    if note:
        assert any(note in a for a in actions), actions
    assert not list(s2.dir.glob(".validated.*.tmp"))
    assert data_rows(s2.validated.read_bytes()) == expected_rows(chart, chart.labels[:expect])
    reconfigure(tmp_path, chart)
    run2, _ = full(reopen(s), eng, env, tmp_path)
    assert run2.ended["end"] == "finished"
    assert data_rows(s2.validated.read_bytes()) == expected_rows(chart)


def test_without_hard_links_the_validated_file_is_an_atomic_copy(tmp_path, monkeypatch):
    def no_link(src, dst):
        raise OSError(errno.EPERM, "hard links not supported")
    monkeypatch.setattr(S.os, "link", no_link)
    chart, s, eng, env = setup(tmp_path)
    crash_at(monkeypatch, "after_link_tmp", nth=3)
    run = start(s, eng, env, tmp_path)
    with pytest.raises(Crash):
        drive(run, timeout=30)
    died(run)
    monkeypatch.setattr(S.Session, "_hook", lambda self, p: None)
    s2 = reopen(s)
    actions = s2.reconcile()
    assert any("removed interrupted temporary" in a for a in actions)
    assert s2.summary()["passes_saved"] == 3 and s2.data["last_checkpoint"]["stored_as"] == "copy"
    run2, _ = full(s2, eng, env, tmp_path)
    assert run2.ended["end"] == "finished" and data_rows(s2.validated.read_bytes()) == expected_rows(chart)
    assert os.stat(s2.validated).st_ino != os.stat(s2.work_ti3).st_ino


# ─── durability and refusals ────────────────────────────────────────────────
def test_reduced_durability_goes_on_with_a_lasting_warning(tmp_path):
    chart, s, eng, env = setup(tmp_path, checkpoints={"3": "reduced", "4": "reduced"})
    run, ns = full(s, eng, env, tmp_path)
    assert run.ended["end"] == "finished"
    w = reopen(s).data["durability_warning"]
    assert w["kind"] == "reduced" and w["count"] == 2 and "F_FULLFSYNC" in w["message"]
    assert [n["durability"] for n in ns if n["kind"] == "checkpoint"][2:4] == ["reduced", "reduced"]


@pytest.mark.parametrize("mode,saved,reason", [
    ("not_durable", 3, "checkpoint_not_durable"),
    ("refused", 2, "checkpoint_refused"),
])
def test_unconfirmed_or_refused_checkpoint_suspends(tmp_path, mode, saved, reason):
    chart, s, eng, env = setup(tmp_path, checkpoints={"3": mode})
    run, ns = full(s, eng, env, tmp_path)
    assert run.ended["end"] == f"suspended:{reason}"
    s2 = reopen(s)
    sm = s2.summary()
    assert sm["passes_saved"] == saved and sm["suspended"]["reason"] == reason
    assert kinds(ns).count("strip_acquired") == 3                 # the third was read…
    if mode == "refused":
        assert sm["suspended"]["unsaved_pass"] == "C"              # …but is not on disk
    else:
        assert s2.data["last_checkpoint"]["durability"] == "not_durable"
    assert data_rows(s2.validated.read_bytes()) == expected_rows(chart, chart.labels[:saved])
    reconfigure(tmp_path, chart)
    run2, _ = full(s2, eng, env, tmp_path)
    s3 = reopen(s)
    assert run2.ended["end"] == "finished" and s3.data["suspended"] is None
    assert s3.data["last_suspension"]["reason"] == reason
    assert data_rows(s3.validated.read_bytes()) == expected_rows(chart)


def test_disk_full_on_freeglaz_side_keeps_the_last_checkpoint(tmp_path, monkeypatch):
    chart, s, eng, env = setup(tmp_path)
    real = S.atomic_write
    state = {"commits": 0, "full": False}

    def write(path, data):
        if state["full"] and path.name == "session.json":
            raise OSError(errno.ENOSPC, "No space left on device")
        real(path, data)

    def hook(self, p):
        if p == "after_replace_validated":
            state["commits"] += 1
            if state["commits"] == 3:
                state["full"] = True                              # the disk fills up now
    monkeypatch.setattr(S, "atomic_write", write)
    monkeypatch.setattr(S.Session, "_hook", hook)
    run, ns = full(s, eng, env, tmp_path)
    assert run.ended["end"] == "suspended:session_write_failed"
    state["full"] = False
    s2 = reopen(s)
    assert s2.data["last_checkpoint"]["passes_saved"] == 2        # session.json could not follow…
    actions = s2.reconcile()
    assert s2.summary()["passes_saved"] == 3                      # …validated.ti3 had the third
    assert any("session.json was behind" in a for a in actions) and any("interrupted" in a for a in actions)


def test_another_instrument_is_refused(tmp_path):
    chart, s, eng, env = setup(tmp_path, target_instrument="X-Rite i1 Pro 3")
    run, _ = full(s, eng, env, tmp_path)
    sm = reopen(s).summary()
    assert run.ended["end"] == "suspended:checkpoint_invalid"
    assert "instrument_mismatch" in sm["suspended"]["detail"] and sm["passes_saved"] == 0
    assert not s.validated.exists()


def test_another_condition_is_refused(tmp_path):
    chart, s, eng, env = setup(tmp_path, n=60, steps=20, model="i1pro3plus", filt="n", type="i1pro3",
                               instrument_filter="UVCUT")
    run, _ = full(s, eng, env, tmp_path)
    assert run.ended["end"] == "suspended:checkpoint_invalid"
    assert "filter_mismatch" in reopen(s).summary()["suspended"]["detail"]


def test_a_reliable_serial_number_change_is_refused(tmp_path):
    chart, s, eng, env = setup(tmp_path, serial="SN-1")
    full(s, eng, env, tmp_path, on_notice=pause_after(2))
    assert reopen(s).data["instrument"]["serial"] == "SN-1"
    reconfigure(tmp_path, chart, serial="SN-2")
    run, ns = full(reopen(s), eng, env, tmp_path)
    s2 = reopen(s)
    assert run.ended["end"] == "suspended:instrument_unit_changed" and s2.summary()["passes_saved"] == 2
    assert "strip_acquired" not in kinds(ns)
    reconfigure(tmp_path, chart)                                  # no serial printed: nothing invented
    run, _ = full(reopen(s), eng, env, tmp_path)
    assert run.ended["end"] == "finished" and reopen(s).data["instrument"]["serial"] == "SN-1"


def test_the_engine_must_not_change_between_runs(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(2))
    other_exe, _ = python_engine(tmp_path, chart, name="other")
    with pytest.raises(S.SessionError) as e:
        start(reopen(s), S.EngineSpec.for_test(str(other_exe)), env, tmp_path)
    assert e.value.code == "engine_changed"
    run, _ = full(reopen(s), eng, env, tmp_path)                  # the lock was released
    assert run.ended["end"] == "finished"


def _rows_edit(b: bytes, fn) -> bytes:
    head, rest = b.split(b"BEGIN_DATA\n", 1)
    rows, tail = rest.split(b"\nEND_DATA", 1)
    rows = fn(rows.split(b"\n"))
    sets = [l for l in head.split(b"\n") if l.startswith(b"NUMBER_OF_SETS")][0]
    return head.replace(sets, b"NUMBER_OF_SETS %d" % len(rows)) + b"BEGIN_DATA\n" + b"\n".join(rows) + b"\nEND_DATA" + tail


def _bump_first_xyz(rows):
    t = rows[0].split()
    t[5] = b"%.6f" % (float(t[5]) + 1)
    return [b" ".join(t)] + rows[1:]


@pytest.mark.parametrize("alter,code", [
    (lambda b: b[: len(b) // 2], "ti3_truncated"),
    (lambda b: _rows_edit(b, lambda r: r + [r[0]]), "duplicate_id"),
    (lambda b: _rows_edit(b, _bump_first_xyz), "pass_changed"),
])
def test_an_altered_work_file_is_kept_aside_never_used(tmp_path, alter, code):
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(4))
    altered = s.work_dir / "edited.tmp"                           # how editors and tools save:
    altered.write_bytes(alter(s.work_ti3.read_bytes()))           # a new file renamed over the old
    os.replace(altered, s.work_ti3)
    s2 = reopen(s)
    actions = s2.reconcile()
    assert any(code in a and "kept as" in a for a in actions), actions
    assert len(list(s2.work_dir.glob("*.unconfirmed-*"))) == 1
    assert s2.work_ti3.read_bytes() == s2.validated.read_bytes()
    run, _ = full(s2, eng, env, tmp_path)
    assert run.ended["end"] == "finished" and data_rows(s2.validated.read_bytes()) == expected_rows(chart)


def test_the_validated_inode_cannot_be_written_in_place(tmp_path):
    """validated.ti3 and the work file share one inode after a checkpoint: it is read-only,
    so a tool writing in place cannot change the validated state behind freeglaz's back."""
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(3))
    assert os.stat(s.validated).st_ino == os.stat(s.work_ti3).st_ino
    if os.geteuid() != 0:
        with pytest.raises(PermissionError):
            s.work_ti3.write_bytes(b"overwritten")
    assert reopen(s).validated_state.passes == tuple(chart.labels[:3])
    run, _ = full(reopen(s), eng, env, tmp_path)                  # chartread replaces it by renaming
    assert run.ended["end"] == "finished"


def test_a_tampered_validated_file_stops_everything(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(2))
    tampered = s.dir / "tampered.tmp"
    tampered.write_bytes(s.validated.read_bytes().replace(b"\nEND_DATA", b""))
    os.replace(tampered, s.validated)
    with pytest.raises(S.SessionError) as e:
        start(reopen(s), eng, env, tmp_path)
    assert e.value.code == "validated_invalid"
    lk = S.InstrumentLock(tmp_path / "lock")
    lk.acquire({"op": "check"})                                    # released after the refusal
    lk.release()


# ─── corrections ────────────────────────────────────────────────────────────
def test_re_reading_a_strip_during_the_session(tmp_path):
    """At D the user goes back to C and reads it again (values change): chartread announces
    "C (This row has been read)", so the change of C is expected and accepted."""
    chart, s, eng, env = setup(tmp_path, actions={"4": "scale=1.05"})
    went_back = {}

    def on(n, run):
        if n["kind"] == "ready" and n["pass"] == "D" and not went_back:
            went_back["notice"] = n
            run.command("prev")

    def press(n):
        return n is not went_back.get("notice")
    run, ns = full(s, eng, env, tmp_path, on_notice=on, press=press)
    assert run.ended["end"] == "finished"
    readies = [(n["pass"], n["read_before"]) for n in ns if n["kind"] == "ready"]
    assert ("C", True) in readies
    assert data_rows(s.validated.read_bytes()) == expected_rows(chart, scale={"C": 1.05})


def test_withdrawing_a_strip_offline_then_resuming(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(5))
    s2 = reopen(s)
    st = s2.remove_pass("B", S.InstrumentLock(tmp_path / "lock"))
    assert "B" not in st.passes and s2.summary()["passes_saved"] == 4
    (backup,) = (s2.dir / "corrections").iterdir()
    assert backup.name.startswith("001_B_") and data_rows(backup.read_bytes()) == expected_rows(chart, chart.labels[:5])
    assert s2.data["corrections"][0]["pass"] == "B" and "pending_correction" not in s2.data
    run, ns = full(reopen(s), eng, env, tmp_path)
    assert next(n for n in ns if n["kind"] == "ready")["pass"] == "B"   # chartread asks for B again
    assert run.ended["end"] == "finished" and data_rows(s.validated.read_bytes()) == expected_rows(chart)


def test_an_interrupted_withdrawal_is_completed_or_dropped(tmp_path, monkeypatch):
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(5))
    crash_at(monkeypatch, "correction_after_work", nth=1)
    with pytest.raises(Crash):
        reopen(s).remove_pass("C", S.InstrumentLock(tmp_path / "lock"))
    monkeypatch.setattr(S.Session, "_hook", lambda self, p: None)
    s2 = reopen(s)
    actions = s2.reconcile()
    assert any("completed the interrupted removal of pass C" in a for a in actions)
    assert "C" not in s2.validated_state.passes and s2.data["corrections"][-1]["pass"] == "C"
    real = S.atomic_write

    def no_work(path, data):
        if path.name.endswith(".ti3") and path.parent.name == "work":
            raise Crash("before the work file")
        real(path, data)
    monkeypatch.setattr(S, "atomic_write", no_work)
    with pytest.raises(Crash):
        reopen(s).remove_pass("D", S.InstrumentLock(tmp_path / "lock"))
    monkeypatch.setattr(S, "atomic_write", real)
    s3 = reopen(s)
    actions = s3.reconcile()
    assert any("removal of pass D had not started" in a for a in actions)
    assert "D" in s3.validated_state.passes


# ─── running ────────────────────────────────────────────────────────────────
def test_one_run_at_a_time_across_processes(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    run = start(s, eng, env, tmp_path)
    with pytest.raises(S.LockBusy) as e:
        start(reopen(s), eng, env, tmp_path)
    assert e.value.info["holder"]["session_id"] == s.data["session_id"]
    run.stop_now("test")
    holder = subprocess.Popen([sys.executable, "-c", (
        "import sys, time; sys.path.insert(0, %r)\n"
        "from lib.z9_client.chartread_fgk.session import InstrumentLock\n"
        "l = InstrumentLock(%r); l.acquire({'op': 'other freeglaz'}); print('held', flush=True); time.sleep(30)")
        % (str(S.Path(__file__).resolve().parents[3]), str(tmp_path / "lock"))], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(S.LockBusy) as e:
            start(reopen(s), eng, env, tmp_path)
        assert e.value.info["holder"]["pid"] == holder.pid
    finally:
        holder.kill()
        holder.wait(5)
    run, _ = full(reopen(s), eng, env, tmp_path)                  # freed by the kernel
    assert run.ended["end"] == "finished"


def test_waiting_at_a_strip_has_no_time_limit(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    run = R.Run.start(s, eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env,
                      phase_limits={"starting": 1, "calibrating": 1, "saving": 1, "finishing": 1})
    drive(run, press=lambda n: False, until=lambda n: n["kind"] == "ready")
    end = time.monotonic() + 3
    while time.monotonic() < end:
        run.poll(0.05)
    assert run.ended is None and run.phase == "ready" and run.error is None
    run.command("pause")
    drive(run, timeout=10)
    assert run.ended["end"] == "user_pause" and not run.proc.alive


def test_a_silent_start_is_a_timeout(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    mute = tmp_path / "mute.sh"
    mute.write_text("#!/bin/sh\nexec sleep 30\n")
    mute.chmod(0o755)
    s.data["engine"] = S.EngineSpec.for_test(str(mute)).to_dict()
    s._save()
    run = R.Run.start(s, S.EngineSpec.for_test(str(mute)), lock=S.InstrumentLock(tmp_path / "lock"),
                      env=env, phase_limits={"starting": 1.0})
    drive(run, timeout=10)
    assert run.ended["end"] == "error:engine_timeout" and not run.proc.alive


def test_shutdown_fits_the_window(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    run = start(s, eng, env, tmp_path)
    drive(run, press=lambda n: n["pass"] != "C", until=lambda n: n["kind"] == "ready" and n["pass"] == "C")
    t0 = time.monotonic()
    run.shutdown(budget=4.0)
    assert time.monotonic() - t0 < 4.5 and run.ended["end"] == "backend_stop" and not run.proc.alive
    assert reopen(s).summary()["passes_saved"] == 2


def test_losing_the_instrument_stops_and_keeps_the_saved_strips(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    state = {"present": True}

    def presence():
        return Presence("present" if state["present"] else "absent", "colormunki")

    def on(n, run):
        if n["kind"] == "checkpoint" and n["passes_saved"] == 3:
            state["present"] = False
    run = R.Run.start(s, eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env, presence=presence)
    drive(run, on_notice=on, press=lambda n: state["present"], timeout=20)
    assert run.ended["end"] == "error:instrument_lost" and not run.proc.alive
    assert reopen(s).summary()["passes_saved"] == 3


def test_abandon_keeps_the_files(tmp_path):
    chart, s, eng, env = setup(tmp_path)
    full(s, eng, env, tmp_path, on_notice=pause_after(2))
    s2 = reopen(s)
    s2.abandon(S.InstrumentLock(tmp_path / "lock"))
    assert reopen(s).data["status"] == "abandoned" and s2.validated.exists()
    with pytest.raises(S.SessionError) as e:
        start(reopen(s), eng, env, tmp_path)
    assert e.value.code == "session_closed"
    assert S.Session.find_open(chart.dir) is None


def test_an_engine_swapped_right_before_it_starts_is_refused(tmp_path):
    """The engine file is hashed again after session.json is written, just before execve:
    a substitution between the check and the start is refused, recorded, nothing started."""
    chart, s, eng, env = setup(tmp_path)
    exe = Path(eng.path)

    def swap(point):
        if point == "after_save_json" and exe.read_bytes().startswith(b"#!/bin/sh"):
            exe.write_bytes(exe.read_bytes().replace(b"#!/bin/sh", b"#!/bin/sh\n# swapped", 1))
    s._hook = swap
    with pytest.raises(S.SessionError) as e:
        start(s, eng, env, tmp_path)
    assert e.value.code == "engine_changed"
    s2 = S.Session.open(s.dir)
    assert s2.data["runs"][-1]["end"] == "not_started" and s2.data["runs"][-1]["error"] == "engine_changed"
    assert not S.InstrumentLock(tmp_path / "lock").held
    lk = S.InstrumentLock(tmp_path / "lock")
    lk.acquire({"op": "probe"})                               # the run released it
    lk.release()


_CREATE_SCRIPT = """
import sys, time
from pathlib import Path
from lib.z9_client.chartread_fgk import session as S
chart_dir, exe, go = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
eng = S.EngineSpec.for_test(exe)
while not go.exists():
    time.sleep(0.001)
try:
    S.Session.create(chart_dir, model="colormunki", filter_option=None, engine=eng)
    print("ok")
except S.SessionError as e:
    print(e.code)
"""


def test_two_processes_never_create_two_open_sessions(tmp_path):
    """Two freeglaz processes on the same store, the same chart, the same instant: one session
    is created, the other process is told one exists (before the fix: two, 20 times in 20)."""
    repo = Path(__file__).resolve().parents[3]
    for trial in range(6):
        root = tmp_path / f"t{trial}"
        chart = make_chart(root, n_useful=52, steps=26)
        exe, _ = python_engine(root, chart)
        go = root / "go"
        procs = [subprocess.Popen([sys.executable, "-c", _CREATE_SCRIPT, str(chart.dir), str(exe), str(go)],
                                  cwd=repo, stdout=subprocess.PIPE, text=True) for _ in range(2)]
        time.sleep(0.3)
        go.touch()
        outs = sorted(p.communicate(timeout=30)[0].strip() for p in procs)
        assert outs == ["ok", "session_exists"], outs
        assert len([s for s in S.Session.sessions(chart.dir) if s.data["status"] == "open"]) == 1
