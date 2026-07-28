#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""CI portability smoke test — bundled pyvips + large-TIFF preview + ICC.

Run on a CLEAN runner (no system libvips installed) via the documented install
(`uv sync`, which pulls `pyvips[binary]`). It proves that a user following the
install guides gets a working preview pipeline WITHOUT any system libvips:

  1. pyvips imports and loads the *bundled* libvips (not a system one);
  2. ``_render_tiff_preview`` thumbnails a >179 Mpx TIFF — the size where Pillow
     raised ``DecompressionBombError`` (bug 3) — into a valid PNG;
  3. that PNG keeps the source ICC (``iCCP`` chunk, color-managed fix #3) and
     drops the XMP/Photoshop bloat.

Exits non-zero on any failure → a red CI signal before publishing. The heavy
TIFF is generated on the fly (nothing versioned) and deleted.

Run: ``uv run python scripts/ci_preview_smoke.py``
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # so `webapp.backend...` imports work under CI

_FAILURES: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        _FAILURES.append(msg)


def _loaded_libvips_path() -> str | None:
    """Best-effort path of the libvips actually loaded by this process.

    macOS: ``vmmap``; Linux: ``/proc/self/maps``. Returns None if undeterminable
    (e.g. Windows) — the functional test on a clean runner is authoritative."""
    try:
        pid = os.getpid()
        if sys.platform == "darwin":
            out = subprocess.run(
                ["vmmap", str(pid)], capture_output=True, text=True, timeout=30,
            ).stdout
        elif sys.platform.startswith("linux"):
            out = Path(f"/proc/{pid}/maps").read_text()
        else:
            return None
    except Exception:  # noqa: BLE001 — diagnostic only
        return None
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        p = parts[-1]
        base = p.rsplit("/", 1)[-1]
        # the CORE libvips shared object (libvips.42.dylib / libvips.so.42 /
        # libvips-cpp.so.42), NOT the pyvips API wrapper `_libvips.abi3.so`
        # (which always lives in site-packages and would mask a system libvips).
        if base.startswith("libvips") and (base.endswith(".dylib") or ".so" in base):
            return p
    return None


def main() -> int:
    print("== freeglaz preview portability smoke test ==")

    # 1. pyvips imports + which libvips is loaded ---------------------------
    import pyvips

    libvips_ver = ".".join(str(pyvips.version(i)) for i in range(3))
    print(f"pyvips {pyvips.__version__} | libvips {libvips_ver} | {sys.platform}")
    _ = pyvips.Image.black(1, 1).avg()  # force the lib to load

    path = _loaded_libvips_path()
    print(f"loaded libvips: {path or '(undeterminable)'}")
    if path is not None:
        SYSTEM_PREFIXES = ("/usr/", "/lib/", "/opt/homebrew/", "/usr/local/")
        bundled = ("pyvips_binary" in path) or ("site-packages" in path)
        system = path.startswith(SYSTEM_PREFIXES)
        check(bundled and not system,
              "libvips comes from the bundled wheel, not a system lib")
    else:
        # Undeterminable path (Windows): on a clean runner with no system
        # libvips, a WORKING pyvips can only be the bundled one → the functional
        # test below is the proof. We just log that the path check was skipped.
        print("  SKIP  bundled-lib path check (not resolvable on this OS)")

    # 2 + 3. large TIFF (>179 Mpx) + ICC through _render_tiff_preview -------
    # 14000×14000 = 196 Mpx: comfortably past Pillow's ~179 Mpx bomb threshold
    # (the point of bug 3) without the full 291 Mpx of source600 — a gradient
    # deflates to a few MB, so generation stays fast/light on CI runners.
    from webapp.backend.routes.files import _render_tiff_preview

    icc = (REPO_ROOT / "lib/z9_client/assets/sRGB2014.icc").read_bytes()
    with tempfile.TemporaryDirectory() as d:
        big = Path(d) / "big.tif"
        w = h = 14000
        grad = (pyvips.Image.xyz(w, h)[0] * 255 // w)
        img = grad.bandjoin([grad, grad]).cast("uchar").copy()
        img.set_type(pyvips.GValue.blob_type, "icc-profile-data", icc)
        # fake XMP bloat, to prove keep='icc' drops it (bug 3 regression guard)
        img.set_type(pyvips.GValue.blob_type, "xmp-data",
                     b"<x:xmpmeta>" + b"Z" * 200_000 + b"</x:xmpmeta>")
        img.tiffsave(str(big), compression="deflate")
        print(f"generated {w*h/1e6:.0f} Mpx TIFF ({big.stat().st_size // 1024} KB)")

        png = _render_tiff_preview(big)

    check(png[:8] == b"\x89PNG\r\n\x1a\n", "output is a valid PNG")
    check(b"iCCP" in png, "output PNG keeps the source ICC (iCCP chunk)")
    check(not any(c in png for c in (b"iTXt", b"tEXt", b"zTXt")),
          "XMP/Photoshop text bloat is stripped (no text chunk)")
    # a thumbnail, not the full image
    import struct
    ihdr_w, ihdr_h = struct.unpack(">II", png[16:24])
    check(max(ihdr_w, ihdr_h) <= 1024, f"thumbnailed to <=1024 px ({ihdr_w}x{ihdr_h})")

    print()
    if _FAILURES:
        print(f"SMOKE TEST FAILED ({len(_FAILURES)} check(s)):")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
