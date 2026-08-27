#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Luminance-priority ABSTRACT POC (voie B) — research bench, measure only.

Falsifiable proof/refutation that the -ir/-ilp trade-off can be broken with a
PCS→PCS abstract inserted via ``collink -s -p``, WITHOUT any freeglaz pixel
engine. NOT wired to convert.py/print. Profile-driven, geometry centred on (0,0)
(established: the roundtrip pipeline neutral ≈ (0,0)).

Abstract construction (§2.1, established): no Argyll/lcms CLI builds an abstract
from a Lab→Lab grid — we hand-author a lut16 (mft2) Lab abstract; identity is
verified against xicclu (§2.2 identity insertion test passes: -s -p identity ≡
-s, device Δ<1e-4).

Vocabulary kept strict: Cgeom (mesh) / Cmeasured (behavioural B2A edge, the
driver here) / Csafe (future, NOT this POC).

Two DISTINCT measures:
  A  abstract alone (PCS→abstract→PCS): L'≈L, h'≈h, C'=compression; probed
     BETWEEN CLUT nodes and at two resolutions (is the CLUT a wall?).
  B  full chain (PCS→abstract→B2A1→device→A2B1→PCS): δLc, dLout/dCin (shadows),
     global neutral ΔL, vs -ir and -ilp.

Run: uv run python scripts/abstract_poc.py --dest <canson.icc> --out-dir <dir>
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib.z9_client import devicelink, xicclu                      # noqa: E402
from lib.z9_client.argyll import resolve_argyll_binary            # noqa: E402

_SRC = Path(__file__).resolve().parents[1] / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc"

# ── OFFICIAL non-regression baseline (witness: Canson Photolustre RC GE-ON) ──
# Reference experimental condition: refined Cmeasured (bracketing+bisection) +
# τref=1, policy unchanged. The heterogeneous-generalisation jalon compares each
# profile's guards against this. τref=1 is a REFERENCE condition, NOT an optimum
# nor a universal value — the chroma↔luminance trade depends on the image; τ will
# become a UI cursor (future). Values measured (rebaseline_tau1.py), not assumed.
BASELINE_TAU1 = {
    "witness": "Canson Photolustre RC GE-ON",
    "condition": "refined Cmeasured (bracketing+bisection) · τref=1 · policy unchanged",
    "shadow_dLoutdCin_mean": 0.0235,      # ombres — la propriété centrale
    "neutral_dLout_mean": 0.348,          # neutre ΔL
    "hue_dh_P95_Ct20": 0.66, "hue_dh_P99_Ct20": 0.81,   # teinte, couleurs chromatiques (clean)
    "hue_dh_P95_Ct10": 1.77,              # Ct>10 : instabilité near-neutral (hue Verdict A), pas un défaut
    "hue_dE00_P95_Ct10": 0.73,
    "L5C60_hotspot_dE00": 2.13,           # résolu (artefact de résolution ≈21 fermé par le raffinement)
}


# ─── lut16 Lab→Lab abstract (hand-authored; identity verified via xicclu) ─────
def _u16(x):
    return struct.pack(">H", max(0, min(65535, round(x))))


def _s15(x):
    return struct.pack(">i", round(x * 65536))


def build_abstract(policy, g: int) -> bytes:
    """Author an ICC 'abst' Lab profile with an mft2 A2B0 CLUT of grid g.

    policy(L,a,b)->(L',a',b'). CLUT node (i,j,k) decodes to Lab
    L=i/(g-1)*100, a=j/(g-1)*255-128, b=k/(g-1)*255-128 (identity-consistent with
    xicclu, proven by the identity round-trip)."""
    inp = b"".join(_u16(0) + _u16(65535) for _ in range(3))       # identity input curves (2 entries)
    clut = bytearray()
    for i in range(g):
        L = i / (g - 1) * 100.0
        for j in range(g):
            a = j / (g - 1) * 255.0 - 128.0
            for k in range(g):
                b = k / (g - 1) * 255.0 - 128.0
                Lp, ap, bp = policy(L, a, b)
                clut += _u16(Lp / 100.0 * 65535) + _u16((ap + 128) / 255 * 65535) + _u16((bp + 128) / 255 * 65535)
    out = b"".join(_u16(0) + _u16(65535) for _ in range(3))       # identity output curves
    matrix = b"".join(_s15(v) for v in (1, 0, 0, 0, 1, 0, 0, 0, 1))
    mft2 = (b"mft2" + b"\x00" * 4 + bytes([3, 3, g, 0]) + matrix
            + struct.pack(">H", 2) + struct.pack(">H", 2) + inp + bytes(clut) + out)

    def desc(s):
        bb = s.encode("ascii") + b"\x00"
        return b"desc" + b"\x00" * 4 + struct.pack(">I", len(bb)) + bb + struct.pack(">I", 0) + struct.pack(">HB", 0, 0) + b"\x00" * 67

    def xyz(X, Y, Z):
        return b"XYZ " + b"\x00" * 4 + _s15(X) + _s15(Y) + _s15(Z)

    tags = {"A2B0": mft2, "desc": desc("freeglaz luminance-priority POC"),
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


# ─── Cdriver ≈ Cmeasured (behavioural boundary), interpolable ─────────────────
class Cdriver:
    """Interpolable Cmeasured(L,h): bilinear in L, circular-linear in h.
    margin = tiny EXPLICIT numerical guard so we don't sit exactly on the B2A
    break — this is NOT Csafe (no policy margin decided here)."""

    def __init__(self, boundary_csv: Path, margin: float = 0.03):
        self.margin = margin
        self.grid = {}                       # (L,h) -> Cmeasured
        self.Ls, self.hs = set(), set()
        with open(boundary_csv) as f:
            for r in csv.DictReader(f):
                L, h = float(r["L"]), float(r["h_N"])
                cm = float(r["Cmeasured"]) if r["Cmeasured"] not in ("", "0", "0.0") else 0.0
                self.grid[(L, h)] = cm
                self.Ls.add(L); self.hs.add(h)
        self.Ls, self.hs = sorted(self.Ls), sorted(self.hs)

    def _at_grid(self, L, h):
        # nearest L bracket
        Ls = self.Ls
        L = max(Ls[0], min(Ls[-1], L))
        for i in range(1, len(Ls)):
            if Ls[i] >= L:
                L0, L1 = Ls[i - 1], Ls[i]; break
        fL = (L - L0) / (L1 - L0) if L1 != L0 else 0.0
        # circular h bracket
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


# ─── policy: keep L,h ; soft-knee compress C toward Cdriver ───────────────────
def make_policy(cdriver, knee=0.7):
    """C' = C below knee·Cd ; smooth tanh knee to asymptote Cd above. Continuous
    value and slope at the knee (tanh'(0)=1). L,h unchanged."""
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


# ─── link building ───────────────────────────────────────────────────────────
def collink(args, out, timeout=900):
    cl = resolve_argyll_binary("collink")
    p = subprocess.run([cl] + args + [str(out)], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0 or not Path(out).exists():
        raise RuntimeError(f"collink {' '.join(args)} failed: {(p.stderr or p.stdout)[:300]}")


# ─── chain measurement (same as bench: Lab→src_dev→link→dst_dev→Lab) ──────────
def measure_chain(link, src_icc, dst_icc, grid):
    labs = [g["lab"] for g in grid]
    srgb = xicclu.run_xicclu(src_icc, labs, direction="b", intent="r", pcs="lab")
    drgb = xicclu.run_xicclu(link, srgb, direction="f")
    out = xicclu.run_xicclu(dst_icc, drgb, direction="f", intent="r", pcs="lab")
    return srgb, out


def metrics(grid, out):
    """δLc (Lref per L), dLout/dCin shadows, neutral ΔL, per condition."""
    Lref = {}
    for i, g in enumerate(grid):
        if g["C"] == 0:
            Lref[g["L"]] = out[i][0]
    rows = []
    for i, g in enumerate(grid):
        Lo = out[i][0]
        rows.append({"L": g["L"], "C": g["C"], "h": g["h"],
                     "Lout": Lo, "dLc": Lo - Lref[g["L"]], "Lref": Lref[g["L"]]})
    # dLout/dCin (shadows L<=20): mean slope of Lout vs C along (L,h) ramps
    ramps = {}
    for r in rows:
        ramps.setdefault((r["L"], r["h"]), []).append(r)
    slopes = []
    for (L, h), pts in ramps.items():
        if L > 20:
            continue
        pts = sorted(pts, key=lambda p: p["C"])
        for i in range(1, len(pts)):
            dC = pts[i]["C"] - pts[i - 1]["C"]
            if dC:
                slopes.append((pts[i]["Lout"] - pts[i - 1]["Lout"]) / dC)
    neutral = [r["dLc"] for r in rows if r["C"] == 0]  # =0 by def; use Lout-Lin
    neutral_dL = [r["Lout"] - r["L"] for r in rows if r["C"] == 0]
    return {
        "shadow_dLoutdCin_mean": round(sum(slopes) / len(slopes), 4) if slopes else None,
        "shadow_dLoutdCin_max": round(max(slopes), 4) if slopes else None,
        "neutral_dLout_mean": round(sum(neutral_dL) / len(neutral_dL), 3) if neutral_dL else None,
    }, rows


def run(dest: Path, out_dir: Path, boundary_csv: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="abstract_poc_") as tmp:
        tmp = Path(tmp)
        src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(_SRC.read_bytes()))
        dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest.read_bytes()))

        cdriver = Cdriver(boundary_csv)
        policy = make_policy(cdriver)

        # ── Measure A: abstract alone, two resolutions, nodes + between-nodes ──
        print("[poc] Measure A — abstract alone (CLUT resolution) …")
        measA = {}
        for g in (17, 33):
            ab = tmp / f"abstract_g{g}.icc"; ab.write_bytes(build_abstract(policy, g))
            # between-node test points (offset from grid) across L,C,h
            pts, exp = [], []
            for L in (8, 12, 18, 23, 37, 52):
                for h in (15, 75, 135, 255, 285, 315):
                    for C in (7, 13, 22, 33, 47):
                        a, b = C * math.cos(math.radians(h)), C * math.sin(math.radians(h))
                        pts.append((L, a, b))
                        Cd = cdriver(L, h); k = 0.7 * Cd
                        Cp = C if (Cd <= 0 or C <= k) else k + (Cd - k) * math.tanh((C - k) / (Cd - k))
                        exp.append((L, h, C, Cp))
            got = xicclu.run_xicclu(ab, pts, direction="f", pcs="lab")
            dL = dh = dC = 0.0
            for i, (L, h, C, Cp) in enumerate(exp):
                Lo, ao, bo = got[i]
                Co = math.hypot(ao, bo); ho = math.degrees(math.atan2(bo, ao)) % 360
                dL = max(dL, abs(Lo - L)); dh = max(dh, abs((ho - h + 180) % 360 - 180))
                dC = max(dC, abs(Co - Cp))
            measA[g] = {"max_dL": round(dL, 3), "max_dh": round(dh, 3), "max_dC_vs_expected": round(dC, 3)}
            print(f"[poc]   g={g}: between-node max |ΔL|={dL:.3f} |Δh|={dh:.3f} |ΔC vs policy|={dC:.3f}")

        # ── build links: POC (-s -p), reference -ir and -ilp (-G) ──
        print("[poc] building links (POC -s -p, -ir, -ilp) …")
        ab = tmp / "abstract_g33.icc"; ab.write_bytes(build_abstract(policy, 33))
        collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], tmp / "poc.icc")
        collink(["-v", "-qh", "-G", "-ir", str(src_n), str(dst_n)], tmp / "ir.icc")
        collink(["-v", "-qh", "-G", "-ilp", str(src_n), str(dst_n)], tmp / "ilp.icc")

        # ── Measure B: full chain metrics vs -ir / -ilp ──
        Ls = [5, 10, 15, 20, 30, 50]; hs = list(range(0, 360, 30)); Cs = list(range(0, 61, 5))
        grid = []
        for L in Ls:
            for h in hs:
                for C in Cs:
                    if C == 0 and h != hs[0]:
                        continue
                    grid.append({"L": L, "C": C, "h": h,
                                 "lab": (L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))})
        print("[poc] Measure B — full chain (POC vs -ir vs -ilp) …")
        measB = {}
        allrows = []
        for tag, link in (("POC", tmp / "poc.icc"), ("ir", tmp / "ir.icc"), ("ilp", tmp / "ilp.icc")):
            _, out = measure_chain(link, src_n, dst_n, grid)
            m, rows = metrics(grid, out)
            measB[tag] = m
            for r in rows:
                r["cond"] = tag; allrows.append(r)
            print(f"[poc]   {tag}: shadow dLout/dCin mean={m['shadow_dLoutdCin_mean']} "
                  f"| neutral ΔL={m['neutral_dLout_mean']}")

        # ── focus: blue/magenta shadow floor (recall B2A floor ~1.9) ──
        bm = {}
        for tag, link in (("POC", tmp / "poc.icc"),):
            _, out = measure_chain(link, src_n, dst_n, grid)
            _, rows = metrics(grid, out)
            vals = [r["dLc"] for r in rows if r["L"] <= 15 and 240 <= r["h"] <= 300 and r["C"] <= 20]
            bm["POC_bluemagenta_shadow_dLc_max"] = round(max(vals), 3) if vals else None

        summary = {"dest": str(dest), "measureA_between_nodes": measA,
                   "measureB": measB, "bluemagenta": bm,
                   "targets": {"shadow_like_ilp": 0.054, "neutral_like_ir": 1.44,
                               "b2a_floor_bluemagenta": 1.9}}
        _csv(out_dir / "poc_points.csv", allrows)
        (out_dir / "poc_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        _reading(out_dir / "poc_reading.txt", summary)
        print(f"[poc] wrote poc_points.csv, poc_summary.json, poc_reading.txt")
        return summary


def _csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)


def _reading(path, s):
    b = s["measureB"]
    L = ["# Luminance-priority abstract POC — reading", "",
         f"dest: {s['dest']}", "",
         "Q: can an abstract (collink -s -p) break the -ir/-ilp trade-off, no pixel engine?",
         "Target: shadow dLout/dCin → like -ilp (~0.054) ; neutral ΔL → like -ir (~1.44).",
         "In blue/magenta shadows, success = the B2A floor (~1.9 δLc), NOT 0.", "",
         "── Measure B (full chain) ──",
         f"{'cond':<6} {'shadow dLout/dCin':>18} {'neutral ΔL':>12}", "-" * 38]
    for tag in ("ir", "ilp", "POC"):
        m = b[tag]
        L.append(f"{tag:<6} {str(m['shadow_dLoutdCin_mean']):>18} {str(m['neutral_dLout_mean']):>12}")
    L += ["",
          f"POC blue/magenta shadow δLc max = {s['bluemagenta']['POC_bluemagenta_shadow_dLc_max']} "
          f"(B2A floor ≈ {s['targets']['b2a_floor_bluemagenta']})",
          "",
          "── Measure A (abstract alone, between CLUT nodes) ──"]
    for g, m in s["measureA_between_nodes"].items():
        L.append(f"  g={g}: max|ΔL|={m['max_dL']} max|Δh|={m['max_dh']} max|ΔC vs policy|={m['max_dC_vs_expected']}")
    L += ["", "READ:",
          "- POC shadow near -ilp AND neutral near -ir  ⇒ trade-off broken (quadrant lower-left).",
          "- Measure A small |ΔL|,|Δh| & CLUT-resolution-stable ⇒ abstract faithful (not a LUT wall).",
          "- If A good but B bad ⇒ the B2A undoes it (voie B compromised — a first-order result)."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Luminance-priority abstract POC (measure only).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--boundary-csv", type=Path,
                    default=Path("/Users/vinz/Documents/PHOTO Ressources/HPZ9/bench_neutral_axis/na_boundary.csv"))
    a = ap.parse_args()
    run(a.dest, a.out_dir, a.boundary_csv)


if __name__ == "__main__":
    main()
