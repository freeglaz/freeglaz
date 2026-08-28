# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Luminance-priority conversion strategy — production abstract generation.

Ported (not rewritten) from the validated research bench:
  - abstract authoring + Cdriver + policy  ← scripts/abstract_poc.py
  - Cmeasured boundary (bracketing+bisection, τ-parameterised) ← scripts/rebaseline_tau1.py
The algorithm was characterised there (POC, rebaseline, real-image, ΔL audits).
This module isolates the three steps cleanly for Convert: measure the destination
Cmeasured boundary at τ · build the Lab→Lab abstract · write/link it via collink.

The abstract is a hand-authored lut16 (mft2) Lab→Lab profile, **radial at constant
L**: policy (L, a, b) → (L, a·s, b·s). It preserves luminance and hue by
construction and compresses chroma toward the measured boundary Cdriver(L,h),
scaled by τ. τ = tolerance to luminance recoupling at the boundary: LOW τ →
luminance maximally protected, chroma more sacrificed; HIGH τ → more chroma kept.
Exposed range 0.5–2.0 (characterised); τref=1 is a reference, not an optimum.

NOT part of lib/z9_client (webapp-only feature). Reuses lib primitives (xicclu,
resolve_argyll_binary) by import only.
"""
from __future__ import annotations

import math
import struct
import subprocess
import tempfile
from pathlib import Path

from lib.z9_client import xicclu
from lib.z9_client.argyll import resolve_argyll_binary

# Boundary grid (same as the consigned BASELINE_TAU1 reference condition).
_Ls = [5, 10, 15, 20, 30, 50]
_hs = list(range(0, 360, 30))
TAU_MIN, TAU_MAX = 0.5, 2.0                # characterised domain — do NOT widen without a dedicated bench
_ABSTRACT_GRID = 33                        # CLUT resolution of the abstract (bench-validated)


# ── Cmeasured boundary (ported from rebaseline_tau1.refine_boundary) ──────────
def _residuals(dest: str, triples):
    labs = [(L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))) for L, C, h in triples]
    dev = xicclu.run_xicclu(dest, labs, direction="b", intent="r", pcs="lab")
    ret = xicclu.run_xicclu(dest, dev, direction="f", intent="r", pcs="lab")
    return [triples[i][1] - math.hypot(ret[i][1], ret[i][2]) for i in range(len(triples))]


def refine_boundary(dest: str, tau: float, prec: float = 1.0) -> dict:
    """Cmeasured(L,h) via bracketing+bisection (residual C-C_ret crosses τ).
    prec ≈ 1.0 = the useful production precision (finer only marginally shifts it)."""
    scan = [0.5, 1, 2, 4, 8, 16, 32, 64, 100]
    triples, key = [], []
    for L in _Ls:
        for h in _hs:
            for C in scan:
                triples.append((L, C, h)); key.append((L, h))
    res = _residuals(dest, triples)
    br = {}
    for i, k in enumerate(key):
        br.setdefault(k, []).append((triples[i][1], res[i]))
    cells = {}
    for k, rs in br.items():
        rs.sort(); lo, hi = 0.0, None
        for C, r in rs:
            if r >= tau:
                hi = C; break
            lo = C
        cells[k] = {"lo": lo, "hi": hi if hi is not None else rs[-1][0]}
    for _ in range(40):
        mids, mk = [], []
        for k, c in cells.items():
            if c["hi"] - c["lo"] > prec:
                mids.append((k[0], (c["lo"] + c["hi"]) / 2, k[1])); mk.append(k)
        if not mids:
            break
        r = _residuals(dest, mids)
        for i, k in enumerate(mk):
            (cells[k].__setitem__("hi", mids[i][1]) if r[i] >= tau
             else cells[k].__setitem__("lo", mids[i][1]))
    return {k: round((c["lo"] + c["hi"]) / 2, 2) for k, c in cells.items()}


# ── Cdriver ≈ Cmeasured, interpolable (ported from abstract_poc.Cdriver) ───────
class Cdriver:
    """Interpolable Cmeasured(L,h): bilinear in L, circular-linear in h. margin =
    tiny explicit numerical guard so we don't sit exactly on the B2A break."""

    def __init__(self, grid: dict, margin: float = 0.03):
        self.margin = margin
        self.grid = dict(grid)                       # (L,h) -> Cmeasured
        self.Ls = sorted({k[0] for k in grid})
        self.hs = sorted({k[1] for k in grid})

    def _at_grid(self, L, h):
        Ls = self.Ls
        L = max(Ls[0], min(Ls[-1], L))
        for i in range(1, len(Ls)):
            if Ls[i] >= L:
                L0, L1 = Ls[i - 1], Ls[i]; break
        fL = (L - L0) / (L1 - L0) if L1 != L0 else 0.0
        hs = self.hs; h = h % 360.0
        hh = hs + [hs[0] + 360.0]
        for j in range(1, len(hh)):
            if hh[j] >= h:
                h0, h1 = hh[j - 1], hh[j]; j0, j1 = j - 1, j % len(hs); break
        else:
            h0, h1, j0, j1 = hh[-2], hh[-1], len(hs) - 1, 0
        fh = (h - h0) / (h1 - h0) if h1 != h0 else 0.0
        def g(Lx, hj): return self.grid.get((Lx, self.hs[hj]), 0.0)
        c00, c01 = g(L0, j0), g(L0, j1)
        c10, c11 = g(L1, j0), g(L1, j1)
        return ((c00 * (1 - fh) + c01 * fh) * (1 - fL) + (c10 * (1 - fh) + c11 * fh) * fL)

    def __call__(self, L, h):
        c = self._at_grid(L, h)
        return c * (1 - self.margin) if c > 0 else 0.0


# ── policy: keep L,h ; soft-knee compress C toward Cdriver (ported) ───────────
def make_policy(cdriver, knee=0.7):
    """C' = C below knee·Cd ; smooth tanh knee to asymptote Cd above. L,h unchanged
    → the abstract is radial at constant L (preserves luminance and hue)."""
    def policy(L, a, b):
        C = math.hypot(a, b)
        if C < 1e-6:
            return (L, a, b)
        h = math.degrees(math.atan2(b, a)) % 360.0
        Cd = cdriver(L, h)
        if Cd <= 0:
            return (L, a, b)
        k = knee * Cd
        if C <= k:
            Cp = C
        else:
            Cp = k + (Cd - k) * math.tanh((C - k) / (Cd - k))
        s = Cp / C
        return (L, a * s, b * s)
    return policy


# ── lut16 Lab→Lab abstract (ported from abstract_poc.build_abstract) ──────────
def _u16(x):
    return struct.pack(">H", max(0, min(65535, round(x))))


def _s15(x):
    return struct.pack(">i", round(x * 65536))


def build_abstract(policy, g: int = _ABSTRACT_GRID) -> bytes:
    """Author an ICC 'abst' Lab profile with an mft2 A2B0 CLUT of grid g."""
    inp = b"".join(_u16(0) + _u16(65535) for _ in range(3))
    clut = bytearray()
    for i in range(g):
        L = i / (g - 1) * 100.0
        for j in range(g):
            a = j / (g - 1) * 255.0 - 128.0
            for k in range(g):
                b = k / (g - 1) * 255.0 - 128.0
                Lp, ap, bp = policy(L, a, b)
                clut += _u16(Lp / 100.0 * 65535) + _u16((ap + 128) / 255 * 65535) + _u16((bp + 128) / 255 * 65535)
    out = b"".join(_u16(0) + _u16(65535) for _ in range(3))
    matrix = b"".join(_s15(v) for v in (1, 0, 0, 0, 1, 0, 0, 0, 1))
    mft2 = (b"mft2" + b"\x00" * 4 + bytes([3, 3, g, 0]) + matrix
            + struct.pack(">H", 2) + struct.pack(">H", 2) + inp + bytes(clut) + out)

    def desc(s):
        bb = s.encode("ascii") + b"\x00"
        return b"desc" + b"\x00" * 4 + struct.pack(">I", len(bb)) + bb + struct.pack(">I", 0) + struct.pack(">HB", 0, 0) + b"\x00" * 67

    def xyz(X, Y, Z):
        return b"XYZ " + b"\x00" * 4 + _s15(X) + _s15(Y) + _s15(Z)

    tags = {"A2B0": mft2, "desc": desc("freeglaz luminance-priority"),
            "wtpt": xyz(0.9642, 1.0, 0.8249), "cprt": desc("GPL-3.0-or-later")}
    header = bytearray(128)
    header[8:12] = bytes([0x02, 0x40, 0, 0]); header[12:16] = b"abst"
    header[16:20] = b"Lab "; header[20:24] = b"Lab "; header[36:40] = b"acsp"
    header[68:80] = _s15(0.9642) + _s15(1.0) + _s15(0.8249)
    order = list(tags)
    tbl = struct.pack(">I", len(order)); off = 128 + 4 + len(order) * 12; data = bytearray()
    for sig in order:
        p = tags[sig]
        tbl += sig.encode("ascii") + struct.pack(">I", off + len(data)) + struct.pack(">I", len(p))
        data += p + b"\x00" * ((4 - len(p) % 4) % 4)
    prof = bytearray(header) + tbl + data
    struct.pack_into(">I", prof, 0, len(prof))
    return bytes(prof)


# ── public API: the three isolated production steps ───────────────────────────
def validate_tau(tau: float) -> float:
    """Clamp/reject τ to the characterised domain [0.5, 2.0]."""
    if not (TAU_MIN <= tau <= TAU_MAX):
        raise ValueError(f"tau {tau} out of characterised range [{TAU_MIN}, {TAU_MAX}]")
    return float(tau)


def build_luminance_priority_abstract(dest_argyll_icc: Path, tau: float) -> bytes:
    """Step 1+2: measure the destination Cmeasured boundary at τ and author the
    radial abstract. `dest_argyll_icc` = the Argyll-readable (normalized) dest."""
    validate_tau(tau)
    boundary = refine_boundary(str(dest_argyll_icc), tau)
    return build_abstract(make_policy(Cdriver(boundary)))


def assert_neutral_chroma_clean(abstract_icc: bytes, tol: float = 0.5) -> dict:
    """Intra-profile neutral guard for an L-scaling abstract (BPC): on the neutral
    axis, a,b must stay ≈0 (no chroma introduced) — but L MAY change by design (the
    BPC lift). This is the a,b-only part of the neutral guard; NOT an L check (that
    would reject the intended lift) and NOT an absolute threshold."""
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=True) as f:
        f.write(abstract_icc); f.flush()
        out = xicclu.run_xicclu(f.name, [(L, 0.0, 0.0) for L in range(2, 99, 8)],
                                direction="f", pcs="lab")
    dmax_ab = max(max(abs(out[i][1]), abs(out[i][2])) for i in range(len(out)))
    if dmax_ab > tol:
        raise RuntimeError(
            f"BPC abstract introduces chroma on the neutral axis (max |a,b|={dmax_ab:.3f} "
            f"> {tol}) — refusing")
    return {"neutral_ab_drift_max": round(dmax_ab, 3)}


# ── BPC (black point compensation) abstract — Phase 2 (NOT wired to convert) ──
# Established (BPC Phase 1, 124ab79): Argyll has no native BPC (-b is a pure
# endpoint pin, no scaling); the lcms oracle BPC = a GLOBAL linear scaling that
# maps source black → dest black across the whole tonal range (lift decreasing
# with L, tapering to 0 at white). We reproduce that FORM with a per-(source,dest)
# L-scaling abstract, anchored at the PROFILE-DERIVED dest black L (Lmin) — the
# real paper Dmax (deeper than lcms's own black-point estimate; the anchor
# deviation from the oracle is documented, not hidden). Chroma is a MEASURED cost
# (the guard bench characterises it), never assumed neutral.
def measure_black_L(argyll_icc: Path, *, source: bool) -> float:
    """Darkest reproducible neutral L of a profile. source=True: forward A2B of
    device (0,0,0) (the source's black). source=False: B2A(0,0,0)→A2B roundtrip
    (the dest's reproducible floor, Lmin)."""
    if source:
        lab = xicclu.run_xicclu(str(argyll_icc), [(0.0, 0.0, 0.0)],
                                direction="f", intent="r", pcs="lab")
        return round(lab[0][0], 3)
    dev = xicclu.run_xicclu(str(argyll_icc), [(0.0, 0.0, 0.0)], direction="b", intent="r", pcs="lab")
    lab = xicclu.run_xicclu(str(argyll_icc), dev, direction="f", intent="r", pcs="lab")
    return round(lab[0][0], 3)


def _bpc_policy(l_src: float, l_dst: float):
    """Linear L scaling [l_src, 100] → [l_dst, 100] (source black → dest black),
    a,b unchanged. Content below l_src (out of the source neutral gamut) clamps to
    l_dst. Monotone (slope (100-l_dst)/(100-l_src) > 0)."""
    span_in = (100.0 - l_src) if (100.0 - l_src) > 1e-6 else 1.0
    span_out = 100.0 - l_dst
    def policy(L, a, b):
        if L <= l_src:
            return (l_dst, a, b)
        return (l_dst + (L - l_src) / span_in * span_out, a, b)
    return policy


def build_bpc_abstract(source_argyll_icc: Path, dest_argyll_icc: Path) -> dict:
    """Author the BPC abstract for (source → dest). Returns {abstract, l_src,
    l_dst} — l_src/l_dst are the measured black anchors (profile-derived Lmin for
    the dest). Reuses the production abstract engine (build_abstract), no dup."""
    l_src = measure_black_L(source_argyll_icc, source=True)
    l_dst = measure_black_L(dest_argyll_icc, source=False)          # Lmin, profile-derived
    abstract = build_abstract(_bpc_policy(l_src, l_dst))
    return {"abstract": abstract, "l_src": l_src, "l_dst": l_dst}


def assert_neutral_abstract(abstract_icc: bytes, tol: float = 0.5) -> dict:
    """Intra-profile neutral guard: the abstract must not drift the neutral axis
    (a,b ≈ 0 stay ≈ 0, whatever L). This is the correct intra-profile check —
    NOT an absolute ΔL threshold (0.348 was témoin-specific; real profiles run
    1.2–1.84 normally). The abstract is radial at constant L so a,b→a,b at C=0 by
    construction; this guards against a malformed/regressed abstract at runtime."""
    with tempfile.NamedTemporaryFile(suffix=".icc", delete=True) as f:
        f.write(abstract_icc); f.flush()
        pts = [(L, 0.0, 0.0) for L in range(2, 99, 8)]
        out = xicclu.run_xicclu(f.name, pts, direction="f", pcs="lab")
    dmax_ab = max(max(abs(out[i][1]), abs(out[i][2])) for i in range(len(out)))
    dmax_L = max(abs(out[i][0] - pts[i][0]) for i in range(len(out)))
    if dmax_ab > tol or dmax_L > tol:
        raise RuntimeError(
            f"luminance-priority abstract drifts the neutral axis "
            f"(max |a,b|={dmax_ab:.3f}, max ΔL={dmax_L:.3f} > {tol}) — refusing")
    return {"neutral_ab_drift_max": round(dmax_ab, 3), "neutral_L_drift_max": round(dmax_L, 3)}


def build_link(source_icc: Path, dest_icc: Path, out_link: Path,
               abstract_icc: bytes, timeout: int = 600) -> Path:
    """Step 3: collink -s -ir -p <abstract> source dest → DeviceLink. The abstract
    (radial chroma compression) is inserted in PCS between source and dest."""
    collink = resolve_argyll_binary("collink")
    with tempfile.TemporaryDirectory(prefix="freeglaz_lumprio_") as td:
        ab = Path(td) / "abstract.icc"; ab.write_bytes(abstract_icc)
        argv = [collink, "-v", "-qh", "-s", "-ir", "-p", str(ab),
                str(source_icc), str(dest_icc), str(out_link)]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not out_link.exists():
        raise RuntimeError(
            f"collink (luminance-priority) failed (code {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}")
    return out_link
