"""Increment 6: the REAL chartread-fgK (official ArgyllCMS 3.5.0 chartread.c + checkpoint
patch v3, built by the lab) linked to the lab's FAKE INSTRUMENT, driven through freeglaz:
Session, Run, the backend service and its HTTP routes, the publication contract, QC and the
profile plan. Every acquisition is synthetic and lives in a throw-away store.

Opt-in, skipped in CI (no such binary there):
  FREEGLAZ_TEST_CHARTREAD_FAKEINST       chartread-ckpt-fake (v3 + fg_fakeinst.c)     required
  FREEGLAZ_TEST_CHARTREAD_STOCK          chartread-stock-fake (official, unpatched)  continuous reference
  FREEGLAZ_TEST_CHARTREAD_FAKEINST_INJ   v3 + fg_fakeinst_inj.c (comms, usbflood, hang, spin)
  strace on PATH                         fault injection on the engine's own file calls

Hardware, by construction: a binary is accepted only if it carries the fake instrument's
text ("this binary only drives a fake instrument") and no USB device path; the fake
replaces new_inst()/new_icompaths() at link time, so no USB or serial code is linked at all.
Each scenario says what drives the fault: the real chartread.c reacting to the fake
instrument, or a controlled injection (strace) on the engine's own system calls.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.z9_client import measurement_identity as mi
from lib.z9_client.chartread_fgk import engine as E
from lib.z9_client.chartread_fgk import run as R
from lib.z9_client.chartread_fgk import session as S
from lib.z9_client.chartread_fgk.prompts import KeyGate, PromptParser
from lib.z9_client.chartread_fgk.pty_process import PtyProcess
from lib.z9_client.chartread_fgk.transcript import ActivityMonitor, Transcript
from lib.z9_client.chartread_fgk.usb_presence import Presence
from webapp.backend.main import app
from webapp.backend.services import external_measure as em
from webapp.backend.tests.session_harness import (NB, data_rows, drive, fakeinst_engine, kinds, make_chart,
                                                  trust_build)

FAKEINST = os.environ.get("FREEGLAZ_TEST_CHARTREAD_FAKEINST")
STOCK = os.environ.get("FREEGLAZ_TEST_CHARTREAD_STOCK")
INJ = os.environ.get("FREEGLAZ_TEST_CHARTREAD_FAKEINST_INJ")
STRACE = shutil.which("strace")
REPO = Path(__file__).resolve().parents[3]
FAKE_TEXT = b"this binary only drives a fake instrument"


def _fake_only(path):
    data = Path(path).read_bytes()
    return FAKE_TEXT in data and b"/dev/bus/usb" not in data and b"usbdevfs" not in data


pytestmark = pytest.mark.skipif(not (FAKEINST and os.access(FAKEINST, os.X_OK)),
                                reason="FREEGLAZ_TEST_CHARTREAD_FAKEINST not set")
needs_inj = pytest.mark.skipif(not (INJ and os.access(INJ, os.X_OK)),
                               reason="FREEGLAZ_TEST_CHARTREAD_FAKEINST_INJ not set")
needs_strace = pytest.mark.skipif(STRACE is None, reason="strace not installed")
needs_stock = pytest.mark.skipif(not (STOCK and os.access(STOCK, os.X_OK)),
                                 reason="FREEGLAZ_TEST_CHARTREAD_STOCK not set")


@pytest.fixture(autouse=True)
def _fake_instrument_binaries_only():
    for b in (FAKEINST, STOCK, INJ):
        if b:
            assert _fake_only(b), f"{b} is not a fake-instrument build: refused"


# ─── helpers ────────────────────────────────────────────────────────────────
def setup(tmp, n=520, *, binary=None, actions="", extra=None, chart_id="CHT-20261124-1015-AB"):
    chart = make_chart(tmp, n_useful=n, steps=26, chart_id=chart_id)
    _, env = fakeinst_engine(tmp, chart, actions=actions, extra=extra)
    eng = S.EngineSpec.for_test(str(binary or FAKEINST))
    s = S.Session.create(chart.dir, model="colormunki", filter_option=None, engine=eng)
    return chart, s, eng, env


def go(s, eng, env, tmp, *, on=None, timeout=240, press=None, until=None, **run_kw):
    """One engine run, the user's side simulated: calibrate when asked, press the instrument's
    button at each unread strip prompt (``press`` may refuse), retry a misread."""
    r = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp / "lock"), env=env, **run_kw)

    def handler(n, rr):
        if n["kind"] in ("misread", "comms_failed", "wrong_strip") and not rr.stopping:
            rr.command("retry")
        if on:
            on(n, rr)
    return r, drive(r, timeout=timeout, on_notice=handler, press=press, until=until)


def group_gone(pgid, wait=3.0):
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def assert_identical_to_continuous(s, ref_rows):
    rows = data_rows(S.Session.open(s.dir).validated.read_bytes())
    assert len(rows) == len(ref_rows) and len(set(r.split()[0] for r in rows)) == len(rows)
    assert rows == ref_rows


_REF: dict = {}


def continuous_rows(tmp, n):
    """Data rows of one uninterrupted session with the same engine (cached per size)."""
    if n not in _REF:
        d = tmp / f"continuous{n}"
        d.mkdir()
        chart, s, eng, env = setup(d, n)
        r, _ = go(s, eng, env, d)
        assert r.ended["end"] == "finished"
        _REF[n] = data_rows(S.Session.open(s.dir).validated.read_bytes())
    return _REF[n]


def stock_rows(tmp, chart):
    """The OFFICIAL chartread (no -K) on the same chart and the same fake instrument, read in
    one go and saved at the end with 'd': what freeglaz must reproduce."""
    _, env = fakeinst_engine(tmp, chart)
    work = tmp / "stock"
    work.mkdir()
    shutil.copy(chart.ti2, work / f"{chart.chart_id}.ti2")
    proc = PtyProcess.spawn([STOCK, "-v", "-c", "1", chart.chart_id], cwd=str(work), env=env)
    tr, parser, gate = Transcript(None), PromptParser(), KeyGate(proc.write, min_delay=0.05)
    end = time.monotonic() + 300
    try:
        while time.monotonic() < end:
            data = proc.read(0.05)
            now = time.monotonic()
            if data is None:
                break
            if data:
                gate.on_output(now)
                for ev in parser.feed(tr.feed(data, now), now):
                    gate.on_event(ev)
                    if ev.kind == "calibration_prompt":
                        gate.request("continue")
                    elif ev.kind == "ready":
                        assert not ev.data["checkpointing"]               # the official one: no -K
                        if ev.data["all_read"]:
                            gate.request("finish")
                        else:
                            proc.write(b" ")
            gate.tick(time.monotonic())
    finally:
        proc.close()
    return data_rows((work / f"{chart.chart_id}.ti3").read_bytes())


def strace_engine(tmp, *args, binary=None):
    """The real engine under strace, which injects one fault on its own system calls. The
    wrapper is a test engine (its own sha256); the PTY, the process group and the
    controlling terminal stay those freeglaz set up."""
    exe = tmp / "strace-engine.sh"
    log = tmp / "strace.log"
    quoted = " ".join(f"'{a}'" for a in args)
    exe.write_text(f'#!/bin/sh\nexec "{STRACE}" -f -qq -o "{log}" {quoted} "{binary or FAKEINST}" "$@"\n')
    exe.chmod(0o755)
    return S.EngineSpec.for_test(str(exe))


def rss_kb(pid):
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


# ─── acquisition ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [2000, 5000])
def test_whole_charts_every_checkpoint_validated(tmp_path, n, request):
    """2 000 and 5 000 patches in one run: one validated checkpoint per strip pass, the
    validated file always whole passes, exactly the useful patches (no padding id 0, no
    duplicate, no missing row), and the bytes freeglaz validated are the work file."""
    chart, s, eng, env = setup(tmp_path, n)
    seen = []

    def on(nt, rr):
        if nt["kind"] == "checkpoint":
            v = rr.session.validated_state
            seen.append((len(v.passes), v.n_patches, v.complete))
    t0 = time.monotonic()
    r, ns = go(s, eng, env, tmp_path, on=on)
    elapsed = time.monotonic() - t0
    s2 = S.Session.open(s.dir)
    sm = s2.summary()
    assert r.ended["end"] == "finished" and sm["complete"] and sm["patches_saved"] == n
    assert len(seen) == len(chart.labels) == kinds(ns).count("checkpoint")
    assert [p for p, _, _ in seen] == list(range(1, len(chart.labels) + 1))
    rows = data_rows(s2.validated.read_bytes())
    ids = [r_.split()[0] for r_ in rows]
    assert len(ids) == len(set(ids)) == n and "0" not in ids
    assert set(ids) == {str(i) for i in range(1, n + 1)}
    assert s2.validated.read_bytes() == s2.work_ti3.read_bytes()
    assert s2.data["runs"][0]["argv"][1:] == ["-v", "-c", "1", "-K", chart.chart_id]
    request.node.user_properties.append(("elapsed_s", round(elapsed, 2)))
    print(f"\n[real v3] {n} patches, {len(chart.labels)} passes, one run: {elapsed:.1f} s")


@needs_stock
@pytest.mark.parametrize("n", [2000, 5000])
def test_freeglaz_session_equals_the_official_chartread_read_in_one_go(tmp_path, n):
    """Same chart, same fake instrument: the TI3 freeglaz validated with -K, read in one go,
    has exactly the data rows the OFFICIAL chartread writes at the end of one session."""
    chart, s, eng, env = setup(tmp_path, n)
    go(s, eng, env, tmp_path)
    assert data_rows(S.Session.open(s.dir).validated.read_bytes()) == stock_rows(tmp_path, chart)


# ─── stops and resumes (each compared with a continuous session) ───────────
def test_voluntary_pauses_then_resumes(tmp_path):
    """Three pauses in one session (at strips 5, 11, 17), each resumed with -r -N -K."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)
    for stop_at in (5, 11, 17):
        def on(nt, rr, k=stop_at):
            if nt["kind"] == "checkpoint" and nt["passes_saved"] == k and not rr.stopping:
                rr.command("pause")
        r, _ = go(s, eng, env, tmp_path / "s", on=on)
        assert r.ended["end"] == "user_pause"
        assert S.Session.open(s.dir).summary()["passes_saved"] == stop_at
    r, ns = go(s, eng, env, tmp_path / "s")
    s2 = S.Session.open(s.dir)
    assert r.ended["end"] == "finished" and len(s2.data["runs"]) == 4
    assert all({"-r", "-N", "-K"} <= set(x["argv"]) for x in s2.data["runs"][1:])
    assert "calibration_prompt" not in kinds(ns)                         # -N: calibration kept
    assert_identical_to_continuous(s, ref)


def test_stop_while_the_instrument_is_reading(tmp_path):
    """The graduated stop (Ctrl-C, SIGTERM, SIGKILL) while a strip is being read (fake read
    time 1.5 s): the strip in flight is lost, nothing validated is, the resume reads it."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520, extra={"FG_FAKEINST_READ_MS": "1500"})
    holder = {}

    def on(nt, rr):
        if nt["kind"] == "ready" and nt["pass"] == chart.labels[3] and not rr.stopping:
            holder["t"] = time.monotonic()
    r = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env)
    end = time.monotonic() + 60
    while r.ended is None and time.monotonic() < end:
        for nt in r.poll(0.02):
            if nt["kind"] == "calibration_prompt":
                r.command("calibrate")
            elif nt["kind"] == "ready" and not nt["all_read"]:
                r.proc.write(b" ")
                on(nt, r)
        if "t" in holder and time.monotonic() - holder["t"] > 0.5:
            pgid = r.proc.pgid
            r.stop_now("test_stop_mid_read")
            assert group_gone(pgid)
    assert S.Session.open(s.dir).summary()["passes_saved"] == 3
    r2, ns = go(s, eng, {**env, "FG_FAKEINST_READ_MS": "0"}, tmp_path / "s")
    assert next(x for x in ns if x["kind"] == "ready")["pass"] == chart.labels[3]
    assert_identical_to_continuous(s, ref)


@needs_strace
@pytest.mark.parametrize("delayed,validated_after,recovered", [
    ("file", 2, False),        # killed while the .tmp is flushed: the third pass is lost
    ("dir", 2, True),          # killed after the rename, during the directory flush
])
def test_engine_killed_while_writing_a_checkpoint(tmp_path, delayed, validated_after, recovered):
    """INJECTION (strace delays chartread's own fsync of the third checkpoint by 4 s), then
    SIGKILL of the whole group during that delay. Freeglaz keeps what it validated; the
    reconciliation adopts a renamed-but-unvalidated file only when it extends the validated
    state; chartread's .tmp is never recovered; the resume ends identical."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, _, env = setup(tmp_path / "s", 520)
    work = s.work_dir
    target = work / f"{chart.chart_id}.ti3.tmp" if delayed == "file" else work
    eng = strace_engine(tmp_path, "-e", "trace=fsync", "-P", str(target),
                        "-e", "inject=fsync:delay_enter=4s:when=3")
    s.data["engine"] = eng.to_dict()
    s._save()
    r = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env)
    t_ok = None
    end = time.monotonic() + 60
    while r.ended is None and time.monotonic() < end:
        for nt in r.poll(0.02):
            if nt["kind"] == "calibration_prompt":
                r.command("calibrate")
            elif nt["kind"] == "ready" and not nt["all_read"]:
                r.proc.write(b" ")
            elif nt["kind"] == "strip_acquired" and nt["pass"] == chart.labels[2]:
                t_ok = time.monotonic()
        if t_ok and time.monotonic() - t_ok > 1.0:
            os.killpg(r.proc.pgid, signal.SIGKILL)
            t_ok = None
    assert r.ended is not None
    left = sorted(p.name for p in work.iterdir())
    s2 = S.Session.open(s.dir)
    assert s2.summary()["passes_saved"] == validated_after
    actions = s2.reconcile()
    assert (f"{chart.chart_id}.ti3.tmp" in left) == (delayed == "file")
    assert any("recovered 1 pass" in a for a in actions) == recovered
    assert S.Session.open(s.dir).summary()["passes_saved"] == validated_after + (1 if recovered else 0)
    eng2 = S.EngineSpec.for_test(FAKEINST)
    s3 = S.Session.open(s.dir)
    s3.data["engine"] = eng2.to_dict()
    s3._save()
    r2, ns = go(s3, eng2, env, tmp_path / "s")
    assert r2.ended["end"] == "finished"
    assert next(x for x in ns if x["kind"] == "ready")["pass"] == chart.labels[2 + (1 if recovered else 0)]
    assert_identical_to_continuous(s, ref)


def test_engine_sigkill_from_the_outside_at_a_prompt(tmp_path):
    """SIGKILL of chartread alone (not its group) while it waits at a strip prompt."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)

    def on(nt, rr):
        if nt["kind"] == "ready" and nt["pass"] == chart.labels[6]:
            os.kill(rr.proc.pid, signal.SIGKILL)
    r, _ = go(s, eng, env, tmp_path / "s", press=lambda nt: nt["pass"] != chart.labels[6], on=on)
    assert r.ended["end"] in ("exited", "engine_error") and r.ended["exit_code"] == -signal.SIGKILL
    assert S.Session.open(s.dir).summary()["passes_saved"] == 6
    go(s, eng, env, tmp_path / "s")
    assert_identical_to_continuous(s, ref)


_DRIVER = r"""
import json, os, sys, time
from pathlib import Path
from lib.z9_client.chartread_fgk import run as R, session as S
sdir, exe, envfile, lock, stop_after, point, nth = sys.argv[1:8]
stop_after, nth = int(stop_after), int(nth)
env = json.loads(Path(envfile).read_text())
s = S.Session.open(Path(sdir))
count = {}
def hook(p):
    count[p] = count.get(p, 0) + 1
    if p == point and count[p] == nth:
        print("DYING", p, flush=True)
        os.kill(os.getpid(), 9)
s._hook = hook
r = R.Run.start(s, S.EngineSpec.for_test(exe), lock=S.InstrumentLock(Path(lock)), env=env)
print("PID", r.proc.pid, r.proc.pgid, flush=True)
saved = 0
while r.ended is None:
    for n in r.poll(0.02):
        if n["kind"] == "calibration_prompt":
            r.command("calibrate")
        elif n["kind"] == "checkpoint":
            saved = n["passes_saved"]
        elif n["kind"] == "ready" and not n["all_read"]:
            if saved >= stop_after:
                print("READY", n["pass"], flush=True)
            else:
                r.proc.write(b" ")
"""


def _driver(tmp, s, env, *, stop_after=999, point="", nth=0):
    envfile = tmp / "driver_env.json"
    envfile.write_text(json.dumps(env))
    return subprocess.Popen([sys.executable, "-c", _DRIVER, str(s.dir), FAKEINST, str(envfile),
                             str(tmp / "lock"), str(stop_after), point, str(nth)],
                            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


def test_driver_sigkill_leaves_no_engine_and_resumes(tmp_path):
    """freeglaz itself killed (SIGKILL) while chartread waits at a strip prompt: the kernel
    hangs up the PTY, chartread exits (no orphan holds the instrument), the store lock is
    free, and the session resumes exactly."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)
    p = _driver(tmp_path, s, env, stop_after=8)
    pid = pgid = None
    for line in p.stdout:
        if line.startswith("PID"):
            pid, pgid = map(int, line.split()[1:3])
        if line.startswith("READY"):
            break
    p.kill()
    p.wait(10)
    assert group_gone(pgid, wait=3.0), "chartread survived its driver"
    lk = S.InstrumentLock(tmp_path / "lock")
    lk.acquire({"op": "probe"})
    lk.release()
    s2 = S.Session.open(s.dir)
    assert s2.data["runs"][0]["ended_at"] is None and s2.summary()["passes_saved"] == 8
    go(s2, eng, env, tmp_path / "s")
    s3 = S.Session.open(s.dir)
    assert s3.data["runs"][0]["end"] == "interrupted"
    assert_identical_to_continuous(s, ref)


@pytest.mark.parametrize("point", ["after_link_tmp", "after_replace_validated", "after_save_json"])
def test_driver_killed_between_two_persistence_steps(tmp_path, point):
    """freeglaz killed INSIDE a checkpoint commit (after linking the temporary, after renaming
    it to validated.ti3, after writing session.json) at the 4th strip: the reconciliation
    makes the three files agree, nothing validated is lost, the end is identical."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)
    nth = 4 if point != "after_save_json" else 5          # create() saved once before the run
    p = _driver(tmp_path, s, env, point=point, nth=nth)
    out = p.communicate(timeout=60)[0]
    assert "DYING" in out
    s2 = S.Session.open(s.dir)
    actions = s2.reconcile()
    v = S.Session.open(s.dir).summary()["passes_saved"]
    assert v >= 3 and not list(s.dir.glob(".validated.*.tmp"))
    go(S.Session.open(s.dir), eng, env, tmp_path / "s")
    assert_identical_to_continuous(s, ref)
    print(f"\n[{point}] reconciliation: {actions}")


# ─── reading errors, as chartread itself reacts to them ────────────────────
def test_wrong_strip_misread_reversed_and_recalibration(tmp_path):
    """Fake instrument: trigger 2 reads pass E instead of B (chartread's own wrong-strip
    check), 4 misreads, 6 is read in reverse (chartread flips it), 8 asks for a calibration.
    freeglaz offers only retry/give up/pause at the wrong strip, never "keep it"."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520, actions="2:as=E,4:misread,6:reverse,8:needcal")
    r, ns = go(s, eng, env, tmp_path / "s")
    k = kinds(ns)
    ws = next(x for x in ns if x["kind"] == "wrong_strip")
    assert (ws["read"], ws["expected"]) == ("E", "B")
    assert "misread" in k and k.count("calibration_prompt") == 2
    assert any(x.get("reversed") for x in ns if x["kind"] == "strip_acquired")
    assert r.ended["end"] == "finished"
    assert_identical_to_continuous(s, ref)


def test_rereading_an_acquired_strip(tmp_path):
    """Back to an acquired pass ('b'), read it again: chartread announces it as already read,
    freeglaz accepts its new values for that pass only (here identical: same end), then
    goes on with the unread ones."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)
    state = {"back": None}

    def on(nt, rr):
        if nt["kind"] == "ready" and nt["pass"] == chart.labels[4] and state["back"] is None:
            state["back"] = nt                                            # this prompt: no button
            rr.command("prev")
    r, ns = go(s, eng, env, tmp_path / "s", on=on, press=lambda nt: nt is not state["back"])
    reread = [x for x in ns if x["kind"] == "ready" and x["read_before"]]
    assert reread and reread[0]["pass"] == chart.labels[3]
    cks = [x for x in ns if x["kind"] == "checkpoint"]
    assert len(cks) == len(chart.labels) + 1                             # one more: the re-read
    assert_identical_to_continuous(s, ref)


def test_a_uniform_error_is_not_flagged_by_chartread(tmp_path):
    """FACT of chartread.c: its strip check normalises Y over the strip, so a whole strip read
    three times too high is accepted without any warning (only a lone wrong patch is seen).
    A wrong calibration of the whole instrument is not caught there: QC must."""
    chart, s, eng, env = setup(tmp_path, 260, actions="3:scale=3.0")
    r, ns = go(s, eng, env, tmp_path)
    assert r.ended["end"] == "finished"
    assert not {"unexpected", "wrong_strip"} & set(kinds(ns))


@needs_inj
@pytest.mark.parametrize("choice", ["retry", "accept"])
def test_unexpected_response_warning(tmp_path, choice):
    """INJECTION: on trigger 3 one patch of the strip reads 6 times too high: chartread's own
    DeltaE check warns. Retry reads it again (same end as a continuous session); accept keeps
    the values and records the acceptance in the session (pass, DeltaE, run)."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520, binary=INJ, actions="3:spike=5")

    def on(nt, rr):
        if nt["kind"] == "unexpected":
            rr.command(choice)
    r, ns = go(s, eng, env, tmp_path / "s", on=on)
    ux = next(x for x in ns if x["kind"] == "unexpected")
    assert ux["delta_e"] >= 30
    s2 = S.Session.open(s.dir)
    assert r.ended["end"] == "finished" and s2.summary()["complete"]
    if choice == "retry":
        assert_identical_to_continuous(s, ref)
        assert not s2.data["accepted_warnings"]
    else:
        acc = s2.data["accepted_warnings"]
        assert len(acc) == 1 and acc[0]["pass"] == chart.labels[2] and acc[0]["kind"] == "unexpected"
        rows = data_rows(s2.validated.read_bytes())
        diff = [i for i, (a, b) in enumerate(zip(rows, ref)) if a != b]
        assert len(diff) == 1 and rows[diff[0]].split()[1].strip('"').startswith(chart.labels[2])


def test_withdrawal_outside_the_session_then_resume(tmp_path):
    """A pass withdrawn offline (corrections/ keeps the old file): chartread -r asks for it
    again, the session ends identical."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)

    def on(nt, rr):
        if nt["kind"] == "checkpoint" and nt["passes_saved"] == 9 and not rr.stopping:
            rr.command("pause")
    go(s, eng, env, tmp_path / "s", on=on)
    S.Session.open(s.dir).remove_pass(chart.labels[5], S.InstrumentLock(tmp_path / "lock"))
    s2 = S.Session.open(s.dir)
    assert s2.summary()["passes_saved"] == 8 and s2.data["corrections"][0]["pass"] == chart.labels[5]
    assert len(list((s.dir / "corrections").iterdir())) == 1
    r, ns = go(s2, eng, env, tmp_path / "s")
    assert next(x for x in ns if x["kind"] == "ready")["pass"] == chart.labels[5]
    assert_identical_to_continuous(s, ref)


# ─── incidents ──────────────────────────────────────────────────────────────
@needs_inj
def test_usb_flood_after_unplugging(tmp_path):
    """INJECTION in the fake instrument: right after strip 4, a thread prints the macOS USB
    loss line (note 139) without end, while the REAL chartread waits at its prompt. freeglaz
    stops on the flood within seconds, the log stays bounded, the resume ends identical."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520, binary=INJ, actions="4:usbflood")
    t0 = time.monotonic()
    r, ns = go(s, eng, env, tmp_path / "s", timeout=60, press=lambda nt: nt["pass"] not in chart.labels[4:])
    assert r.ended["error"]["kind"] == "output_flood" and time.monotonic() - t0 < 15
    log = s.dir / "logs" / "run1.log"
    assert log.stat().st_size < (1 << 20) + (256 << 10)
    assert S.Session.open(s.dir).summary()["passes_saved"] == 4
    r2, _ = go(s, eng, env | {"FG_FAKEINST_ACTIONS": ""}, tmp_path / "s")
    assert_identical_to_continuous(s, ref)


@needs_inj
def test_communication_failure_prompt(tmp_path):
    """INJECTION: the instrument returns inst_coms_fail on trigger 3; the REAL chartread shows
    its communication-failure prompt; freeglaz answers retry; same end."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520, binary=INJ, actions="3:comms")
    r, ns = go(s, eng, env, tmp_path / "s")
    assert "comms_failed" in kinds(ns) and r.ended["end"] == "finished"
    assert_identical_to_continuous(s, ref)


@needs_inj
def test_hung_engine_is_stopped_by_a_pause(tmp_path):
    """INJECTION: the instrument never returns from a read (no output, no CPU). Nothing tells
    a hang from a user who has not pressed the button yet: the user's Pause is what stops it,
    and it escalates to the graduated stop after its deadline. Nothing validated is lost."""
    chart, s, eng, env = setup(tmp_path, 520, binary=INJ, actions="5:hang")
    r = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env)
    t_hang = None
    end = time.monotonic() + 60
    while r.ended is None and time.monotonic() < end:
        for nt in r.poll(0.02):
            if nt["kind"] == "calibration_prompt":
                r.command("calibrate")
            elif nt["kind"] == "ready" and not nt["all_read"]:
                r.proc.write(b" ")
                if nt["pass"] == chart.labels[4]:
                    t_hang = time.monotonic()
        if t_hang and time.monotonic() - t_hang > 2 and not r.stopping:
            t_pause = time.monotonic()
            r.pause("user_pause", deadline=3.0)
    elapsed = time.monotonic() - t_pause
    assert r.ended["end"] == "user_pause" and 3.0 <= elapsed < 6.5
    assert group_gone(r.proc.pgid)
    assert S.Session.open(s.dir).summary()["passes_saved"] == 4


@needs_inj
def test_spinning_engine(tmp_path):
    """INJECTION: the instrument loops on the CPU after a trigger, printing nothing (how a
    lost instrument may look on Linux). The CPU probe stops it."""
    chart, s, eng, env = setup(tmp_path, 520, binary=INJ, actions="3:spin")
    t0 = time.monotonic()
    r, ns = go(s, eng, env, tmp_path, monitor=ActivityMonitor(spin_seconds=3.0), timeout=60)
    assert r.ended["error"]["kind"] == "engine_spinning" and time.monotonic() - t0 < 20
    assert S.Session.open(s.dir).summary()["passes_saved"] == 2


def test_instrument_unplugged_seen_by_the_presence_probe(tmp_path):
    """The passive presence probe (simulated here) reports the instrument gone during the
    session: freeglaz stops the REAL chartread at once; the resume is refused while it is
    absent (service level), then completes identically."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520, extra={"FG_FAKEINST_READ_MS": "400"})
    plugged = {"v": True}

    def presence():
        return Presence("present" if plugged["v"] else "absent", "colormunki", detail="test")

    def on(nt, rr):
        if nt["kind"] == "checkpoint" and nt["passes_saved"] == 7:
            plugged["v"] = False
    r, _ = go(s, eng, env, tmp_path / "s", on=on, presence=presence)
    assert r.ended["error"]["kind"] == "instrument_lost"
    assert 7 <= S.Session.open(s.dir).summary()["passes_saved"] < len(chart.labels)
    go(s, eng, {**env, "FG_FAKEINST_READ_MS": "0"}, tmp_path / "s")
    assert_identical_to_continuous(s, ref)


@needs_strace
def test_disk_full_refuses_the_checkpoint(tmp_path):
    """INJECTION: writes to chartread's .tmp fail with ENOSPC during run 2. chartread prints
    "Checkpoint not saved"; freeglaz suspends the session, records the unsaved pass, keeps
    the validated state; run 3 (no fault) reads that pass again; same end."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)

    def pause_at(k):
        def on(nt, rr):
            if nt["kind"] == "checkpoint" and nt["passes_saved"] == k and not rr.stopping:
                rr.command("pause")
        return on
    go(s, eng, env, tmp_path / "s", on=pause_at(6))
    full = strace_engine(tmp_path, "-e", "trace=write", "-P", str(s.work_dir / f"{chart.chart_id}.ti3.tmp"),
                         "-e", "inject=write:error=ENOSPC")
    s2 = S.Session.open(s.dir)
    s2.data["engine"] = full.to_dict()
    s2._save()
    r, ns = go(s2, full, env, tmp_path / "s")
    s3 = S.Session.open(s.dir)
    assert s3.data["suspended"]["reason"] == "checkpoint_refused"
    print(f"\n[disk full] chartread said: {s3.data['suspended']['detail']}")
    assert s3.data["suspended"]["unsaved_pass"] == chart.labels[6]
    assert s3.summary()["passes_saved"] == 6
    s3.data["engine"] = eng.to_dict()
    s3._save()
    r3, ns3 = go(s3, eng, env, tmp_path / "s")
    assert next(x for x in ns3 if x["kind"] == "ready")["pass"] == chart.labels[6]
    assert S.Session.open(s.dir).data["last_suspension"]["reason"] == "checkpoint_refused"
    assert_identical_to_continuous(s, ref)


@needs_strace
def test_directory_flush_failure_is_not_durable(tmp_path):
    """INJECTION: the directory fsync after the 2nd rename fails (EIO). chartread says
    "Checkpoint not durable"; freeglaz validates the file (it is in place) but suspends
    the session; the resume ends identical."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, _, env = setup(tmp_path / "s", 520)
    eng = strace_engine(tmp_path, "-e", "trace=fsync", "-P", str(s.work_dir),
                        "-e", "inject=fsync:error=EIO:when=2")
    s.data["engine"] = eng.to_dict()
    s._save()
    r, ns = go(s, eng, env, tmp_path / "s")
    s2 = S.Session.open(s.dir)
    ck = [x for x in ns if x["kind"] == "checkpoint"]
    assert ck[1]["durability"] == "not_durable"
    assert s2.data["suspended"]["reason"] == "checkpoint_not_durable" and s2.summary()["passes_saved"] == 2
    eng2 = S.EngineSpec.for_test(FAKEINST)
    s2.data["engine"] = eng2.to_dict()
    s2._save()
    go(s2, eng2, env, tmp_path / "s")
    assert_identical_to_continuous(s, ref)


def test_reduced_durability_cannot_happen_on_linux():
    """The reduced-durability path needs F_FULLFSYNC (macOS): on Linux the genuine build does
    not even contain its message. Its handling is proven with the Python fake only."""
    data = Path(FAKEINST).read_bytes()
    if sys.platform.startswith("linux"):
        assert b"Checkpoint durability reduced" not in data
        assert b"Checkpoint not durable" in data


def test_altered_work_file_between_runs(tmp_path):
    """Between two runs another program changes a value of a validated pass in the work file:
    the reconciliation sets it aside (kept, never deleted), restores the validated state,
    and chartread -r resumes from it; same end."""
    ref = continuous_rows(tmp_path, 520)
    chart, s, eng, env = setup(tmp_path / "s", 520)

    def on(nt, rr):
        if nt["kind"] == "checkpoint" and nt["passes_saved"] == 5 and not rr.stopping:
            rr.command("pause")
    go(s, eng, env, tmp_path / "s", on=on)
    work = s.work_ti3
    text = work.read_bytes()
    tampered = work.with_name("tampered")
    rows = data_rows(text)
    first = rows[0].split()
    first[5] = f"{float(first[5]) * 1.01:.6f}"
    tampered.write_bytes(text.replace(rows[0].encode(), " ".join(first).encode(), 1))
    os.replace(tampered, work)                                          # a rename, as a program would
    actions = S.Session.open(s.dir).reconcile()
    assert any("not adopted" in a and "pass_changed" in a for a in actions)
    assert list(s.work_dir.glob(f"{chart.chart_id}.ti3.unconfirmed-*"))
    go(S.Session.open(s.dir), eng, env, tmp_path / "s")
    assert_identical_to_continuous(s, ref)


def test_in_place_write_to_the_validated_inode_fails(tmp_path):
    """While the work file and validated.ti3 are one inode, it is read-only: an in-place
    write by another program fails instead of changing the validated state."""
    chart, s, eng, env = setup(tmp_path, 260)

    def on(nt, rr):
        if nt["kind"] == "checkpoint" and nt["passes_saved"] == 3 and not rr.stopping:
            rr.command("pause")
    go(s, eng, env, tmp_path, on=on)
    s2 = S.Session.open(s.dir)
    assert os.stat(s2.work_ti3).st_ino == os.stat(s2.validated).st_ino
    with pytest.raises(PermissionError):
        open(s2.work_ti3, "r+b")
    sha = hashlib.sha256(s2.validated.read_bytes()).hexdigest()
    assert sha == s2.data["last_checkpoint"]["sha256"]


def test_corrupted_validated_file_blocks_the_run(tmp_path):
    """validated.ti3 truncated by hand: no run starts on it (validated_invalid), nothing is
    overwritten, the work file is still there."""
    chart, s, eng, env = setup(tmp_path, 260)

    def on(nt, rr):
        if nt["kind"] == "checkpoint" and nt["passes_saved"] == 3 and not rr.stopping:
            rr.command("pause")
    go(s, eng, env, tmp_path, on=on)
    v = s.validated
    data = v.read_bytes()
    v.unlink()
    v.write_bytes(data[: len(data) // 2])
    with pytest.raises(S.SessionError) as e:
        R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp_path / "lock"), env=env)
    assert e.value.code == "validated_invalid"
    assert s.work_ti3.read_bytes() == data


# ─── through the backend and HTTP ───────────────────────────────────────────
def ship_real_engine(tmp, monkeypatch, *, binary=None, trust=True):
    """The real binary installed where freeglaz looks for its shipped copy, with a manifest;
    engine.py checks it for real (manifest, sha256, the patch's text). It becomes approved
    only through the explicit test injection into the trusted builds."""
    d = tmp / "app" / "chartread-fgK"
    d.mkdir(parents=True, exist_ok=True)
    exe = d / E.ENGINE_NAME
    shutil.copy2(binary or FAKEINST, exe)
    (d / E.MANIFEST_NAME).write_text(json.dumps({
        "schema": E.MANIFEST_SCHEMA, "schema_version": 1, "binary": E.ENGINE_NAME,
        "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "argyll_version": "3.5.0",
        "patch": {"name": "fg-chartread-checkpoint", "version": "3", "sha256": next(iter(E.ACCEPTED_PATCHES))},
        "build": {"platform": E._platform(), "arch": E._arch(), "usb_only": True,
                  "note": "TEST: fake-instrument build, never shipped"}}))
    info = tmp / "app" / ".flatpak-info"
    info.write_text("[Application]\n")
    monkeypatch.setattr(E, "FLATPAK_INFO", info)
    monkeypatch.setattr(E, "FLATPAK_DIR", d)
    monkeypatch.delenv(E.ENV_PATH, raising=False)
    if trust:
        trust_build(monkeypatch, hashlib.sha256(exe.read_bytes()).hexdigest())
    return exe


@pytest.fixture
def hlab(tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(store))
    chart = make_chart(store, n_useful=520, steps=26)
    _, env = fakeinst_engine(tmp_path, chart)

    class L:
        pass
    lb = L()
    lb.tmp, lb.chart, lb.mp, lb.env = tmp_path, chart, monkeypatch, env

    def service():
        svc = em.ExternalMeasureService(
            engine_provider=lambda: (S.EngineSpec.from_status(E.engine_status()), dict(lb.env)),
            locks_provider=lambda: S.LockSet([("store", S.InstrumentLock(tmp_path / "store.lock")),
                                              ("user", S.InstrumentLock(tmp_path / "user.lock"))]),
            presence_provider=lambda m: Presence("present", m), lock_scope=lambda: "user")
        em.set_service(svc)
        return svc
    lb.service = service
    lb.svc = service()
    lb.client = TestClient(app)
    lb.base = f"/api/charts/{chart.chart_id}/external-measure"
    yield lb
    svc = em.get_service()
    if svc is not None:
        svc.shutdown(3.0)


def hsnap(lb):
    r = lb.client.get(lb.base)
    assert r.status_code == 200, r.text
    return r.json()


def hpost(lb, path, s, **extra):
    return lb.client.post(lb.base + path, json={"expect_state": s["state"], "seq": s["state_seq"], **extra})


def hwait(lb, pred, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = hsnap(lb)
        if pred(s):
            return s
        time.sleep(0.02)
    raise AssertionError(f"not reached; last {s['state']} running={s['running']}")


def press_button(lb):
    """The instrument's button: a trigger key written to the engine's terminal by the test,
    never through freeglaz's commands (there is none that starts a reading)."""
    act = lb.svc._active
    if act is not None:
        try:
            lb.svc._submit(act, lambda run: run.proc.write(b" "))
        except Exception:
            pass


def hdrive(lb, *, until_saved=None, timeout=120):
    """Calibrate over HTTP when asked, press at each unread strip prompt, until the run ends
    or ``until_saved`` passes are saved."""
    end = time.monotonic() + timeout
    last_prompt = None
    while time.monotonic() < end:
        s = hsnap(lb)
        if not s["running"]:
            return s
        sm = s.get("session") or {}
        if until_saved is not None and sm.get("passes_saved", 0) >= until_saved and s["state"] == "ready":
            return s
        p = s.get("prompt") or {}
        key = (s["state_seq"], p.get("kind"))
        if key != last_prompt:
            if s["state"] == "calibration" and "calibrate" in s["actions"]:
                hpost(lb, "/command", s, command="calibrate")
                last_prompt = key
            elif s["state"] == "ready" and p.get("kind") == "ready" and not p.get("all_read"):
                press_button(lb)
                last_prompt = key
        time.sleep(0.01)
    raise AssertionError("hdrive timeout")


def test_the_genuine_v3_binary_passes_the_engine_check_and_is_not_trusted_by_itself(hlab):
    exe = ship_real_engine(hlab.tmp, hlab.mp, trust=False)
    st = E.engine_status()
    assert (st.ok, st.source, st.patch_version, st.trusted) == (True, "bundle", "3", False), st.message
    assert S.EngineSpec.from_status(st).origin == "development"
    trust_build(hlab.mp, hashlib.sha256(exe.read_bytes()).hexdigest())
    assert S.EngineSpec.from_status(E.engine_status()).origin == "approved"


def test_http_session_pause_backend_restart_resume_publish_qc_and_profile_plan(hlab):
    """Through the routes, with the real engine as the shipped (and, for this test, trusted)
    copy: create, start, calibrate, 9 strips, pause; a NEW service (the backend restarted)
    reconciles at start-up without starting anything; resume -r -N -K; finish; publish; the
    scan is listed with its identity, its QC is computed, the profile plan accepts it."""
    ship_real_engine(hlab.tmp, hlab.mp)
    (hlab.tmp / "ref").mkdir()
    ref = continuous_rows(hlab.tmp / "ref", 520)
    s = hlab.client.post(hlab.base, json={}).json()
    assert s["publishable"] is True and s["context"]["engine"]["origin"] == "approved"
    assert hpost(hlab, "/start", s).status_code == 200
    s = hdrive(hlab, until_saved=9)
    assert hpost(hlab, "/command", s, command="pause").status_code == 200
    s = hwait(hlab, lambda x: not x["running"])
    assert s["state"] == "idle" and s["session"]["passes_saved"] == 9
    hlab.svc.shutdown(3.0)
    svc2 = hlab.service()                                                 # the backend restarted
    hlab.svc = svc2
    rep = svc2.boot_reconcile()
    assert rep and "error" not in rep[0]
    s = hsnap(hlab)
    assert s["state"] == "idle" and s["session"]["passes_saved"] == 9
    assert hpost(hlab, "/start", s).status_code == 200
    s = hdrive(hlab)
    assert s["state"] == "complete", s
    sess = S.Session.find_open(hlab.chart.dir)
    assert {"-r", "-N", "-K"} <= set(sess.data["runs"][1]["argv"])
    r = hpost(hlab, "/finalize", s)
    assert r.status_code == 200, r.text
    stem = r.json()["published"]["stem"]
    meas = hlab.chart.dir / "measurements"
    ti3 = meas / f"{stem}.ti3"
    assert data_rows(ti3.read_bytes()) == ref
    ident = mi.read_identity(ti3)
    assert (ident.method, ident.instrument, len(ident.spectral_bands)) == ("external", "colormunki", NB)
    rec = json.loads((meas / f"{stem}.json").read_text())
    assert rec["acquisition"]["engine"]["origin"] == "approved" and rec["acquisition"]["argv"][0] == "chartread-fgK"
    assert [x["end"] for x in rec["acquisition"]["runs"]] == ["user_pause", "finished"]
    assert rec["instrument"]["serial"] is None
    detail = hlab.client.get(f"/api/charts/{hlab.chart.chart_id}")
    assert detail.status_code == 200, detail.text
    qc = hlab.client.get(f"/api/charts/{hlab.chart.chart_id}/scans/{stem}.ti3/qc")
    assert qc.status_code == 200, qc.text
    prev = hlab.client.post(f"/api/charts/{hlab.chart.chart_id}/profile/preview", json={})
    assert prev.status_code == 200, prev.text
    hlab.qc, hlab.preview = qc.json(), prev.json()


def test_backend_process_killed_then_restarted(hlab):
    """A real backend PROCESS (the service, in a child Python) is SIGKILLed while chartread
    waits at a prompt: chartread dies with it (PTY hang-up), the next backend reconciles at
    start-up (run marked interrupted), resumes and finishes identically."""
    exe = ship_real_engine(hlab.tmp, hlab.mp)
    (hlab.tmp / "ref").mkdir()
    ref = continuous_rows(hlab.tmp / "ref", 520)
    envfile = hlab.tmp / "env.json"
    envfile.write_text(json.dumps(hlab.env))
    child = r"""
import hashlib, json, os, sys, time
from pathlib import Path
from lib.z9_client.chartread_fgk import engine as E, session as S
from lib.z9_client.chartread_fgk.usb_presence import Presence
from webapp.backend.services import external_measure as em
tmp, exe, envfile, cid = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
E.FLATPAK_INFO, E.FLATPAK_DIR = tmp / "app" / ".flatpak-info", exe.parent
E.TRUSTED_BUILDS = {(E._platform(), E._arch()): frozenset({hashlib.sha256(exe.read_bytes()).hexdigest()})}
env = json.loads(envfile.read_text())
svc = em.ExternalMeasureService(engine_provider=lambda: (S.EngineSpec.from_status(E.engine_status()), env),
    locks_provider=lambda: S.LockSet([("store", S.InstrumentLock(tmp / "store.lock"))]),
    presence_provider=lambda m: Presence("present", m), lock_scope=lambda: "store")
s = svc.create(cid)
s = svc.start(cid, s["state"], s["state_seq"])
print("PGID", svc._active.run.proc.pgid, flush=True)
last = None
while True:
    s = svc.snapshot(cid)
    key = (s["state_seq"], (s.get("prompt") or {}).get("kind"))
    if key != last:
        if s["state"] == "calibration":
            svc.command(cid, "calibrate", s["state"], s["state_seq"]); last = key
        elif s["state"] == "ready" and (s.get("prompt") or {}).get("kind") == "ready":
            if (s["session"] or {}).get("passes_saved", 0) >= 11:
                print("READY", flush=True); time.sleep(60)
            svc._submit(svc._active, lambda run: run.proc.write(b" ")); last = key
    time.sleep(0.01)
"""
    p = subprocess.Popen([sys.executable, "-c", child, str(hlab.tmp), str(exe), str(envfile), hlab.chart.chart_id],
                         cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                         env={**os.environ, "FREEGLAZ_STORE_ROOT": str(hlab.tmp / "store")})
    pgid = None
    for line in p.stdout:
        if line.startswith("PGID"):
            pgid = int(line.split()[1])
        if line.startswith("READY"):
            break
    p.kill()
    p.wait(10)
    assert group_gone(pgid), "chartread survived the backend"
    rep = hlab.svc.boot_reconcile()
    assert any("marked interrupted" in a for a in rep[0]["actions"])
    s = hsnap(hlab)
    assert s["state"] == "idle" and s["session"]["passes_saved"] == 11
    assert hpost(hlab, "/start", s).status_code == 200
    s = hdrive(hlab)
    assert s["state"] == "complete"
    r = hpost(hlab, "/finalize", s)
    assert r.status_code == 200, r.text
    ti3 = hlab.chart.dir / "measurements" / f"{r.json()['published']['stem']}.ti3"
    assert data_rows(ti3.read_bytes()) == ref


@pytest.mark.parametrize("where", ["prompt", "reading", "hang"])
def test_backend_shutdown_under_4_seconds(hlab, where):
    """Application shutdown with the real engine at a strip prompt, in the middle of a 3 s
    read, or hung (injection): the service pauses or stops it within its 4 s budget, the
    group is gone, the lock is free, the validated strips are kept."""
    if where == "hang" and not (INJ and os.access(INJ, os.X_OK)):
        pytest.skip("FREEGLAZ_TEST_CHARTREAD_FAKEINST_INJ not set")
    ship_real_engine(hlab.tmp, hlab.mp, binary=INJ if where == "hang" else None)
    if where == "reading":
        hlab.env["FG_FAKEINST_READ_MS"] = "3000"
    if where == "hang":
        hlab.env["FG_FAKEINST_ACTIONS"] = "4:hang"
    s = hlab.client.post(hlab.base, json={}).json()
    assert hpost(hlab, "/start", s).status_code == 200
    hdrive(hlab, until_saved=3, timeout=60)
    if where != "prompt":
        press_button(hlab)
        time.sleep(0.6)
    pgid = hlab.svc._active.run.proc.pgid
    t0 = time.monotonic()
    hlab.svc.shutdown(4.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 4.0 and group_gone(pgid, wait=0.5)
    lk = S.InstrumentLock(hlab.tmp / "store.lock")
    lk.acquire({"op": "probe"})
    lk.release()
    sess = S.Session.find_open(hlab.chart.dir)
    assert sess.summary()["passes_saved"] >= 3
    print(f"\n[shutdown at {where}] {elapsed:.2f} s, end={sess.data['runs'][-1]['end']}")


def test_two_subscribers_and_concurrent_commands_with_the_real_engine(hlab):
    """Two event streams see the same progression; two windows sending a command for the same
    prompt: one acts, the other is refused as stale; a pause racing a calibration does not
    leave the engine running."""
    ship_real_engine(hlab.tmp, hlab.mp)
    s = hlab.client.post(hlab.base, json={}).json()
    assert hpost(hlab, "/start", s).status_code == 200
    s = hwait(hlab, lambda x: x["state"] == "calibration")
    out = [None, None]

    def send(i):
        out[i] = hpost(hlab, "/command", s, command="calibrate").status_code
    ts = [threading.Thread(target=send, args=(i,)) for i in range(2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert sorted(out) == [200, 409]
    seqs = {"a": [], "b": []}
    stop = threading.Event()

    def watch(k):
        last = -1
        while not stop.is_set():
            v = hlab.svc.broadcast.wait(hlab.chart.chart_id, last, 0.2)
            if v != last:
                seqs[k].append(v)
                last = v
    ws = [threading.Thread(target=watch, args=(k,)) for k in seqs]
    [w.start() for w in ws]
    s = hdrive(hlab, until_saved=6)
    snap_a = hsnap(hlab)
    r1 = hpost(hlab, "/command", snap_a, command="pause")
    r2 = hpost(hlab, "/command", snap_a, command="next")
    assert r1.status_code == 200 and r2.status_code == 409
    s = hwait(hlab, lambda x: not x["running"])
    stop.set()
    [w.join(5) for w in ws]
    assert s["state"] == "idle" and seqs["a"] and seqs["b"]
    assert max(seqs["a"]) == max(seqs["b"])


# ─── robustness ─────────────────────────────────────────────────────────────
def test_long_session_many_interruptions_bounded_memory_and_logs(tmp_path):
    """5 000 patches, eight interruptions of four kinds in one session (pause, engine SIGKILL,
    graduated stop, pause), then the end: identical to a continuous session; the driver's
    memory does not grow with the runs; every log is bounded; no process left."""
    ref = continuous_rows(tmp_path, 5000)
    chart, s, eng, env = setup(tmp_path / "s", 5000)
    rss = []
    kinds_used = []
    for k, stop_at in enumerate((20, 45, 70, 95, 120, 145, 170, 185)):
        mode = ("pause", "kill", "stop", "pause")[k % 4]
        kinds_used.append(mode)

        def on(nt, rr, n=stop_at, m=mode):
            if nt["kind"] == "checkpoint" and nt["passes_saved"] == n and not rr.stopping:
                if m == "pause":
                    rr.command("pause")
                elif m == "kill":
                    os.kill(rr.proc.pid, signal.SIGKILL)
                else:
                    rr.stop_now("test_stop")
        r, _ = go(s, eng, env, tmp_path / "s", on=on)
        assert group_gone(r.proc.pgid)
        rss.append(rss_kb(os.getpid()))
        assert S.Session.open(s.dir).summary()["passes_saved"] >= stop_at
    go(s, eng, env, tmp_path / "s")
    assert_identical_to_continuous(s, ref)
    logs = list((s.dir / "logs").iterdir())
    assert len(logs) == 9 and all(p.stat().st_size < (1 << 20) + (128 << 10) for p in logs)
    s2 = S.Session.open(s.dir)
    assert len(s2.data["reconciliations"]) <= S.RECONCILIATIONS_KEPT
    growth = rss[-1] - rss[1]
    print(f"\n[5000, 8 interruptions] driver RSS kB per run: {rss}; growth after run 2: {growth} kB; "
          f"session.json {s2.json_path.stat().st_size} B; logs {[p.stat().st_size for p in logs]}")
    assert growth < 30_000


def test_engine_memory_for_5000_patches(tmp_path):
    """The engine's resident memory during a 5 000-patch session stays flat (v3 frees each
    checkpoint's CGATS sets; the ChromIQ leak of note 136 grew by ~1 GB here)."""
    chart, s, eng, env = setup(tmp_path, 5000)
    samples = []

    def on(nt, rr):
        if nt["kind"] == "checkpoint" and nt["passes_saved"] % 20 == 0:
            samples.append(rss_kb(rr.proc.pid))
    go(s, eng, env, tmp_path, on=on)
    samples = [x for x in samples if x]
    print(f"\n[engine RSS kB every 20 passes] {samples}")
    assert samples and max(samples) - min(samples) < 20_000
