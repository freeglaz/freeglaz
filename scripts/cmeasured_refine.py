#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refine Cmeasured(L,h) at low L by bracketing + bisection — diagnosis only.

POLICY UNCHANGED (the whole point: refine the MEASUREMENT, keep the policy, then
rebench — the causal test). We do NOT redefine Cmeasured: same behavioural
criterion as the bench (roundtrip Lab→B2A1(dest)→device→A2B1(dest)→Lab stops
tracking the requested chroma), i.e. residual(C) = C - C_return(C) crosses the
bank threshold τ. Only the LOCALISATION improves: coarse C-step (4) recorded a
real boundary <4 as Cmeasured=0; bracketing+bisection resolves it. A value below
the step is NEVER silently a zero.

Vocabulary strict: Cgeom / Cmeasured / Csafe never merged. Terminology:
BELOW_CURRENT_RESOLUTION (not "zero"). GE-ON / Canson only. Dest-only (source
irrelevant to Cmeasured — established: neutral_axis_diag.py:192-193).

Outputs: refined_boundary.csv (L,h_N,Cmeasured,status,precision), convergence.json,
rebench summary vs the known hotspot (L5/C60) + 3 non-regression guards, verdict.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT)); sys.path.insert(0, str(_ROOT / "scripts"))

import abstract_poc as poc                                   # noqa: E402
from lib.z9_client import devicelink, xicclu                 # noqa: E402
from webapp.backend.services.scan_delta import ciede2000     # noqa: E402

TAU = 2.0                                                    # bank behavioural threshold (unchanged)
_SRC = _ROOT / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc"


# ─── batched roundtrip residual ──────────────────────────────────────────────
def residuals(dest, triples):
    """triples: list of (L,C,h) → residual = C - C_return (dest B2A1→A2B1)."""
    labs = [(L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))) for L, C, h in triples]
    dev = xicclu.run_xicclu(dest, labs, direction="b", intent="r", pcs="lab")
    ret = xicclu.run_xicclu(dest, dev, direction="f", intent="r", pcs="lab")
    return [triples[i][1] - math.hypot(ret[i][1], ret[i][2]) for i in range(len(triples))]


# ─── A. refine Cmeasured over the SAME (L,h) grid, bracketing + bisection ─────
def refine_grid(dest, Ls, hs, prec=0.25):
    scan = [0.5, 1, 2, 4, 8, 16, 32, 64, 100]
    # bracketing (batched): residual at all scan C for all cells
    triples, key = [], []
    for L in Ls:
        for h in hs:
            for C in scan:
                triples.append((L, C, h)); key.append((L, h))
    res = residuals(dest, triples)
    brackets = {}
    for i, (L, h) in enumerate(key):
        brackets.setdefault((L, h), []).append((triples[i][1], res[i]))
    cells = {}
    for (L, h), rs in brackets.items():
        rs.sort()
        lo, hi = 0.0, None
        for C, r in rs:
            if r >= TAU:
                hi = C; break
            lo = C
        if hi is None:
            cells[(L, h)] = {"lo": rs[-1][0], "hi": None, "status": "ABOVE_RANGE"}
        else:
            cells[(L, h)] = {"lo": lo, "hi": hi, "status": "RESOLVED"}
    # bisection (batched per iteration)
    for _ in range(40):
        mids, mk = [], []
        for (L, h), c in cells.items():
            if c["hi"] is not None and c["hi"] - c["lo"] > prec:
                mids.append((L, (c["lo"] + c["hi"]) / 2, h)); mk.append((L, h))
        if not mids:
            break
        r = residuals(dest, mids)
        for i, (L, h) in enumerate(mk):
            c = cells[(L, h)]; mid = mids[i][1]
            if r[i] >= TAU:
                c["hi"] = mid
            else:
                c["lo"] = mid
    out = {}
    for (L, h), c in cells.items():
        if c["status"] == "ABOVE_RANGE":
            out[(L, h)] = (round(c["lo"], 2), "RESOLVED", ">100 unresolved (huge gamut)")
        else:
            out[(L, h)] = (round((c["lo"] + c["hi"]) / 2, 2), "RESOLVED", round(c["hi"] - c["lo"], 3))
    return out


# ─── C. precision convergence test (L=5,8,10,15) ─────────────────────────────
def convergence(dest, Ls=(5, 8, 10, 15), hs=(30, 90, 128, 270)):
    conv = {}
    for L in Ls:
        for h in hs:
            row = {}
            for prec in (2.0, 1.0, 0.5, 0.25):
                row[prec] = refine_grid(dest, [L], [h], prec=prec)[(L, h)][0]
            conv[f"L{L}_h{h}"] = row
    return conv


# ─── refined boundary CSV → Cdriver ──────────────────────────────────────────
def write_boundary(path, refined, Ls, hs):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["L", "h_N", "Cmeasured", "status", "precision"])
        w.writeheader()
        for L in Ls:
            for h in hs:
                c, st, pr = refined[(L, h)]
                w.writerow({"L": L, "h_N": h, "Cmeasured": c, "status": st, "precision": pr})


# ─── D. rebench (policy strictly unchanged, only the driver changes) ─────────
def rebench(dest_icc, refined_csv, out_dir):
    with tempfile.TemporaryDirectory(prefix="cmref_") as tmp:
        tmp = Path(tmp)
        src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(_SRC.read_bytes()))
        dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest_icc.read_bytes()))
        cdriver = poc.Cdriver(refined_csv)                   # ONLY change: refined driver
        policy = poc.make_policy(cdriver)                    # SAME policy (soft-knee, if Cd<=0 identity, …)
        ab = tmp / "abstract_g33.icc"; ab.write_bytes(poc.build_abstract(policy, 33))

        # abstract-alone hotspot: L5, saturated (C_in high) — old ΔC≈−53, ΔE00≈21
        hot_pts, hot_meta = [], []
        for h in range(0, 360, 8):
            for C in (40, 50, 60):
                hot_pts.append((5.0, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))))
                hot_meta.append((5, C, h))
        lut = xicclu.run_xicclu(str(ab), hot_pts, direction="f", pcs="lab")
        worst = {"dE00": 0}
        for i, (L, a, b) in enumerate(hot_pts):
            La, aa, ba = policy(L, a, b)
            Ll, al, bl = lut[i]
            dC = math.hypot(al, bl) - math.hypot(aa, ba)
            dE = ciede2000((La, aa, ba), (Ll, al, bl))
            if dE > worst["dE00"]:
                worst = {"L": L, "C_in": hot_meta[i][1], "h": hot_meta[i][2],
                         "dC": round(dC, 2), "dE00": round(dE, 2)}

        # full-chain link (-s -ir -p refined abstract) — SAME command as POC
        poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], tmp / "poc.icc")
        Ls, hs, Cs = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30)), list(range(0, 61, 5))
        grid = []
        for L in Ls:
            for h in hs:
                for C in Cs:
                    if C == 0 and h != hs[0]:
                        continue
                    grid.append({"L": L, "C": C, "h": h,
                                 "lab": (L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))})
        _, lab = poc.measure_chain(tmp / "poc.icc", src_n, dst_n, grid)
        guards, rows = poc.metrics(grid, lab)

        # guard 3: hue Δh at Ct>10 / >20 (abstract alone, same as verdict jalon)
        vp, vm = [], []
        for L in (8, 13, 18, 23, 33, 52):
            for h in range(0, 360, 12):
                for C in (13, 22, 33, 47):
                    vp.append((L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))); vm.append((L, C, h))
        vlut = xicclu.run_xicclu(str(ab), vp, direction="f", pcs="lab")
        dh10, dh20 = [], []
        for i, (L, a, b) in enumerate(vp):
            La, aa, ba = policy(L, a, b); Ct = math.hypot(aa, ba)
            Ll, al, bl = vlut[i]
            dh = abs((math.degrees(math.atan2(bl, al)) - math.degrees(math.atan2(ba, aa)) + 180) % 360 - 180)
            if Ct > 10:
                dh10.append(dh)
            if Ct > 20:
                dh20.append(dh)
        dh10.sort(); dh20.sort()
        def p95(x): return round(x[min(len(x) - 1, int(0.95 * (len(x) - 1)))], 2) if x else None
        return {
            "abstract_hotspot_L5_worst": worst,
            "guards": {
                "shadow_dLoutdCin_mean": guards["shadow_dLoutdCin_mean"],
                "neutral_dLout_mean": guards["neutral_dLout_mean"],
                "hue_dh_P95_Ct10": p95(dh10), "hue_dh_P95_Ct20": p95(dh20),
            },
        }


def run(dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    dest_s = str(dest)
    Ls, hs = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30))    # SAME grid as na_boundary

    print("[cmref] A — refine Cmeasured (bracketing + bisection, τ=2, prec=0.25) …")
    refined = refine_grid(dest_s, Ls, hs, prec=0.25)
    n_zero_old = sum(1 for v in refined.values() if v[0] <= 0)
    write_boundary(out_dir / "refined_boundary.csv", refined, Ls, hs)
    print(f"[cmref]   refined cells: {len(refined)} ; Cmeasured<=0 now: {n_zero_old}")

    print("[cmref] C — precision convergence (L=5,8,10,15) …")
    conv = convergence(dest_s)

    print("[cmref] D — rebench POC (policy UNCHANGED, refined driver) …")
    reb = rebench(dest, out_dir / "refined_boundary.csv", out_dir)
    print(f"[cmref]   L5 abstract worst hotspot: {reb['abstract_hotspot_L5_worst']}")
    print(f"[cmref]   guards: {reb['guards']}")

    summary = {"dest": dest_s, "n_cells": len(refined), "n_nonpositive_after_refine": n_zero_old,
               "convergence": conv, "rebench": reb,
               "baseline_targets": {"shadow": 0.028, "neutral": 0.35, "hue_P95": "<~1",
                                    "old_L5_hotspot": {"dC": -53, "dE00": 21}}}
    (out_dir / "cmref_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _reading(out_dir / "cmref_reading.txt", summary)
    print("[cmref] wrote refined_boundary.csv, cmref_summary.json, cmref_reading.txt")
    return summary


def _reading(path, s):
    r = s["rebench"]; g = r["guards"]; w = r["abstract_hotspot_L5_worst"]
    L = ["# Cmeasured refinement (bracketing+bisection) — VERDICT", "",
         f"dest: {s['dest']}", "",
         f"A. refined cells: {s['n_cells']} ; Cmeasured≤0 after refine: {s['n_nonpositive_after_refine']} "
         f"(old coarse step produced artificial zeros — see na_boundary L5 h90/h120=0)", "",
         "D. rebench (policy STRICTLY unchanged, only Cmeasured coarse→refined):",
         f"  L5 abstract worst hotspot NOW: {w}   (OLD: ΔC≈-53, ΔE00≈21)", "",
         "§3 non-regression guards:",
         f"  shadow dLout/dCin = {g['shadow_dLoutdCin_mean']}  (target ≈ 0.028)",
         f"  neutral ΔL        = {g['neutral_dLout_mean']}  (target ≈ +0.35)",
         f"  hue Δh P95 Ct>10  = {g['hue_dh_P95_Ct10']} ; Ct>20 = {g['hue_dh_P95_Ct20']}  (target < ~1°)", "",
         "C. precision convergence (Cmeasured at prec 2/1/0.5/0.25):"]
    for k, row in s["convergence"].items():
        L.append(f"  {k}: " + " ".join(f"{p}→{v}" for p, v in row.items()))
    L += ["", "VERDICT:",
          "- A (resolution artefact): refined driver has no artificial zeros, the L5 chroma hotspot",
          "  collapses (ΔE00 ≫→ small), guards intact ⇒ it was a Cmeasured resolution artefact.",
          "- B (not resolution): hotspot persists despite a properly resolved boundary ⇒ a POLICY",
          "  design question (behaviour when Cd is truly tiny) — next jalon, not this one.",
          "(Concluded in the .md report from the figures above.)"]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Refine Cmeasured at low L (diagnosis only).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
