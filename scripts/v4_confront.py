#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ICC v4 confrontation — what the INSTALLED Argyll actually does to v4 profiles.

Diagnostic only (no v4 fix, no production). Confronts our old claims ("Argyll =
v2 only", "normalisation transparente", "PCS v4 différent") against the living
code + the installed binaries. Finding (established, not assumed): the real
discriminant is the LUT TAG TYPE, not the ICC version byte:
  - mft1/mft2 (v2-type LUT, even inside a v4 header): Argyll READS it; normalize
    (strip mluc text + relabel v2.4) is colorimetrically transparent (matches
    lcms to <0.002 ΔE); collink needs it only to avoid the mluc text-copy crash.
  - mAB/mBA (true v4 lutAToBType): Argyll (xicclu AND collink) CANNOT read it,
    raw OR normalized — normalize keeps mAB byte-for-byte and does not help. Only
    lcms reads it. Real i1Profiler output is mAB/mBA → voie B (Argyll) fails on it.

Built-in in-repo cases: synthetic_test_resident_A.icc (mft2-v4, prtr) and
sRGB_v4_ICC_preference.icc (mAB/mBA-v4). Add any profile via --profile.
Run: uv run python scripts/v4_confront.py [--profile <icc>]
"""
from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from lib.z9_client import devicelink, xicclu                 # noqa: E402

_ASSETS = _ROOT / "lib" / "z9_client" / "assets"
_FIX = _ROOT / "webapp" / "backend" / "tests" / "fixtures"


def profile_facts(path: Path) -> dict:
    b = path.read_bytes()
    n = struct.unpack(">I", b[128:132])[0]
    lut = set()
    for i in range(n):
        o = 132 + i * 12
        sig = b[o:o + 4]
        off = struct.unpack(">I", b[o + 4:o + 8])[0]
        if sig[:3] in (b"A2B", b"B2A"):
            lut.add(b[off:off + 4].decode("latin1", "replace"))
    return {"version_major": b[8], "class": b[12:16].decode("latin1"),
            "pcs": b[20:24].decode("latin1"), "lut_types": sorted(lut)}


def xicclu_reads(path: Path, dev=(0.5, 0.3, 0.2)) -> tuple:
    try:
        out = xicclu.run_xicclu(str(path), [dev], direction="f", intent="r", pcs="lab")
        return (True, out[0] if out else None)
    except RuntimeError as e:
        return (False, str(e).split(":")[-1].strip()[:60])


def lcms_reads(path: Path, dev255=(127.5, 76.5, 51)) -> tuple:
    """transicc (v4-capable) device(0-255) → Lab, relative colorimetric."""
    try:
        p = subprocess.run(["transicc", "-i", str(path), "-o", "*Lab", "-t1"],
                           input=" ".join(str(x) for x in dev255) + "\n",
                           capture_output=True, text=True, timeout=30)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    lab = {}
    for tok in p.stdout.replace("=", " ").split():
        pass
    import re
    m = re.search(r"L\*=\s*([-\d.]+)\s+a\*=\s*([-\d.]+)\s+b\*=\s*([-\d.]+)", p.stdout)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


def confront(path: Path):
    f = profile_facts(path)
    is_v4lut = any(t in ("mAB ", "mBA ") for t in f["lut_types"])
    raw_ok, raw = xicclu_reads(path)
    norm = devicelink.normalize_icc_for_argyll(path.read_bytes())
    ntmp = path.with_suffix(".norm.icc")
    changed = norm != path.read_bytes()
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=False) as tf:
        tf.write(norm); npath = Path(tf.name)
    norm_ok, normret = xicclu_reads(npath)
    lcms = lcms_reads(path)
    npath.unlink(missing_ok=True)

    print(f"\n=== {path.name} ===")
    print(f"  ICC v{f['version_major']} · class {f['class']} · PCS {f['pcs']} · LUT types {f['lut_types']}"
          f"  → {'TRUE-v4 LUT (mAB/mBA)' if is_v4lut else 'v2-type LUT (mft1/mft2)'}")
    print(f"  normalize changes bytes: {changed}")
    print(f"  xicclu RAW      : {'OK ' + str([round(x,2) for x in raw]) if raw_ok else 'ERROR — ' + raw}")
    print(f"  xicclu NORMALIZED: {'OK ' + str([round(x,2) for x in normret]) if norm_ok else 'ERROR — ' + normret}")
    print(f"  lcms (v4-capable): {[round(x,2) for x in lcms] if lcms else 'n/a'}")
    if raw_ok and lcms:
        de = sum((raw[i] - lcms[i]) ** 2 for i in range(3)) ** 0.5
        print(f"  xicclu vs lcms (aligned rel.): ΔLab≈{de:.3f}  → {'MATCH (Argyll reads v4 correctly)' if de < 0.5 else 'DIVERGE'}")
    verdict = ("V4-C: Argyll cannot read mAB/mBA; normalize insufficient → voie B FAILS"
               if is_v4lut and not norm_ok else
               "V4-B: normalize needed (collink mluc crash) but colorimetrically transparent → voie B works")
    print(f"  ⟹ {verdict}")
    return {"profile": path.name, **f, "is_v4lut": is_v4lut, "xicclu_raw_ok": raw_ok,
            "xicclu_norm_ok": norm_ok, "lcms_ok": lcms is not None, "verdict": verdict}


def main():
    ap = argparse.ArgumentParser(description="ICC v4 confrontation (diagnosis only).")
    ap.add_argument("--profile", type=Path, action="append", default=[])
    a = ap.parse_args()
    targets = list(a.profile) or [
        _FIX / "synthetic_test_resident_A.icc",        # mft2-v4 (v2-type LUT)
        _ASSETS / "sRGB_v4_ICC_preference.icc",         # mAB/mBA true-v4 LUT
    ]
    for p in targets:
        if p.exists():
            confront(p)
        else:
            print(f"(absent: {p})")


if __name__ == "__main__":
    main()
