"""Passive USB presence (lib.z9_client.chartread_fgk.usb_presence): sysfs (Linux) and
IORegistry (macOS) listings injected; nothing under /dev is ever opened; absence, other
instruments, several instruments and a failed listing are kept apart."""
import builtins
import io
import os
import threading
import time
from pathlib import Path

import pytest

from lib.z9_client.chartread_fgk import usb_presence as U

HUB = ("1d6b", "0002", "xHCI Host Controller")
COLORMUNKI = ("0971", "2007", "colormunki")
I1STUDIO = ("0765", "6008", "i1Studio")
I1PRO3 = ("0765", "6009", "i1Pro3")
I1DISPLAY = ("0765", "5020", "i1Display")
MOUSE = ("046d", "c077", "USB Optical Mouse")


def sysfs(tmp_path, *devices, interfaces=True):
    root = tmp_path / "devices"
    root.mkdir()
    for i, (vid, pid, product) in enumerate(devices):
        d = root / f"1-{i + 1}"
        d.mkdir()
        (d / "idVendor").write_text(vid + "\n")
        (d / "idProduct").write_text(pid + "\n")
        (d / "product").write_text(product + "\n")
        if interfaces:
            (root / f"1-{i + 1}:1.0").mkdir()           # an interface: no idVendor
    return root


def presence(tmp_path, model, *devices):
    root = sysfs(tmp_path, *devices)
    return U.check_presence(model, scan=lambda: U.scan_sysfs(root))


@pytest.mark.parametrize("model,devices,status", [
    ("colormunki", (HUB, MOUSE, COLORMUNKI), "present"),
    ("colormunki", (HUB, MOUSE), "absent"),
    ("colormunki", (HUB, I1STUDIO), "other"),            # same Argyll driver, another product
    ("colormunki", (COLORMUNKI, I1DISPLAY), "several"),   # chartread -c 1 would take the first
    ("colormunki", (COLORMUNKI, COLORMUNKI), "several"),
    ("i1pro3plus", (HUB, I1PRO3), "present"),
    ("i1pro3plus", (HUB, COLORMUNKI), "other"),
])
def test_verdicts(tmp_path, model, devices, status):
    p = presence(tmp_path, model, *devices)
    assert p.status == status, p.detail
    assert p.present is (status == "present")
    assert [d.argyll_name is not None for d in p.instruments] == [True] * len(p.instruments)


def test_i1pro3plus_identifier_only_names_the_family(tmp_path):
    p = presence(tmp_path, "i1pro3plus", I1PRO3)
    assert p.present and p.family_only and p.to_dict()["instruments"][0]["pid"] == "6009"
    cm = U.check_presence("colormunki", devices=[U.UsbDevice(0x0971, 0x2007, "1-1")])
    assert cm.present and not cm.family_only


def test_a_failed_listing_is_an_error_not_an_absence(tmp_path):
    assert U.check_presence("colormunki", scan=lambda: U.scan_sysfs(tmp_path / "nope")).status == "error"
    root = sysfs(tmp_path, ("zz", "2007", "x"))
    p = U.check_presence("colormunki", scan=lambda: U.scan_sysfs(root))
    assert p.status == "error" and "unreadable" in p.detail
    assert U.check_presence("spyder", devices=[]).status == "error"


def test_nothing_under_dev_is_ever_opened(tmp_path, monkeypatch):
    root = sysfs(tmp_path, HUB, COLORMUNKI)
    opened = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(f, *a, **kw):
        opened.append(str(f))
        return real_open(f, *a, **kw)

    def spy_os_open(f, *a, **kw):
        opened.append(str(f))
        return real_os_open(f, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)                  # what pathlib calls
    monkeypatch.setattr(os, "open", spy_os_open)
    assert U.check_presence("colormunki", scan=lambda: U.scan_sysfs(root)).present
    assert opened and all(p.startswith(str(root)) for p in opened)
    assert not any(p.startswith("/dev") for p in opened)
    src = Path(U.__file__).read_text()
    assert '"/dev' not in src and "'/dev" not in src


IOREG = """+-o Root  <class IORegistryEntry, id 0x100000100, retain 13>
  +-o AppleT6000USBXHCI@02000000  <class AppleT6000USBXHCI, id 0x1000002a4, registered, matched>
    | {
    |   "locationID" = 33554432
    | }
    +-o colormunki@02100000  <class IOUSBHostDevice, id 0x100000abc, registered, matched, active>
    |   {
    |     "sessionID" = 1234
    |     "idProduct" = 8199
    |     "USB Product Name" = "colormunki"
    |     "locationID" = 34603008
    |     "idVendor" = 2417
    |   }
    +-o USB2.0 Hub@02200000  <class IOUSBHostDevice, id 0x100000abd, registered, matched, active>
        {
          "idProduct" = 2069
          "idVendor" = 8457
          "USB Product Name" = "USB2.0 Hub"
          "locationID" = 35651584
        }
"""


def test_ioreg_listing():
    devs = U.parse_ioreg(IOREG)
    assert [(hex(d.vid), hex(d.pid), d.product, d.location) for d in devs] == [
        ("0x971", "0x2007", "colormunki", "34603008"), ("0x2109", "0x815", "USB2.0 Hub", "35651584")]
    assert U.check_presence("colormunki", scan=lambda: U.scan_ioreg(lambda: IOREG)).present


def test_ioreg_failures_are_errors():
    def boom():
        raise U.UsbScanError("ioreg failed: not found")
    assert U.check_presence("colormunki", scan=lambda: U.scan_ioreg(boom)).status == "error"
    assert U.check_presence("colormunki", scan=lambda: U.scan_ioreg(lambda: "")).status == "error"


def test_identifiers_come_from_argyll_and_models_are_known():
    for model, ids in U.MODEL_USB_IDS.items():
        assert all(i in U.ARGYLL_USB_IDS for i in ids), model
    assert U.ARGYLL_USB_IDS[(0x0971, 0x2007)] == "ColorMunki"
    assert U.ARGYLL_USB_IDS[(0x0765, 0x6009)] == "i1Pro 3"


# ─── the ioreg process: bounded, killable ───────────────────────────────────
def fake_ioreg(tmp_path, monkeypatch, body):
    """A stand-in for /usr/sbin/ioreg (a shell script; no device is touched)."""
    exe = tmp_path / "ioreg"
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(0o755)
    monkeypatch.setattr(U, "IOREG", (str(exe),))
    return exe


def gone(pid, within=3.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_ioreg_process_is_read_like_the_listing(tmp_path, monkeypatch):
    listing = tmp_path / "listing.txt"
    listing.write_text(IOREG)
    fake_ioreg(tmp_path, monkeypatch, f'cat "{listing}"\n')
    assert U.check_presence("colormunki", scan=U.scan_ioreg).present
    fake_ioreg(tmp_path, monkeypatch, "exit 3\n")
    assert U.check_presence("colormunki", scan=U.scan_ioreg).status == "error"


def test_ioreg_past_its_timeout_is_killed(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    fake_ioreg(tmp_path, monkeypatch, f'echo $$ > "{pidfile}"\nexec sleep 30\n')
    monkeypatch.setattr(U, "IOREG_TIMEOUT_S", 0.5)
    t0 = time.monotonic()
    p = U.check_presence("colormunki", scan=U.scan_ioreg)
    assert p.status == "error" and "did not answer" in p.detail and time.monotonic() - t0 < 2.0
    assert gone(int(pidfile.read_text())) and not U._live


def test_abort_listings_kills_a_running_ioreg(tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    fake_ioreg(tmp_path, monkeypatch, f'echo $$ > "{pidfile}"\nexec sleep 30\n')
    out = []
    t = threading.Thread(target=lambda: out.append(U.check_presence("colormunki", scan=U.scan_ioreg)))
    t.start()
    end = time.monotonic() + 5
    while not pidfile.exists() and time.monotonic() < end:
        time.sleep(0.02)
    assert U.abort_listings() == 1
    t.join(3)
    assert not t.is_alive() and out[0].status == "error"            # an error, never an absence
    assert gone(int(pidfile.read_text())) and not U._live


# ─── the probe: one listing at a time, never on the caller's thread ──────────
class Gate:
    """A provider the test drives: each listing waits until released (or answers at once)."""

    def __init__(self, *statuses, hang=False):
        self.statuses, self.hang, self.release = list(statuses) or ["present"], hang, threading.Event()
        self.calls, self.active, self.max_active, self.models = 0, 0, 0, []
        self._lock = threading.Lock()

    def __call__(self, model):
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.models.append(model)
            status = self.statuses[min(self.calls, len(self.statuses)) - 1]
        try:
            if self.hang:
                self.release.wait(10)
            if status == "raise":
                raise RuntimeError("listing broke")
            return U.Presence(status, model)
        finally:
            with self._lock:
                self.active -= 1


def no_worker(within=2.0):
    end = time.monotonic() + within
    while time.monotonic() < end:
        if not [t for t in threading.enumerate() if t.name == "usb-presence"]:
            return True
        time.sleep(0.01)
    return False


def test_probe_callers_share_one_listing():
    g = Gate(hang=True)
    probe = U.PresenceProbe(g)
    out = []
    threads = [threading.Thread(target=lambda: out.append(probe.get("colormunki", fresh_s=1.0, wait_s=5.0)))
               for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.2)
    assert g.calls == 1 and probe.running == "colormunki"
    g.release.set()
    for t in threads:
        t.join(3)
    assert len(out) == 8 and all(p.present for p in out) and g.calls == 1
    assert probe.get("colormunki", fresh_s=1.0).present and g.calls == 1     # fresh: reused, no listing
    assert no_worker()                                                       # the thread ends when idle


def test_probe_waits_a_bounded_time_then_falls_back():
    g = Gate()
    probe = U.PresenceProbe(g)
    assert probe.get("colormunki", wait_s=2.0).present
    g.hang = True
    t0 = time.monotonic()
    assert probe.get("colormunki", wait_s=0.2) is None                       # late, no fallback asked
    assert 0.15 < time.monotonic() - t0 < 0.6
    p = probe.get("colormunki", wait_s=0.0, stale_s=5.0)
    assert p is not None and p.present                                       # the last verdict
    assert g.calls == 2 and g.max_active == 1                                # the late listing, not another
    g.release.set()


def test_probe_since_waits_for_a_listing_begun_after_the_call():
    g = Gate("absent", "present", hang=True)
    probe = U.PresenceProbe(g)
    assert probe.get("colormunki", wait_s=0.0) is None                       # listing 1 starts, hangs
    time.sleep(0.05)
    out = []
    t = threading.Thread(target=lambda: out.append(probe.get("colormunki", since=time.monotonic(), wait_s=5.0)))
    t.start()
    time.sleep(0.1)
    g.release.set()                                                          # listing 1 answers "absent"…
    t.join(3)
    assert out[0].present and g.calls == 2 and g.max_active == 1             # …but only listing 2 counts


def test_probe_lists_one_model_at_a_time_and_a_failure_is_an_error():
    g = Gate("raise", "present", hang=True)
    probe = U.PresenceProbe(g)
    out = {}
    ts = [threading.Thread(target=lambda m=m: out.__setitem__(m, probe.get(m, wait_s=5.0)))
          for m in ("colormunki", "i1pro3plus")]
    for t in ts:
        t.start()
    time.sleep(0.1)
    g.release.set()
    for t in ts:
        t.join(3)
    assert g.max_active == 1 and sorted(g.models) == ["colormunki", "i1pro3plus"]
    assert sorted(p.status for p in out.values()) == ["error", "present"]   # the raising one: error
    err = next(p for p in out.values() if p.status == "error")
    assert "listing broke" in err.detail


def test_probe_close_releases_waiters_and_lists_no_more():
    g = Gate(hang=True)
    aborted = []
    probe = U.PresenceProbe(g, abort=lambda: aborted.append(1) or 1)
    out = []
    t = threading.Thread(target=lambda: out.append(probe.get("colormunki", wait_s=10.0)))
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    probe.close()
    t.join(2)
    assert not t.is_alive() and out == [None] and time.monotonic() - t0 < 0.5
    assert aborted == [1]                                                    # the running listing killed
    g.release.set()
    assert no_worker()
    assert probe.get("colormunki", wait_s=1.0) is None and g.calls == 1      # closed: no new listing
