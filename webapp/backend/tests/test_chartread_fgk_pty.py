"""PtyProcess (lib.z9_client.chartread_fgk.pty_process): the engine on a PTY that is its
controlling terminal, dying with its driver, stopped step by step, safe to start from a
multi-threaded backend. Offline: the "engine" is the fake chartread (tests/fakes)."""
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from lib.z9_client.chartread_fgk import pty_process as P
from webapp.backend.tests.chartread_harness import HOLDER, Pump, scenario, spawn_fake

REPO = str(Path(__file__).resolve().parents[3])
pytestmark = pytest.mark.skipif(not sys.platform.startswith(("linux", "darwin")), reason="POSIX PTY")


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    try:                                        # a zombie waiting for init is gone too
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except OSError:
        return False


def _wait_gone(pid: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if _gone(pid):
            return True
        time.sleep(0.05)
    return _gone(pid)


def _drain(proc: P.PtyProcess, timeout: float = 5.0) -> str:
    out, end = b"", time.monotonic() + timeout
    while time.monotonic() < end:
        d = proc.read(0.05)
        if d is None:
            proc.wait(2.0)                          # end of file can precede the exit
            break
        out += d
    return out.decode("latin-1")


def _ready(proc, timeout=10.0) -> Pump:
    pump = Pump(proc)
    pump.run(timeout=timeout, until=lambda e: e.kind == "ready")
    return pump


# ─── controlling terminal ───────────────────────────────────────────────────
def test_the_engine_owns_its_controlling_terminal(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"ctty": True}, {"exit": 0}])
    out = _drain(proc)
    line = next(l for l in out.splitlines() if l.startswith("CTTY"))
    f = dict(kv.split("=") for kv in line.split()[1:])
    assert f["sid"] == f["pid"] == f["pgrp"] == f["tcpgrp"] == str(proc.pid)   # session leader, foreground
    assert f["hup"] == "default" and f["mask"] == "[]"
    assert proc.poll() == 0 and proc.pgid == proc.pid
    proc.close()


def test_the_engine_dies_with_its_driver_and_the_orphan_case_is_real(tmp_path):
    """X17 again: driver SIGKILLed → the engine gets SIGHUP and goes. Without a controlling
    terminal (Popen + start_new_session) it survives, holding the instrument."""
    results = {}
    for mode in ("ctty", "no-ctty"):
        scen, _ = scenario(tmp_path, [{"say": "ready\n"}, {"sleep": 30}], max_seconds=40, name=mode)
        holder = subprocess.Popen([sys.executable, str(HOLDER), mode, REPO, str(scen)],
                                  stdout=subprocess.PIPE, text=True, start_new_session=True)
        try:
            pid = int(holder.stdout.readline())
            time.sleep(0.3)
            holder.kill()
            holder.wait(5)
            results[mode] = _wait_gone(pid, 3.0)
        finally:
            if not _gone(pid):
                os.killpg(pid, signal.SIGKILL)
                _wait_gone(pid, 3.0)
    assert results == {"ctty": True, "no-ctty": False}


# ─── multi-threaded backend ─────────────────────────────────────────────────
def test_spawning_from_a_worker_thread_under_load_is_safe(tmp_path):
    """uvicorn runs many threads: start 25 engines from a worker thread while 8 threads
    allocate, log and hold a lock; with SIGHUP ignored in the process and SIGTERM blocked in
    the spawning thread. Every engine starts, gets default signal handling, exits."""
    stop, lock = threading.Event(), threading.Lock()
    log = logging.getLogger("fgk.load")

    def busy():
        while not stop.is_set():
            with lock:
                junk = [bytes(64) for _ in range(200)]
            log.debug("busy %d", len(junk))
    workers = [threading.Thread(target=busy, daemon=True) for _ in range(8)]
    for w in workers:
        w.start()
    old_hup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
    results, errors = [], []

    def spawner():
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            for i in range(25):
                proc, _ = spawn_fake(tmp_path, [{"ctty": True}, {"exit": 0}], name=f"s{i}")
                results.append((_drain(proc), proc.poll()))
                proc.close()
        except Exception as e:                      # noqa: BLE001 — reported below
            errors.append(e)
    t0 = time.monotonic()
    try:
        t = threading.Thread(target=spawner)
        t.start()
        t.join(90)
        assert not t.is_alive(), "spawning blocked"
    finally:
        signal.signal(signal.SIGHUP, old_hup)
        stop.set()
    assert not errors and len(results) == 25
    for out, rc in results:
        assert rc == 0 and "hup=default mask=[]" in out
    assert time.monotonic() - t0 < 60


# ─── stopping ───────────────────────────────────────────────────────────────
def test_stop_budget_fits_the_shutdown_window():
    """freeglaz waits 5 s for its backend; the session's prompt-aware pause comes first."""
    assert sum(w for _, w in P.DEFAULT_STOP_STEPS) <= 2.5


@pytest.mark.parametrize("ignore,outcome", [
    ([], "interrupt"),                                   # Ctrl-C through the terminal
    (["SIGINT"], "terminate"),
    (["SIGINT", "SIGTERM", "SIGHUP"], "kill"),           # a defective engine
])
def test_graduated_stop_leaves_nothing_behind(tmp_path, ignore, outcome):
    proc, _ = spawn_fake(tmp_path, [{"ignore": ignore}, {"prompt": "ready", "pass": "A"}])
    _ready(proc)
    rep = proc.terminate()
    assert (rep.outcome, rep.group_empty) == (outcome, True), rep
    assert rep.elapsed < 2.8 and proc.poll() is not None
    assert [s for s, _ in rep.steps][-1] == outcome
    proc.close()


def test_the_whole_process_group_is_stopped(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"ignore": ["SIGINT"]}, {"grandchild": 30},
                                    {"prompt": "ready", "pass": "A"}])
    pump = _ready(proc)
    gpid = int(next(l for l in pump.transcript.recent() if l.startswith("GRANDCHILD")).split()[1])
    rep = proc.terminate()
    assert rep.group_empty and _wait_gone(gpid, 2.0)
    proc.close()


def test_exit_end_of_file_and_reaping(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"say": "bye\n"}, {"exit": 3}])
    assert "bye" in _drain(proc)
    assert proc.read(0.05) is None and proc.poll() == 3 and not proc.alive
    rep = proc.terminate()
    assert rep.outcome == "already_exited" and rep.group_empty
    with pytest.raises(P.ProcessGone):
        proc.close()
        proc.write(b"n")


def test_a_broken_fake_never_outlives_its_alarm(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"ignore": ["SIGINT", "SIGTERM", "SIGHUP"]}, {"sleep": 60}],
                         max_seconds=1)
    end = time.monotonic() + 4
    while proc.poll() is None and time.monotonic() < end:
        proc.read(0.05)
    assert proc.poll() == -signal.SIGALRM
    proc.close()


def test_spawn_preconditions(tmp_path):
    with pytest.raises(ValueError):
        P.PtyProcess.spawn(["python3", "x"], cwd=str(tmp_path), env={})
    with pytest.raises(FileNotFoundError):
        P.PtyProcess.spawn([sys.executable, "-c", "0"], cwd=str(tmp_path / "missing"), env={})


def test_cpu_time_of_the_engine_is_readable(tmp_path):
    proc, _ = spawn_fake(tmp_path, [{"spin": 0.6}, {"exit": 0}])
    time.sleep(0.4)
    c = proc.cpu_seconds()
    assert c is not None and c > 0.1
    _drain(proc)
    proc.close()
