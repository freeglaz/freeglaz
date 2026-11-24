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

"""Is the instrument plugged in? Answered WITHOUT opening it, nor any port.

- Linux: the USB descriptors the kernel already read at enumeration, as sysfs files
  (``/sys/bus/usb/devices/<dev>/idVendor`` and ``idProduct``). Nothing under ``/dev``.
- macOS: the IORegistry listing of the USB plane (``ioreg -p IOUSB -l``), which only reads
  the registry. No IOKit device is opened.

Why it matters:
- ``chartread -c 1`` takes the FIRST instrument ArgyllCMS recognises, so a run is only
  started when exactly one is there, of the chart's model;
- after a disconnection, ArgyllCMS on Linux does not print its USB transfer errors (macOS
  does, by the million): the device disappearing from this list is then the signal.

Three outcomes are kept apart: the instrument is there (``present``), it is not
(``absent``, ``other``, ``several``), or the list could not be read (``error``: a Flatpak
without access to sysfs, ioreg missing…). An error is never reported as an absence.

A listing can be slow (ioreg is a process, bounded by ``IOREG_TIMEOUT_S``): the backend asks
through a :class:`PresenceProbe`, which lists on its own thread, one listing at a time, and
lets each caller wait only as long as it can afford.

The identifiers are ArgyllCMS 3.5.0's (``spectro/insttypes.c``, ``inst_usb_match``).
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .. import measurement_identity as mi

SYSFS_USB = Path("/sys/bus/usb/devices")
IOREG = ("/usr/sbin/ioreg", "-p", "IOUSB", "-l", "-w", "0")
IOREG_TIMEOUT_S = 10.0

# Every USB device ArgyllCMS 3.5.0 recognises (vendor, product) → its name there.
ARGYLL_USB_IDS: dict[tuple[int, int], str] = {
    (0x04DB, 0x005B): "HCFR", (0x0670, 0x0001): "i1 Display 1",
    (0x0765, 0x5001): "Huey L", (0x0765, 0x5010): "Huey L", (0x0765, 0x5020): "i1 Display Pro",
    (0x0765, 0x6003): "ColorMunki Smile", (0x0765, 0x6008): "ColorMunki i1Studio",
    (0x0765, 0x6009): "i1Pro 3", (0x0765, 0xD020): "DTP20", (0x0765, 0xD092): "DTP92",
    (0x0765, 0xD094): "DTP94",
    (0x085C, 0x0100): "Spyder1", (0x085C, 0x0200): "Spyder2", (0x085C, 0x0300): "Spyder3",
    (0x085C, 0x0400): "Spyder4", (0x085C, 0x0500): "Spyder5", (0x085C, 0x0A00): "SpyderX",
    (0x085C, 0x0A0A): "SpyderX2", (0x085C, 0x0A0B): "Spyder2024",
    (0x0971, 0x2000): "i1Pro / i1Pro 2", (0x0971, 0x2001): "i1 Monitor",
    (0x0971, 0x2003): "i1 Display 2", (0x0971, 0x2005): "Huey", (0x0971, 0x2007): "ColorMunki",
    (0x2457, 0x4000): "EX1", (0x04D8, 0xF8DA): "ColorHug", (0x273F, 0x1001): "ColorHug",
    (0x273F, 0x1004): "ColorHug2",
}

# freeglaz instrument models → their USB identifiers, from the models themselves
# (measurement_identity.INSTRUMENT_MODELS):
#  - colormunki: 0971:2007, seen on the real instrument (B1). The ColorMunki i1Studio
#    (0765:6008) uses the same Argyll driver but is another product: "other", not this one;
#  - i1pro3plus: 0765:6009 is any i1Pro 3, Plus or not: it only identifies the family
#    (``family_only``). The model is checked when chartread opens the device (run.py).
MODEL_USB_IDS: dict[str, tuple[tuple[int, int], ...]] = {
    k: m.usb_ids for k, m in mi.INSTRUMENT_MODELS.items() if m.usb_ids}
FAMILY_ONLY = frozenset(k for k, m in mi.INSTRUMENT_MODELS.items() if m.usb_family_only)

PRESENT, ABSENT, OTHER, SEVERAL, ERROR = "present", "absent", "other", "several", "error"
CHECKING = "checking"               # the backend has no verdict yet: a listing is running


class UsbScanError(RuntimeError):
    """The device list could not be read (this is not an absence)."""


@dataclass(frozen=True)
class UsbDevice:
    vid: int
    pid: int
    location: str                  # sysfs name ("1-2.3") or IORegistry locationID
    product: Optional[str] = None

    @property
    def argyll_name(self) -> Optional[str]:
        return ARGYLL_USB_IDS.get((self.vid, self.pid))

    def to_dict(self) -> dict:
        return {"vid": f"{self.vid:04x}", "pid": f"{self.pid:04x}", "location": self.location,
                "product": self.product, "argyll_name": self.argyll_name}


@dataclass(frozen=True)
class Presence:
    status: str                    # present | absent | other | several | error (| checking)
    model: str
    instruments: tuple[UsbDevice, ...] = ()     # every Argyll instrument seen
    family_only: bool = False      # present, but the identifier names a family, not the model
    detail: str = ""

    @property
    def present(self) -> bool:
        return self.status == PRESENT

    def to_dict(self) -> dict:
        return {"status": self.status, "model": self.model, "family_only": self.family_only,
                "instruments": [d.to_dict() for d in self.instruments], "detail": self.detail}


# ─── Linux ──────────────────────────────────────────────────────────────────
def scan_sysfs(root: Path = SYSFS_USB) -> list[UsbDevice]:
    """Devices listed in sysfs. Interfaces (``1-2:1.0``) have no idVendor and are skipped."""
    try:
        entries = sorted(root.iterdir())
    except OSError as e:
        raise UsbScanError(f"cannot list {root}: {e}") from None
    out = []
    for d in entries:
        vid_f, pid_f = d / "idVendor", d / "idProduct"
        if not vid_f.is_file():
            continue
        try:
            vid = int(vid_f.read_text().strip(), 16)
            pid = int(pid_f.read_text().strip(), 16)
        except (OSError, ValueError) as e:
            raise UsbScanError(f"unreadable USB descriptor in {d}: {e}") from None
        try:
            product = (d / "product").read_text().strip() or None
        except OSError:
            product = None
        out.append(UsbDevice(vid, pid, d.name, product))
    return out


# ─── macOS ──────────────────────────────────────────────────────────────────
_IOREG_KEY = re.compile(r'"(idVendor|idProduct|locationID)"\s*=\s*(\d+)')
_IOREG_NAME = re.compile(r'"USB Product Name"\s*=\s*"([^"]*)"')


def parse_ioreg(text: str) -> list[UsbDevice]:
    """Devices of an ``ioreg -p IOUSB -l`` listing: every node with an idVendor and an
    idProduct (values are printed in decimal)."""
    out, node = [], None
    for line in text.splitlines() + ["+-o <end>"]:
        if "+-o " in line:
            if node and "idVendor" in node and "idProduct" in node:
                out.append(UsbDevice(node["idVendor"], node["idProduct"],
                                     str(node.get("locationID", "")), node.get("name")))
            node = {}
            continue
        if node is None:
            continue
        for key, val in _IOREG_KEY.findall(line):
            node.setdefault(key, int(val))
        m = _IOREG_NAME.search(line)
        if m:
            node.setdefault("name", m.group(1))
    return out


_live: set = set()                    # ioreg processes running now
_live_lock = threading.Lock()


def run_ioreg() -> str:
    """The IORegistry listing. Killed after ``IOREG_TIMEOUT_S``, or by :func:`abort_listings`."""
    try:
        p = subprocess.Popen(IOREG, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True)
    except OSError as e:
        raise UsbScanError(f"ioreg failed: {e}") from None
    with _live_lock:
        _live.add(p)
    try:
        try:
            out, _ = p.communicate(timeout=IOREG_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            raise UsbScanError(f"ioreg did not answer within {IOREG_TIMEOUT_S:.0f} s") from None
    finally:
        with _live_lock:
            _live.discard(p)
    if p.returncode != 0:
        raise UsbScanError(f"ioreg exited with {p.returncode}")
    return out


def abort_listings() -> int:
    """Kill the ioreg listings still running (backend shutdown: none is left behind). Each
    one's caller gets an error, never an absence."""
    with _live_lock:
        procs = list(_live)
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass
    return len(procs)


def scan_ioreg(run: Optional[Callable[[], str]] = None) -> list[UsbDevice]:
    text = (run or run_ioreg)()
    if "+-o " not in text:
        raise UsbScanError("ioreg printed no USB registry tree")
    return parse_ioreg(text)


# ─── verdict ────────────────────────────────────────────────────────────────
def scan_devices(platform: Optional[str] = None) -> list[UsbDevice]:
    p = platform or sys.platform
    if p.startswith("linux"):
        return scan_sysfs()
    if p == "darwin":
        return scan_ioreg()
    raise UsbScanError(f"no passive USB listing on {p}")


def check_presence(model: str, devices: Optional[list[UsbDevice]] = None, *,
                   scan: Callable[[], list[UsbDevice]] = scan_devices) -> Presence:
    """The verdict for the chart's ``model``. ``devices`` (already listed) or ``scan``
    (listing function) can be injected; a listing failure gives ``error``."""
    if model not in MODEL_USB_IDS:
        return Presence(ERROR, model, detail=f"no USB identifier known for {model!r}")
    if devices is None:
        try:
            devices = scan()
        except UsbScanError as e:
            return Presence(ERROR, model, detail=str(e))
    argyll = tuple(d for d in devices if d.argyll_name is not None)
    wanted = MODEL_USB_IDS[model]
    mine = [d for d in argyll if (d.vid, d.pid) in wanted]
    if not argyll:
        return Presence(ABSENT, model, detail="no ArgyllCMS instrument connected")
    if len(argyll) > 1:
        return Presence(SEVERAL, model, argyll,
                        detail=f"{len(argyll)} ArgyllCMS instruments connected: chartread -c 1 would "
                               "take the first one; leave only the chart's instrument")
    if not mine:
        return Presence(OTHER, model, argyll,
                        detail=f"connected: {argyll[0].argyll_name}, not the chart's instrument")
    return Presence(PRESENT, model, argyll, family_only=model in FAMILY_ONLY,
                    detail=f"{argyll[0].argyll_name} connected")


# ─── one listing at a time, never on the caller's thread ────────────────────
class PresenceProbe:
    """Listings for the backend, off the caller's thread, one at a time; each caller waits
    only as long as it can afford.

    - A worker thread lists, then ends when nothing is queued. At most one listing runs and
      at most one is queued per model: callers asking meanwhile share it. A listing that
      hangs (until ioreg's own timeout) holds this thread only, never a caller's.
    - :meth:`get` never raises: a failing provider gives ``error``, never an absence.
    - :meth:`close` (backend shutdown): no new listing, the running ioreg is killed, and
      waiting callers return at once, until :meth:`reopen` (the next start-up in the same
      process: tests, a server started again).
    """

    def __init__(self, provider: Callable[[str], Presence], *, clock: Callable[[], float] = time.monotonic,
                 abort: Callable[[], int] = abort_listings):
        self._provider, self._clock, self._abort = provider, clock, abort
        self._cond = threading.Condition()
        self._done: dict[str, tuple[float, float, Presence]] = {}     # model → (started, finished, verdict)
        self._queue: list[str] = []
        self._running: Optional[tuple[str, float]] = None                # (model, started)
        self._worker: Optional[threading.Thread] = None
        self._closed = False
        self.listings = 0                                                # finished, for tests and benches

    def get(self, model: str, *, fresh_s: float = 0.0, since: Optional[float] = None,
            wait_s: float = 0.0, stale_s: Optional[float] = None) -> Optional[Presence]:
        """The verdict of a listing finished less than ``fresh_s`` ago or, with ``since``, of
        a listing STARTED at or after ``since`` (an authoritative check). Without one, a
        listing is started (unless the running one will do) and awaited up to ``wait_s``;
        past that, the newest verdict finished less than ``stale_s`` ago, or None."""
        t_call = self._clock()
        deadline = t_call + wait_s

        def good(hit) -> bool:
            if hit is None:
                return False
            return hit[0] >= since if since is not None else hit[1] >= t_call - fresh_s

        with self._cond:
            while True:
                hit = self._done.get(model)
                if good(hit):
                    return hit[2]
                if self._closed:
                    break
                self._want(model, since)
                left = deadline - self._clock()
                if left <= 0:
                    break
                self._cond.wait(left)
            if stale_s is not None and hit is not None and self._clock() - hit[1] <= stale_s:
                return hit[2]
            return None

    @property
    def running(self) -> Optional[str]:
        with self._cond:
            return self._running[0] if self._running else None

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._queue.clear()
            running = self._running is not None
            self._cond.notify_all()
        if running:
            self._abort()

    def reopen(self) -> None:
        with self._cond:
            self._closed = False

    def _want(self, model: str, since: Optional[float]) -> None:
        r = self._running
        if r is not None and r[0] == model and (since is None or r[1] >= since):
            return                                       # the listing running now will do
        if model in self._queue:
            return
        self._queue.append(model)
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, name="usb-presence", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            with self._cond:
                if self._closed or not self._queue:
                    self._worker = None
                    return
                model = self._queue.pop(0)
                started = self._clock()
                self._running = (model, started)
            try:
                verdict = self._provider(model)
            except Exception as e:                       # a listing problem is never an absence
                verdict = Presence(ERROR, model, detail=f"USB listing failed: {e}")
            with self._cond:
                self._running = None
                self._done[model] = (started, self._clock(), verdict)
                self.listings += 1
                self._cond.notify_all()
