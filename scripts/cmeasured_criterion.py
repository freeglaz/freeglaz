#!/usr/bin/env python3
# freeglaz - open toolkit for the HP DesignJet Z9
# Copyright (C) 2026 The freeglaz contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cmeasured DEFINITION diagnostic (criterion τ vs intrinsic knee) — semantics.

POLICY UNCHANGED, single profile (Canson GE-ON), dest-only. Question: is τ=2 a
justified definition of Cmeasured, or a historical convention with a real effect
on the mapper? Is there an INTRINSIC boundary of Cret(Cin) (knee/slope-change)
that describes the behaviour better? NEVER choose a criterion on dLout/dCin alone.

Candidate boundaries per (L,h):
  - residual τ = 0.5 / 1 / 2  (C - Cret = τ, the existing bank criterion)
  - intrinsic KNEE of Cret(Cin) (sharp slope drop) — IF objectively identifiable
  - δLc-onset (C where δLc crosses 1) — REFERENCE only (the property we control;
    does NOT fix an εL product), to see which C-criterion tracks it.

Axes B (not just dLout/dCin): roundtrip fidelity inside (residual/δLc), continuity
in (L,h), knee detectability/sharpness, stability, δLc-vs-residual relationship.
§C indicative rebench of the 3 guards per τ (attribution, not selection).

Run: uv run python scripts/cmeasured_criterion.py --dest <icc> --out-dir <dir>
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

_SRC = _ROOT / "lib" / "z9_client" / "assets" / "LargeRGB-elle-V2-g18.icc"
_CFINE = [round(c * 0.5, 1) for c in range(0, 161)]          # 0..80 step 0.5


def curves(dest, Ls, hs):
    """Cret(Cin) and δLc(Cin) fine curves per (L,h)."""
    triples, key = [], []
    for L in Ls:
        for h in hs:
            for C in _CFINE:
                triples.append((L, C, h)); key.append((L, h))
    labs = [(L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h))) for L, C, h in triples]
    dev = xicclu.run_xicclu(dest, labs, direction="b", intent="r", pcs="lab")
    ret = xicclu.run_xicclu(dest, dev, direction="f", intent="r", pcs="lab")
    out = {}
    for i, (L, h) in enumerate(key):
        out.setdefault((L, h), []).append((triples[i][1], math.hypot(ret[i][1], ret[i][2]), ret[i][0]))
    for k in out:
        out[k].sort()
    return out


def boundaries(cur):
    """From one (L,h) curve [(Cin,Cret,Lret)]: residual-τ / knee / δLc-onset."""
    Cin = [p[0] for p in cur]; Cret = [p[1] for p in cur]; Lret = [p[2] for p in cur]
    Lref = Lret[0]
    res = [Cin[i] - Cret[i] for i in range(len(cur))]
    dLc = [Lret[i] - Lref for i in range(len(cur))]

    def cross(vals, thr):
        for i in range(1, len(vals)):
            if vals[i] >= thr:
                # linear interp on Cin
                if vals[i] == vals[i - 1]:
                    return round(Cin[i], 2)
                f = (thr - vals[i - 1]) / (vals[i] - vals[i - 1])
                return round(Cin[i - 1] + f * (Cin[i] - Cin[i - 1]), 2)
        return None

    # knee: sharpest drop of slope dCret/dCin (min second difference), + sharpness
    slope = [(Cret[i] - Cret[i - 1]) / (Cin[i] - Cin[i - 1]) if Cin[i] != Cin[i - 1] else 1
             for i in range(1, len(cur))]
    knee_i, knee_drop = None, 0.0
    win = 8                                                  # ~4 chroma window
    for i in range(win, len(slope) - win):
        before = sum(slope[i - win:i]) / win
        after = sum(slope[i:i + win]) / win
        drop = before - after
        if drop > knee_drop:
            knee_drop, knee_i = drop, i
    knee_C = round(Cin[knee_i], 2) if knee_i is not None else None
    return {
        "tau0.5": cross(res, 0.5), "tau1": cross(res, 1.0), "tau2": cross(res, 2.0),
        "knee_C": knee_C, "knee_sharpness": round(knee_drop, 3),
        "dLc_onset1": cross(dLc, 1.0),
    }


def fidelity_inside(cur, C_bound):
    """Mean/max residual & δLc for Cin <= C_bound (how faithful up to the boundary)."""
    if C_bound is None:
        return {"res_mean": None, "res_max": None, "dLc_mean": None, "dLc_max": None}
    Lref = cur[0][2]
    inside = [(Cin - Cret, Lret - Lref) for Cin, Cret, Lret in cur if Cin <= C_bound and Cin > 0]
    if not inside:
        return {"res_mean": 0, "res_max": 0, "dLc_mean": 0, "dLc_max": 0}
    r = [x[0] for x in inside]; d = [abs(x[1]) for x in inside]
    return {"res_mean": round(sum(r) / len(r), 3), "res_max": round(max(r), 3),
            "dLc_mean": round(sum(d) / len(d), 3), "dLc_max": round(max(d), 3)}


def continuity(bmap, Ls, hs, keyname):
    """Mean |Δboundary| between adjacent h (circular) and adjacent L, for a criterion."""
    dh, dL = [], []
    for L in Ls:
        vals = [bmap[(L, h)][keyname] for h in hs if bmap[(L, h)][keyname] is not None]
        for i in range(len(vals)):
            dh.append(abs(vals[i] - vals[(i + 1) % len(vals)]))
    for h in hs:
        vals = [bmap[(L, h)][keyname] for L in Ls if bmap[(L, h)][keyname] is not None]
        for i in range(1, len(vals)):
            dL.append(abs(vals[i] - vals[i - 1]))
    return {"mean_dh": round(sum(dh) / len(dh), 2) if dh else None,
            "mean_dL": round(sum(dL) / len(dL), 2) if dL else None}


def rebench_guard(dest_icc, boundary_csv, tmp):
    src_n = tmp / "src.icc"; src_n.write_bytes(devicelink.normalize_icc_for_argyll(_SRC.read_bytes()))
    dst_n = tmp / "dst.icc"; dst_n.write_bytes(devicelink.normalize_icc_for_argyll(dest_icc.read_bytes()))
    cdriver = poc.Cdriver(boundary_csv)
    policy = poc.make_policy(cdriver)
    ab = tmp / "ab.icc"; ab.write_bytes(poc.build_abstract(policy, 33))
    link = tmp / "poc.icc"
    poc.collink(["-v", "-qh", "-s", "-ir", "-p", str(ab), str(src_n), str(dst_n)], link)
    Ls, hs, Cs = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30)), list(range(0, 61, 5))
    grid = [{"L": L, "C": C, "h": h,
             "lab": (L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))}
            for L in Ls for h in hs for C in Cs if not (C == 0 and h != hs[0])]
    _, lab = poc.measure_chain(link, src_n, dst_n, grid)
    m, _ = poc.metrics(grid, lab)
    return {"shadow_dLoutdCin_mean": m["shadow_dLoutdCin_mean"], "neutral_dLout_mean": m["neutral_dLout_mean"]}


def write_boundary(path, bmap, Ls, hs, key):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["L", "h_N", "Cmeasured"]); w.writeheader()
        for L in Ls:
            for h in hs:
                v = bmap[(L, h)][key]
                w.writerow({"L": L, "h_N": h, "Cmeasured": v if v is not None else 0})


def run(dest: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    dest_s = str(dest)
    Ls, hs = [5, 10, 15, 20, 30, 50], list(range(0, 360, 30))

    print("[crit] measuring Cret/δLc curves over grid …")
    cur = curves(dest_s, Ls, hs)
    bmap = {k: boundaries(cur[k]) for k in cur}

    # axis B: fidelity inside for τ2 and knee ; knee detectability ; continuity
    print("[crit] axis-B metrics …")
    fid_tau2, fid_knee = [], []
    knee_sharp, knee_missing = [], 0
    dLc_minus_res = []                                       # relationship
    for k in cur:
        b = bmap[k]
        fid_tau2.append(fidelity_inside(cur[k], b["tau2"]))
        fid_knee.append(fidelity_inside(cur[k], b["knee_C"]))
        knee_sharp.append(b["knee_sharpness"])
        if b["knee_sharpness"] < 0.15:                       # no clean knee
            knee_missing += 1
        if b["tau2"] and b["dLc_onset1"]:
            dLc_minus_res.append(b["dLc_onset1"] - b["tau2"])

    def agg(lst, key):
        v = [x[key] for x in lst if x[key] is not None]
        return round(sum(v) / len(v), 3) if v else None

    cont = {c: continuity(bmap, Ls, hs, c) for c in ("tau0.5", "tau1", "tau2", "knee_C")}

    # §C indicative rebench per τ
    print("[crit] indicative rebench (guards) per τ …")
    rebench = {}
    for key in ("tau0.5", "tau1", "tau2"):
        with tempfile.TemporaryDirectory(prefix=f"crit_{key}_") as tmp:
            tmp = Path(tmp)
            bcsv = tmp / "b.csv"; write_boundary(bcsv, bmap, Ls, hs, key)
            try:
                rebench[key] = rebench_guard(dest, bcsv, tmp)
            except Exception as e:
                rebench[key] = {"error": str(e)[:120]}
        print(f"[crit]   {key}: {rebench[key]}")

    summary = {
        "dest": dest_s,
        "knee_detectability": {
            "mean_sharpness": round(sum(knee_sharp) / len(knee_sharp), 3),
            "n_cells_no_clean_knee": knee_missing, "n_cells": len(cur),
        },
        "fidelity_inside": {
            "tau2": {"res_mean": agg(fid_tau2, "res_mean"), "dLc_mean": agg(fid_tau2, "dLc_mean"),
                     "dLc_max": agg(fid_tau2, "dLc_max")},
            "knee": {"res_mean": agg(fid_knee, "res_mean"), "dLc_mean": agg(fid_knee, "dLc_mean"),
                     "dLc_max": agg(fid_knee, "dLc_max")},
        },
        "continuity": cont,
        "dLc_onset_minus_tau2_mean": round(sum(dLc_minus_res) / len(dLc_minus_res), 2) if dLc_minus_res else None,
        "rebench_guards": rebench,
        "per_cell": {f"L{L}_h{h}": bmap[(L, h)] for L in Ls for h in hs},
    }
    (out_dir / "criterion_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with open(out_dir / "boundaries.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["L", "h", "tau0.5", "tau1", "tau2", "knee_C",
                                          "knee_sharpness", "dLc_onset1"])
        w.writeheader()
        for L in Ls:
            for h in hs:
                w.writerow({"L": L, "h": h, **bmap[(L, h)]})
    _reading(out_dir / "criterion_reading.txt", summary)
    print("[crit] wrote criterion_summary.json, boundaries.csv, criterion_reading.txt")
    return summary


def _reading(path, s):
    k = s["knee_detectability"]; f = s["fidelity_inside"]; c = s["continuity"]; r = s["rebench_guards"]
    L = ["# Cmeasured definition — criterion τ vs intrinsic knee (VERDICT A/B/C/D)", "",
         f"dest: {s['dest']}", "",
         "Knee detectability of Cret(Cin):",
         f"  mean sharpness={k['mean_sharpness']} ; cells with NO clean knee={k['n_cells_no_clean_knee']}/{k['n_cells']}",
         f"  → a clean intrinsic knee is {'NOT universal' if k['n_cells_no_clean_knee'] else 'universal'}"
         " (low L has gradual roll-off, high L has sharp knee).", "",
         "Fidelity INSIDE the boundary (residual / δLc up to the boundary):",
         f"  τ=2 : res_mean={f['tau2']['res_mean']} δLc_mean={f['tau2']['dLc_mean']} δLc_max={f['tau2']['dLc_max']}",
         f"  knee: res_mean={f['knee']['res_mean']} δLc_mean={f['knee']['dLc_mean']} δLc_max={f['knee']['dLc_max']}",
         "  (knee lets δLc grow large inside ⇒ knee is too permissive for the luminance goal.)", "",
         "Continuity of the boundary surface (mean |Δ| adjacent h / L):"]
    for crit, v in c.items():
        L.append(f"  {crit:<8}: mean_dh={v['mean_dh']} mean_dL={v['mean_dL']}")
    L += ["",
          f"δLc-onset(1) − τ2 boundary (mean): {s['dLc_onset_minus_tau2_mean']}  "
          "(≈0 ⇒ τ2 tracks the L-recoupling onset; >0 ⇒ τ2 conservative)", "",
          "§C indicative rebench guards per τ (NOT the selection criterion):"]
    for key, v in r.items():
        L.append(f"  {key}: {v}")
    L += ["", "VERDICT (A/B/C/D) — concluded in the .md report on the axes above, never on dLout/dCin alone."]
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Cmeasured criterion diagnostic (semantics).")
    ap.add_argument("--dest", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    a = ap.parse_args()
    run(a.dest, a.out_dir)


if __name__ == "__main__":
    main()
