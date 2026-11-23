"""Passive USB presence (lib.z9_client.chartread_fgk.usb_presence): sysfs (Linux) and
IORegistry (macOS) listings injected; nothing under /dev is ever opened; absence, other
instruments, several instruments and a failed listing are kept apart."""
import builtins
import io
import os
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
