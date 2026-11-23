"""Sessions with the REAL chartread v3 (official chartread.c + the -K patch) linked to the
lab's fake instrument instead of the USB drivers (the binary holds no USB or serial code).
Opt-in: FREEGLAZ_TEST_CHARTREAD_FAKEINST=<path of chartread-ckpt-fake>; skipped otherwise
(CI has no such binary). They check that the Python fake's assumptions hold with chartread
itself: its prompts and file format, -r -N resuming where it says, an offline withdrawal,
misreads and reversed strips."""
import math
import os
import time

import pytest

from lib.z9_client.chartread_fgk import run as R
from lib.z9_client.chartread_fgk import session as S
from webapp.backend.tests.session_harness import FAKEINST, data_rows, drive, fakeinst_engine, kinds, make_chart

pytestmark = pytest.mark.skipif(not (FAKEINST and os.access(FAKEINST, os.X_OK)),
                                reason="FREEGLAZ_TEST_CHARTREAD_FAKEINST not set")


def setup(tmp, n=2000, **eng_kw):
    chart = make_chart(tmp, n_useful=n, steps=26)
    exe, env = fakeinst_engine(tmp, chart, **eng_kw)
    eng = S.EngineSpec.for_test(str(exe))
    return chart, S.Session.create(chart.dir, model="colormunki", filter_option=None, engine=eng), eng, env


def run(s, eng, env, tmp, **kw):
    r = R.Run.start(S.Session.open(s.dir), eng, lock=S.InstrumentLock(tmp / "lock"), env=env)

    def on(n, rr):
        if n["kind"] == "misread":
            rr.command("retry")
        if kw.get("on"):
            kw["on"](n, rr)
    notices = drive(r, timeout=180, on_notice=on)
    return r, notices


def values_match(chart, ti3: bytes) -> bool:
    for line in data_rows(ti3):
        t = line.split()
        v = chart.values[t[1].strip('"')]
        if any(not math.isclose(float(a), b, rel_tol=1e-5, abs_tol=1e-5) for a, b in zip(t[5:], v)):
            return False
    return True


@pytest.mark.parametrize("n", [2000, 5000])
def test_a_whole_session_with_the_real_chartread(tmp_path, n):
    chart, s, eng, env = setup(tmp_path, n)
    t0 = time.monotonic()
    r, ns = run(s, eng, env, tmp_path)
    sm = S.Session.open(s.dir).summary()
    assert r.ended["end"] == "finished" and sm["complete"] and sm["patches_saved"] == n
    assert kinds(ns).count("checkpoint") == len(chart.labels)
    assert values_match(chart, s.validated.read_bytes())
    print(f"real chartread, {n} patches: {time.monotonic() - t0:.1f} s")


def test_crash_and_resume_with_the_real_chartread_equals_a_continuous_session(tmp_path):
    ref_chart, ref, ref_eng, ref_env = setup(tmp_path / "continuous")
    run(ref, ref_eng, ref_env, tmp_path / "continuous")
    chart, s, eng, env = setup(tmp_path / "crash", actions="40:kill")
    r1, _ = run(s, eng, env, tmp_path / "crash")
    assert r1.ended["end"] == "exited" and S.Session.open(s.dir).summary()["passes_saved"] == 39
    env2 = {**env, "FG_FAKEINST_ACTIONS": ""}
    r2, ns2 = run(s, eng, env2, tmp_path / "crash")
    s2 = S.Session.open(s.dir)
    assert "-r" in s2.data["runs"][1]["argv"] and "-N" in s2.data["runs"][1]["argv"]
    assert "calibration_prompt" not in kinds(ns2)                 # -N: the saved calibration is reused
    assert next(n for n in ns2 if n["kind"] == "ready")["pass"] == chart.labels[39]
    assert r2.ended["end"] == "finished"
    assert data_rows(s2.validated.read_bytes()) == data_rows(ref.validated.read_bytes())


def test_withdrawal_misread_and_reversed_strip_with_the_real_chartread(tmp_path):
    chart, s, eng, env = setup(tmp_path, n=260, actions="3:misread,5:reverse")

    def pause_after_6(n, rr):
        if n["kind"] == "checkpoint" and n["passes_saved"] == 6 and not rr.stopping:
            rr.command("pause")
    r1, ns = run(s, eng, env, tmp_path, on=pause_after_6)
    assert r1.ended["end"] == "user_pause"
    assert "misread" in kinds(ns) and any(n.get("reversed") for n in ns if n["kind"] == "strip_acquired")
    S.Session.open(s.dir).remove_pass("B", S.InstrumentLock(tmp_path / "lock"))
    r2, ns2 = run(s, eng, {**env, "FG_FAKEINST_ACTIONS": ""}, tmp_path)
    assert next(n for n in ns2 if n["kind"] == "ready")["pass"] == "B"     # chartread -r asks for B
    assert r2.ended["end"] == "finished" and values_match(chart, s.validated.read_bytes())
