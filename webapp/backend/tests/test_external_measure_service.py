"""External measurement in the backend (webapp/backend/services/external_measure.py and its
routes): sessions over HTTP, one live run, commands checked against state and seq, SSE
for several windows, publication through measurement_publish only, start-up
reconciliation without opening the instrument, shutdown within the window, races.
Offline: throw-away store, the fake chartread as a "shipped" engine, no USB."""
import json
import logging
import os
import signal
import socket
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from lib.z9_client import measurement_identity as mi
from lib.z9_client.chartread_fgk import engine as E
from lib.z9_client.chartread_fgk import run as R
from lib.z9_client.chartread_fgk import session as S
from lib.z9_client.chartread_fgk.usb_presence import Presence
from webapp.backend import main as backend_main
from webapp.backend.main import app
from webapp.backend.services import external_measure as em
from webapp.backend.tests.session_harness import approved_engine, make_chart

SERIAL_SECRET = "SN-SECRET-4711"


def present(model):
    return Presence("present", model)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """An isolated store with one chart, a shipped engine, a service with temporary locks."""
    store = tmp_path / "store"
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(store))
    # no engine but the lab's: not the developer's (environment, ~/.freeglazrc.toml), not the
    # one a Flatpak ships in /app (these tests also run inside the sandbox, note 143)
    monkeypatch.delenv(E.ENV_PATH, raising=False)
    monkeypatch.setattr(E, "_config_path", lambda: None)
    monkeypatch.setattr(E, "FLATPAK_INFO", tmp_path / "no-flatpak-info")
    chart = make_chart(store, n_useful=130, steps=26)

    class Lab:
        pass
    lb = Lab()
    lb.tmp, lb.store, lb.chart, lb.mp = tmp_path, store, chart, monkeypatch
    lb.engine = lambda **kw: approved_engine(tmp_path, chart, monkeypatch, **{"serial": SERIAL_SECRET, **kw})
    lb.user_lock = tmp_path / "user.lock"

    def service(store_lock="store.lock", presence=present):
        svc = em.ExternalMeasureService(
            locks_provider=lambda: S.LockSet([("store", S.InstrumentLock(tmp_path / store_lock)),
                                              ("user", S.InstrumentLock(lb.user_lock))]),
            presence_provider=presence, lock_scope=lambda: "user")
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


def snap(lb, base=None):
    r = lb.client.get(base or lb.base)
    assert r.status_code == 200, r.text
    return r.json()


def post(lb, path, s, base=None, **extra):
    return lb.client.post((base or lb.base) + path, json={"expect_state": s["state"], "seq": s["state_seq"], **extra})


def wait(lb, pred, timeout=20.0, base=None):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        s = snap(lb, base)
        if pred(s):
            return s
        time.sleep(0.02)
    raise AssertionError(f"state not reached; last {s['state']} running={s['running']}")


def create_and_start(lb, base=None, **engine_kw):
    lb.engine(**engine_kw)
    s = lb.client.post(base or lb.base, json={}).json()
    r = post(lb, "/start", s, base)
    assert r.status_code == 200, r.text
    return r.json()


def calibrate(lb, base=None):
    s = wait(lb, lambda s: s["state"] == "calibration", base=base)
    r = post(lb, "/command", s, base, command="calibrate")
    assert r.status_code == 200, r.text


def run_to_end(lb, base=None):
    return wait(lb, lambda s: not s["running"], timeout=30, base=base)


def complete_session(lb, base=None):
    create_and_start(lb, base, button_ms=20)
    calibrate(lb, base)
    s = run_to_end(lb, base)
    assert s["state"] == "complete", s
    return s


def code(r):
    return r.json()["detail"]["code"]


# ─── the whole flow ─────────────────────────────────────────────────────────
def test_a_session_from_creation_to_publication(lab, caplog):
    caplog.set_level(logging.INFO)
    lab.engine(button_ms=20)
    s = lab.client.post(lab.base, json={"filter_option": None, "engine_path": "/bin/sh",
                                        "argv": ["--replay"], "origin": "approved"}).json()
    assert (s["state"], s["actions"], s["publishable"]) == ("idle", ["start", "abandon"], True)
    sess = S.Session.find_open(lab.chart.dir)
    assert sess.data["engine"]["source"] == "bundle"                 # the client chose nothing
    assert post(lab, "/start", s).status_code == 200
    calibrate(lab)
    s = run_to_end(lab)
    assert s["state"] == "complete" and s["session"]["patches_saved"] == 130 and s["actions"][0] == "finalize"
    r = post(lab, "/finalize", s)
    assert r.status_code == 200, r.text
    pub = r.json()["published"]
    meas = lab.chart.dir / "measurements"
    assert sorted(p.name for p in meas.iterdir()) == [f"{pub['stem']}.json", f"{pub['stem']}.ti3"]
    sess = S.Session.open(sess.dir)
    assert sess.data["status"] == "published" and sess.validated.exists() and sess.work_ti3.exists()
    assert (meas / f"{pub['stem']}.ti3").read_bytes() == sess.validated.read_bytes()   # bytes unchanged
    ident = mi.read_identity(meas / f"{pub['stem']}.ti3")
    assert (ident.method, ident.instrument, ident.condition, len(ident.spectral_bands)) == \
        ("external", "colormunki", "fixed", 36)
    rec = json.loads((meas / f"{pub['stem']}.json").read_text())
    assert rec["acquisition"]["argv"][0] == "chartread-fgK" and rec["acquisition"]["engine"]["origin"] == "approved"
    assert rec["instrument"]["serial"] is None and len(rec["instrument"]["unit_fingerprint"]) == 16
    detail = lab.client.get(f"/api/charts/{lab.chart.chart_id}").json()
    assert detail["identity"]["external_measure"]["state"] == "published"
    assert [x["measurement_identity"]["has_record"] for x in detail["scans"]] == [True]
    public = json.dumps(snap(lab)) + json.dumps(detail) + caplog.text
    assert SERIAL_SECRET not in public and str(lab.tmp) not in public


def test_the_interface_context_and_summary(lab):
    """What the UI shows comes from the snapshot: chart size, engine, presence, conditions, the
    strip labels and which are saved, the last session once published. Nothing private in it."""
    lab.mp.setattr(em.ExternalMeasureService, "CONTEXT_TTL_S", 0.0)
    s = snap(lab)
    ctx = s["context"]
    assert (s["state"], s["actions"], s["last_session"]) == ("none", ["create"], None)
    assert (ctx["model"], ctx["filter_options"], ctx["filter_required"]) == ("colormunki", [], False)
    assert ctx["chart"] == {"patch_count": 130, "n_strips": 5}
    assert ctx["engine"] == {"ok": False, "code": "engine_missing"}
    assert ctx["presence"]["status"] == "present" and ctx["lock_scope"] == "user"
    lab.engine(button_ms=20)
    eng = snap(lab)["context"]["engine"]
    assert eng["ok"] and eng["origin"] == "approved" and eng["publishable"] and eng["patch_version"]
    s = lab.client.post(lab.base, json={}).json()
    sm = s["session"]
    assert sm["pass_labels"] == ["A", "B", "C", "D", "E"] and sm["passes"] == [] and sm["identity"] is None
    assert post(lab, "/start", s).status_code == 200
    calibrate(lab)
    s = run_to_end(lab)
    sm = s["session"]
    assert sm["passes"] == sm["pass_labels"] and sm["final"] and sm["corrections"] == []
    assert sm["identity"]["n_bands"] == 36 and sm["last_stop"]["reason"] == sm["last_run_end"] == "finished"
    assert post(lab, "/finalize", s).status_code == 200
    s = snap(lab)
    assert s["state"] == "published" and s["session"] is None
    assert s["last_session"]["published"]["stem"] and s["last_session"]["passes_saved"] == 5
    public = json.dumps(s)
    assert SERIAL_SECRET not in public and str(lab.tmp) not in public


def test_i1pro3plus_context_offers_conditions_without_default(tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(store))
    chart = make_chart(store, n_useful=52, steps=26, model="i1pro3plus")
    svc = em.ExternalMeasureService(presence_provider=lambda m: Presence("absent", m), lock_scope=lambda: "user")
    ctx = svc.context(chart.chart_id)
    assert ctx["filter_required"] and [o["condition"] for o in ctx["filter_options"]] == ["M0", "M1", "M2"]
    assert ctx["presence"]["status"] == "absent"


@pytest.mark.parametrize("status", ["absent", "other", "several"])
def test_start_is_refused_without_the_chart_instrument(lab, status):
    """The backend's refusal stays the authority, whatever a window shows: no run, no engine,
    the session unchanged, start still offered. The snapshot that follows the refusal shows
    the presence the start found (P1b, note 147 §16)."""
    plugged = {"status": "present"}
    lab.service(presence=lambda m: Presence(plugged["status"], m, detail="test"))
    lab.engine(button_ms=20)
    s = lab.client.post(lab.base, json={}).json()
    assert s["context"]["presence"]["status"] == "present"
    plugged["status"] = status                                       # unplugged (or another one)
    r = post(lab, "/start", s)
    assert r.status_code == 409 and code(r) == f"instrument_{status}"
    after = snap(lab)
    assert (after["state"], after["running"], after["state_seq"]) == ("idle", False, s["state_seq"])
    assert "start" in after["actions"] and after["session"]["runs"] == 0
    assert em.get_service().active_chart() is None
    assert after["context"]["presence"]["status"] == status          # not the "present" shown before
    plugged["status"] = "present"                                    # plugged in again
    r = post(lab, "/start", after)
    assert r.status_code == 200 and r.json()["running"], r.text


def test_a_detection_error_does_not_refuse_the_start(lab):
    """A device list that cannot be read (a Flatpak without sysfs…) is not an absence: the
    start goes on, without presence monitoring; the engine finds the instrument or says so.
    The wizard therefore never disables the start on "error" (and never shows it as present)."""
    lab.service(presence=lambda m: Presence("error", m, detail="test"))
    s = create_and_start(lab, button_ms=20)
    assert s["running"] and s["context"]["presence"]["status"] == "error"


class Listing:
    """A presence provider the test controls: answers at once, fails, or hangs until released.
    Counts the listings and how many ran at the same time."""

    def __init__(self, status="present"):
        self.status, self.hang, self.release = status, False, threading.Event()
        self.calls = self.active = self.max_active = 0
        self._lock = threading.Lock()

    def __call__(self, model):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.hang:
                self.release.wait(30)
            if self.status == "raise":
                raise RuntimeError("listing broke")
            return Presence(self.status, model, detail="test")
        finally:
            with self._lock:
                self.active -= 1


def test_a_hung_listing_blocks_neither_snapshots_nor_commands(lab):
    """During a run the instrument is watched through the probe: a listing that hangs holds the
    probe's thread only. Snapshots, the current run and pause answer at once; the pump goes on."""
    lst = Listing()
    svc = lab.service(presence=lst)
    create_and_start(lab, button_ms=20)
    wait(lab, lambda s: s["state"] == "calibration")
    lst.hang = True                                                  # the next listing hangs
    end = time.monotonic() + 6
    while svc.probe.running is None and time.monotonic() < end:     # the run's watch starts one
        time.sleep(0.02)
    assert svc.probe.running == "colormunki"
    try:
        t0 = time.monotonic()
        s = snap(lab)
        assert time.monotonic() - t0 < 0.5 and s["running"]
        t0 = time.monotonic()
        assert lab.client.get("/api/external-measure/current").json()["chart_id"] == lab.chart.chart_id
        assert time.monotonic() - t0 < 0.5
        t0 = time.monotonic()
        r = post(lab, "/command", s, command="pause")
        assert r.status_code == 200, r.text
        assert time.monotonic() - t0 < 1.0
        s = run_to_end(lab)
        assert s["session"]["last_stop"]["reason"] == "user_pause"
        assert svc.probe.running == "colormunki" and lst.max_active == 1  # still hanging, never doubled
    finally:
        lst.release.set()


def test_idle_snapshots_wait_a_bounded_time_and_share_one_listing(lab):
    """Without a live run a snapshot waits at most SNAPSHOT_PRESENCE_WAIT_S for a listing; many
    windows at once share it. Without any verdict yet the presence is "checking", never a guess;
    with one, the last verdict is shown while the new listing is late."""
    lst = Listing()
    svc = lab.service(presence=lst)
    lab.mp.setattr(em.ExternalMeasureService, "SNAPSHOT_PRESENCE_WAIT_S", 0.3)
    lst.hang = True
    out = []

    def one():
        t0 = time.monotonic()
        s = lab.client.get(lab.base).json()
        out.append((time.monotonic() - t0, s["context"]["presence"]["status"]))
    try:
        threads = [threading.Thread(target=one) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        assert len(out) == 6 and all(took < 1.0 and st == "checking" for took, st in out), out
        assert lst.calls == 1 and lst.max_active == 1
    finally:
        lst.release.set()
    lst.hang = False
    lst.release = threading.Event()
    end = time.monotonic() + 3
    while svc.probe.running and time.monotonic() < end:
        time.sleep(0.01)
    assert snap(lab)["context"]["presence"]["status"] == "present"
    time.sleep(svc.PRESENCE_FRESH_S + 0.05)
    lst.hang = True                                                  # now a late listing…
    try:
        t0 = time.monotonic()
        s = snap(lab)
        assert time.monotonic() - t0 < 1.0 and s["context"]["presence"]["status"] == "present"   # …shows the last verdict
    finally:
        lst.release.set()


def test_shutdown_kills_a_hung_ioreg_and_stops_listing(lab, monkeypatch, tmp_path):
    """Backend shutdown with an ioreg listing hanging: the process is killed, the probe lists no
    more, and a snapshot taken afterwards does not wait."""
    pidfile = tmp_path / "ioreg.pid"
    fake = tmp_path / "ioreg"
    fake.write_text(f'#!/bin/sh\necho $$ > "{pidfile}"\nexec sleep 30\n')
    fake.chmod(0o755)
    from lib.z9_client.chartread_fgk import usb_presence as U
    monkeypatch.setattr(U, "IOREG", (str(fake),))
    svc = lab.service(presence=lambda m: U.check_presence(m, scan=U.scan_ioreg))
    assert snap(lab)["context"]["presence"]["status"] == "checking"   # the listing is still running
    end = time.monotonic() + 5
    while not pidfile.exists() and time.monotonic() < end:
        time.sleep(0.02)
    pid = int(pidfile.read_text())
    t0 = time.monotonic()
    svc.shutdown(3.0)
    assert time.monotonic() - t0 < 1.0
    end = time.monotonic() + 3
    while svc.probe.running and time.monotonic() < end:
        time.sleep(0.02)
    assert svc.probe.running is None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    pidfile.unlink()
    t0 = time.monotonic()
    assert snap(lab)["context"]["presence"]["status"] in ("checking", "error")
    assert time.monotonic() - t0 < 0.5 and not pidfile.exists()       # no new listing after shutdown


# ─── commands ───────────────────────────────────────────────────────────────
def test_stale_and_forbidden_commands_do_nothing(lab):
    create_and_start(lab)
    s = wait(lab, lambda s: s["state"] == "calibration")
    for body in ({"expect_state": "ready", "seq": s["state_seq"]},
                 {"expect_state": "calibration", "seq": s["state_seq"] - 1},
                 {"expect_state": "calibration", "seq": s["seq"] + 1000}):
        r = lab.client.post(lab.base + "/command", json={"command": "calibrate", **body})
        assert (r.status_code, code(r)) == (409, "stale_command")
    assert code(post(lab, "/command", s, command="accept")) == "command_not_allowed"
    assert lab.client.post(lab.base + "/command", json={"command": "start_reading", "expect_state": s["state"],
                                                        "seq": s["state_seq"]}).status_code == 422
    assert snap(lab)["state"] == "calibration"                       # nothing happened
    assert post(lab, "/command", s, command="calibrate").status_code == 200
    again = post(lab, "/command", s, command="calibrate")             # the same decision twice
    assert (again.status_code, code(again)) == (409, "stale_command")


def test_one_run_per_backend_and_per_user(lab):
    other = make_chart(lab.store, n_useful=60, steps=26, chart_id="CHT-20261124-1016-CD")
    base2 = f"/api/charts/{other.chart_id}/external-measure"
    create_and_start(lab)
    wait(lab, lambda s: s["state"] == "calibration")
    s2 = lab.client.post(base2, json={}).json()
    r = post(lab, "/start", s2, base2)
    assert (r.status_code, code(r)) == (409, "run_active")
    lab.service(store_lock="another-store.lock")                      # a second freeglaz, other store
    s2 = lab.client.post(base2, json={}).json()
    r = post(lab, "/start", s2, base2)
    assert (r.status_code, code(r), r.json()["detail"]["scope"]) == (409, "instrument_busy", "user")


# ─── interruptions ──────────────────────────────────────────────────────────
def test_start_up_reconciles_and_never_opens_the_instrument(lab, monkeypatch):
    """A backend that died during a run: the next start closes the run, keeps the strips,
    starts no engine; the session then resumes with -r -N -K."""
    lab.engine(button_ms=20, button_limit=2)
    lab.client.post(lab.base, json={})
    sess = S.Session.find_open(lab.chart.dir)
    run = R.Run.start(sess, S.EngineSpec(**sess.data["engine"]),
                      lock=S.InstrumentLock(lab.tmp / "store.lock"), env=dict(os.environ))
    end = time.monotonic() + 20
    while S.Session.open(sess.dir).summary()["passes_saved"] < 2 and time.monotonic() < end:
        for n in run.poll(0.02):
            if n["kind"] == "calibration_prompt":
                run.command("calibrate")
    os.killpg(run.proc.pgid, signal.SIGKILL)                        # the backend "dies" here
    run.proc.wait(2)
    run.lock.release()
    assert S.Session.open(sess.dir).data["runs"][0]["ended_at"] is None

    def no_spawn(*a, **kw):
        raise AssertionError("an engine was started at start-up")
    with monkeypatch.context() as m:
        m.setattr(R.PtyProcess, "spawn", no_spawn)
        with TestClient(app):                                       # lifespan: start-up then shutdown
            pass
    lab.service()                       # the next backend (a shut-down service lists USB no more)
    approved_engine(lab.tmp, lab.chart, lab.mp, button_ms=20, serial=SERIAL_SECRET)   # no button limit now
    after = S.Session.open(sess.dir)
    assert after.data["runs"][0]["end"] == "interrupted" and after.summary()["passes_saved"] == 2
    s = snap(lab)
    assert s["state"] == "idle" and "start" in s["actions"]
    assert post(lab, "/start", s).status_code == 200
    s = run_to_end(lab)
    argv = S.Session.open(sess.dir).data["runs"][1]["argv"]
    assert s["state"] == "complete" and {"-r", "-N", "-K"} <= set(argv)


def test_backend_shutdown_pauses_the_run_within_the_window(lab):
    with TestClient(app) as client:
        lab.client = client
        create_and_start(lab, button_ms=20, button_limit=2)
        calibrate(lab)
        wait(lab, lambda s: s["state"] == "ready" and s["session"]["passes_saved"] == 2)
        pid = em.get_service()._active.run.proc.pid
        t0 = time.monotonic()
    elapsed = time.monotonic() - t0
    assert elapsed < 4.5
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    sess = S.Session.find_open(lab.chart.dir)
    assert sess.data["runs"][-1]["end"] == "backend_stop" and sess.summary()["passes_saved"] == 2
    lk = S.InstrumentLock(lab.user_lock)
    lk.acquire({"op": "check"})                                     # nothing holds the instrument
    lk.release()


def test_a_chart_is_not_deleted_during_a_run(lab):
    create_and_start(lab)
    s = wait(lab, lambda s: s["state"] == "calibration")
    r = lab.client.delete(f"/api/charts/{lab.chart.chart_id}")
    assert (r.status_code, code(r)) == (409, "external_measure_running")
    assert post(lab, "/command", s, command="pause").status_code == 200
    run_to_end(lab)
    assert lab.client.delete(f"/api/charts/{lab.chart.chart_id}").status_code == 200


# ─── publication ────────────────────────────────────────────────────────────
def test_a_partial_measurement_is_never_published(lab):
    create_and_start(lab, button_ms=20, button_limit=2)
    calibrate(lab)
    s = wait(lab, lambda s: s["state"] == "ready" and s["session"]["passes_saved"] == 2)
    assert post(lab, "/command", s, command="pause").status_code == 200
    s = run_to_end(lab)
    assert s["state"] == "idle" and "finalize" not in s["actions"]
    assert code(post(lab, "/finalize", s)) == "command_not_allowed"
    sess = S.Session.find_open(lab.chart.dir)                        # even if session.json lied:
    sess.data["last_checkpoint"]["complete"] = True
    sess._save()
    s = snap(lab)
    assert s["state"] == "complete"
    r = post(lab, "/finalize", s)
    assert (r.status_code, code(r)) == (409, "measurement_incomplete")
    assert not (lab.chart.dir / "measurements").exists() or not any((lab.chart.dir / "measurements").iterdir())


def test_a_development_engine_cannot_publish(lab):
    exe = lab.engine(button_ms=20)
    lab.mp.setenv(E.ENV_PATH, str(exe))                               # pinned for development
    s = lab.client.post(lab.base, json={}).json()
    assert s["publishable"] is False
    assert S.Session.find_open(lab.chart.dir).data["engine"]["origin"] == "development"
    assert post(lab, "/start", s).status_code == 200
    calibrate(lab)
    s = run_to_end(lab)
    assert s["state"] == "complete"
    r = post(lab, "/finalize", s)
    assert (r.status_code, code(r), r.json()["detail"]["origin"]) == (409, "engine_not_approved", "development")
    assert not (lab.chart.dir / "measurements").exists()


def test_a_valid_engine_in_the_shipped_place_is_not_approved_unless_trusted(lab):
    """A binary with the patch's text and a matching manifest, in the very place freeglaz ships
    its engine, but not a trusted build: it runs and saves the session (development), it is
    never published."""
    lab.engine(button_ms=20, trust=False)
    s = lab.client.post(lab.base, json={}).json()
    assert s["publishable"] is False and s["context"]["engine"]["origin"] == "development"
    sess = S.Session.find_open(lab.chart.dir)
    assert (sess.data["engine"]["source"], sess.data["engine"]["origin"]) == ("bundle", "development")
    assert post(lab, "/start", s).status_code == 200
    calibrate(lab)
    s = run_to_end(lab)
    assert s["state"] == "complete"
    r = post(lab, "/finalize", s)
    assert (r.status_code, code(r)) == (409, "engine_not_approved")
    assert not (lab.chart.dir / "measurements").exists()


def test_an_origin_written_in_session_json_is_not_a_proof(lab):
    """session.json is a file of the store. Claiming "approved" there (origin, source) for an
    untrusted binary does not publish: the trusted list is checked again at finalize."""
    lab.engine(button_ms=20, trust=False)
    s = lab.client.post(lab.base, json={}).json()
    assert post(lab, "/start", s).status_code == 200
    calibrate(lab)
    run_to_end(lab)
    sess = S.Session.find_open(lab.chart.dir)
    sess.data["engine"].update(origin="approved", source="bundle")
    sess._save()
    s = snap(lab)
    assert s["state"] == "complete" and s["publishable"] is False
    r = post(lab, "/finalize", s)
    assert (r.status_code, code(r)) == (409, "engine_not_approved")
    assert not (lab.chart.dir / "measurements").exists()


def test_the_system_chartread_is_never_used(lab, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "chartread").write_text("#!/bin/sh\ntouch /tmp/never\n")
    (bindir / "chartread").chmod(0o755)
    lab.mp.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    lab.mp.setattr(E, "FLATPAK_INFO", tmp_path / "none")             # no shipped engine
    r = lab.client.post(lab.base, json={})
    assert (r.status_code, code(r)) == (409, "engine_missing")


def test_seq_keeps_growing_across_a_backend_restart(lab):
    """A window keeps the snapshot with the highest seq. The snapshots of a restarted backend
    must therefore come after those of the backend that died, and a command decided on the
    dead backend's state must be refused as stale, never applied."""
    lab.engine(button_ms=20)
    s_old = lab.client.post(lab.base, json={}).json()
    for _ in range(50):
        lab.svc.broadcast.bump(lab.chart.chart_id)
    old_seq = snap(lab)["seq"]
    lab.svc = lab.service()                                          # the backend restarted
    s_new = snap(lab)
    assert s_new["seq"] > old_seq and s_new["state_seq"] > s_old["state_seq"]
    assert s_new["seq"] < 2 ** 53
    r = post(lab, "/start", {**s_old, "state_seq": s_old["state_seq"]})
    assert (r.status_code, code(r)) == (409, "stale_command")


# ─── races between HTTP commands ────────────────────────────────────────────
def _race(*calls):
    barrier, out = threading.Barrier(len(calls)), [None] * len(calls)

    def go(i, fn):
        barrier.wait()
        out[i] = fn()
    threads = [threading.Thread(target=go, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    return out


def test_finalize_against_abandon(lab):
    s = complete_session(lab)
    fin, ab = _race(lambda: post(lab, "/finalize", s), lambda: post(lab, "/abandon", s))
    codes = sorted([fin.status_code, ab.status_code])
    assert codes == [200, 409], (fin.text, ab.text)
    status = S.Session.sessions(lab.chart.dir)[-1].data["status"]
    n_meas = len(list((lab.chart.dir / "measurements").glob("*.ti3"))) if (lab.chart.dir / "measurements").exists() else 0
    assert (status, n_meas) == (("published", 1) if fin.status_code == 200 else ("abandoned", 0))


def test_resume_against_abandon(lab):
    lab.engine(button_ms=20, button_limit=1)
    s = lab.client.post(lab.base, json={}).json()
    st, ab = _race(lambda: post(lab, "/start", s), lambda: post(lab, "/abandon", s))
    assert sorted([st.status_code, ab.status_code]) == [200, 409], (st.text, ab.text)
    if st.status_code == 200:
        assert code(ab) in ("stale_command", "instrument_busy")
        s2 = wait(lab, lambda x: x["state"] == "calibration")
        post(lab, "/command", s2, command="pause")
        run_to_end(lab)
        assert S.Session.find_open(lab.chart.dir) is not None
    else:
        assert code(st) in ("stale_command", "session_not_found") and not snap(lab)["running"]
        assert S.Session.sessions(lab.chart.dir)[-1].data["status"] == "abandoned"


def test_pause_against_finalize_and_double_commands(lab):
    create_and_start(lab, button_ms=20, button_limit=1)
    s = wait(lab, lambda s: s["state"] == "calibration")
    c1, c2 = _race(lambda: post(lab, "/command", s, command="calibrate"),
                   lambda: post(lab, "/command", s, command="calibrate"))
    assert sorted([c1.status_code, c2.status_code]) == [200, 409]
    s = wait(lab, lambda s: s["state"] == "ready" and s["session"]["passes_saved"] == 1)
    pa, fi = _race(lambda: post(lab, "/command", s, command="pause"), lambda: post(lab, "/finalize", s))
    assert pa.status_code == 200 and fi.status_code == 409 and code(fi) == "command_not_allowed"
    s = run_to_end(lab)
    assert s["state"] == "idle" and code(post(lab, "/finalize", s)) == "command_not_allowed"


# ─── events ─────────────────────────────────────────────────────────────────
def test_broadcast_never_waits_for_readers():
    b = em.Broadcast()
    base = b.seq
    stop = threading.Event()

    def reader():
        seen = 0
        while not stop.is_set():
            seen = b.wait("C", seen, 0.5)                          # and never reads anything else
    readers = [threading.Thread(target=reader, daemon=True) for _ in range(6)]
    for t in readers:
        t.start()
    t0 = time.monotonic()
    for _ in range(20000):
        b.bump("C", [{"kind": "x"}])
    assert time.monotonic() - t0 < 2.0 and b.rev("C") == base + 20000 and len(b.notices) == em.NOTICES_KEPT
    stop.set()


def _free_port():
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        return so.getsockname()[1]


def _sse(url, out, stop, headers=None, limit=None):
    with httpx.Client(timeout=httpx.Timeout(30.0, read=30.0)) as c:
        with c.stream("GET", url, headers=headers or {}) as r:
            ev = {}
            for line in r.iter_lines():
                line = line.rstrip("\r")
                if not line:
                    if ev.get("event") == "snapshot":
                        out.append({"id": int(ev["id"]), **json.loads(ev["data"])})
                        if stop(out[-1]) or (limit and len(out) >= limit):
                            return
                    ev = {}
                    continue
                k, _, v = line.partition(":")
                ev[k] = v.strip()


def test_two_windows_and_a_reconnection_over_real_http(lab):
    port = _free_port()
    server, thread = backend_main.run_background("127.0.0.1", port)
    try:
        assert backend_main.wait_until_up("127.0.0.1", port, timeout=20)
        url = f"http://127.0.0.1:{port}{lab.base}/events"
        lab.engine(button_ms=40)
        s = lab.client.post(lab.base, json={}).json()
        done = lambda e: e["state"] == "complete"                  # noqa: E731
        a, b = [], []
        ta = threading.Thread(target=_sse, args=(url, a, done))
        tb = threading.Thread(target=_sse, args=(url, b, done))
        ta.start()
        tb.start()
        time.sleep(0.5)
        assert post(lab, "/start", s).status_code == 200
        calibrate(lab)
        first = []
        _sse(url, first, lambda e: e["session"] and e["session"]["passes_saved"] >= 2)
        last_id = first[-1]["id"]
        again = []
        _sse(url, again, lambda e: True, headers={"Last-Event-ID": str(last_id)})
        ta.join(30)
        tb.join(30)
    finally:
        server.should_exit = True
        thread.join(10)
    for got in (a, b):
        assert got[0]["id"] <= got[-1]["id"] and got[-1]["state"] == "complete"
        assert [e["id"] for e in got] == sorted({e["id"] for e in got})          # increasing, no repeat
        saved = [e["session"]["passes_saved"] for e in got if e.get("session")]
        assert saved == sorted(saved) and saved[-1] == 5
    assert a[-1]["id"] == b[-1]["id"]                                 # both windows end on the same state
    assert again[0]["resumed_from"] == last_id and again[0]["id"] >= last_id
    assert snap(lab)["seq"] >= a[-1]["id"]                            # polling gives the same present


def test_a_hung_listing_never_blocks_the_event_loop(lab):
    """The SSE route builds its snapshots on worker threads and does not wait for a listing:
    with the USB listing hanging, a window connecting gets its first snapshot at once
    ("checking") and every other route still answers (note 149 §14: ioreg on MainThread)."""
    lst = Listing()
    lab.service(presence=lst)
    lst.hang = True
    port = _free_port()
    server, thread = backend_main.run_background("127.0.0.1", port)
    try:
        assert backend_main.wait_until_up("127.0.0.1", port, timeout=20)
        first = []
        t0 = time.monotonic()
        sse = threading.Thread(target=_sse, args=(f"http://127.0.0.1:{port}{lab.base}/events", first, lambda e: True))
        sse.start()
        sse.join(5)
        took_sse = time.monotonic() - t0
        with httpx.Client(timeout=5.0) as c:
            t0 = time.monotonic()
            assert c.get(f"http://127.0.0.1:{port}/api/health").status_code == 200
            took_health = time.monotonic() - t0
        assert first and first[0]["context"]["presence"]["status"] == "checking"
        assert took_sse < 1.0 and took_health < 0.5, (took_sse, took_health)
        assert lst.max_active == 1
    finally:
        lst.release.set()
        server.should_exit = True
        thread.join(10)


# ─── the hardware lock ──────────────────────────────────────────────────────
def test_user_lock_scope_is_never_overstated(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    info = tmp_path / ".flatpak-info"
    assert S.user_lock_path("darwin")[1] == "user"
    assert S.user_lock_path("linux", {}, info) == (None, "no_runtime_dir")
    assert S.user_lock_path("linux", {"XDG_RUNTIME_DIR": str(run)}, info) == \
        (run / "freeglaz" / S.USER_LOCK_NAME, "user")
    env = {"XDG_RUNTIME_DIR": str(run)}
    info.write_text("[Application]\nname=io.github.freeglaz.freeglaz\n")   # inside a Flatpak…
    assert S.user_lock_path("linux", env, info) == (None, "sandbox")
    (run / "freeglaz").mkdir()        # …a directory of that name, not the permission
    assert S.user_lock_path("linux", env, info) == (None, "sandbox")


@pytest.mark.parametrize("filesystems,scope", [
    ("xdg-documents;xdg-run/freeglaz:create;", "user"),
    ("xdg-run/freeglaz;", "user"),
    ("xdg-run/freeglaz:rw;", "user"),
    ("xdg-run/freeglaz:ro;", "sandbox"),                  # cannot create or hold the lock file
    ("xdg-run/freeglaz:create;!xdg-run/freeglaz;", "sandbox"),   # revoked by an override
    ("xdg-run/freeglazX:create;xdg-run;", "sandbox"),
    ("", "sandbox"),
])
def test_user_lock_scope_follows_the_granted_flatpak_permission(tmp_path, filesystems, scope):
    """Found in the real sandbox (note 143): after one run WITH xdg-run/freeglaz, the per-app
    $XDG_RUNTIME_DIR keeps an empty freeglaz/ mountpoint; a later run WITHOUT the permission
    saw it, claimed the user scope, and took a private lock while the host held the shared
    one. The scope now comes from the permissions Flatpak wrote in /.flatpak-info."""
    run = tmp_path / "run"
    (run / "freeglaz").mkdir(parents=True)
    info = tmp_path / ".flatpak-info"
    info.write_text("[Application]\nname=io.github.freeglaz.freeglaz\nruntime=runtime/org.gnome.Platform/x86_64/50\n\n"
                    f"[Context]\nshared=network;ipc;\nfilesystems={filesystems}\n\n"
                    "[Session Bus Policy]\norg.freedesktop.portal.*=talk\n")
    path, got = S.user_lock_path("linux", {"XDG_RUNTIME_DIR": str(run)}, info)
    assert got == scope and (path is not None) == (scope == "user")


def test_user_lock_scope_with_an_unreadable_flatpak_info(tmp_path):
    run = tmp_path / "run"
    (run / "freeglaz").mkdir(parents=True)
    info = tmp_path / ".flatpak-info"
    info.write_bytes(b"\xff\xfe not a key file")
    assert S.user_lock_path("linux", {"XDG_RUNTIME_DIR": str(run)}, info) == (None, "sandbox")


def test_lock_set_is_all_or_nothing(tmp_path):
    held = S.InstrumentLock(tmp_path / "user.lock")
    held.acquire({"op": "other freeglaz"})
    store = S.InstrumentLock(tmp_path / "store.lock")
    ls = S.LockSet([("store", store), ("user", S.InstrumentLock(tmp_path / "user.lock"))])
    with pytest.raises(S.LockBusy) as e:
        ls.acquire({"op": "run"})
    assert e.value.info["scope"] == "user" and not store.held
    held.release()
    ls.acquire({"op": "run"})
    assert ls.held
    ls.release()
