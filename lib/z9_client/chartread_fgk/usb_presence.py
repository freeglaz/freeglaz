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

The identifiers are ArgyllCMS 3.5.0's (``spectro/insttypes.c``, ``inst_usb_match``).
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

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

# freeglaz instrument models → their USB identifiers. Only what was studied:
#  - colormunki: 0971:2007, seen on the real instrument (B1). The ColorMunki i1Studio
#    (0765:6008) uses the same Argyll driver but is another product: "other", not this one;
#  - i1pro3plus: 0765:6009 is Argyll's "i1Pro 3"; whether an i1Pro 3 PLUS reports it too is
#    not established (no reading yet), so it only identifies the family (``family_only``).
MODEL_USB_IDS: dict[str, tuple[tuple[int, int], ...]] = {
    "colormunki": ((0x0971, 0x2007),),
    "i1pro3plus": ((0x0765, 0x6009),),
}
FAMILY_ONLY = frozenset({"i1pro3plus"})

PRESENT, ABSENT, OTHER, SEVERAL, ERROR = "present", "absent", "other", "several", "error"


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
    status: str                    # present | absent | other | several | error
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


def scan_ioreg(run: Optional[Callable[[], str]] = None) -> list[UsbDevice]:
    if run is None:
        def run() -> str:
            try:
                p = subprocess.run(IOREG, capture_output=True, text=True, timeout=IOREG_TIMEOUT_S)
            except (OSError, subprocess.SubprocessError) as e:
                raise UsbScanError(f"ioreg failed: {e}") from None
            if p.returncode != 0:
                raise UsbScanError(f"ioreg exited with {p.returncode}")
            return p.stdout
    text = run()
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
