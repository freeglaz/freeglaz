"""Stage 1 backend — roll scan: NON-BLOCKING background job + persistent session.

HARD guards tested off-Z9 (sol_native mocked): anti-concurrent, cooldown ≥30 s,
ti3 written ONLY on OP_FINISHED_OK; per-file session + "one active" lock.
Ref: Docs/Design_Webapp_Scan_Rouleau_Multi_Scan.md §1/§3.
"""
import time
import threading
from types import SimpleNamespace

import pytest

from webapp.backend.services import chart_scan_job as job
from webapp.backend.services import scan_session as sess

SERIAL = "TESTSN"   # per-serial: scan_sessions/<serial>/


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("FREEGLAZ_STORE_ROOT", str(tmp_path / "store"))
    job.reset()
    yield
    job.reset()


def _wait_terminal(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        s = job.status()
        if s and s["state"] in ("done", "error"):
            return s
        time.sleep(0.01)
    raise AssertionError("job unfinished (timeout)")


def _ti3_ok_mocks(monkeypatch, ti3_text="CTI3\n"):
    monkeypatch.setattr("lib.z9_client.sol_native.measure",
                        lambda host, fields=None, on_status=None: b"CGATS")
    def _fake_build(cgats_text, desc, ti3_path):
        from pathlib import Path
        Path(ti3_path).write_text(ti3_text)
        return SimpleNamespace(n_patches=10, n_spectral_bands=16)
    monkeypatch.setattr("lib.z9_client.sol_ti3.build_ti3_from_native_cgats", _fake_build)


# ─── Background job ─────────────────────────────────────────────────────────────
def test_scan_non_blocking_and_completes(tmp_path, monkeypatch):
    _ti3_ok_mocks(monkeypatch)
    snap = job.start(host="127.0.0.1", chart_id="C", fields=[], desc={}, meas_dir=tmp_path)
    assert snap["state"] == "running"               # IMMEDIATE return (non-blocking)
    final = _wait_terminal()
    assert final["state"] == "done"
    assert final["ti3"] and final["n_patches"] == 10 and final["bands"] == 16
    assert list(tmp_path.glob("*.ti3"))             # ti3 written


def test_anti_concurrent(tmp_path, monkeypatch):
    gate = threading.Event()
    def _slow(host, fields=None, on_status=None):
        gate.wait(5); return b"CGATS"
    monkeypatch.setattr("lib.z9_client.sol_native.measure", _slow)
    monkeypatch.setattr("lib.z9_client.sol_ti3.build_ti3_from_native_cgats",
                        lambda *a, **k: SimpleNamespace(n_patches=1, n_spectral_bands=16))
    job.start(host="h", chart_id="C", fields=[], desc={}, meas_dir=tmp_path)
    with pytest.raises(job.ScanBusyError):           # 2nd scan while one is running → refused
        job.start(host="h", chart_id="C", fields=[], desc={}, meas_dir=tmp_path)
    gate.set()
    _wait_terminal()


def test_no_ti3_if_not_finished_ok(tmp_path, monkeypatch):
    def _cancel(host, fields=None, on_status=None):
        raise RuntimeError("measurement failed: OperationStatus = OP_CANCELLED")
    monkeypatch.setattr("lib.z9_client.sol_native.measure", _cancel)
    job.start(host="h", chart_id="C", fields=[], desc={}, meas_dir=tmp_path)
    final = _wait_terminal()
    assert final["state"] == "error" and "OP_CANCELLED" in final["error"]
    assert final["ti3"] is None
    assert not list(tmp_path.glob("*.ti3"))          # NO half-written ti3
    assert not list(tmp_path.glob("*.cgats"))


def test_cooldown_blocks_then_allows(tmp_path, monkeypatch):
    _ti3_ok_mocks(monkeypatch)
    job.reset()
    job._last_end_monotonic = time.monotonic()       # a scan just finished
    assert job.cooldown_remaining() > 0
    with pytest.raises(job.ScanCooldownError):        # < 30 s → refused
        job.start(host="h", chart_id="C", fields=[], desc={}, meas_dir=tmp_path)
    job._last_end_monotonic = time.monotonic() - (job.SOL_COOLDOWN_S + 1)   # 31 s later
    assert job.cooldown_remaining() == 0
    snap = job.start(host="h", chart_id="C", fields=[], desc={}, meas_dir=tmp_path)  # ≥30 s → OK
    assert snap["state"] == "running"
    _wait_terminal()


# ─── Persistent session ─────────────────────────────────────────────────────
def test_session_create_active_and_lock():
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", door=1, stage=sess.STAGE_SCANNING)
    assert sess.active_session()["session_id"] == s["session_id"]
    with pytest.raises(sess.SessionLockError):       # lock: only one active
        sess.create_session(serial=SERIAL, chart_id="CHT-B")
    sess.close_session(s["session_id"])
    assert sess.active_session() is None             # lock released
    sess.create_session(serial=SERIAL, chart_id="CHT-B")            # a new one can start


def test_session_per_file_no_overwrite():
    from datetime import datetime
    s1 = sess.create_session(serial=SERIAL, chart_id="CHT-A", now=datetime(2026, 6, 11, 8, 0, 0))
    sess.close_session(s1["session_id"])
    s2 = sess.create_session(serial=SERIAL, chart_id="CHT-A", now=datetime(2026, 6, 11, 9, 0, 0))
    assert s1["session_id"] != s2["session_id"]      # 1 file per session
    assert len(sess.list_sessions()) == 2            # the old one is NOT overwritten


def test_session_add_scan_and_stage_transitions():
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    sid = s["session_id"]
    sess.add_scan(sid, ti3="20260611_080000.ti3", n_patches=464)
    r = sess.read_session(sid)
    assert r["stage"] == sess.STAGE_AWAITING_NEXT
    assert len(r["scans"]) == 1 and r["scans"][0]["ti3"] == "20260611_080000.ti3"
    sess.add_scan(sid, ti3="20260611_170000.ti3", n_patches=464)   # multi-scan
    assert len(sess.read_session(sid)["scans"]) == 2
    sess.close_session(sid)                                        # → STAGE_CLOSED (single terminal)
    assert sess.read_session(sid)["stage"] == sess.STAGE_CLOSED
    assert sess.active_session() is None


def test_session_persists_across_restart(tmp_path, monkeypatch):
    """The marker survives a "restart" (simulated new process = disk re-read)."""
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    sess.add_scan(s["session_id"], ti3="a.ti3", n_patches=464)
    # "restart": we re-read from disk (nothing in memory)
    again = sess.read_session(s["session_id"])
    assert again["stage"] == sess.STAGE_AWAITING_NEXT
    assert again["scans"][0]["ti3"] == "a.ti3"
    assert sess.active_session()["session_id"] == s["session_id"]


# ─── Anti-orphan: TTL auto-close / boot cleanup / error-close (LOT 1) ─────────
def test_session_ttl_expires_stale_awaiting_next():
    """An `awaiting_next` session idle beyond the TTL is auto-closed by active_session
    (forgotten multi-scan lock) — but a FRESH one is kept."""
    from datetime import datetime, timezone, timedelta
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    sess.add_scan(s["session_id"], ti3="a.ti3", n_patches=10)          # → awaiting_next
    future = datetime.now(timezone.utc) + timedelta(seconds=sess.SCAN_SESSION_TTL_S + 60)
    assert sess.active_session(now=future) is None                     # stale → auto-closed
    assert sess.read_session(s["session_id"])["stage"] == sess.STAGE_CLOSED
    # a fresh awaiting_next (real now) is NOT expired
    s2 = sess.create_session(serial=SERIAL, chart_id="CHT-B", stage=sess.STAGE_SCANNING)
    sess.add_scan(s2["session_id"], ti3="b.ti3", n_patches=10)
    assert sess.active_session()["session_id"] == s2["session_id"]


def test_session_scanning_never_expired_by_ttl():
    """A long-running `scanning` session (2000+ patches) is NEVER cut by the idle TTL —
    it is resolved by the scan job (success/error) or boot cleanup, not a clock."""
    from datetime import datetime, timezone, timedelta
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    future = datetime.now(timezone.utc) + timedelta(seconds=sess.SCAN_SESSION_TTL_S * 10)
    assert sess.active_session(now=future)["session_id"] == s["session_id"]
    assert sess.read_session(s["session_id"])["stage"] == sess.STAGE_SCANNING


def test_cleanup_orphans_boot_closes_active_leaves_terminal():
    """Boot cleanup closes EVERY non-terminal session (crash orphan) regardless of age;
    already-closed ones are untouched."""
    s1 = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    sess.close_session(s1["session_id"])                              # already terminal
    s2 = sess.create_session(serial=SERIAL, chart_id="CHT-B", stage=sess.STAGE_SCANNING)  # active
    assert sess.cleanup_orphans() == 1                               # only the active one
    assert sess.read_session(s2["session_id"])["stage"] == sess.STAGE_CLOSED
    assert sess.active_session() is None


def test_scan_error_closes_session_anti_orphan(tmp_path, monkeypatch):
    """A failed scan must CLOSE its session (no orphan lock left behind); the ti3 of any
    prior successful scan stays on disk."""
    def _fail(host, fields=None, on_status=None):
        raise RuntimeError("measurement failed: OperationStatus = OP_CANCELLED")
    monkeypatch.setattr("lib.z9_client.sol_native.measure", _fail)
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    job.start(host="h", chart_id="CHT-A", fields=[], desc={}, meas_dir=tmp_path,
              session_id=s["session_id"])
    final = _wait_terminal()
    assert final["state"] == "error"
    assert sess.read_session(s["session_id"])["stage"] == sess.STAGE_CLOSED
    assert sess.active_session() is None


def test_scan_success_keeps_session_open_for_multiscan(tmp_path, monkeypatch):
    """A successful scan keeps the session `awaiting_next` (deliberate multi-scan not broken)."""
    _ti3_ok_mocks(monkeypatch)
    s = sess.create_session(serial=SERIAL, chart_id="CHT-A", stage=sess.STAGE_SCANNING)
    job.start(host="h", chart_id="CHT-A", fields=[], desc={}, meas_dir=tmp_path,
              session_id=s["session_id"])
    _wait_terminal()
    assert sess.read_session(s["session_id"])["stage"] == sess.STAGE_AWAITING_NEXT


# ─── Mini ΔE report inter-scans (safeguard before averaging, read-only) ─────────
def _ti3(patches):
    """patches = [(sid, R, G, B, L, a, b)] → minimal ti3 with native columns."""
    hdr = ("CTI3\nNUMBER_OF_FIELDS 7\nBEGIN_DATA_FORMAT\n"
           "SAMPLE_ID RGB_R RGB_G RGB_B LAB_L LAB_A LAB_B\nEND_DATA_FORMAT\n"
           f"NUMBER_OF_SETS {len(patches)}\nBEGIN_DATA\n")
    rows = "\n".join(" ".join(str(x) for x in p) for p in patches)
    return hdr + rows + "\nEND_DATA\n"


def test_ciede2000_matches_sharma():
    from webapp.backend.services.scan_delta import ciede2000
    assert abs(ciede2000((50, 2.6772, -79.7751), (50, 0, -82.7485)) - 2.0425) < 1e-3
    assert abs(ciede2000((50, -1.3802, -84.2814), (50, 0, -82.7485)) - 1.0000) < 1e-3
    assert abs(ciede2000((50, 0, 0), (50, -1, 2)) - 2.3669) < 1e-3


def test_scan_delta_identical_scans_near_zero(tmp_path):
    from webapp.backend.services import scan_delta
    pts = [(i, 100, 100, 100, 50.0, 1.0, -2.0) for i in range(20)]
    t1 = tmp_path / "a.ti3"; t1.write_text(_ti3(pts))
    t2 = tmp_path / "b.ti3"; t2.write_text(_ti3(pts))
    r = scan_delta.compute_scan_delta([t1, t2])
    assert r["n_patches"] == 20 and r["n_scans"] == 2
    assert r["summary"]["max"] < 0.01 and r["summary"]["mean"] < 0.01
    assert r["summary"]["isolated_outlier"] is False


def test_scan_delta_isolated_outlier_flagged(tmp_path):
    """1 heavily shifted patch (contamination) → high max + isolated flag + worst_patch_id."""
    from webapp.backend.services import scan_delta
    base = [(i, 100, 100, 100, 50.0, 1.0, -2.0) for i in range(20)]
    pert = list(base); pert[7] = (7, 100, 100, 100, 62.0, 9.0, -14.0)
    t1 = tmp_path / "a.ti3"; t1.write_text(_ti3(base))
    t2 = tmp_path / "b.ti3"; t2.write_text(_ti3(pert))
    s = scan_delta.compute_scan_delta([t1, t2])["summary"]
    assert s["max"] > 5 and s["worst_patch_id"] == "7"
    assert s["isolated_outlier"] is True            # peak ≫ p95 = contamination signature


def test_scan_delta_warm_family_not_isolated(tmp_path):
    """A FAMILY of moderately shifted patches (drying) → NOT an isolated outlier."""
    from webapp.backend.services import scan_delta
    base = [(i, 100, 100, 100, 50.0, 1.0, -2.0) for i in range(20)]
    fam = list(base)
    for k in range(6):
        fam[k] = (k, 100, 100, 100, 51.3, 2.0, -1.0)   # ~family, several patches
    t1 = tmp_path / "a.ti3"; t1.write_text(_ti3(base))
    t2 = tmp_path / "b.ti3"; t2.write_text(_ti3(fam))
    assert scan_delta.compute_scan_delta([t1, t2])["summary"]["isolated_outlier"] is False


def test_scan_delta_needs_two_scans(tmp_path):
    from webapp.backend.services import scan_delta
    t1 = tmp_path / "a.ti3"; t1.write_text(_ti3([(0, 100, 100, 100, 50, 1, -2)]))
    with pytest.raises(ValueError):
        scan_delta.compute_scan_delta([t1])
